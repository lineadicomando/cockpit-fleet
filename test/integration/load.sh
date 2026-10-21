#!/bin/sh
# Load test: real check cycles, started by the timer, on many hosts. The
# hosts are aliases of the controller reached with the local connection, so
# the modules measure the sandbox of a run; beside them a few Linux nodes
# over SSH (containers, see nodes.sh) show what opening connections adds.
# Every IT_LOAD_SAMPLE seconds it reads system.health and the database,
# times the calls the interface makes, and writes one line to samples.jsonl;
# at the end it says whether the cycles kept up. It DELETES the database, the
# runs and the projects: set IT_WIPE=yes to accept. IT_KEEP=yes leaves it all.
#
#   IT_LOAD_HOSTS     aliases (200)            IT_LOAD_MINUTES   how long (60)
#   IT_LOAD_INTERVAL  seconds between checks (600)
#   IT_LOAD_SSH       nodes over SSH (3; 0 for none)
#   IT_LOAD_SAMPLE    seconds between samples (60)
#   IT_LOAD_OUT       where the samples go (/var/tmp/fleet-load/DATE-HOSTS)
#
# Ten checks per host: nine of the catalog in one task per host (seven
# kinds, disk and certificates twice with other parameters), and a
# playbook of the user in cycles of its own. For a day, start it so that it outlives the session:
#   systemd-run --unit fleet-load --setenv IT_WIPE=yes --setenv IT_LOAD_HOSTS=500 \
#       --setenv IT_LOAD_MINUTES=1440 sh /tmp/it/integration/load.sh
#   journalctl -u fleet-load -f
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe
HERE=$(cd "$(dirname "$0")" && pwd)
HOSTS=${IT_LOAD_HOSTS:-200}
MINUTES=${IT_LOAD_MINUTES:-60}
INTERVAL=${IT_LOAD_INTERVAL:-600}
SSH=${IT_LOAD_SSH:-3}
SAMPLE=${IT_LOAD_SAMPLE:-60}
OUT=${IT_LOAD_OUT:-/var/tmp/fleet-load/$(date +%Y%m%d-%H%M)-$HOSTS}
KEYS=/run/fleet-load-keys
# Two intervals: the first cycles, and the sizing that follows them.
WARMUP=$((2 * INTERVAL))

cleanup() { wipe; [ "$SSH" -gt 0 ] && sh "$HERE/nodes.sh" down 2>/dev/null; rm -rf "$KEYS"; }
[ "${IT_KEEP:-}" = yes ] || trap cleanup EXIT
cleanup
prepare
mkdir -p "$OUT" "$KEYS"
echo "$HOSTS aliases, $SSH nodes over SSH, every $INTERVAL s, for $MINUTES min; samples in $OUT"
cat > "$OUT/settings" <<EOF
hosts=$HOSTS ssh=$SSH interval=$INTERVAL minutes=$MINUTES sample=$SAMPLE
package=$(dpkg-query -W -f '${Version}' cockpit-fleet 2>/dev/null || rpm -q cockpit-fleet)
cpus=$(nproc) memory_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
EOF
cat "$OUT/settings"

# --- what the checks read ----------------------------------------------------------
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 365 \
    -subj /CN=fleet-load -keyout "$KEYS/cert.key" -out /etc/fleet-load.pem 2>/dev/null
chmod 0644 /etc/fleet-load.pem
mkdir -p /srv/fleet-it/load
cat > /srv/fleet-it/load/load.yml <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - name: Read the load
      ansible.builtin.command: cat /proc/loadavg
      register: load
      changed_when: false
    - lineadicomando.fleet.fleet_result:
        summary: "load {{ load.stdout.split()[0] }}"
        metrics:
          - {name: load1, value: "{{ load.stdout.split()[0] }}"}
YAML
chmod -R a+rX /srv/fleet-it
project=$(get "$(admin project.create '{"name": "load", "kind": "local", "path": "/srv/fleet-it/load"}')" 'a["data"]["id"]')

