#!/bin/sh
# The collections a project asks for in its requirements file: installed by a
# job in the sandbox, kept beside the project, copied into every run.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
# With IT_GALAXY_NET=yes it also installs a collection from a public git
# repository, which needs the network.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

WORK=/tmp/fleet-it-galaxy
SITE=/srv/fleet-it/site
cleanup() { wipe; rm -rf "$WORK"; }
trap cleanup EXIT
cleanup
prepare
mkdir -p "$WORK" "$SITE/vendor"

log() { sed 's/\x1b\[[0-9;]*m//g' "/var/lib/cockpit-fleet/runs/$1/stdout.log" 2>/dev/null; }
state() { get "$(admin project.list)" "[p['collections'] for p in a['data']['projects'] if p['id'] == '$1'][0]$2"; }

# --- a collection of our own, as an archive inside the project -----------------------
(
    export HOME="$WORK/home" ANSIBLE_HOME="$WORK/home/.ansible"
    mkdir -p "$HOME"
    ansible-galaxy collection init acme.tools --init-path "$WORK/src" > /dev/null
    mkdir -p "$WORK/src/acme/tools/plugins/modules"
    cat > "$WORK/src/acme/tools/plugins/modules/marker.py" <<'PY'
#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(argument_spec={"text": {"type": "str", "default": "hello"}},
                           supports_check_mode=True)
    module.exit_json(changed=False, text=module.params["text"])


if __name__ == "__main__":
    main()
PY
    ansible-galaxy collection build "$WORK/src/acme/tools" --output-path "$SITE/vendor" > /dev/null
)
expect_ok "the test collection is built" test -f "$SITE/vendor/acme-tools-1.0.0.tar.gz"

cat > "$SITE/site.yml" <<'YAML'
- name: Use a module of an installed collection
  hosts: all
  connection: local
  gather_facts: false
  tasks:
    - name: Call it
      acme.tools.marker:
        text: from the collection
      register: said
    - name: Check what it said
      ansible.builtin.assert:
        that: said.text == 'from the collection'
YAML
cat > "$SITE/requirements.yml" <<'YAML'
collections:
  - name: vendor/acme-tools-1.0.0.tar.gz
    type: file
YAML
digest=$(sha256sum "$SITE/requirements.yml" | cut -d' ' -f1)

project=$(admin project.create "{\"name\": \"site\", \"kind\": \"local\", \"path\": \"$SITE\"}")
id=$(get "$project" 'a["data"]["id"]')
home=/var/lib/cockpit-fleet/projects/$id/collections
inventory=$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "localhost\n"}')
template=$(admin template.create "{\"name\": \"site\", \"project_id\": \"$id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$(get "$inventory" 'a["data"]["id"]')\"}")
template_id=$(get "$template" 'a["data"]["id"]')

