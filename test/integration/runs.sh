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
SRC=/srv/fleet-it
require_wipe

cleanup() { wipe; rm -f /tmp/fleet-it-marker /run/cockpit-fleet/answers/a-*.cred; }
trap cleanup EXIT
cleanup
prepare 1

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
      check_mode: false
    - ansible.builtin.copy:
        content: "{{ who.stdout }} {{ greeting | default('none') }}"
        dest: /tmp/fleet-it-marker
        mode: "0644"
    - ansible.builtin.debug:
        msg: "the password is TopSecretValue"
      no_log: true
YAML
cat > "$SRC/demo/secret.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        content: "{{ token }}"
        dest: /tmp/fleet-it-marker
        mode: "0600"
      no_log: true
    - ansible.builtin.debug:
        msg: "the token is {{ token }}"
    - ansible.builtin.command: "echo {{ token | quote }}"
      changed_when: false
YAML
cat > "$SRC/demo/wait.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.wait_for:
        timeout: 300
      delegate_to: localhost
YAML
cat > "$SRC/demo/hosts.yml" <<'YAML'
- hosts: all
  gather_facts: false
  connection: local
  tasks:
    - ansible.builtin.debug:
        msg: here
YAML
chmod -R a+rX "$SRC"

# --- credential, host key, project, inventory -----------------------------------
cred=$(admin credential.create "{\"name\": \"local ssh\", \"kind\": \"machine\", \"username\": \"$IT_SSH_USER\"}")
cred_id=$(get "$cred" 'a["data"]["id"]')
printf '%s' "$IT_SSH_PASSWORD" | set_secret "$cred" password
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
expect_eq "project files are listed" "$(get "$(admin project.files "{\"id\": \"$project_id\"}")" 'sorted(a["data"]["files"])')" "['hosts.yml', 'secret.yml', 'site.yml', 'wait.yml']"

inventory=$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "[local]\nself ansible_host=127.0.0.1\n"}')
inventory_id=$(get "$inventory" 'a["data"]["id"]')
parse=$(ctl itoperator inventory.parse "{\"id\": \"$inventory_id\"}")
expect_eq "inventory job finishes on its own" "$(wait_status "$(get "$parse" 'a["data"]["run_id"]')" 60)" successful
expect_eq "inventory hosts and groups are stored" \
    "$(get "$(ctl itoperator inventory.get "{\"id\": \"$inventory_id\"}")" 'a["data"]["hosts"]')" \
    "[{'name': 'self', 'address': '127.0.0.1', 'port': None, 'connection': '', 'groups': ['local']}]"

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

# --- dry run: the events say what would change, and nothing does ----------------
rm -f /tmp/fleet-it-marker
run=$(ctl itoperator run.start "{\"template_id\": \"$template_id\", \"check\": true}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_run "operator asks for a dry run" "$run_id" 120 successful
expect_denied "a dry run changes nothing" test -e /tmp/fleet-it-marker
events=$(ctl itoperator run.events "{\"id\": \"$run_id\"}")
expect_eq "the events hold the difference as text" \
    "$(get "$events" 'sorted(d for d in (json.loads(l).get("diff", "") for l in a["data"]["text"].splitlines()) if d)[0].split("\n")[1]')" \
    "+++ after: /tmp/fleet-it-marker"
if grep -q 'TopSecretValue' "/var/lib/cockpit-fleet/runs/$run_id/events.jsonl"; then fail "a no_log value is in a difference"; else pass "no_log values are in no difference"; fi

# --- survey: an answer is a variable of the run, never a template ---------------
survey=$(admin template.update "{\"id\": \"$template_id\", \"name\": \"demo\", \"project_id\": \"$project_id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\", \"extra_vars\": {\"greeting\": \"hello\"}, \"survey\": [{\"name\": \"greeting\", \"type\": \"text\", \"required\": true}]}")
expect_eq "template takes a survey" "$(get "$survey" 'a["data"]["survey"][0]["name"]')" greeting
expect_eq "operator cannot define a survey" \
    "$(get "$(ctl itoperator template.update "{\"id\": \"$template_id\"}")" 'a["error"]["code"]')" forbidden
expect_eq "a required answer cannot be skipped" \
    "$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")" 'a["error"]["code"]')" bad_request
expect_eq "a variable that was not asked for is refused" \
    "$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\", \"answers\": {\"greeting\": \"x\", \"ansible_connection\": \"local\"}}")" 'a["error"]["code"]')" bad_request
