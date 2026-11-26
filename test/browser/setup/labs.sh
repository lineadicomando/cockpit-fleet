#!/bin/sh
# The data of the scenario labs-launch (phase 21, the inventory chosen at
# launch): the public project ansible-win_edulab from GitHub, two labs as
# two inventories written in Fleet, "aula-1" with win11 (the teacher's PC)
# and "aula-2" with a PC that is off, each with the machine credential of
# its own; the templates "accensione" (wol.yaml) and "aggiornamenti"
# (wu_run.yaml) on aula-1 that may run on aula-2 too; a schedule of the
# updates on aula-1; the workflow "manutenzione", wake then update.
#
# On a VM of the lab with win11 up: 'BROWSER_LAB=deb13 browser.sh setup labs'.
# Needs the secret it-win-key on the controller, a key authorised on win11
# for BROWSER_WIN_USER (vm-lab, "Integration scripts on a VM"). Starts from
# nothing: it deletes the database, the runs and the projects first.
set -eu
IT=${IT_DIR:-/it}
. "$IT/lib.sh"
require_root
USER=${BROWSER_USER:-tester}
WIN_USER=${BROWSER_WIN_USER:?the account on win11}
NET=${BROWSER_SUBNET:-192.168.140}
REPO=https://github.com/lineadicomando/ansible-win_edulab.git
as() { ctl "$USER" "$@"; }
id_of() {  # id_of WHAT ANSWER
    found=$(get "$2" 'a["data"]["id"]') || true
    [ -n "$found" ] || { echo "setup: $1 failed: $2" >&2; exit 1; }
    echo "$found"
}
[ -f /etc/cockpit-fleet/secrets/it-win-key.cred ] ||
    { echo "setup: no secret it-win-key on this controller" >&2; exit 1; }

echo "clearing"
wipe || true  # lib.sh is written for set -u alone

echo "credential"
cred=$(as credential.create "{\"name\": \"chiave-laboratori\", \"kind\": \"machine\", \"username\": \"$WIN_USER\"}")
cred_id=$(id_of credential "$cred")
# The key is already an encrypted secret: its ciphertext goes to the credential.
install -m 0640 -g cockpit-fleet /etc/cockpit-fleet/secrets/it-win-key.cred \
    "/etc/cockpit-fleet/secrets/$(get "$cred" 'a["data"]["secret_names"]["private_key"]').cred"

echo "project"
project_id=$(id_of project "$(as project.create "{\"name\": \"win-edulab\", \"kind\": \"git\", \"url\": \"$REPO\", \"ref\": \"main\"}")")
sync=$(as project.sync "{\"id\": \"$project_id\"}")
[ "$(wait_status "$(get "$sync" 'a["data"].get("run_id") or a["data"]["id"]')" 300)" = successful ] ||
    { echo "setup: the project was not fetched: $sync" >&2; exit 1; }

echo "inventories"
inventory() {  # inventory NAME HOST LAST-BYTE: its id
    params=$(python3 - "$1" "$2" "$NET.$3" "$(printf '52:54:00:cf:14:%02d' "$3")" "$cred_id" <<'PY'
import json, sys
name, host, address, mac, cred = sys.argv[1:]
content = "\n".join([
    "all:", "  children:", "    win_managed:", "      vars:",
    "        ansible_connection: ssh", "        ansible_shell_type: powershell",
    "        ansible_remote_tmp: C:\\Windows\\Temp\\ansible", "      hosts:",
    f"        {host}:", f"          ansible_host: {address}", f'          ansible_mac: "{mac}"', ""])
print(json.dumps({"name": name, "kind": "managed", "format": "yaml", "content": content,
                  "machine_credential_id": cred}))
PY
)
    made=$(id_of "inventory $1" "$(as inventory.create "$params")")
    parse=$(as inventory.parse "{\"id\": \"$made\"}")
    [ "$(wait_status "$(get "$parse" 'a["data"]["run_id"]')" 90)" = successful ] ||
        { echo "setup: $1 was not read" >&2; exit 1; }
    as hostkey.scan "{\"inventory_id\": \"$made\"}" | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({k: key[k] for k in ("host", "key_type", "key")})
    subprocess.run(["sudo", "-u", sys.argv[2], "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL" "$USER"
    echo "$made"
}
# win11's MAC address ends as its address does (lab.conf): .20 is :14:20,
# in decimal digits.
aula1=$(inventory aula-1 pc-docente 20 | tail -n 1)
aula2=$(inventory aula-2 pc-01 31 | tail -n 1)

echo "templates, schedule, workflow"
template() {  # template NAME PLAYBOOK
    id_of "template $1" "$(as template.create "{\"name\": \"$1\", \"project_id\": \"$project_id\", \"playbook\": \"playbooks/$2\", \"inventory_id\": \"$aula1\", \"inventory_ids\": [\"$aula2\"]}")"
}
wol=$(template accensione wol.yaml)
wu=$(template aggiornamenti wu_run.yaml)
id_of schedule "$(as schedule.create "{\"name\": \"aggiornamenti aula-1\", \"template_id\": \"$wu\", \"inventory_id\": \"$aula1\", \"calendar\": \"Sat *-*-* 02:00:00\", \"timezone\": \"Europe/Rome\"}")" >/dev/null
# No inventory of its own: whoever launches it chooses one that both templates allow.
id_of workflow "$(as workflow.create "{\"name\": \"manutenzione\", \"nodes\": [
    {\"id\": \"accendi\", \"template_id\": \"$wol\"}, {\"id\": \"aggiorna\", \"template_id\": \"$wu\"}],
  \"edges\": [{\"from\": \"accendi\", \"to\": \"aggiorna\", \"condition\": \"success\"}]}")" >/dev/null
echo "ready: aula-1 $aula1, aula-2 $aula2"
