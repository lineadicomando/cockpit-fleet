#!/bin/sh
# Schedules and the timer of the scheduler: roles, ten schedules on the same
# minute under the limit of the queue, times missed while the controller was
# off, overlap.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# It waits for the timer: about three minutes.
set -u
. "$(dirname "$0")/lib.sh"
require_root
SRC=/srv/fleet-it
require_wipe

cleanup() { systemctl start cockpit-fleet-scheduler.timer; wipe; }
trap cleanup EXIT
cleanup
prepare 2

op() { ctl itoperator "$@"; }
as_service_sql() {  # writes as the account that owns the database, never as root
    sudo -u cockpit-fleet python3 -c "import sqlite3,sys; c=sqlite3.connect('$DB'); c.execute(sys.argv[1]); c.commit()" "$1"
}
max_running=0
watch_queue() {  # watch_queue SECONDS: the most runs seen running at once
    n=0
    while [ "$n" -lt "$1" ]; do
        r=$(db "SELECT count(*) FROM runs WHERE status = 'running'")
        [ "$r" -gt "$max_running" ] && max_running=$r
        [ "$(db "SELECT count(*) FROM runs WHERE status IN ('queued','running')")" -eq 0 ] && break
        sleep 1; n=$((n + 1))
    done
}
next_minute() {  # sleep until a few seconds after the next tick
    sleep $((62 - $(date +%-S)))
}

# --- the timer ------------------------------------------------------------------
expect_eq "the timer is enabled by the package" "$(systemctl is-enabled cockpit-fleet-scheduler.timer)" enabled
expect_eq "the timer is active" "$(systemctl is-active cockpit-fleet-scheduler.timer)" active
expect_eq "the tick runs as the service account" \
    "$(systemctl show -P User cockpit-fleet-scheduler.service)" cockpit-fleet
check=$(admin selfcheck)
expect_eq "selfcheck sees the timer" "$(get "$check" '[c["status"] for c in a["data"]["checks"] if c["id"] == "scheduler"][0]')" ok

# --- a project that waits a little ----------------------------------------------
mkdir -p "$SRC/sched"
cat > "$SRC/sched/site.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.pause:
        seconds: 4
YAML
chown -R root:root "$SRC" && chmod -R a+rX "$SRC"
project=$(admin project.create '{"name":"sched","kind":"local","path":"/srv/fleet-it/sched"}')
inventory=$(admin inventory.create '{"name":"local","kind":"managed","content":"all:\n  hosts:\n    localhost:\n      ansible_connection: local\n"}')
template=$(admin template.create "{\"name\":\"wait\",\"project_id\":\"$(get "$project" 'a["data"]["id"]')\",\"playbook\":\"site.yml\",\"inventory_id\":\"$(get "$inventory" 'a["data"]["id"]')\",\"survey\":[{\"name\":\"note\",\"type\":\"text\"},{\"name\":\"token\",\"type\":\"secret\"}]}")
tid=$(get "$template" 'a["data"]["id"]')

# --- roles and checks -----------------------------------------------------------
denied=$(op schedule.create "{\"name\":\"x\",\"template_id\":\"$tid\",\"calendar\":\"daily\"}")
expect_eq "an operator does not create a schedule" "$(get "$denied" 'a["error"]["code"]')" forbidden
bad=$(admin schedule.create "{\"name\":\"x\",\"template_id\":\"$tid\",\"calendar\":\"every tuesday\"}")
expect_eq "systemd-analyze refuses a calendar it cannot read" "$(get "$bad" 'a["error"]["code"]')" bad_request
bad=$(admin schedule.create "{\"name\":\"x\",\"template_id\":\"$tid\",\"calendar\":\"daily\",\"timezone\":\"Europe/Nowhere\"}")
expect_eq "an unknown time zone is refused" "$(get "$bad" 'a["error"]["code"]')" bad_request
bad=$(admin schedule.create "{\"name\":\"x\",\"template_id\":\"$tid\",\"calendar\":\"daily\",\"answers\":{\"token\":\"a-00000000000000000000000000000000\"}}")
expect_eq "a secret answer is not stored" "$(get "$bad" 'a["error"]["code"]')" bad_request
preview=$(op schedule.preview '{"cron":"30 6 * * 1-5","timezone":"Europe/Rome","count":3}')
expect_eq "a cron line is converted" "$(get "$preview" 'a["data"]["calendar"]')" "Mon..Fri *-*-* 06:30:00"
expect_eq "with its next three times" "$(get "$preview" 'len(a["data"]["next"])')" 3

# --- ten on the same minute -----------------------------------------------------
for i in 0 1 2 3 4 5 6 7 8 9; do
    admin schedule.create "{\"name\":\"s$i\",\"template_id\":\"$tid\",\"calendar\":\"*:*:00\",\"answers\":{\"note\":\"n$i\"},\"priority\":$((i % 3))}" >/dev/null
