#!/bin/sh
# Checks: hosts from an inventory, cycles started by the tick of the timer, the
# catalog on Linux nodes (containers, see nodes.sh), an unreachable host told
# about once, and a disk that fills up told about within two intervals, then
# its recovery, to a webhook (a stand-in on this machine, notify-sinks.py); a
# cycle slower than its interval, which skips and is told to fall behind.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# It waits for the timer: about ten minutes. IT_KEEP=yes leaves it all behind.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe
HERE=$(dirname "$0")
KEYS=/run/fleet-it-keys
SINKS=/run/fleet-it-sinks
NODES=3
DATA=4  # a node with a small filesystem of its own, to fill

stop_sinks() { pkill -f "notify-sinks.py $SINKS" 2>/dev/null; rm -rf "$SINKS"; }
cleanup() { stop_sinks; wipe; sh "$HERE/nodes.sh" down 2>/dev/null; rm -rf "$KEYS"; }
# With IT_KEEP=yes what it made is left in place, to be looked at.
[ "${IT_KEEP:-}" = yes ] || trap cleanup EXIT
cleanup
prepare
printf '[checks]\nreminder_hours = 0\nlate_after = 1\n' >> "$CONFIG"

op() { ctl itoperator "$@"; }
count() { if [ -f "$SINKS/$1" ]; then grep -c -- "${2:-}" "$SINKS/$1"; else echo 0; fi; }
wait_count() {  # wait_count FILE PATTERN N SECONDS
    n=0
    while [ "$n" -lt "$4" ] && [ "$(count "$1" "$2")" -lt "$3" ]; do sleep 1; n=$((n + 1)); done
    echo "$n"
}
state() {  # state HOST CHECK: its state and its hard state
    db "SELECT coalesce(s.state, 'none') || ' ' || s.hard_state FROM check_states s
        JOIN hosts h ON h.id = s.host_id JOIN checks c ON c.id = s.check_id
        WHERE h.name = '$1' AND c.name = '$2'"
}
summary() {
    db "SELECT s.summary FROM check_states s JOIN hosts h ON h.id = s.host_id
        JOIN checks c ON c.id = s.check_id WHERE h.name = '$1' AND c.name = '$2'"
}
wait_state() {  # wait_state HOST CHECK STATE SECONDS: until its last result is STATE (a number)
    n=0
    while [ "$n" -lt "$4" ] && [ "$(state "$1" "$2" | cut -d' ' -f1)" != "$3" ]; do
        sleep 2; n=$((n + 2))
    done
}

# --- the nodes ------------------------------------------------------------------
mkdir -p "$KEYS"
ssh-keygen -q -t ed25519 -N '' -C fleet-it -f "$KEYS/key"
cp "$KEYS/key.pub" "$KEYS/authorized_keys"
chmod 0644 "$KEYS/authorized_keys"
sh "$HERE/nodes.sh" up "$NODES" "$KEYS/authorized_keys"
podman run -d --quiet --name "fleet-node-$DATA" --hostname "node$DATA" \
    --ip "$(sh "$HERE/nodes.sh" address "$DATA")" --cap-add AUDIT_WRITE \
    --tmpfs /data:size=16m -v "$KEYS/authorized_keys:/home/ansible/.ssh/authorized_keys:ro,z" \
    localhost/fleet-node:trixie >/dev/null
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3 \
    -subj /CN=fleet-it -keyout "$KEYS/cert.key" -out "$KEYS/cert.pem" 2>/dev/null
podman cp "$KEYS/cert.pem" fleet-node-1:/etc/fleet-it.pem
n=0
until ssh-keyscan -T 1 "$(sh "$HERE/nodes.sh" address "$DATA")" >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
    sleep 1; n=$((n + 1))
done

