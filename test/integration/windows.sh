#!/bin/sh
# A run against a Windows node over SSH, through fleetctl: credential with a
# private key, inventory, host key, template, launch. Prints what each task took.
#   IT_WIN_HOST=192.0.2.10 IT_WIN_USER=admin IT_WIN_KEY=secret-name windows.sh
# IT_WIN_KEY names an existing secret holding a key authorised on the node.
# The ansible.windows collection is downloaded into the project when the
# controller does not have it.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# With IT_KEEP=yes everything is left in place at the end, to look at it in the
# interface; run any wiping test afterwards to clean up.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe
: "${IT_WIN_HOST:?}" "${IT_WIN_USER:?}" "${IT_WIN_KEY:?}"

SRC=/srv/fleet-it
[ "${IT_KEEP:-}" = yes ] || trap wipe EXIT
wipe
prepare

mkdir -p "$SRC/win"
if ! ansible-galaxy collection list ansible.windows 2>/dev/null | grep -q '^ansible.windows'; then
    ansible-galaxy collection install ansible.windows -p "$SRC/win" >/dev/null 2>&1 ||
        { echo "cannot install ansible.windows" >&2; exit 2; }
fi
cat > "$SRC/win/site.yml" <<'YAML'
- name: Look at a Windows node
  hosts: all
  gather_facts: true
  tasks:
    - name: Who am I
      ansible.windows.win_command: whoami
      register: who
      changed_when: false
    - name: Free space on the disks
      ansible.windows.win_shell: >-
        Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
        Select-Object DeviceID, Size, FreeSpace | ConvertTo-Json -Compress
      register: disks
      changed_when: false
    - name: Report
      ansible.builtin.debug:
        msg: "{{ ansible_facts.os_family }} | {{ who.stdout | trim }} | {{ disks.stdout | trim }}"
YAML
chmod -R a+rX "$SRC"

cred=$(admin credential.create "{\"name\": \"windows\", \"kind\": \"machine\", \"username\": \"$IT_WIN_USER\"}")
# The key is already an encrypted secret: give its ciphertext to the credential.
install -m 0640 -g cockpit-fleet "/etc/cockpit-fleet/secrets/$IT_WIN_KEY.cred" \
    "/etc/cockpit-fleet/secrets/$(get "$cred" 'a["data"]["secret_names"]["private_key"]').cred"

params=$(python3 -c '
import json, sys
content = "\n".join([
    "all:", "  hosts:", "    win:",
    f"      ansible_host: {sys.argv[1]}",
    "      ansible_connection: ssh",
    "      ansible_shell_type: powershell",
    "      ansible_remote_tmp: C:\\\\Windows\\\\Temp\\\\ansible", ""])
print(json.dumps({"name": "windows", "kind": "managed", "format": "yaml", "content": content}))' "$IT_WIN_HOST")
inventory_id=$(get "$(admin inventory.create "$params")" 'a["data"]["id"]')
parse=$(admin inventory.parse "{\"id\": \"$inventory_id\"}")
expect_run "inventory is read" "$(get "$parse" 'a["data"]["run_id"]')" 60 successful

scan=$(admin hostkey.scan "{\"inventory_id\": \"$inventory_id\"}")
expect_eq "the Windows node offers its SSH host keys" "$(get "$scan" 'len(a["data"]["keys"]) > 0, a["data"]["unanswered"]')" "True []"
printf '%s' "$scan" | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({**{k: key[k] for k in ("host", "key_type", "key")}, "replace": True})
    subprocess.run(["sudo", "-u", "itadmin", "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL"

project_id=$(get "$(admin project.create "{\"name\": \"windows\", \"kind\": \"local\", \"path\": \"$SRC/win\"}")" 'a["data"]["id"]')
template_id=$(get "$(admin template.create "{\"name\": \"Windows report\", \"project_id\": \"$project_id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$(get "$cred" 'a["data"]["id"]')\"}")" 'a["data"]["id"]')

run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")" 'a["data"]["id"]')
expect_run "run on the Windows node succeeds" "$run_id" 300 successful
detail=$(admin run.get "{\"id\": \"$run_id\"}")
expect_eq "four tasks ran, none failed" "$(get "$detail" 'a["data"]["stats"]["win"]["ok"], a["data"]["stats"]["win"]["failures"]')" "4 0"
log="/var/lib/cockpit-fleet/runs/$run_id/stdout.log"
expect_ok "facts identify a Windows node" grep -q '"msg": "Windows | ' "$log"
expect_ok "PowerShell output came back as JSON" grep -q 'DeviceID' "$log"

python3 - "/var/lib/cockpit-fleet/runs/$run_id/events.jsonl" <<'PY'
import json, sys
events = [json.loads(line) for line in open(sys.argv[1])]
started = {}
for event in events:
    if event["event"] == "task_start":
        started[event["task_uuid"]] = (event["task"], event["ts"])
    elif event["event"].startswith("host_"):
        name, ts = started[event["task_uuid"]]
        print(f"  {event['event'][5:]:12} {name:32} {event['ts'] - ts:5.1f} s")
print(f"  total {events[-1]['ts'] - events[0]['ts']:.1f} s")
PY

finish
