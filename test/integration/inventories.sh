#!/bin/sh
# Inventories that are more than a file of hosts: the YAML file of an inventory
# plugin, a directory of sources, an executable script (off unless config.toml
# allows it), one that nothing can read, and one written in the interface as a
# tree of files (hosts, group_vars, host_vars), like ansible-win_edulab's school.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe
SRC=/srv/fleet-it

trap wipe EXIT
wipe
prepare

mkdir -p "$SRC/site/dir"
cat > "$SRC/site/generated.yml" <<'YAML'
plugin: ansible.builtin.generator
hosts:
  name: "{{ app }}-{{ env }}"
  parents:
    - name: "{{ env }}"
layers:
  app: [web, db]
  env: [dev, prod]
YAML
cat > "$SRC/site/dir/01-static.yml" <<'YAML'
all:
  hosts:
    self:
      ansible_connection: local
    other:
      ansible_connection: local
YAML
cat > "$SRC/site/dir/02-constructed.yml" <<'YAML'
plugin: ansible.builtin.constructed
groups:
  made: inventory_hostname == 'self'
YAML
cat > "$SRC/site/script.sh" <<'SH'
#!/bin/sh
echo '{"scripted": {"hosts": ["fromscript"]}, "_meta": {"hostvars": {"fromscript": {"ansible_connection": "local"}}}}'
SH
chmod 755 "$SRC/site/script.sh"
printf 'this is [not an inventory\n\t- at: all\n' > "$SRC/site/broken.yml"
cat > "$SRC/site/site.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.ping:
YAML
chmod -R a+rX "$SRC"

