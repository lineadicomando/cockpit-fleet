#!/bin/sh
# Notifications: a failed run reaches a Telegram chat, a webhook, a mail
# server and a Matrix room (stand-ins on this machine, notify-sinks.py), and
# a channel that cannot be reached is tried again, by the tick, without
# holding up the others. Secrets stay out of the database and the journal.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# It waits for the timer: about three minutes.
set -u
. "$(dirname "$0")/lib.sh"
require_root
SRC=/srv/fleet-it
SINKS=/run/fleet-it-sinks
require_wipe

stop_sinks() { pkill -f "notify-sinks.py $SINKS" 2>/dev/null; rm -rf "$SINKS"; }
cleanup() { stop_sinks; wipe; }
trap cleanup EXIT
cleanup
prepare

op() { ctl itoperator "$@"; }
count() { if [ -f "$SINKS/$1" ]; then grep -c -- "${2:-}" "$SINKS/$1"; else echo 0; fi; }
wait_count() {  # wait_count FILE PATTERN N SECONDS
    n=0
    while [ "$n" -lt "$4" ] && [ "$(count "$1" "$2")" -lt "$3" ]; do sleep 1; n=$((n + 1)); done
}
channel() {  # channel JSON SECRET: the id of a new channel, its secret set
    answer=$(admin channel.create "$1")
    id=$(get "$answer" 'a["data"]["id"]')
    [ -n "$2" ] && printf '%s' "$2" | sudo -u itadmin sudo -n "$SECRET" set "$(get "$answer" 'a["data"]["secret_name"]')"
    echo "$id"
}

# --- the units ------------------------------------------------------------------
expect_eq "the path unit is enabled by the package" "$(systemctl is-enabled cockpit-fleet-notify.path)" enabled
expect_eq "the path unit is active" "$(systemctl is-active cockpit-fleet-notify.path)" active
expect_eq "the delivery runs as the service account" \
    "$(systemctl show -P User cockpit-fleet-notify.service)" cockpit-fleet
check=$(admin selfcheck)
expect_eq "selfcheck sees the path unit" "$(get "$check" '[c["status"] for c in a["data"]["checks"] if c["id"] == "notify"][0]')" ok
denied=$(admin notification.deliver)
expect_eq "nobody but the unit delivers" "$(get "$denied" 'a["error"]["code"]')" unavailable

# --- the services ---------------------------------------------------------------
mkdir -p "$SINKS"
python3 "$(dirname "$0")/notify-sinks.py" "$SINKS" 8025 2525 &
sleep 1

denied=$(op channel.create '{"name":"x","kind":"webhook","settings":{"url":"http://127.0.0.1:8025/x"}}')
expect_eq "an operator does not create a channel" "$(get "$denied" 'a["error"]["code"]')" forbidden

telegram=$(channel '{"name":"telegram","kind":"telegram","language":"it","settings":{"chat_id":"-1001","server":"http://127.0.0.1:8025"}}' "4242:TELEGRAMTOKEN")
webhook=$(channel '{"name":"webhook","kind":"webhook","settings":{"url":"http://127.0.0.1:8025/hook/$secret","template":"{\"text\": \"$title\", \"events\": \"$events\"}"}}' "WEBHOOKPATHSECRET")
mail=$(channel '{"name":"mail","kind":"smtp","settings":{"host":"127.0.0.1","port":2525,"security":"none","sender":"fleet@example.org","recipients":["ops@example.org"]}}' "")
matrix=$(channel '{"name":"matrix","kind":"matrix","settings":{"homeserver":"http://127.0.0.1:8025","room_id":"!room:example.org"}}' "syt_MATRIXTOKEN")
late=$(channel '{"name":"late","kind":"webhook","settings":{"url":"http://127.0.0.1:8026/late"}}' "")
secret_file=/etc/cockpit-fleet/secrets/notify/n-$telegram-token.cred
expect_ok "a channel's secret is kept apart from those of runs" test -f "$secret_file"
expect_eq "readable by the service account as ciphertext only" "$(stat -c '%U:%G %a' "$secret_file")" "root:cockpit-fleet 640"
for id in "$telegram" "$webhook" "$mail" "$matrix" "$late"; do
    admin rule.create "{\"name\":\"to-$id\",\"channel_id\":\"$id\",\"events\":[\"failed\"]}" >/dev/null
done
expect_eq "five rules" "$(db "SELECT count(*) FROM notify_rules")" 5

# --- a run that fails -----------------------------------------------------------
mkdir -p "$SRC/notify"
cat > "$SRC/notify/site.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.fail:
        msg: on purpose
