#!/bin/sh
# The whole path of a run through fleetctl: project, credential, host key,
# inventory, template, launch, outcome, cancel, queue, git sync.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# Needs IT_SSH_USER and IT_SSH_PASSWORD (an account of this machine that accepts
# password logins); IT_GIT_URL (a public https repository) is optional.
set -u
. "$(dirname "$0")/lib.sh"
require_root
: "${IT_SSH_USER:?}" "${IT_SSH_PASSWORD:?}"
# This test starts from an empty database and leaves one: never on real data.
[ "${IT_WIPE:-}" = yes ] || { echo "set IT_WIPE=yes: this test deletes the database, runs and projects" >&2; exit 2; }

FLEETCTL=/usr/libexec/cockpit-fleet/fleetctl
SECRET=/usr/libexec/cockpit-fleet/fleet-secret
CONFIG=/etc/cockpit-fleet/config.toml
SRC=/srv/fleet-it
DB=/var/lib/cockpit-fleet/fleet.db

ctl() {  # ctl USER COMMAND [JSON]
    printf '%s' "${3:-}" | sudo -u "$1" sudo -n -u cockpit-fleet "$FLEETCTL" "$2" 2>&1
}
get() {  # get JSON-TEXT PYTHON-EXPRESSION on the parsed answer "a"
    printf '%s' "$1" | python3 -c "import json,sys; a=json.load(sys.stdin); print($2)" 2>/dev/null
}
admin() { ctl itadmin "$@"; }
expect_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got: '$2', wanted: '$3')"; fi; }
db() { python3 -c "import sqlite3,sys; print(sqlite3.connect('file:$DB?mode=ro', uri=True).execute(sys.argv[1]).fetchone()[0])" "$1"; }
wait_status() {  # wait_status RUN_ID SECONDS: poll the database only, never fleetctl
    n=0
    while [ "$n" -lt "$2" ]; do
        s=$(db "SELECT status FROM runs WHERE id = '$1'")
        case "$s" in queued|running) sleep 1; n=$((n + 1)) ;; *) break ;; esac
    done
    echo "$s"
}