project_id=$(get "$(admin project.create "{\"name\": \"site\", \"kind\": \"local\", \"path\": \"$SRC/site\"}")" 'a["data"]["id"]')
inventory() {  # inventory NAME PATH: the id of a file inventory of the project
    get "$(admin inventory.create "{\"name\": \"$1\", \"kind\": \"file\", \"project_id\": \"$project_id\", \"path\": \"$2\"}")" 'a["data"]["id"]'
}
parse() {  # parse INVENTORY-ID: the run that reads it
    get "$(admin inventory.parse "{\"id\": \"$1\"}")" 'a["data"]["run_id"]'
}
hosts() {  # hosts INVENTORY-ID: "name:groups" of what was read
    get "$(admin inventory.get "{\"id\": \"$1\"}")" '" ".join(h["name"] + ":" + ",".join(h["groups"]) for h in a["data"]["hosts"])'
}
template() {  # template NAME INVENTORY-ID [LIMIT]
    get "$(admin template.create "{\"name\": \"$1\", \"project_id\": \"$project_id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$2\", \"limit_hosts\": \"${3:-}\"}")" 'a["data"]["id"]'
}
launch() { get "$(admin run.start "{\"template_id\": \"$1\"}")" 'a["data"]["id"]'; }

# --- the YAML file of an inventory plugin ----------------------------------------
generated=$(inventory generated generated.yml)
expect_run "an inventory plugin's file is read" "$(parse "$generated")" 60 successful
expect_eq "its hosts and groups are those the plugin makes" "$(hosts "$generated")" "db-dev:dev db-prod:prod web-dev:dev web-prod:prod"

# --- a directory of sources -------------------------------------------------------
dir=$(inventory directory dir)
expect_run "a directory of sources is read" "$(parse "$dir")" 60 successful
expect_eq "static hosts and a constructed group together" "$(hosts "$dir")" "other: self:made"
run_id=$(launch "$(template "on a directory" "$dir" made)")
expect_run "a playbook runs on it" "$run_id" 90 successful
expect_eq "on the hosts of the constructed group" "$(get "$(admin run.get "{\"id\": \"$run_id\"}")" 'sorted(a["data"]["stats"])')" "['self']"

# --- an executable script: read as a file, never run, unless allowed --------------
script=$(inventory script script.sh)
expect_run "a script is not run as an inventory" "$(parse "$script")" 60 failed
expect_eq "and gives no host" "$(hosts "$script")" ""
run_id=$(launch "$(template "on a script" "$script")")
expect_run "a playbook whose inventory cannot be read fails, it does not run on nothing" "$run_id" 90 failed
broken=$(inventory broken broken.yml)
expect_run "a file that is no inventory fails too" "$(parse "$broken")" 60 failed

printf '[inventories]\nscripts = true\n' >> "$CONFIG"
expect_run "config.toml lets a script be run" "$(parse "$script")" 60 successful
expect_eq "and its hosts are read" "$(hosts "$script")" "fromscript:scripted"
written=$(get "$(admin inventory.create '{"name": "written", "kind": "managed", "format": "yaml", "content": "plugin: ansible.builtin.generator\nhosts:\n  name: \"{{ app }}\"\nlayers:\n  app: [one, two]\n"}')" 'a["data"]["id"]')
expect_run "a plugin's file can be written in the interface" "$(parse "$written")" 60 successful
expect_eq "with its hosts" "$(hosts "$written")" "one: two:"

# --- written in the interface as a tree of files ----------------------------------
school=$(get "$(admin inventory.create '{"name": "school", "kind": "managed", "files": [
  {"path": "hosts.yaml", "content": "all:\n  children:\n    servers:\n      hosts:\n        dc01:\n    students:\n      hosts:\n        pc01:\n        pc02:\n    win_managed:\n      children:\n        students:\n"},
  {"path": "group_vars/all/vars.yaml", "content": "ansible_user: lab\n"},
  {"path": "group_vars/students/vars.yaml", "content": "ansible_connection: local\n"},
  {"path": "host_vars/pc02.yaml", "content": "ansible_host: 192.0.2.22\n"},
  {"path": "group_vars/all/vault.yaml.example", "content": "not: [yaml\n"}]}')" 'a["data"]["id"]')
addresses() {  # addresses INVENTORY-ID: "name=address" of what was read
    get "$(admin inventory.get "{\"id\": \"$1\"}")" '" ".join(h["name"] + "=" + h["address"] for h in sorted(a["data"]["hosts"], key=lambda h: h["name"]))'
}
groups_of() {  # groups_of INVENTORY-ID: "name:groups", groups sorted
    get "$(admin inventory.get "{\"id\": \"$1\"}")" '" ".join(h["name"] + ":" + ",".join(sorted(h["groups"])) for h in sorted(a["data"]["hosts"], key=lambda h: h["name"]))'
}
expect_run "a tree like a school is read" "$(parse "$school")" 60 successful
expect_eq "its groups, children included" "$(groups_of "$school")" "dc01:servers pc01:students,win_managed pc02:students,win_managed"
# The address shown is the host's own: the listing is exported, so what a group
# sets stays with the group (group_vars are seen at work by the run below).
expect_eq "host_vars beside its hosts are read" "$(addresses "$school")" "dc01= pc01= pc02=192.0.2.22"
run_id=$(launch "$(template "on a school" "$school" win_managed)")
expect_run "a playbook of another project runs on it" "$run_id" 90 successful
expect_eq "on the hosts of a group of groups, connected as group_vars say" "$(get "$(admin run.get "{\"id\": \"$run_id\"}")" 'sorted(a["data"]["stats"])')" "['pc01', 'pc02']"
sha=$(get "$(admin file.read "{\"id\": \"$school\", \"path\": \"host_vars/pc02.yaml\"}")" 'a["data"]["sha256"]')
answer=$(admin file.change "{\"id\": \"$school\", \"operations\": [{\"op\": \"write\", \"path\": \"host_vars/pc02.yaml\", \"content\": \"ansible_host: 192.0.2.23\\n\", \"expected\": \"$sha\"}, {\"op\": \"copy\", \"path\": \"host_vars/pc02.yaml\", \"to\": \"host_vars/pc01.yaml\"}]}")
expect_run "a change of its files reads it again" "$(get "$answer" 'a["data"]["inventory_run_ids"][0]')" 60 successful
expect_eq "with what changed" "$(addresses "$school")" "dc01= pc01=192.0.2.23 pc02=192.0.2.23"
admin inventory.update "{\"id\": \"$school\", \"path\": \"hosts.yaml\"}" >/dev/null
expect_run "a file of its tree can be the source" "$(parse "$school")" 60 successful
expect_eq "with the variables beside it" "$(addresses "$school")" "dc01= pc01=192.0.2.23 pc02=192.0.2.23"
expect_run "a change of its files checks its syntax" "$(get "$answer" 'a["data"]["check_run_ids"][0]')" 60 successful
sha=$(get "$(admin file.read "{\"id\": \"$school\", \"path\": \"host_vars/pc01.yaml\"}")" 'a["data"]["sha256"]')
answer=$(admin file.write "{\"id\": \"$school\", \"path\": \"host_vars/pc01.yaml\", \"content\": \"ansible_host: [\\n\", \"expected\": \"$sha\"}")
expect_run "a variables file that is no YAML fails the check" "$(get "$answer" 'a["data"]["check_run_id"]')" 60 failed
sha=$(get "$(admin file.read "{\"id\": \"$school\", \"path\": \"host_vars/pc01.yaml\"}")" 'a["data"]["sha256"]')
admin file.write "{\"id\": \"$school\", \"path\": \"host_vars/pc01.yaml\", \"content\": \"ansible_host: 192.0.2.23\\n\", \"expected\": \"$sha\"}" >/dev/null
# The whole tree as the source, with a file no plugin reads beside hosts.yaml:
# the reading takes the other one, the check does not.
admin inventory.update "{\"id\": \"$school\", \"path\": \"\"}" >/dev/null
answer=$(admin file.write "{\"id\": \"$school\", \"path\": \"broken\", \"content\": \"[web\\n\", \"expected\": \"\"}")
expect_run "a source that cannot be read leaves the reading to the others" "$(parse "$school")" 60 successful
expect_run "but fails the check" "$(get "$answer" 'a["data"]["check_run_id"]')" 60 failed
expect_run "the check is offered on request" "$(get "$(admin file.check "{\"id\": \"$school\", \"path\": \"hosts.yaml\"}")" 'a["data"]["run_id"]')" 60 failed
admin file.delete "{\"id\": \"$school\", \"path\": \"broken\"}" >/dev/null
expect_run "and passes once it is gone" "$(get "$(admin file.check "{\"id\": \"$school\", \"path\": \"hosts.yaml\"}")" 'a["data"]["run_id"]')" 60 successful
admin inventory.update "{\"id\": \"$school\", \"path\": \"hosts.yaml\"}" >/dev/null

# A file inventory of another project: copied for the run with what is beside it.
mkdir -p "$SRC/labs/inventories/lab1/group_vars/all" "$SRC/labs/inventories/lab1/host_vars"
printf 'all:\n  hosts:\n    lab1-pc01:\n    lab1-pc02:\n' > "$SRC/labs/inventories/lab1/hosts.yaml"
printf 'ansible_connection: local\n' > "$SRC/labs/inventories/lab1/group_vars/all/vars.yaml"
printf 'ansible_host: 192.0.2.31\n' > "$SRC/labs/inventories/lab1/host_vars/lab1-pc01.yaml"
chmod -R a+rX "$SRC/labs"
labs_id=$(get "$(admin project.create "{\"name\": \"labs\", \"kind\": \"local\", \"path\": \"$SRC/labs\"}")" 'a["data"]["id"]')
lab1=$(get "$(admin inventory.create "{\"name\": \"lab1\", \"kind\": \"file\", \"project_id\": \"$labs_id\", \"path\": \"inventories/lab1/hosts.yaml\"}")" 'a["data"]["id"]')
expect_run "an inventory of a project of its own is read" "$(parse "$lab1")" 60 successful
run_id=$(launch "$(template "on another project's inventory" "$lab1")")
expect_run "a template of another project runs on it" "$run_id" 90 successful
expect_eq "connected as its group_vars say" "$(get "$(admin run.get "{\"id\": \"$run_id\"}")" 'sorted(a["data"]["stats"])')" "['lab1-pc01', 'lab1-pc02']"

# An inventory written before inventories were trees: its text, in the database.
as_fleet python3 -c "import sqlite3, time; c = sqlite3.connect('$DB'); c.execute(\"INSERT INTO inventories (id, name, kind, format, content, created_at) VALUES ('iold00000001', 'old', 'managed', 'ini', '[web]\\nweb01 ansible_connection=local\\n', ?)\", (time.time(),)); c.commit()"
expect_run "an inventory of before trees is read" "$(parse iold00000001)" 60 successful
expect_eq "its text is the main file of its tree" "$(get "$(admin file.list '{"id": "iold00000001"}')" '[f["path"] for f in a["data"]["files"]]')" "['hosts']"
expect_eq "and no longer in the database" "$(db "SELECT length(content) FROM inventories WHERE id = 'iold00000001'")" "0"
expect_eq "with its hosts" "$(hosts iold00000001)" "web01:web"

finish