YAML
chown -R root:root "$SRC" && chmod -R a+rX "$SRC"
project=$(admin project.create '{"name":"notify","kind":"local","path":"/srv/fleet-it/notify"}')
inventory=$(admin inventory.create '{"name":"local","kind":"managed","content":"all:\n  hosts:\n    localhost:\n      ansible_connection: local\n"}')
template=$(admin template.create "{\"name\":\"boom\",\"project_id\":\"$(get "$project" 'a["data"]["id"]')\",\"playbook\":\"site.yml\",\"inventory_id\":\"$(get "$inventory" 'a["data"]["id"]')\"}")
run=$(op run.start "{\"template_id\":\"$(get "$template" 'a["data"]["id"]')\"}")
rid=$(get "$run" 'a["data"]["id"]')
expect_run "the run fails" "$rid" 60 failed
started=$(date '+%Y-%m-%d %H:%M:%S')

wait_count http.jsonl sendMessage 1 20
wait_count smtp.jsonl "" 1 10
wait_count http.jsonl _matrix 1 10
expect_eq "Telegram got it, with the token in the path" "$(count http.jsonl '/bot4242:TELEGRAMTOKEN/sendMessage')" 1
expect_eq "in Italian" "$(count http.jsonl 'Run fallita: boom')" 1
expect_eq "the webhook got it, its secret in the URL" "$(count http.jsonl '/hook/WEBHOOKPATHSECRET')" 1
hook=$(grep '/hook/' "$SINKS/http.jsonl" | head -n 1)
expect_eq "with its template filled in" "$(get "$hook" 'json.loads(a["body"])["events"][0]["run_id"]')" "$rid"
expect_eq "the mail server got it" "$(count smtp.jsonl 'Subject: Fleet: Run failed: boom')" 1
expect_eq "Matrix got it, with the token as a bearer" "$(count http.jsonl 'Bearer syt_MATRIXTOKEN')" 1
expect_eq "four notifications sent" "$(db "SELECT count(*) FROM notifications WHERE state = 'sent'")" 4
expect_eq "the one that cannot be reached waits" \
    "$(db "SELECT state || ' ' || attempts FROM notifications WHERE channel_id = '$late'")" "pending 1"
expect_eq "and says why" "$(db "SELECT substr(error, 1, 14) FROM notifications WHERE channel_id = '$late'")" "cannot connect"
expect_eq "the link points at the run" "$(count http.jsonl "/fleet#/runs/$rid")" 3

for token in TELEGRAMTOKEN WEBHOOKPATHSECRET MATRIXTOKEN; do
    expect_denied "$token is not in the database" grep -qa "$token" "$DB" "$DB-wal"
    expect_denied "$token is not in the journal" sh -c "journalctl --since '$started' -o verbose | grep -q $token"
done

# --- tried again by the tick ----------------------------------------------------
python3 "$(dirname "$0")/notify-sinks.py" "$SINKS" 8026 &
wait_count http.jsonl '"port": 8026' 1 150
expect_eq "the late channel got it once it answered" "$(count http.jsonl '"port": 8026')" 1
expect_eq "and nothing went twice" "$(count http.jsonl '"method"')" 4
expect_eq "five notifications sent" "$(db "SELECT count(*) FROM notifications WHERE state = 'sent'")" 5

# --- a test message -------------------------------------------------------------
denied=$(op channel.test "{\"id\":\"$telegram\"}")
expect_eq "an operator does not send a test" "$(get "$denied" 'a["error"]["code"]')" forbidden
admin channel.test "{\"id\":\"$telegram\"}" >/dev/null
wait_count http.jsonl 'Messaggio di prova' 1 15
expect_eq "a test message is sent at once" "$(count http.jsonl 'Messaggio di prova')" 1
expect_eq "the audit trail has who sent it" "$(db "SELECT user FROM audit WHERE command = 'channel.test' AND outcome = 'ok'")" itadmin

# --- the secret goes before the channel -----------------------------------------
refused=$(admin channel.delete "{\"id\":\"$telegram\"}")
expect_eq "a channel with a secret is not deleted" "$(get "$refused" 'a["error"]["code"]')" conflict
sudo -u itadmin sudo -n "$SECRET" delete "n-$telegram-token"
gone=$(admin channel.delete "{\"id\":\"$telegram\"}")
expect_eq "deleted with its rule" "$(get "$gone" 'a["ok"]'):$(db "SELECT count(*) FROM notify_rules")" "True:4"

finish
