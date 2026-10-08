#!/bin/sh
# A run against a Windows node over SSH with a private key secret, with the
# time each task takes. Needs a secret holding a key authorised on the node:
#   IT_WIN_HOST=192.0.2.10 IT_WIN_USER=admin IT_WIN_KEY=secret-name windows.sh
# The ansible.windows collection is downloaded into the run's project when the
# controller does not have it.
set -u
. "$(dirname "$0")/lib.sh"
require_root
: "${IT_WIN_HOST:?}" "${IT_WIN_USER:?}" "${IT_WIN_KEY:?}"

ID=itwin
RUN=/var/lib/cockpit-fleet/runs/$ID
UNIT=cockpit-fleet-run@$ID.service
trap 'systemctl stop "$UNIT" 2>/dev/null; systemctl reset-failed "$UNIT" 2>/dev/null; rm -rf "$RUN"' EXIT

install -d -m 0700 "$RUN" "$RUN/project" "$RUN/creds"
cp "/etc/cockpit-fleet/secrets/$IT_WIN_KEY.cred" "$RUN/creds/private_key"
if ! ansible-galaxy collection list ansible.windows 2>/dev/null | grep -q '^ansible.windows'; then
    ansible-galaxy collection install ansible.windows -p "$RUN/project" >/dev/null 2>&1 ||
        { echo "cannot install ansible.windows" >&2; exit 2; }
fi
cat > "$RUN/project/hosts" <<INV
win ansible_host=$IT_WIN_HOST ansible_connection=ssh ansible_shell_type=powershell ansible_remote_tmp='C:\\Windows\\Temp\\ansible'
INV
cat > "$RUN/project/site.yml" <<'YAML'
- hosts: win
  gather_facts: true
  tasks:
    - ansible.windows.win_command: whoami
      register: who
      changed_when: false
    - ansible.windows.win_shell: >-
        Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
        Select-Object DeviceID, Size, FreeSpace | ConvertTo-Json -Compress
      register: disks
      changed_when: false
    - ansible.builtin.copy:
        content: "{{ ansible_facts.os_family }}|{{ who.stdout | trim }}|{{ disks.stdout | trim }}"
        dest: "{{ playbook_dir }}/../result"
      delegate_to: localhost
YAML
ssh-keyscan -T 5 "$IT_WIN_HOST" 2>/dev/null > "$RUN/known_hosts"
printf '{"playbook": "site.yml", "inventory": "hosts", "user": "%s"}\n' "$IT_WIN_USER" > "$RUN/spec.json"
chown -R cockpit-fleet:cockpit-fleet "$RUN"

as_fleet sudo -n /usr/libexec/cockpit-fleet/fleet-unit start "$ID"
n=0
while systemctl is-active --quiet "$UNIT" && [ "$n" -lt 300 ]; do sleep 1; n=$((n + 1)); done

expect_ok "run finished successfully" sh -c "[ \"\$(systemctl show -P ExecMainStatus $UNIT)\" = 0 ] && grep -q 'PLAY RECAP' $RUN/stdout.log"
expect_ok "facts identify a Windows node" grep -q '^Windows|' "$RUN/result"
expect_ok "PowerShell output came back as JSON" grep -q '"DeviceID":"C:"' "$RUN/result"
[ "$FAILED" -eq 0 ] || sed 's/\x1b\[[0-9;]*m//g' "$RUN/stdout.log" | tail -n 25 | cut -c1-300

python3 - "$RUN/events.jsonl" <<'PY'
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