# --- the inventories -----------------------------------------------------------------
hosts=$(python3 -c '
import json, sys
n = int(sys.argv[1])
lines = ["[load]"] + [f"load{i:04d}" for i in range(1, n + 1)]
lines += ["[load:vars]", "ansible_connection=local", "ansible_python_interpreter=/usr/bin/python3"]
print(json.dumps({"name": "load", "kind": "managed", "format": "ini", "content": "\n".join(lines) + "\n"}))' "$HOSTS")
local_id=$(get "$(admin inventory.create "$hosts")" 'a["data"]["id"]')
expect_run "the inventory of aliases is read" \
    "$(get "$(admin inventory.parse "{\"id\": \"$local_id\"}")" 'a["data"]["run_id"]')" 300 successful

ssh_id= cred_id=
if [ "$SSH" -gt 0 ]; then
    ssh-keygen -q -t ed25519 -N '' -C fleet-load -f "$KEYS/key"
    cp "$KEYS/key.pub" "$KEYS/authorized_keys"
    chmod 0644 "$KEYS/authorized_keys"
    sh "$HERE/nodes.sh" up "$SSH" "$KEYS/authorized_keys"
    content='[ssh]\n'
    i=1
    while [ "$i" -le "$SSH" ]; do
        podman cp /etc/fleet-load.pem "fleet-node-$i:/etc/fleet-load.pem"
        content="$content""node$i ansible_host=$(sh "$HERE/nodes.sh" address "$i")\\n"
        i=$((i + 1))
    done
    cred=$(admin credential.create '{"name": "nodes", "kind": "machine", "username": "ansible"}')
    cred_id=$(get "$cred" 'a["data"]["id"]')
    set_secret "$cred" private_key < "$KEYS/key"
    ssh_id=$(get "$(admin inventory.create "{\"name\": \"ssh\", \"kind\": \"managed\", \"format\": \"ini\", \"content\": \"$content\"}")" 'a["data"]["id"]')
    expect_run "the inventory of nodes is read" \
        "$(get "$(admin inventory.parse "{\"id\": \"$ssh_id\"}")" 'a["data"]["run_id"]')" 60 successful
    printf '%s' "$(admin hostkey.scan "{\"inventory_id\": \"$ssh_id\"}")" | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({k: key[k] for k in ("host", "key_type", "key")})
    subprocess.run(["sudo", "-u", "itadmin", "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL"
fi
expect_eq "every host is registered" "$(get "$(admin host.list)" 'len(a["data"]["hosts"])')" $((HOSTS + SSH))

# --- the checks ------------------------------------------------------------------------
checks() {  # checks PREFIX INVENTORY_ID [CREDENTIAL_ID]: names are of the whole controller
    prefix=$1; shift
    base="\"inventory_id\": \"$1\", \"interval\": $INTERVAL, \"retry_interval\": $INTERVAL"
    [ -n "${2:-}" ] && base="$base, \"machine_credential_id\": \"$2\""
    # The aliases are this machine: its processor and memory under the load
    # of the test would make warnings that say nothing about the cycles.
    off='{"warning": null, "critical": null}'
    for spec in \
        '"name": "reach", "kind": "reachability"' \
        '"name": "disk", "kind": "disk"' \
        '"name": "disk-root", "kind": "disk", "params": {"mounts": ["/"]}' \
        "\"name\": \"cpu\", \"kind\": \"cpu\", \"thresholds\": {\"cpu_pct\": $off}" \
        "\"name\": \"memory\", \"kind\": \"memory\", \"thresholds\": {\"memory_used_pct\": $off, \"swap_used_pct\": $off}" \
        '"name": "uptime", "kind": "uptime"' \
        '"name": "clock", "kind": "clock"' \
        '"name": "cert", "kind": "certificates", "params": {"paths": ["/etc/fleet-load.pem"]}' \
        '"name": "cert-again", "kind": "certificates", "params": {"paths": ["/etc/fleet-load.pem"]}' \
        "\"name\": \"load\", \"kind\": \"playbook\", \"project_id\": \"$project\", \"playbook\": \"load.yml\""
    do
        made=$(admin check.create "{$(echo "$spec" | sed "s/\"name\": \"/&$prefix-/"), $base}")
        [ "$(get "$made" 'a["ok"]')" = True ] || fail "a check is created: $made"
    done
}
checks l "$local_id"
[ -n "$ssh_id" ] && checks s "$ssh_id" "$cred_id"
expect_eq "every host has ten states" "$(db "SELECT count(*) FROM check_states")" $(((HOSTS + SSH) * 10))

# --- let the timer run, and sample ----------------------------------------------------
host_id=$(db "SELECT id FROM hosts WHERE name = 'load0001'")
disk_id=$(db "SELECT id FROM checks WHERE name = 'l-disk' AND inventory_id = '$local_id'")
timed() {  # timed NAME COMMAND [JSON]: the time the operator waits for it, in ms
    start=$(date +%s%N)
    ctl itoperator "$2" "${3:-}" > "$OUT/answer.json"
    echo "$1 $((($(date +%s%N) - start) / 1000000))" >> "$OUT/timings"
}
end=$(($(date +%s) + MINUTES * 60))
while [ "$(date +%s)" -lt "$end" ]; do
    next=$(($(date +%s) + SAMPLE))
    : > "$OUT/timings"
    timed host.list host.list
    timed state.list state.list
    timed run.list run.list '{"kind": "check"}'
    timed metric.query metric.query "{\"host_id\": \"$host_id\", \"check_id\": \"$disk_id\", \"name\": \"used_pct\", \"instance\": \"/\"}"
    timed system.health system.health
    if python3 "$HERE/load-sample.py" sample "$DB" "$OUT/answer.json" "$OUT/timings" "$OUT/last.json" > "$OUT/new.json"; then
        cp "$OUT/new.json" "$OUT/last.json"
        cat "$OUT/new.json" >> "$OUT/samples.jsonl"
        python3 -c '
import json, sys
s = json.load(open(sys.argv[1]))
q = {x["lane"]: x for x in s["queue"]}
print("%s  states %d (unchecked %d, late %d)  cycles %d  queue %d+%d  cpu %s%%  mem %d MB  db %d MB  ui %s" % (
    __import__("time").strftime("%H:%M"), s["states"]["total"], s["states"]["unchecked"],
    s["states"]["late"], len(s["cycles"]), q["checks"]["running"], q["checks"]["queued"],
    s["cpu_busy_pct"], s["mem_available_mb"], s["database"].get("bytes", 0) >> 20,
    max(s["ui_ms"].values())))' "$OUT/last.json"
    else
        fail "a sample is taken"
    fi
    while [ "$(date +%s)" -lt "$next" ]; do sleep 1; done
done

echo
journalctl --since "-$((MINUTES + 30))min" -o cat --no-pager | grep -E '^(checks|maintenance): ' \
    | sort | uniq -c | sort -rn | head -n 20 > "$OUT/journal"
if [ -s "$OUT/journal" ]; then echo "what the scheduler said in the journal:"; cat "$OUT/journal"; echo; fi
python3 "$HERE/load-sample.py" summary "$OUT/samples.jsonl" "$INTERVAL" "$WARMUP" > "$OUT/summary" || FAILED=1
cat "$OUT/summary"
echo "samples and summary in $OUT"
finish
