#!/bin/sh
# Ad-hoc commands through fleetctl: one module on the hosts of an inventory,
# administrators only, among the modules config.toml allows.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# Needs IT_SSH_USER and IT_SSH_PASSWORD (an account of this machine that accepts
# password logins).
set -u
. "$(dirname "$0")/lib.sh"
require_root
: "${IT_SSH_USER:?}" "${IT_SSH_PASSWORD:?}"
require_wipe

trap wipe EXIT
wipe
prepare

cred=$(admin credential.create "{\"name\": \"local ssh\", \"kind\": \"machine\", \"username\": \"$IT_SSH_USER\"}")
cred_id=$(get "$cred" 'a["data"]["id"]')
printf '%s' "$IT_SSH_PASSWORD" | set_secret "$cred" password
admin hostkey.scan '{"host": "127.0.0.1"}' | python3 -c '
import json, subprocess, sys
for key in json.load(sys.stdin)["data"]["keys"]:
    params = json.dumps({k: key[k] for k in ("host", "key_type", "key")})
    subprocess.run(["sudo", "-u", "itadmin", "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL"
inventory=$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "[local]\nself ansible_host=127.0.0.1\n"}')
inventory_id=$(get "$inventory" 'a["data"]["id"]')
base="\"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\""

adhoc() {  # adhoc JSON-FIELDS: an administrator's ad-hoc command on the test inventory
    admin run.adhoc "{$base, $1}"
}

# --- a command, and what is kept of it ------------------------------------------
run=$(adhoc '"limit": "all", "module": "ansible.builtin.command", "free_form": "id -un"')
run_id=$(get "$run" 'a["data"]["id"]')
expect_eq "an ad-hoc command is a run of its own kind" "$(get "$run" 'a["data"]["kind"], a["data"]["template_id"]')" "adhoc None"
expect_run "administrator runs a command" "$run_id" 90 successful
log=$(sed 's/\x1b\[[0-9;]*m//g' "/var/lib/cockpit-fleet/runs/$run_id/stdout.log")
expect_eq "the output is what the host answered" "$(printf '%s\n' "$log" | grep -c "^$IT_SSH_USER\$")" 1
expect_eq "and says which host" "$(printf '%s\n' "$log" | grep -c '^self | CHANGED | rc=0')" 1
detail=$(admin run.get "{\"id\": \"$run_id\"}")
expect_eq "per-host totals are stored" "$(get "$detail" 'a["data"]["stats"]["self"]["changed"]')" 1
expect_eq "module and arguments are kept with the run" "$(get "$detail" 'a["data"]["options"]["module"], a["data"]["options"]["free_form"]')" "ansible.builtin.command id -un"
events=$(admin run.events "{\"id\": \"$run_id\"}")
expect_eq "events name the task after the module" \
    "$(get "$events" '[json.loads(l)["task"] for l in a["data"]["text"].splitlines() if "task_start" in l]')" "['ansible.builtin.command']"
expect_eq "the command is in the audit log" "$(get "$(admin audit.list)" 'any(e["command"]=="run.adhoc" and "id -un" in e["params"] and e["outcome"]=="ok" for e in a["data"]["entries"])')" True
if grep -rq "$IT_SSH_PASSWORD" "/var/lib/cockpit-fleet/runs/$run_id/"; then fail "the password was stored in the run directory"; else pass "the password is nowhere in the run directory"; fi

# --- arguments as a mapping, and with the line ---------------------------------
run_id=$(get "$(adhoc '"limit": "self", "module": "ansible.builtin.ping", "args": {"data": "hello"}')" 'a["data"]["id"]')
expect_run "arguments can be a mapping" "$run_id" 90 successful
expect_eq "and reach the module" "$(grep -c '"ping": "hello"' "/var/lib/cockpit-fleet/runs/$run_id/events.jsonl")" 1
run_id=$(get "$(adhoc '"limit": "self", "module": "ansible.builtin.command", "free_form": "pwd", "args": {"chdir": "/usr/share"}')" 'a["data"]["id"]')
expect_run "a line and a mapping together" "$run_id" 90 successful
expect_eq "both are used" "$(sed 's/\x1b\[[0-9;]*m//g' "/var/lib/cockpit-fleet/runs/$run_id/stdout.log" | grep -c '^/usr/share$')" 1

# --- a file inventory brings its project; a vault without an identifier ------------
SRC=/srv/fleet-it
mkdir -p "$SRC/site/inv/group_vars/all"
printf 'all:\n  hosts:\n    self:\n      ansible_host: 127.0.0.1\n' > "$SRC/site/inv/hosts.yml"
printf 'vaulted: from-the-vault\n' > "$SRC/site/inv/group_vars/all/vault.yml"
printf 'it-vault-password' > /tmp/fleet-it-vault
ansible-vault encrypt --vault-password-file /tmp/fleet-it-vault "$SRC/site/inv/group_vars/all/vault.yml" >/dev/null
chmod -R a+rX "$SRC"
vault=$(admin credential.create '{"name": "vault", "kind": "vault"}')
set_secret "$vault" vault_password < /tmp/fleet-it-vault; rm -f /tmp/fleet-it-vault
project_id=$(get "$(admin project.create "{\"name\": \"site\", \"kind\": \"local\", \"path\": \"$SRC/site\"}")" 'a["data"]["id"]')
file_inv=$(get "$(admin inventory.create "{\"name\": \"file\", \"kind\": \"file\", \"project_id\": \"$project_id\", \"path\": \"inv/hosts.yml\"}")" 'a["data"]["id"]')
run=$(admin run.adhoc "{\"inventory_id\": \"$file_inv\", \"machine_credential_id\": \"$cred_id\", \"vault_credential_ids\": [\"$(get "$vault" 'a["data"]["id"]')\"], \"limit\": \"all\", \"module\": \"ansible.builtin.command\", \"free_form\": \"echo {{ vaulted }}\"}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_eq "a file inventory brings its project" "$(get "$run" 'a["data"]["project_id"]')" "$project_id"
expect_run "variables beside the inventory are read, with a vault that has no identifier" "$run_id" 90 successful
expect_eq "and reach the command" "$(sed 's/\x1b\[[0-9;]*m//g' "/var/lib/cockpit-fleet/runs/$run_id/stdout.log" | grep -c '^from-the-vault$')" 1

# --- outcomes -------------------------------------------------------------------
run_id=$(get "$(adhoc '"limit": "all", "module": "ansible.builtin.command", "free_form": "false"')" 'a["data"]["id"]')
expect_run "a command that fails makes the run fail" "$run_id" 90 failed
run_id=$(get "$(adhoc '"limit": "nobody", "module": "ansible.builtin.ping"')" 'a["data"]["id"]')
expect_run "a pattern that matches no host makes the run fail" "$run_id" 90 failed
rm -f /tmp/fleet-it-marker
run_id=$(get "$(adhoc '"limit": "all", "module": "ansible.builtin.command", "free_form": "touch /tmp/fleet-it-marker", "check": true')" 'a["data"]["id"]')
expect_run "a dry run of a command" "$run_id" 90 successful
expect_denied "runs nothing" test -e /tmp/fleet-it-marker

# --- who, and which modules -----------------------------------------------------
expect_eq "operator cannot run ad-hoc commands" \
    "$(get "$(ctl itoperator run.adhoc "{$base, \"limit\": \"all\", \"module\": \"ansible.builtin.ping\"}")" 'a["error"]["code"]')" forbidden
expect_eq "a module outside the list is refused" \
    "$(get "$(adhoc '"limit": "all", "module": "ansible.builtin.copy", "free_form": "content=x dest=/tmp/fleet-it-marker"')" 'a["error"]["code"]')" conflict
expect_denied "and did not run" test -e /tmp/fleet-it-marker
expect_eq "the refusal is in the audit log" "$(get "$(admin audit.list)" 'any(e["command"]=="run.adhoc" and "ansible.builtin.copy" in e["params"] and e["outcome"]=="error" for e in a["data"]["entries"])')" True
expect_eq "a short module name is refused" \
    "$(get "$(adhoc '"limit": "all", "module": "ping"')" 'a["error"]["code"]')" bad_request
expect_eq "a limit is required" \
    "$(get "$(adhoc '"module": "ansible.builtin.ping"')" 'a["error"]["code"]')" bad_request

printf '[adhoc]\nmodules = ["ansible.builtin.copy"]\n' >> "$CONFIG"
expect_eq "config.toml takes a module off the list" \
    "$(get "$(adhoc '"limit": "all", "module": "ansible.builtin.ping"')" 'a["error"]["code"]')" conflict
run_id=$(get "$(adhoc '"limit": "all", "module": "ansible.builtin.copy", "free_form": "content=x dest=/tmp/fleet-it-marker", "diff": true')" 'a["data"]["id"]')
expect_run "and puts another one on it" "$run_id" 90 successful
expect_eq "which ran" "$(cat /tmp/fleet-it-marker 2>/dev/null)" x
expect_eq "its difference is in the events" "$(grep -c '+++ after' "/var/lib/cockpit-fleet/runs/$run_id/events.jsonl")" 1
rm -f /tmp/fleet-it-marker

finish