cleanup() {
    for id in $(db "SELECT group_concat(id, ' ') FROM runs WHERE status IN ('queued','running')" 2>/dev/null); do
        [ "$id" = None ] || systemctl stop "cockpit-fleet-run@$id.service" 2>/dev/null
    done
    for name in $("$SECRET" list | grep '^c-'); do "$SECRET" delete "$name"; done
    for user in itadmin itoperator; do userdel -r "$user" 2>/dev/null; done
    [ -f "$CONFIG.it-backup" ] && mv "$CONFIG.it-backup" "$CONFIG"
    rm -rf "$SRC" /var/lib/cockpit-fleet/runs/* /var/lib/cockpit-fleet/projects/*
    rm -f "$DB" "$DB-wal" "$DB-shm" /tmp/fleet-it-marker
    systemctl reset-failed 'cockpit-fleet-run@*' 'cockpit-fleet-finish@*' 2>/dev/null
}
trap cleanup EXIT
cleanup

useradd -m -G cockpit-fleet-admin itadmin
useradd -m -G cockpit-fleet-operator itoperator
cp -p "$CONFIG" "$CONFIG.it-backup"
printf '[runs]\nmax_concurrent = 1\n[projects]\nlocal_roots = ["%s"]\n' "$SRC" > "$CONFIG"

mkdir -p "$SRC/demo"
cat > "$SRC/demo/ansible.cfg" <<'CFG'
[defaults]
host_key_checking = False
callback_plugins = /tmp
[ssh_connection]
ssh_args = -o StrictHostKeyChecking=no
CFG
cat > "$SRC/demo/site.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.command: id -un
      register: who
      changed_when: false
    - ansible.builtin.copy:
        content: "{{ who.stdout }} {{ greeting | default('none') }}"
        dest: /tmp/fleet-it-marker
        mode: "0644"
    - ansible.builtin.debug:
        msg: "the password is TopSecretValue"
      no_log: true
YAML
cat > "$SRC/demo/wait.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.wait_for:
        timeout: 300
      delegate_to: localhost
YAML
chmod -R a+rX "$SRC"

# --- credential, host key, project, inventory -----------------------------------
cred=$(admin credential.create "{\"name\": \"local ssh\", \"kind\": \"machine\", \"username\": \"$IT_SSH_USER\"}")
cred_id=$(get "$cred" 'a["data"]["id"]')
printf '%s' "$IT_SSH_PASSWORD" | sudo -u itadmin sudo -n "$SECRET" set "$(get "$cred" 'a["data"]["secret_names"]["password"]')"
expect_eq "credential shows that its password is set" \
    "$(get "$(admin credential.list)" 'a["data"]["credentials"][0]["secrets"]["password"]')" True
expect_eq "operator cannot create credentials" \
    "$(get "$(ctl itoperator credential.create '{"name": "x", "kind": "vault"}')" 'a["error"]["code"]')" forbidden

scan=$(admin hostkey.scan '{"host": "127.0.0.1"}')
expect_eq "host key scan returns keys with fingerprints" "$(get "$scan" 'a["data"]["keys"][0]["fingerprint"][:7]')" "SHA256:"

project=$(admin project.create "{\"name\": \"demo\", \"kind\": \"local\", \"path\": \"$SRC/demo\"}")
project_id=$(get "$project" 'a["data"]["id"]')
expect_eq "local project is accepted under an allowed root" "$(get "$project" 'a["ok"]')" True
expect_eq "local project is refused elsewhere" \
    "$(get "$(admin project.create '{"name": "etc", "kind": "local", "path": "/etc"}')" 'a["error"]["code"]')" conflict
expect_eq "project files are listed" "$(get "$(admin project.files "{\"id\": \"$project_id\"}")" 'sorted(a["data"]["files"])')" "['site.yml', 'wait.yml']"

inventory=$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "[local]\nself ansible_host=127.0.0.1\n"}')
inventory_id=$(get "$inventory" 'a["data"]["id"]')
parse=$(ctl itoperator inventory.parse "{\"id\": \"$inventory_id\"}")
expect_eq "inventory job finishes on its own" "$(wait_status "$(get "$parse" 'a["data"]["run_id"]')" 60)" successful
expect_eq "inventory hosts and groups are stored" \
    "$(get "$(ctl itoperator inventory.get "{\"id\": \"$inventory_id\"}")" 'a["data"]["hosts"]')" \
    "[{'name': 'self', 'address': '127.0.0.1', 'groups': ['local']}]"

template=$(admin template.create "{\"name\": \"demo\", \"project_id\": \"$project_id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\", \"extra_vars\": {\"greeting\": \"hello\"}}")
template_id=$(get "$template" 'a["data"]["id"]')
expect_eq "template is created" "$(get "$template" 'a["ok"]')" True

# --- a run against an unknown host key must fail -----------------------------------
run=$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_eq "run without a trusted host key fails" "$(wait_status "$run_id" 90)" failed
if [ -e /tmp/fleet-it-marker ]; then fail "the project's ansible.cfg disabled host key checking"; else pass "the project's ansible.cfg cannot disable host key checking"; fi

# --- trust the key, run again ---------------------------------------------------
printf '%s' "$scan" | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({k: key[k] for k in ("host", "key_type", "key")})
    subprocess.run(["sudo", "-u", "itadmin", "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL"
expect_ok "host keys are trusted after confirmation" sh -c "[ \$(python3 -c \"import sqlite3; print(sqlite3.connect('$DB').execute('select count(*) from host_keys').fetchone()[0])\") -ge 1 ]"

run=$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_eq "operator launches a template" "$(get "$run" 'a["data"]["status"]')" running
expect_eq "outcome is recorded without anyone asking" "$(wait_status "$run_id" 120)" successful
expect_eq "playbook ran on the node as the credential's user, with the extra vars" \
    "$(cat /tmp/fleet-it-marker 2>/dev/null)" "$IT_SSH_USER hello"
detail=$(ctl itoperator run.get "{\"id\": \"$run_id\"}")
expect_eq "per-host totals are stored" "$(get "$detail" 'a["data"]["stats"]["self"]["failures"], a["data"]["stats"]["self"]["ok"]')" "0 3"
expect_eq "launcher is recorded" "$(get "$detail" 'a["data"]["created_by"]')" itoperator
log=$(ctl itoperator run.log "{\"id\": \"$run_id\"}")
expect_eq "log is readable by the operator" "$(get "$log" '"PLAY RECAP" in a["data"]["text"]')" True
events=$(ctl itoperator run.events "{\"id\": \"$run_id\"}")
expect_eq "events are readable by the operator" "$(get "$events" '"\"event\": \"stats\"" in a["data"]["text"]')" True
if grep -rq 'TopSecretValue' "/var/lib/cockpit-fleet/runs/$run_id/"; then fail "a no_log value was stored"; else pass "no_log values are not stored"; fi
if grep -rq "$IT_SSH_PASSWORD" "/var/lib/cockpit-fleet/runs/$run_id/"; then fail "the password was stored in the run directory"; else pass "the password is nowhere in the run directory"; fi
expect_ok "project copy and ciphertext are removed after the run" sh -c "! test -e /var/lib/cockpit-fleet/runs/$run_id/project && ! test -e /var/lib/cockpit-fleet/runs/$run_id/creds"
expect_eq "launch is in the audit log" "$(get "$(admin audit.list)" 'any(e["command"]=="run.start" and e["user"]=="itoperator" and e["outcome"]=="ok" for e in a["data"]["entries"])')" True

# --- queue and cancel ----------------------------------------------------------
admin template.create "{\"name\": \"wait\", \"project_id\": \"$project_id\", \"playbook\": \"wait.yml\", \"inventory_id\": \"$inventory_id\"}" > /tmp/fleet-it-wait.json
wait_id=$(get "$(cat /tmp/fleet-it-wait.json)" 'a["data"]["id"]'); rm -f /tmp/fleet-it-wait.json
first=$(get "$(ctl itoperator run.start "{\"template_id\": \"$wait_id\"}")" 'a["data"]["id"]')
second=$(get "$(ctl itoperator run.start "{\"template_id\": \"$wait_id\"}")" 'a["data"]["id"]')
sleep 3
expect_eq "first run is running" "$(db "SELECT status FROM runs WHERE id = '$first'")" running
expect_eq "second run waits for the concurrency limit" "$(db "SELECT status FROM runs WHERE id = '$second'")" queued
expect_eq "operator cancels a running run" "$(get "$(ctl itoperator run.cancel "{\"id\": \"$first\"}")" 'a["data"]["status"]')" canceled
expect_denied "its unit is gone" systemctl is-active --quiet "cockpit-fleet-run@$first.service"
sleep 4
expect_eq "the queued run starts when the slot frees" "$(db "SELECT status FROM runs WHERE id = '$second'")" running
expect_eq "cancel the second run" "$(get "$(ctl itoperator run.cancel "{\"id\": \"$second\"}")" 'a["data"]["status"]')" canceled

# --- git project ---------------------------------------------------------------
if [ -n "${IT_GIT_URL:-}" ]; then
    git=$(admin project.create "{\"name\": \"git\", \"kind\": \"git\", \"url\": \"$IT_GIT_URL\", \"ref\": \"${IT_GIT_REF:-main}\"}")
    git_id=$(get "$git" 'a["data"]["id"]')
    sync=$(admin project.sync "{\"id\": \"$git_id\"}")
    expect_eq "git sync job succeeds" "$(wait_status "$(get "$sync" 'a["data"]["run_id"]')" 180)" successful
    expect_eq "revision is a commit id" "$(get "$(admin project.list)" 'len([p for p in a["data"]["projects"] if p["id"]=="'"$git_id"'"][0]["revision"])')" 40
    expect_ok "fetched tree has files and no .git" sh -c "ls /var/lib/cockpit-fleet/projects/$git_id/tree | grep -q . && ! test -e /var/lib/cockpit-fleet/projects/$git_id/tree/.git"
    expect_eq "file URLs are refused" "$(get "$(admin project.create '{"name": "f", "kind": "git", "url": "file:///etc"}')" 'a["error"]["code"]')" bad_request
else
    echo "SKIP  git project checks (IT_GIT_URL not set)"
fi

finish
