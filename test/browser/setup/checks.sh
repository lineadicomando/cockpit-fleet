#!/bin/sh
# The data of the scenario checks-cycles: a classroom of three PCs and a file
# server (containers, nodes.sh), the inventory "laboratorio" that names them,
# the checks of the catalog on it, a disk of the server nearly full, a template
# of maintenance run once; then two weeks of history (backfill.py), so that the
# overview and the charts have a past. Runs as root on the controller of
# 'browser.sh up', through 'browser.sh setup checks'; what an account does goes
# through fleetctl as BROWSER_USER, as in the interface. Starts from nothing: it
# deletes the database, the runs and the projects first.
set -eu
IT=${IT_DIR:-/it}
DIR=${BROWSER_DIR:-/browser}
. "$IT/lib.sh"
require_root
USER=${BROWSER_USER:-tester}
KEYS=/run/fleet-browser-keys
NODES=3
SERVER=4
as() { ctl "$USER" "$@"; }
ok() {  # ok WHAT ANSWER: the id of what was made, or stop
    id=$(get "$2" 'a["data"]["id"]')
    [ -n "$id" ] || { echo "setup: $1 failed: $2" >&2; exit 1; }
    echo "$id"
}

echo "clearing"
sh "$IT/nodes.sh" down 2>/dev/null || true
wipe || true  # lib.sh is written for set -u alone
rm -rf "$KEYS" /srv/browser
sed -i 's|^#\{0,1\}local_roots = .*|local_roots = ["/srv/browser"]|' "$CONFIG"

echo "nodes"
mkdir -p "$KEYS"
ssh-keygen -q -t ed25519 -N '' -C fleet-browser -f "$KEYS/key"
cp "$KEYS/key.pub" "$KEYS/authorized_keys"
chmod 0644 "$KEYS/authorized_keys"
sh "$IT/nodes.sh" up "$NODES" "$KEYS/authorized_keys" >/run/fleet-browser-nodes.log 2>&1 ||
    { tail -n 20 /run/fleet-browser-nodes.log >&2; exit 1; }
podman run -d --quiet --name "fleet-node-$SERVER" --hostname srv-dati \
    --ip "$(sh "$IT/nodes.sh" address "$SERVER")" --cap-add AUDIT_WRITE --tmpfs /data:size=64m \
    -v "$KEYS/authorized_keys:/home/ansible/.ssh/authorized_keys:ro,z" localhost/fleet-node:trixie >/dev/null
# The archive of the server fills 88% of its disk: above the warning (85%).
podman exec "fleet-node-$SERVER" dd if=/dev/zero of=/data/archivio-2026.tar bs=1M count=56 status=none
n=0
until ssh-keyscan -T 1 "$(sh "$IT/nodes.sh" address "$SERVER")" >/dev/null 2>&1 || [ "$n" -ge 30 ]; do
    sleep 1; n=$((n + 1))
done

echo "credential, inventory, host keys"
cred=$(as credential.create '{"name": "chiave-nodi", "kind": "machine", "username": "ansible"}')
cred_id=$(ok credential "$cred")
sudo -u "$USER" sudo -n "$SECRET" set "$(get "$cred" 'a["data"]["secret_names"]["private_key"]')" < "$KEYS/key"
hosts='[aula]\n'
i=1
while [ "$i" -le "$NODES" ]; do
    hosts="${hosts}aula-pc0$i ansible_host=$(sh "$IT/nodes.sh" address "$i")\\n"
    i=$((i + 1))
done
hosts="${hosts}[server]\\nsrv-dati ansible_host=$(sh "$IT/nodes.sh" address "$SERVER")\\n"
inventory_id=$(ok inventory "$(as inventory.create "{\"name\": \"laboratorio\", \"kind\": \"managed\", \"format\": \"ini\", \"content\": \"$hosts\"}")")
parse=$(as inventory.parse "{\"id\": \"$inventory_id\"}")
[ "$(wait_status "$(get "$parse" 'a["data"]["run_id"]')" 90)" = successful ] || { echo "setup: the inventory was not read" >&2; exit 1; }
as hostkey.scan "{\"inventory_id\": \"$inventory_id\"}" | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({k: key[k] for k in ("host", "key_type", "key")})
    subprocess.run(["sudo", "-u", sys.argv[2], "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL" "$USER"

echo "checks"
base="\"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\", \"interval\": 120, \"retry_interval\": 60"
check() { ok "check $1" "$(as check.create "{\"name\": \"$1\", \"kind\": \"$2\", $base$3}")" >/dev/null; }
check raggiungibile reachability ', "max_attempts": 1'
check disco disk ', "group": "aula", "become": true'
check disco-dati disk ', "group": "server", "become": true, "max_attempts": 1, "params": {"mounts": ["/data"]}'
# The nodes are containers, which show them the memory and the processor of
# the machine that runs them: no thresholds, the value only.
off='{"warning": null, "critical": null}'
check memoria memory ", \"thresholds\": {\"memory_used_pct\": $off, \"swap_used_pct\": $off}"
check cpu cpu ", \"thresholds\": {\"cpu_pct\": $off}"
check uptime uptime ''
check orologio clock ''

echo "a template of maintenance"
mkdir -p /srv/browser/manutenzione
cat > /srv/browser/manutenzione/pulizia.yml <<'YAML'
- name: Pulizia dei file temporanei
  hosts: all
  gather_facts: false
  tasks:
    - name: Trova i file temporanei più vecchi di una settimana
      ansible.builtin.find:
        paths: /tmp
        age: 1w
      register: vecchi
    - name: Riepilogo
      ansible.builtin.debug:
        msg: "{{ vecchi.matched }} file da togliere"
YAML
chmod -R a+rX /srv/browser
project_id=$(ok project "$(as project.create '{"name": "manutenzione", "kind": "local", "path": "/srv/browser/manutenzione"}')")
template_id=$(ok template "$(as template.create "{\"name\": \"pulizia-temporanei\", \"project_id\": \"$project_id\", \"playbook\": \"pulizia.yml\", \"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\"}")")
run_id=$(ok run "$(as run.start "{\"template_id\": \"$template_id\"}")")

echo "waiting for the first cycles"
n=0
while [ "$n" -lt 300 ] && [ "$(db "SELECT count(*) FROM check_states WHERE state IS NULL")" != 0 ]; do
    sleep 5; n=$((n + 5))
done
[ "$(db "SELECT count(*) FROM check_states WHERE state IS NULL")" = 0 ] ||
    { echo "setup: the checks did not run within 5 minutes" >&2; exit 1; }
[ "$(wait_status "$run_id" 120)" = successful ] || echo "setup: the maintenance run did not succeed" >&2

echo "two weeks of history"
# As the service's account: a file of the database made by root (its log)
# would stop the service.
sudo -u cockpit-fleet python3 -I "$DIR/backfill.py" "$DB" --days 14 --user "$USER"
echo "ready: $(db "SELECT count(*) FROM hosts") hosts, $(db "SELECT count(*) FROM checks") checks"
