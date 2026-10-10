#!/bin/sh
# A project written in the interface: files, commits by the caller, syntax
# checks in the sandbox, and a run from it.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

cleanup() { wipe; rm -f /tmp/fleet-it-write.json /tmp/fleet-it-rule.py; }
trap cleanup EXIT
cleanup
prepare

write() {  # write PROJECT PATH EXPECTED < content: the answer of file.write
    python3 -c 'import json,sys; print(json.dumps({"id": sys.argv[1], "path": sys.argv[2], "expected": sys.argv[3], "content": sys.stdin.read()}))' "$@" > /tmp/fleet-it-write.json
    admin file.write "$(cat /tmp/fleet-it-write.json)"; rm -f /tmp/fleet-it-write.json
}

project=$(admin project.create '{"name": "edited", "kind": "managed"}')
id=$(get "$project" 'a["data"]["id"]')
home=/var/lib/cockpit-fleet/projects/$id
expect_eq "a managed project is created empty" "$(get "$project" 'a["data"]["kind"], a["data"]["revision"]')" "managed "
expect_ok "its repository is beside the tree, not in it" sh -c "test -f $home/repo.git/HEAD && ! test -e $home/tree/.git"
expect_eq "operator cannot write files" \
    "$(get "$(ctl itoperator file.write "{\"id\": \"$id\", \"path\": \"x\", \"content\": \"\", \"expected\": \"\"}")" 'a["error"]["code"]')" forbidden
expect_eq "operator cannot read files" \
    "$(get "$(ctl itoperator file.list "{\"id\": \"$id\"}")" 'a["error"]["code"]')" forbidden

# --- a playbook: saved, committed, checked ------------------------------------------
saved=$(write "$id" site.yml "" <<'YAML'
- name: Leave a marker
  hosts: all
  connection: local
  gather_facts: false
  tasks:
    - name: Write the marker
      ansible.builtin.copy:
        content: "from the editor\n"
        dest: "{{ lookup('ansible.builtin.env', 'HOME') }}/marker"
        mode: "0644"
YAML
)
revision=$(get "$saved" 'a["data"]["revision"]')
check=$(get "$saved" 'a["data"]["check_run_id"]')
expect_eq "a write is a commit" "${#revision}" 40
expect_eq "the caller is the author" \
    "$(as_fleet git -C "$home/tree" --git-dir="$home/repo.git" log -1 --format='%an|%cn|%s')" "itadmin|cockpit-fleet|Add site.yml"
expect_eq "the project is at that revision" "$(db "SELECT revision FROM projects WHERE id = '$id'")" "$revision"
expect_run "saving a playbook checks its syntax, in the sandbox" "$check" 90 successful
expect_denied "a syntax check runs no task and leaves no events" test -e "/var/lib/cockpit-fleet/runs/$check/events.jsonl"
expect_ok "the copy of the project is removed after the check" sh -c "! test -e /var/lib/cockpit-fleet/runs/$check/project"
if [ -x /usr/bin/ansible-lint ]; then
    expect_eq "ansible-lint ran after the check" "$(get "$(admin run.get "{\"id\": \"$check\"}")" 'a["data"]["options"]["lint"] in ("clean", "findings")')" True
else
    expect_eq "without ansible-lint the check says nothing about it" "$(get "$(admin run.get "{\"id\": \"$check\"}")" '"lint" in a["data"]["options"]')" False
fi

# --- a stale write, a broken playbook --------------------------------------------------
expect_eq "a write that does not quote the current content is refused" \
    "$(get "$(write "$id" site.yml "" < /dev/null)" 'a["error"]["code"]')" conflict
sha=$(get "$saved" 'a["data"]["sha256"]')
broken=$(printf -- '- hosts: all\n  tasks:\n    - nosuch.module.at_all:\n  bogus_keyword: 1\n' | write "$id" broken.yml "")
expect_run "a playbook with a mistake fails its check" "$(get "$broken" 'a["data"]["check_run_id"]')" 90 failed
log=$(admin run.log "{\"id\": \"$(get "$broken" 'a["data"]["check_run_id"]')\"}")
expect_eq "the output says what is wrong" "$(get "$log" '"bogus_keyword" in a["data"]["text"] or "nosuch.module" in a["data"]["text"]')" True

# --- ansible-lint runs code of the project: it must stay in the sandbox ---------------
if [ -x /usr/bin/ansible-lint ]; then
    cat > /tmp/fleet-it-rule.py <<'PY'
from ansiblelint.rules import AnsibleLintRule

print("RULE LOADED", flush=True)
for target in ("/var/lib/cockpit-fleet/projects/pwned", "/etc/cockpit-fleet/pwned"):
    try:
        open(target, "w").close()
    except OSError:
        pass


