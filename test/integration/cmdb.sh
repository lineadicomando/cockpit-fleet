#!/bin/sh
# The inventory of hosts: what Linux nodes (containers, see nodes.sh) are made
# of, their updates, and drift. A cmdb, an updates and a compliance check run
# from the timer, in cycles of their own; a package removed by hand is a change
# of the CMDB, a managed file changed by hand is drift at the next cycle, with
# its diff for an operator and not for a viewer.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# It waits for the timer: about four minutes. IT_KEEP=yes leaves it all behind.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe
HERE=$(dirname "$0")
KEYS=/run/fleet-it-keys
NODES=2

cleanup() { wipe; userdel -r itviewer 2>/dev/null; sh "$HERE/nodes.sh" down 2>/dev/null; rm -rf "$KEYS"; }
[ "${IT_KEEP:-}" = yes ] || trap cleanup EXIT
cleanup
prepare
useradd -m -G cockpit-fleet-viewer itviewer

state() {  # state HOST CHECK: its last state, or none
    db "SELECT coalesce(s.state, 'none') FROM check_states s JOIN hosts h ON h.id = s.host_id
        JOIN checks c ON c.id = s.check_id WHERE h.name = '$1' AND c.name = '$2'"
}
summary() {
    db "SELECT s.summary FROM check_states s JOIN hosts h ON h.id = s.host_id
        JOIN checks c ON c.id = s.check_id WHERE h.name = '$1' AND c.name = '$2'"
}
checked() {  # checked HOST CHECK: when its last result came
    db "SELECT coalesce(s.checked_at, 0) FROM check_states s JOIN hosts h ON h.id = s.host_id
        JOIN checks c ON c.id = s.check_id WHERE h.name = '$1' AND c.name = '$2'"
}
wait_after() {  # wait_after HOST CHECK TIME SECONDS: until a result newer than TIME
    n=0
    while [ "$n" -lt "$4" ] && python3 -c "import sys; sys.exit(float(sys.argv[1]) > float(sys.argv[2]))" \
            "$(checked "$1" "$2")" "$3"; do
        sleep 3; n=$((n + 3))
    done
}

# --- the nodes ------------------------------------------------------------------
mkdir -p "$KEYS"
ssh-keygen -q -t ed25519 -N '' -C fleet-it -f "$KEYS/key"
cp "$KEYS/key.pub" "$KEYS/authorized_keys"
chmod 0644 "$KEYS/authorized_keys"
sh "$HERE/nodes.sh" up "$NODES" "$KEYS/authorized_keys"
for i in 1 2; do podman exec "fleet-node-$i" sh -c 'printf "managed by the baseline\n" > /etc/fleet-managed'; done