cred=$(admin credential.create '{"name": "nodes", "kind": "machine", "username": "ansible"}')
cred_id=$(get "$cred" 'a["data"]["id"]')
set_secret "$cred" private_key < "$KEYS/key"
hosts='[web]\n'
i=1
while [ "$i" -le "$NODES" ]; do
    hosts="$hosts""node$i ansible_host=$(sh "$HERE/nodes.sh" address "$i")\\n"
    i=$((i + 1))
done
hosts="$hosts""[data]\\nnode$DATA ansible_host=$(sh "$HERE/nodes.sh" address "$DATA")\\n"
hosts="$hosts""[gone]\\nghost ansible_host=10.88.0.199\\n"
inventory=$(admin inventory.create "{\"name\": \"nodes\", \"kind\": \"managed\", \"format\": \"ini\", \"content\": \"$hosts\"}")
inventory_id=$(get "$inventory" 'a["data"]["id"]')
parse=$(admin inventory.parse "{\"id\": \"$inventory_id\"}")
expect_run "the inventory is read" "$(get "$parse" 'a["data"]["run_id"]')" 60 successful
expect_eq "its hosts are registered" "$(get "$(admin host.list)" 'len(a["data"]["hosts"])')" 5
scan=$(admin hostkey.scan "{\"inventory_id\": \"$inventory_id\"}")
printf '%s' "$scan" | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({k: key[k] for k in ("host", "key_type", "key")})
    subprocess.run(["sudo", "-u", "itadmin", "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL"

# --- the channel ----------------------------------------------------------------
mkdir -p "$SINKS"
python3 "$HERE/notify-sinks.py" "$SINKS" 8025 2525 </dev/null >/dev/null 2>&1 &
sleep 1
channel=$(get "$(admin channel.create '{"name":"hook","kind":"webhook","settings":{"url":"http://127.0.0.1:8025/hook","template":"{\"title\": \"$title\", \"text\": \"$text\"}"}}')" 'a["data"]["id"]')
admin rule.create "{\"name\":\"checks\",\"channel_id\":\"$channel\",\"events\":[\"problem\",\"recovery\"],\"scope\":\"group\",\"scope_id\":\"$inventory_id\",\"group\":\"all\"}" >/dev/null
admin rule.create "{\"name\":\"runs\",\"channel_id\":\"$channel\",\"events\":[\"failed\"]}" >/dev/null
admin rule.create "{\"name\":\"late\",\"channel_id\":\"$channel\",\"events\":[\"late\",\"caught_up\"],\"scope\":\"inventory\",\"scope_id\":\"$inventory_id\"}" >/dev/null

# --- the checks -----------------------------------------------------------------
create() { admin check.create "$1"; }
base="\"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\", \"interval\": 60, \"retry_interval\": 30"
denied=$(op check.create "{\"name\": \"x\", \"kind\": \"disk\", $base}")
expect_eq "an operator does not configure checks" "$(get "$denied" 'a["error"]["code"]')" forbidden
created=$(create "{\"name\": \"reach\", \"kind\": \"reachability\", $base, \"max_attempts\": 1}")
expect_eq "a check is created" "$(get "$created" 'a["ok"]')" True
# With become: pipelined through sudo too.
create "{\"name\": \"disk\", \"kind\": \"disk\", $base, \"group\": \"data\", \"become\": true, \"max_attempts\": 2, \"params\": {\"mounts\": [\"/data\"]}}" >/dev/null
create "{\"name\": \"cert\", \"kind\": \"certificates\", $base, \"group\": \"web\", \"max_attempts\": 1, \"params\": {\"paths\": [\"/etc/fleet-it.pem\"]}}" >/dev/null
# The nodes are containers, which show them the memory and the processor of
# whatever runs the controller: with no thresholds, the state says that the
# module answered, not how busy that machine is.
off='{"warning": null, "critical": null}'
create "{\"name\": \"memory\", \"kind\": \"memory\", $base, \"group\": \"web\", \"thresholds\": {\"memory_used_pct\": $off, \"swap_used_pct\": $off}}" >/dev/null
create "{\"name\": \"cpu\", \"kind\": \"cpu\", $base, \"group\": \"web\", \"thresholds\": {\"cpu_pct\": $off}}" >/dev/null
for kind in uptime clock; do
    create "{\"name\": \"$kind\", \"kind\": \"$kind\", $base, \"group\": \"web\"}" >/dev/null
done
# A check of the user: a playbook of a project, which ends with fleet_result.
mkdir -p /srv/fleet-it/own
cat > /srv/fleet-it/own/logins.yml <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - name: Count the accounts with a shell
      ansible.builtin.command: grep -c /bin/bash /etc/passwd
      register: accounts
      changed_when: false
    - lineadicomando.fleet.fleet_result:
        summary: "{{ accounts.stdout }} accounts with a shell"
        metrics:
          - {name: accounts, value: "{{ accounts.stdout }}"}
YAML
chmod -R a+rX /srv/fleet-it
own=$(get "$(admin project.create '{"name": "own", "kind": "local", "path": "/srv/fleet-it/own"}')" 'a["data"]["id"]')
create "{\"name\": \"logins\", \"kind\": \"playbook\", $base, \"group\": \"web\", \"max_attempts\": 1, \"project_id\": \"$own\", \"playbook\": \"logins.yml\", \"thresholds\": {\"accounts\": {\"warning\": 2, \"critical\": null}}}" >/dev/null
wrong=$(create "{\"name\": \"x\", \"kind\": \"disk\", $base, \"group\": \"nope\"}")
expect_eq "a group the inventory lacks is refused" "$(get "$wrong" 'a["error"]["code"]')" conflict
expect_eq "every host of each group has a state" "$(db "SELECT count(*) FROM check_states")" 24

# --- the first cycles, started by the timer ---------------------------------------
n=0
while [ "$n" -lt 150 ] && [ "$(db "SELECT count(*) FROM check_states WHERE state IS NULL")" != 0 ]; do
    sleep 3; n=$((n + 3))
done
expect_eq "the timer checked everything" "$(db "SELECT count(*) FROM check_states WHERE state IS NULL")" 0
expect_eq "in cycles of their own" "$(db "SELECT count(*) > 0 FROM runs WHERE kind = 'check' AND created_by = 'system'")" 1
cycle=$(db "SELECT id FROM runs WHERE kind = 'check' ORDER BY created_at LIMIT 1")
expect_eq "which the list of runs leaves out" "$(get "$(admin run.list)" "'$cycle' in [r['id'] for r in a['data']['runs']]")" False
expect_eq "unless asked for" "$(get "$(admin run.list '{"kind": "check"}')" "'$cycle' in [r['id'] for r in a['data']['runs']]")" True
expect_eq "a cycle runs in the sandbox" "$(journalctl -u "cockpit-fleet-run@$cycle.service" -o cat | grep -c 'TASK \[Checks\]')" 1
expect_eq "what a cycle took is kept" "$(db "SELECT count(*) FROM cycles WHERE run_id = '$cycle'")" 1
expect_eq "its memory read from the sandbox" \
    "$(db "SELECT memory_peak > 10000000 AND memory_peak < 4000000000 FROM cycles WHERE run_id = '$cycle'")" 1
expect_eq "and its processor time" "$(db "SELECT cpu_usec > 0 FROM cycles WHERE run_id = '$cycle'")" 1
expect_eq "the platform comes from the first cycle" "$(db "SELECT platform FROM hosts WHERE name = 'node1'")" linux
expect_eq "and the system" "$(db "SELECT os LIKE 'Debian GNU/Linux 13%' FROM hosts WHERE name = 'node1'")" 1
for check in memory cpu uptime clock; do
    expect_eq "$check is ok on node1" "$(state node1 $check)" "0 0"
done
expect_eq "the clock is within a second or two" \
    "$(db "SELECT json_extract(metrics, '\$[0].value') < 3 FROM check_states s JOIN checks c ON c.id = s.check_id WHERE c.name = 'clock' LIMIT 1")" 1
expect_eq "a certificate that expires in three days is critical" "$(state node1 cert)" "2 2"
expect_eq "one that cannot be read is unknown" "$(state node2 cert)" "3 3"
expect_eq "the disk of node$DATA is ok" "$(state "node$DATA" disk)" "0 0"
# Only the catalog reaches this node, pipelined, with become and without:
# nothing is copied to it.
expect_denied "the catalog leaves no temporary files on a node" \
    podman exec "fleet-node-$DATA" test -e /home/ansible/.ansible/tmp
# root and ansible have bash on the nodes: over the warning of 2 accounts.
expect_eq "a playbook of the user gives its result, thresholds applied" "$(state node1 logins)" "1 1"
expect_eq "with its own summary" "$(summary node1 logins | cut -c1-20)" "accounts 2 (warning "
expect_ok "in a cycle of its own" test "$(db "SELECT count(*) FROM runs WHERE kind = 'check' AND title LIKE 'logins:%'")" -gt 0
expect_eq "the unreachable host is critical" "$(state ghost reach)" "2 2"
wait_count http.jsonl 'reach on ghost' 1 20 >/dev/null
expect_eq "and told about once" "$(count http.jsonl 'reach on ghost')" 1
expect_eq "the reachable ones are not" "$(count http.jsonl 'reach on node')" 0

# --- a disk that fills up ---------------------------------------------------------
podman exec "fleet-node-$DATA" dd if=/dev/zero of=/data/fill bs=1M count=16 2>/dev/null
filled=$(date +%s)
waited=$(wait_count http.jsonl "disk on node$DATA" 1 200)
took=$(( $(date +%s) - filled ))
expect_eq "a full disk is told about" "$(count http.jsonl "disk on node$DATA")" 1
expect_ok "within two intervals ($took s)" test "$took" -le 150
expect_eq "as a hard critical" "$(state "node$DATA" disk)" "2 2"
expect_eq "saying which threshold" "$(summary "node$DATA" disk | cut -c1-17)" "used_pct on /data"
disk_id=$(db "SELECT id FROM checks WHERE name = 'disk'")
host_id=$(db "SELECT id FROM hosts WHERE name = 'node$DATA'")
ack=$(op state.ack "{\"host_id\": \"$host_id\", \"check_id\": \"$disk_id\", \"comment\": \"on it\"}")
expect_eq "an operator acknowledges it" "$(get "$ack" 'a["ok"]')" True
expect_eq "which the audit trail records" "$(db "SELECT user FROM audit WHERE command = 'state.ack' AND outcome = 'ok'")" itoperator

podman exec "fleet-node-$DATA" rm -f /data/fill
now=$(op check.run "{\"id\": \"$disk_id\", \"host_id\": \"$host_id\"}")
expect_eq "an operator checks again at once" "$(get "$now" 'len(a["data"]["runs"])')" 1
wait_count http.jsonl "Recovered: disk on node$DATA" 1 90 >/dev/null
expect_eq "the recovery is told about" "$(count http.jsonl "Recovered: disk on node$DATA")" 1
expect_eq "and ends the acknowledgement" "$(db "SELECT ack_by FROM check_states WHERE host_id = '$host_id'")" ""
expect_eq "a cycle is not told about as a failed run" "$(count http.jsonl 'Run failed')" 0
series=$(op metric.query "{\"host_id\": \"$host_id\", \"check_id\": \"$disk_id\", \"name\": \"used_pct\", \"instance\": \"/data\"}")
expect_eq "the use of the disk is kept as a time series" "$(get "$series" 'a["data"]["resolution"]')" raw
expect_eq "with the full disk among its values" \
    "$(get "$series" 'max(p[1] for p in a["data"]["series"][0]["points"]) >= 90')" True
expect_eq "and its hourly aggregate" \
    "$(db "SELECT max(h.high) >= 90 FROM metric_hourly h JOIN metric_series s ON s.id = h.series_id WHERE s.host_id = '$host_id' AND s.instance = '/data' AND s.name = 'used_pct'")" 1
expect_ok "later cycles are sized on what the first ones took" \
    test "$(db "SELECT count(*) FROM runs WHERE kind = 'check' AND json_extract(options, '\$.forks') IS NOT NULL")" -gt 0
expect_eq "the database gives back free pages a few at a time" "$(db "PRAGMA auto_vacuum")" 2
expect_eq "and the maintenance of the ticks never failed" \
    "$(journalctl --since "-30min" -o cat --no-pager | grep -c '^maintenance: .* failed')" 0

# --- a cycle slower than its interval ---------------------------------------------
expect_eq "no cycle fell behind so far" "$(count http.jsonl 'Falling behind')" 0
cat > /srv/fleet-it/own/slow.yml <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.command: sleep 45
      changed_when: false
    - ansible.builtin.command: sleep 45
      changed_when: false
    - lineadicomando.fleet.fleet_result:
        summary: slow
YAML
chmod a+r /srv/fleet-it/own/slow.yml
slow=$(get "$(create "{\"name\": \"slow\", \"kind\": \"playbook\", $base, \"group\": \"web\", \"project_id\": \"$own\", \"playbook\": \"slow.yml\"}")" 'a["data"]["id"]')
wait_count http.jsonl 'Falling behind: check cycles of nodes, slow' 1 240 >/dev/null
expect_eq "a cycle still running when due again is told to fall behind" \
    "$(count http.jsonl 'Falling behind: check cycles of nodes, slow')" 1
expect_ok "and says so in the journal at each skip" \
    test "$(journalctl --since "-10min" -o cat --no-pager | grep -c 'a cycle of nodes, slow has not ended when due again')" -ge 1
health=$(op system.health)
expect_eq "the page of health shows it behind" \
    "$(get "$health" '[g["behind"] is not None for g in a["data"]["groups"] if g["group"] == "'"$slow"'"]')" "[True]"
expect_eq "with the due times it skipped" \
    "$(get "$health" '[g["skipped"] > 0 for g in a["data"]["groups"] if g["group"] == "'"$slow"'"]')" "[True]"
expect_eq "and the queue of each lane" "$(get "$health" '[q["lane"] for q in a["data"]["queue"]]')" "['runs', 'checks', 'slow']"
expect_eq "and the size of the database" "$(get "$health" 'a["data"]["database"]["bytes"] > 0')" True
# Quick again: the next cycle ends in time, and the group is on time.
sed -i 's/sleep 45/true/' /srv/fleet-it/own/slow.yml
wait_count http.jsonl 'Back on time: check cycles of nodes, slow' 1 300 >/dev/null
expect_eq "a cycle that ends in time is told to be back on time" \
    "$(count http.jsonl 'Back on time: check cycles of nodes, slow')" 1
expect_eq "what the slow cycle skipped is kept with what it took" \
    "$(db "SELECT sum(skipped) > 0 FROM cycles WHERE grp = '$slow'")" 1
expect_eq "and the group is no longer behind" "$(db "SELECT count(*) FROM cycle_lag WHERE grp = '$slow'")" 0
admin check.delete "{\"id\": \"$slow\"}" >/dev/null

# --- what stays out ----------------------------------------------------------------
expect_denied "the private key is not in the database" grep -qa "$(sed -n 2p "$KEYS/key")" "$DB" "$DB-wal"
expect_eq "a host that cannot be reached leaves no unit failed" \
    "$(systemctl list-units --failed --plain --no-legend 'cockpit-fleet-run@*' | wc -l)" 0
expect_eq "nor a cycle failed" "$(db "SELECT count(*) FROM runs WHERE kind = 'check' AND status IN ('failed', 'error')")" 0

finish