class ItRule(AnsibleLintRule):
    id = "it-rule"
    description = "a rule that comes with the project"
    severity = "LOW"
    tags = ["it"]
PY
    write "$id" rules/it_rule.py "" < /tmp/fleet-it-rule.py > /dev/null; rm -f /tmp/fleet-it-rule.py
    printf 'rulesdir:\n  - rules\n' | write "$id" .ansible-lint "" > /dev/null
    rule=$(get "$(admin file.check "{\"id\": \"$id\", \"path\": \"site.yml\"}")" 'a["data"]["run_id"]')
    expect_run "a check with rules of the project" "$rule" 120 successful
    expect_ok "the rule of the project was executed" grep -q "RULE LOADED" "/var/lib/cockpit-fleet/runs/$rule/stdout.log"
    expect_denied "and could not write to the state of the backend" sh -c "test -e /var/lib/cockpit-fleet/projects/pwned || test -e /etc/cockpit-fleet/pwned"
else
    echo "SKIP  ansible-lint checks (not installed)"
fi

# --- paths ---------------------------------------------------------------------------
for path in ../x /etc/cron.d/x .git/config a/.GIT/hooks/post-commit; do
    expect_eq "path $path is refused" "$(get "$(write "$id" "$path" "" < /dev/null)" 'a["error"]["code"]')" bad_request
done
write "$id" repo.git/config "" < /dev/null > /dev/null
expect_ok "a file called like the repository stays in the tree" test -f "$home/tree/repo.git/config"
expect_eq "nothing was written outside the tree" "$(ls /var/lib/cockpit-fleet/projects/"$id" | sort | tr '\n' ' ')" "lock repo.git tree "

# --- rename, delete, history ---------------------------------------------------------
expect_eq "a file is renamed" "$(get "$(admin file.move "{\"id\": \"$id\", \"path\": \"broken.yml\", \"to\": \"old/broken.yml\"}")" 'len(a["data"]["revision"])')" 40
expect_eq "a file is deleted" "$(get "$(admin file.delete "{\"id\": \"$id\", \"path\": \"old/broken.yml\"}")" 'a["ok"]')" True
expect_denied "its directory goes with it" test -e "$home/tree/old"
history=$(admin file.history "{\"id\": \"$id\"}")
expect_eq "the changes are listed, newest first" "$(get "$history" '[c["subject"] for c in a["data"]["commits"]][:2]')" \
    "['Delete old/broken.yml', 'Rename broken.yml to old/broken.yml']"
expect_eq "a change shows what it did" \
    "$(get "$(admin file.diff "{\"id\": \"$id\", \"revision\": \"$revision\"}")" '"+- name: Leave a marker" in a["data"]["text"]')" True
expect_eq "writes are in the audit log without their content" \
    "$(get "$(admin audit.list)" 'any(e["command"]=="file.write" and "***" in e["params"] and "marker" not in e["params"] for e in a["data"]["entries"])')" True

# --- a run from the project ----------------------------------------------------------
inventory=$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "localhost\n"}')
template=$(admin template.create "{\"name\": \"edited\", \"project_id\": \"$id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$(get "$inventory" 'a["data"]["id"]')\"}")
run=$(ctl itoperator run.start "{\"template_id\": \"$(get "$template" 'a["data"]["id"]')\"}")
run_id=$(get "$run" 'a["data"]["id"]')
expect_run "a template runs a playbook written here" "$run_id" 120 successful
expect_eq "the playbook did its work" "$(get "$(admin run.get "{\"id\": \"$run_id\"}")" 'a["data"]["stats"]["localhost"]["changed"]')" 1
expect_eq "the run records the revision it ran" \
    "$(get "$run" 'a["data"]["revision"]')" "$(db "SELECT revision FROM projects WHERE id = '$id'")"

# --- reading the files of a project that is not written here ---------------------------
mkdir -p /srv/fleet-it/plain && echo '- hosts: all' > /srv/fleet-it/plain/site.yml && ln -s /etc/shadow /srv/fleet-it/plain/link
chmod -R a+rX /srv/fleet-it
plain=$(get "$(admin project.create '{"name": "plain", "kind": "local", "path": "/srv/fleet-it/plain"}')" 'a["data"]["id"]')
expect_eq "any project can be read" "$(get "$(admin file.read "{\"id\": \"$plain\", \"path\": \"site.yml\"}")" 'a["data"]["content"]')" "- hosts: all"
expect_eq "only one written here can be changed" \
    "$(get "$(write "$plain" site.yml "" < /dev/null)" 'a["error"]["code"]')" conflict
expect_eq "a symbolic link is not followed" "$(get "$(admin file.read "{\"id\": \"$plain\", \"path\": \"link\"}")" 'a["error"]["code"]')" conflict

finish