expect_eq "the project says what it asks for, and that it is not there" "$(state "$id" '["file"]') $(state "$id" '["state"]')" "requirements.yml missing"
expect_eq "a playbook does not start without its collections" "$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")" 'a["error"]["code"]')" conflict
before=$(get "$(admin file.check "{\"id\": \"$id\", \"path\": \"site.yml\"}")" 'a["data"]["run_id"]')
expect_run "a syntax check does not find the module yet" "$before" 180 failed
expect_eq "operator cannot install collections" "$(get "$(ctl itoperator project.collections "{\"id\": \"$id\"}")" 'a["error"]["code"]')" forbidden

# --- installed by a job, kept beside the project ---------------------------------------
run_id=$(get "$(admin project.collections "{\"id\": \"$id\"}")" 'a["data"]["run_id"]')
expect_run "the collections are installed by a job, in the sandbox" "$run_id" 180 successful
expect_ok "they are kept under the digest of the requirements file" test -f "$home/$digest/ansible_collections/acme/tools/plugins/modules/marker.py"
expect_eq "which is the only thing kept" "$(ls "$home")" "$digest"
expect_eq "they belong to the service account, and to nobody else" "$(stat -c '%U %a' "$home/$digest")" "cockpit-fleet 700"
expect_eq "the project says they are installed" "$(state "$id" '["state"]') $(state "$id" '["error"]')" "installed "
expect_eq "nothing of the job is left but its record" "$(ls "/var/lib/cockpit-fleet/runs/$run_id" | sort | tr '\n' ' ')" "known_hosts result.json spec.json stdout.log "
expect_eq "nothing was installed in the project itself" "$(ls "$SITE" | tr '\n' ' ')" "requirements.yml site.yml vendor "
expect_eq "the installation is in the audit log" \
    "$(get "$(admin audit.list)" 'any(e["command"]=="project.collections" and e["outcome"]=="ok" for e in a["data"]["entries"])')" True

# --- a run, a check ----------------------------------------------------------------------
play=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")" 'a["data"]["id"]')
expect_run "a playbook uses a module of the installed collection" "$play" 120 successful
expect_eq "the module answered" "$(get "$(admin run.get "{\"id\": \"$play\"}")" 'a["data"]["stats"]["localhost"]["ok"]')" 2
expect_denied "the run's copy is removed with it" test -e "/var/lib/cockpit-fleet/runs/$play/collections"
check=$(get "$(admin file.check "{\"id\": \"$id\", \"path\": \"site.yml\"}")" 'a["data"]["run_id"]')
expect_run "a syntax check finds the module too" "$check" 180 successful

# --- again, while the runs are being read: what the job left is taken once ---------------
again=$(get "$(admin project.collections "{\"id\": \"$id\"}")" 'a["data"]["run_id"]')
( while [ "$(db "SELECT status FROM runs WHERE id = '$again'")" = running ]; do admin run.list > /dev/null; done ) &
expect_run "installing again, with someone reading the runs meanwhile" "$again" 180 successful
wait
expect_ok "leaves the collections where they were" test -f "$home/$digest/ansible_collections/acme/tools/plugins/modules/marker.py"

# --- sources that must not work ----------------------------------------------------------
# A repository of this machine: git would read it with a local transport.
git init --quiet "$WORK/repo"
cp -r "$WORK/src/acme/tools/." "$WORK/repo/"
git -C "$WORK/repo" add --all
git -C "$WORK/repo" -c user.name=it -c user.email=it@localhost commit --quiet -m init
chmod -R a+rX "$WORK"
cat > "$SITE/requirements.yml" <<YAML
collections:
  - name: git+file://$WORK/repo
    type: git
YAML
expect_eq "other requirements: what is installed no longer counts" "$(state "$id" '["state"]')" missing
bad=$(get "$(admin project.collections "{\"id\": \"$id\"}")" 'a["data"]["run_id"]')
expect_run "a git source on a local path is refused" "$bad" 120 failed
expect_eq "by git itself, for its transport" "$(log "$bad" | grep -c "transport 'file' not allowed")" 1
expect_eq "the project shows the end of the job's output" "$(state "$id" '["error"]' | grep -q 'Failed to clone' && echo shown)" shown
expect_ok "what was installed before is still there" test -d "$home/$digest"
expect_denied "the failed job left nothing to copy" test -e "/var/lib/cockpit-fleet/runs/$bad/galaxy"

# --- written in the interface: installed when the file is saved ---------------------------
managed=$(get "$(admin project.create '{"name": "edited", "kind": "managed"}')" 'a["data"]["id"]')
saved=$(admin file.write "{\"id\": \"$managed\", \"path\": \"requirements.yml\", \"content\": \"collections: []\\n\", \"expected\": \"\"}")
auto=$(get "$saved" 'a["data"]["collections_run_id"]')
expect_run "saving a requirements file queues the installation" "$auto" 120 successful
expect_eq "nothing to install is an installation too" "$(state "$managed" '["state"]')" installed
expect_eq "saving it again as it is queues nothing" \
    "$(get "$(admin file.write "{\"id\": \"$managed\", \"path\": \"site.yml\", \"content\": \"# nothing\\n\", \"expected\": \"\"}")" 'a["data"]["collections_run_id"]')" None

# --- from the network ----------------------------------------------------------------------
if [ "${IT_GALAXY_NET:-}" = yes ]; then
    cat > "$SITE/requirements.yml" <<'YAML'
collections:
  - name: git+https://github.com/lineadicomando/ansible-collection-win_workman.git
    type: git
    version: main
YAML
    start=$(date +%s)
    net=$(get "$(admin project.collections "{\"id\": \"$id\"}")" 'a["data"]["run_id"]')
    expect_run "a collection is fetched from a public git repository" "$net" 300 successful
    echo "      from git: $(($(date +%s) - start)) s, $(du -sh "$home" | cut -f1)"
    expect_ok "it is kept for the project" test -f "$home/$(sha256sum "$SITE/requirements.yml" | cut -d' ' -f1)/ansible_collections/lineadicomando/win_workman/MANIFEST.json"
    expect_denied "and replaces what was kept before" test -e "$home/$digest"
    expect_eq "the failure is forgotten" "$(state "$id" '["error"]')" ""
else
    echo "SKIP  from the network (set IT_GALAXY_NET=yes)"
fi

finish