done
expect_eq "ten schedules" "$(db "SELECT count(*) FROM schedules WHERE next_at IS NOT NULL")" 10
next_minute
expect_eq "the tick launched all ten" "$(db "SELECT count(*) FROM runs WHERE schedule_id IS NOT NULL")" 10
for id in $(db "SELECT group_concat(id, ' ') FROM schedules"); do op schedule.disable "{\"id\":\"$id\"}" >/dev/null; done
expect_eq "an operator turns them off" "$(db "SELECT count(*) FROM schedules WHERE enabled = 0 AND next_at IS NULL")" 10
watch_queue 120
expect_eq "never more than two at once" "$max_running" 2
expect_eq "all ten successful" "$(db "SELECT count(*) FROM runs WHERE schedule_id IS NOT NULL AND status = 'successful'")" 10
expect_eq "as their schedule" "$(db "SELECT created_by FROM runs WHERE title = 'wait' ORDER BY created_at LIMIT 1" | cut -c1-10)" "schedule:s"
expect_eq "with the stored answers" "$(db "SELECT count(*) FROM runs WHERE options LIKE '%\"note\": \"n%'")" 10
expect_eq "the first to start had the highest priority" \
    "$(db "SELECT priority FROM runs WHERE schedule_id IS NOT NULL ORDER BY started_at LIMIT 1")" 2
expect_eq "the ticks left no audit record" "$(db "SELECT count(*) FROM audit WHERE command = 'scheduler.tick'")" 0

# --- run now ----------------------------------------------------------------------
sid=$(db "SELECT id FROM schedules WHERE name = 's0'")
run=$(op schedule.run "{\"id\":\"$sid\"}")
rid=$(get "$run" 'a["data"]["id"]')
expect_eq "run now is the operator's" "$(get "$run" 'a["data"]["created_by"]')" itoperator
expect_run "and it ends" "$rid" 60 successful
expect_eq "it is in the audit trail" "$(db "SELECT count(*) FROM audit WHERE command = 'schedule.run' AND user = 'itoperator'")" 1

# --- missed while the controller was off ------------------------------------------
systemctl stop cockpit-fleet-scheduler.timer
for name in s1 s2; do op schedule.enable "{\"id\":\"$(db "SELECT id FROM schedules WHERE name = '$name'")\"}" >/dev/null; done
as_service_sql "UPDATE schedules SET catch_up = 1 WHERE name = 's1'"
as_service_sql "UPDATE schedules SET next_at = strftime('%s', 'now') - 3600 WHERE name IN ('s1', 's2')"
before=$(db "SELECT count(*) FROM runs")
systemctl start cockpit-fleet-scheduler.service
expect_eq "caught up once" "$(db "SELECT last_outcome FROM schedules WHERE name = 's1'")" launched
expect_eq "or recorded as missed" "$(db "SELECT last_outcome FROM schedules WHERE name = 's2'")" missed
expect_eq "one run, not one per minute" "$(( $(db "SELECT count(*) FROM runs") - before ))" 1
expect_eq "both planned from now" "$(db "SELECT count(*) FROM schedules WHERE name IN ('s1','s2') AND next_at > strftime('%s', 'now')")" 2

# --- overlap ----------------------------------------------------------------------
watch_queue 60
cat > "$SRC/sched/site.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.pause:
        seconds: 300
YAML
for name in s1 s2; do op schedule.disable "{\"id\":\"$(db "SELECT id FROM schedules WHERE name = '$name'")\"}" >/dev/null; done
long=$(admin schedule.create "{\"name\":\"long\",\"template_id\":\"$tid\",\"calendar\":\"*:*:00\"}")
lid=$(get "$long" 'a["data"]["id"]')
systemctl start cockpit-fleet-scheduler.timer
next_minute
expect_eq "the first time launches" "$(db "SELECT last_outcome FROM schedules WHERE id = '$lid'")" launched
next_minute
expect_eq "the next is skipped while it runs" "$(db "SELECT last_outcome FROM schedules WHERE id = '$lid'")" skipped
expect_eq "so one run only" "$(db "SELECT count(*) FROM runs WHERE schedule_id = '$lid'")" 1
as_service_sql "UPDATE schedules SET overlap = 'cancel' WHERE id = '$lid'"
first=$(db "SELECT id FROM runs WHERE schedule_id = '$lid'")
next_minute
expect_eq "with cancel, the previous one is canceled" "$(wait_status "$first" 20)" canceled
expect_eq "and a new one launched" "$(db "SELECT count(*) FROM runs WHERE schedule_id = '$lid' AND status IN ('queued','running')")" 1
op schedule.disable "{\"id\":\"$lid\"}" >/dev/null
for id in $(db "SELECT group_concat(id, ' ') FROM runs WHERE status IN ('queued','running')"); do admin run.cancel "{\"id\":\"$id\"}" >/dev/null; done

# --- deletion -----------------------------------------------------------------------
del=$(admin template.delete "{\"id\":\"$tid\"}")
expect_eq "a scheduled template is not deleted" "$(get "$del" 'a["error"]["code"]')" conflict
admin schedule.delete "{\"id\":\"$lid\"}" >/dev/null
expect_eq "the runs of a deleted schedule stay" "$(db "SELECT count(*) FROM runs WHERE id = '$first'")" 1

finish