cred=$(admin credential.create '{"name": "nodes", "kind": "machine", "username": "ansible"}')
cred_id=$(get "$cred" 'a["data"]["id"]')
set_secret "$cred" private_key < "$KEYS/key"
hosts="[web]\\nnode1 ansible_host=$(sh "$HERE/nodes.sh" address 1)\\nnode2 ansible_host=$(sh "$HERE/nodes.sh" address 2)\\n"
inventory_id=$(get "$(admin inventory.create "{\"name\": \"nodes\", \"kind\": \"managed\", \"format\": \"ini\", \"content\": \"$hosts\"}")" 'a["data"]["id"]')
parse=$(admin inventory.parse "{\"id\": \"$inventory_id\"}")
expect_run "the inventory is read" "$(get "$parse" 'a["data"]["run_id"]')" 60 successful
scan=$(admin hostkey.scan "{\"inventory_id\": \"$inventory_id\"}")
printf '%s' "$scan" | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({k: key[k] for k in ("host", "key_type", "key")})
    subprocess.run(["sudo", "-u", "itadmin", "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL"

# A compliance playbook: what the managed file must say.
mkdir -p /srv/fleet-it/baseline
cat > /srv/fleet-it/baseline/baseline.yml <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - name: The managed file
      ansible.builtin.copy:
        content: "managed by the baseline\n"
        dest: /etc/fleet-managed
        mode: "0644"
YAML
chmod -R a+rX /srv/fleet-it
project=$(get "$(admin project.create '{"name": "baseline", "kind": "local", "path": "/srv/fleet-it/baseline"}')" 'a["data"]["id"]')

# --- the checks -----------------------------------------------------------------
base="\"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\", \"interval\": 60, \"max_attempts\": 1"
admin check.create "{\"name\": \"cmdb\", \"kind\": \"cmdb\", $base}" >/dev/null
admin check.create "{\"name\": \"updates\", \"kind\": \"updates\", $base}" >/dev/null
created=$(admin check.create "{\"name\": \"baseline\", \"kind\": \"compliance\", $base, \"become\": true, \"project_id\": \"$project\", \"playbook\": \"baseline.yml\"}")
expect_eq "a compliance check is created" "$(get "$created" 'a["ok"]')" True
refused=$(admin check.create "{\"name\": \"x\", \"kind\": \"updates\", $base, \"params\": {\"refresh\": \"yes\"}}")
expect_eq "a flag must be true or false" "$(get "$refused" 'a["error"]["code"]')" bad_request

n=0
while [ "$n" -lt 150 ] && [ "$(db "SELECT count(*) FROM check_states WHERE state IS NULL")" != 0 ]; do
    sleep 3; n=$((n + 3))
done
expect_eq "the timer checked everything" "$(db "SELECT count(*) FROM check_states WHERE state IS NULL")" 0
expect_ok "the slow kinds run in cycles of their own" test \
    "$(db "SELECT count(*) FROM runs WHERE kind = 'check' AND json_extract(options, '\$.slow') = 1")" -ge 3
expect_eq "none failed" "$(db "SELECT count(*) FROM runs WHERE kind = 'check' AND status IN ('failed', 'error')")" 0

# --- what the nodes are made of ----------------------------------------------------
expect_eq "the cmdb check is ok" "$(state node1 cmdb)" 0
host1=$(db "SELECT id FROM hosts WHERE name = 'node1'")
detail=$(ctl itviewer cmdb.get "{\"host_id\": \"$host1\"}")
expect_eq "a viewer sees what a host is made of" "$(get "$detail" 'a["ok"]')" True
expect_eq "its packages, from dpkg" "$(get "$detail" 'a["data"]["hardware"]["package_manager"]')" dpkg
expect_ok "openssh-server among them" test "$(get "$detail" 'len([p for p in a["data"]["package_list"] if p["name"] == "openssh-server"])')" = 1
expect_ok "its processors and memory" test "$(get "$detail" 'a["data"]["hardware"]["cpus"] > 0 and a["data"]["hardware"]["memory"] > 0')" = True
expect_eq "with no change at the first snapshot" "$(get "$detail" 'len(a["data"]["changes"])')" 0
found=$(ctl itviewer cmdb.packages '{"name": "openssh-serv"}')
expect_eq "a package is found on both nodes" "$(get "$found" 'sorted(p["host"] for p in a["data"]["packages"])')" "['node1', 'node2']"

# The image of the nodes keeps no package lists: apt knows of no update.
expect_eq "the updates check is ok" "$(state node1 updates)" 0
expect_eq "saying what it asked" "$(summary node1 updates)" "0 updates, 0 for security (apt)"
expect_eq "the node is compliant" "$(state node1 baseline)" 0

# --- changes by hand ----------------------------------------------------------------
since=$(db "SELECT max(checked_at) FROM check_states")
podman exec fleet-node-1 dpkg -r git >/dev/null 2>&1
podman exec fleet-node-1 sh -c 'printf "changed by hand\n" > /etc/fleet-managed'
wait_after node1 cmdb "$since" 120
wait_after node1 baseline "$since" 120
changes=$(ctl itviewer cmdb.get "{\"host_id\": \"$host1\"}")
expect_eq "a package removed by hand is a change" \
    "$(get "$changes" '[(c["area"], c["before"] != "", c["after"]) for c in a["data"]["changes"] if c["name"].startswith("git ")]')" \
    "[('package', True, '')]"
expect_eq "a managed file changed by hand is drift at the next cycle" "$(state node1 baseline)" 1
expect_eq "saying how much" "$(summary node1 baseline)" "changed 1 (warning 1) (1 task would change)"
expect_eq "the other node is still compliant" "$(state node2 baseline)" 0
denied=$(ctl itviewer drift.get "{\"host_id\": \"$host1\"}")
expect_eq "a viewer does not see the diff" "$(get "$denied" 'a["error"]["code"]')" forbidden
drift=$(ctl itoperator drift.get "{\"host_id\": \"$host1\"}")
expect_eq "an operator does" "$(get "$drift" 'a["data"]["drift"][0]["changes"][0]["task"]')" "The managed file"
expect_eq "with what changed" "$(get "$drift" '"-changed by hand" in a["data"]["drift"][0]["changes"][0]["diff"]')" True
expect_eq "the file was not touched: check mode" "$(podman exec fleet-node-1 cat /etc/fleet-managed)" "changed by hand"
listed=$(ctl itviewer cmdb.list)
expect_eq "the list says which host drifted" \
    "$(get "$listed" 'sorted((h["name"], h["compliant"]) for h in a["data"]["hosts"])')" "[('node1', False), ('node2', True)]"

expect_eq "no cycle failed" "$(db "SELECT count(*) FROM runs WHERE kind = 'check' AND status IN ('failed', 'error')")" 0
expect_eq "nor left a unit failed" "$(systemctl list-units --failed --plain --no-legend 'cockpit-fleet-run@*' | wc -l)" 0

finish
