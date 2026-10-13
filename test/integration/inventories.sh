#!/bin/sh
# Inventories that are more than a file of hosts: the YAML file of an inventory
# plugin, a directory of sources, an executable script (off unless config.toml
# allows it), and one that nothing can read.
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

finish