run=$(ctl itoperator run.start "{\"template_id\": \"$template_id\", \"answers\": {\"greeting\": \"{{ lookup('pipe', 'id -un') }}\"}}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_run "operator launches with an answer" "$run_id" 120 successful
expect_eq "the answer wins over the template's variable and is not rendered" \
    "$(cat /tmp/fleet-it-marker 2>/dev/null)" "$IT_SSH_USER {{ lookup('pipe', 'id -un') }}"
expect_eq "the answer is kept with the run" \
    "$(get "$(ctl itoperator run.get "{\"id\": \"$run_id\"}")" 'a["data"]["options"]["answers"]["greeting"][:9]')" "{{ lookup"

# --- survey: a secret answer reaches its run and is kept nowhere --------------------
secret_tpl=$(admin template.create "{\"name\": \"secret\", \"project_id\": \"$project_id\", \"playbook\": \"secret.yml\", \"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\", \"survey\": [{\"name\": \"token\", \"type\": \"secret\", \"required\": true}]}")
secret_id=$(get "$secret_tpl" 'a["data"]["id"]')
expect_eq "a secret answer cannot travel in clear" \
    "$(get "$(ctl itoperator run.start "{\"template_id\": \"$secret_id\", \"answers\": {\"token\": \"typed-in-clear\"}}")" 'a["error"]["code"]')" bad_request
ticket=$(printf '%s' 'S3cretAnswer {{ 1 + 1 }}' | sudo -u itoperator sudo -n "$SECRET" answer)
run=$(ctl itoperator run.start "{\"template_id\": \"$secret_id\", \"answers\": {\"token\": \"$ticket\"}}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_run "operator launches with a secret answer" "$run_id" 120 successful
expect_eq "the playbook got the secret, as it was typed" "$(cat /tmp/fleet-it-marker 2>/dev/null)" 'S3cretAnswer {{ 1 + 1 }}'
expect_eq "the run records that a secret was given, not its value" \
    "$(get "$(ctl itoperator run.get "{\"id\": \"$run_id\"}")" 'a["data"]["options"]["secret_answers"], a["data"]["options"]["answers"]')" "['token'] {}"
if grep -rqa 'S3cretAnswer' /var/lib/cockpit-fleet /run/cockpit-fleet /etc/cockpit-fleet; then fail "the secret answer was stored somewhere"; else pass "the secret answer is in no file, database or log of the backend"; fi
# The playbook prints the secret on purpose, in a message and as a command.
log=$(get "$(ctl itoperator run.log "{\"id\": \"$run_id\"}")" 'a["data"]["text"]')
expect_eq "a secret the playbook prints is masked in the output" "$(printf '%s' "$log" | grep -c 'the token is \*\*\*\*\*\*\*\*')" 1
expect_eq "and in the events" "$(grep -c '\*\*\*\*\*\*\*\*' "/var/lib/cockpit-fleet/runs/$run_id/events.jsonl" | sed 's/^[1-9][0-9]*$/some/')" some
if journalctl -t cockpit-fleet-run --since -5min 2>/dev/null | grep -q 'S3cretAnswer'; then fail "the secret answer is in the journal"; else pass "the secret answer is not in the journal"; fi
expect_denied "the ticket is gone once used" test -e "/run/cockpit-fleet/answers/$ticket.cred"
expect_eq "a ticket serves one launch" \
    "$(get "$(ctl itoperator run.start "{\"template_id\": \"$secret_id\", \"answers\": {\"token\": \"$ticket\"}}")" 'a["error"]["code"]')" bad_request

# --- approval: the run waits, prepared, until another user approves it ------------
useradd -m -G cockpit-fleet-approver itapprover
admin template.update "{\"id\": \"$secret_id\", \"name\": \"secret\", \"project_id\": \"$project_id\", \"playbook\": \"secret.yml\", \"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$cred_id\", \"survey\": [{\"name\": \"token\", \"type\": \"secret\", \"required\": true}], \"needs_approval\": true}" > /dev/null
rm -f /tmp/fleet-it-marker
ticket=$(printf '%s' 'Approved5ecret' | sudo -u itoperator sudo -n "$SECRET" answer)
run=$(ctl itoperator run.start "{\"template_id\": \"$secret_id\", \"answers\": {\"token\": \"$ticket\"}}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_eq "a run of a template that needs approval waits" "$(get "$run" 'a["data"]["status"]')" pending_approval
sleep 3
expect_eq "nothing starts it meanwhile" "$(db "SELECT status FROM runs WHERE id = '$run_id'")" pending_approval
expect_denied "it has no unit" systemctl is-active --quiet "cockpit-fleet-run@$run_id.service"
expect_ok "its secret answer waits in the run as ciphertext" test -s "/var/lib/cockpit-fleet/runs/$run_id/creds/survey_0"
if grep -rqa 'Approved5ecret' /var/lib/cockpit-fleet /run/cockpit-fleet; then fail "the waiting secret answer is in clear"; else pass "the waiting secret answer is nowhere in clear"; fi
expect_eq "who launched it cannot approve it" \
    "$(get "$(ctl itoperator approval.approve "{\"id\": \"$run_id\"}")" 'a["error"]["code"]')" forbidden
expect_eq "an approver with no role sees it" \
    "$(get "$(ctl itapprover approval.list)" '[e["id"] for e in a["data"]["approvals"] if e["can_decide"]]')" "['$run_id']"
expect_eq "and nothing else" "$(get "$(ctl itapprover run.list)" 'a["error"]["code"]')" forbidden
approved=$(ctl itapprover approval.approve "{\"id\": \"$run_id\", \"comment\": \"checked\"}")
expect_eq "the approver approves it and it starts" "$(get "$approved" 'a["data"]["status"]')" running
expect_run "the approved run ends" "$run_id" 120 successful
expect_eq "the playbook got the secret given at launch" "$(cat /tmp/fleet-it-marker 2>/dev/null)" Approved5ecret
expect_eq "the decision is kept with the run" \
    "$(get "$(ctl itoperator run.get "{\"id\": \"$run_id\"}")" 'a["data"]["approval"]["decision"], a["data"]["approval"]["decided_by"], a["data"]["approval"]["comment"]')" "approved itapprover checked"
expect_eq "the approval is in the audit log" \
    "$(get "$(admin audit.list)" 'any(e["command"]=="approval.approve" and e["user"]=="itapprover" and e["outcome"]=="ok" for e in a["data"]["entries"])')" True
ticket=$(printf '%s' 'Rejected5ecret' | sudo -u itoperator sudo -n "$SECRET" answer)
run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$secret_id\", \"answers\": {\"token\": \"$ticket\"}}")" 'a["data"]["id"]')
expect_eq "an administrator rejects one" \
    "$(get "$(admin approval.reject "{\"id\": \"$run_id\"}")" 'a["data"]["status"]')" rejected
expect_denied "what was prepared for it is gone, its secret with it" test -e "/var/lib/cockpit-fleet/runs/$run_id"

# --- a limit narrows the template's, never widens it -----------------------------
many=$(admin inventory.create '{"name": "many", "kind": "managed", "format": "ini", "content": "[web]\na1\na2\n[db]\nb1\n[all:vars]\nansible_connection=local\n"}')
limited=$(admin template.create "{\"name\": \"limited\", \"project_id\": \"$project_id\", \"playbook\": \"hosts.yml\", \"inventory_id\": \"$(get "$many" 'a["data"]["id"]')\", \"limit_hosts\": \"web\"}")
limited_id=$(get "$limited" 'a["data"]["id"]')
for case in "all:all|['a1', 'a2']" "a1,b1|['a1']" "all:!a1|['a2']"; do
    run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$limited_id\", \"limit\": \"${case%%|*}\"}")" 'a["data"]["id"]')
    expect_run "a run limited to ${case%%|*} on a template limited to web" "$run_id" 120 successful
    expect_eq "it reached only hosts of web" \
        "$(get "$(ctl itoperator run.get "{\"id\": \"$run_id\"}")" 'sorted(a["data"]["stats"])')" "${case#*|}"
done
run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$limited_id\", \"limit\": \"b1\"}")" 'a["data"]["id"]')
expect_run "a limit with no host of the template's fails the run" "$run_id" 120 failed

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

# --- export, and import on an empty installation ---------------------------------
exported=$(admin config.export)
expect_eq "the configuration is exported by name" \
    "$(get "$exported" '[t["name"] + ">" + t["project"] + ">" + t["inventory"] + ">" + t["machine_credential"] for t in a["data"]["templates"]][0]')" "demo>demo>self>local ssh"
if printf '%s' "$exported" | grep -q "$IT_SSH_PASSWORD"; then fail "the export holds a secret"; else pass "the export holds no secret"; fi
expect_eq "operator cannot export" "$(get "$(ctl itoperator config.export)" 'a["error"]["code"]')" forbidden
printf '%s' "$exported" | python3 -c 'import json,sys; print(json.dumps({"document": json.load(sys.stdin)["data"]}))' > /tmp/fleet-it-export.json
again=$(admin config.import "$(cat /tmp/fleet-it-export.json)")
expect_eq "importing where everything is leaves it as it is" "$(get "$again" 'a["data"]["created"], a["data"]["failed"]')" "0 0"
for id in $(db "SELECT group_concat(id, ' ') FROM runs WHERE status IN ('queued','running')"); do [ "$id" = None ] || systemctl stop "cockpit-fleet-run@$id.service" 2>/dev/null; done
rm -f "$DB" "$DB-wal" "$DB-shm"
imported=$(admin config.import "$(cat /tmp/fleet-it-export.json)"); rm -f /tmp/fleet-it-export.json
expect_eq "importing on an empty database creates everything" "$(get "$imported" 'a["data"]["failed"], a["data"]["exists"], a["data"]["created"] > 5')" "0 0 True"
expect_eq "and exports the same again" \
    "$(get "$(admin config.export)" '{k: v for k, v in a["data"].items() if k != "exported_at"}')" \
    "$(get "$exported" '{k: v for k, v in a["data"].items() if k != "exported_at"}')"
expect_eq "an imported credential has no secret" "$(get "$(admin credential.list)" '[c["secrets"]["password"] for c in a["data"]["credentials"] if c["name"] == "local ssh"]')" "[False]"
expect_eq "each thing imported is in the audit log" "$(get "$(admin audit.list)" 'sum(1 for e in a["data"]["entries"] if e["command"] == "template.create")')" 4

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
