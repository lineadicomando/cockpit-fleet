#!/bin/sh
# fleet, the command for a terminal: a project set up and launched from it,
# the role of each user, "cli" in the audit trail, secrets on stdin only, the
# exit status of a run, completion and the manual.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

wipe
cleanup() {
    for user in itviewer itnobody; do userdel -r "$user" 2>/dev/null; done
    wipe
}
trap cleanup EXIT
prepare
useradd -m -G cockpit-fleet-viewer itviewer
useradd -m itnobody

fleet_as() {  # fleet_as USER ARGS...: what that user gets in $out, its exit status in $code
    user=$1; shift
    out=$(cd /tmp && sudo -u "$user" -H fleet "$@" 2>&1); code=$?
}

# --- set up by an administrator, from the terminal ------------------------------------
fleet_as itadmin project create --name cli --kind managed --json; project=$(get "$out" 'a["id"]')
expect_eq "an administrator creates a project" "${#project}" 13
printf '%s\n' '- name: Greet' '  hosts: all' '  connection: local' '  gather_facts: false' \
    '  tasks:' '    - ansible.builtin.debug:' '        msg: "greeting is {{ greeting }}"' \
    > /tmp/fleet-it-site.yml
printf '%s\n' '- hosts: all' '  connection: local' '  gather_facts: false' '  tasks:' \
    '    - ansible.builtin.fail:' '        msg: on purpose' > /tmp/fleet-it-fail.yml
chmod 644 /tmp/fleet-it-site.yml /tmp/fleet-it-fail.yml
fleet_as itadmin file write "$project" --path site.yml --expected "" --content @/tmp/fleet-it-site.yml
expect_eq "a file is written from a file of the terminal" "$code" 0
fleet_as itadmin file write "$project" --path fail.yml --expected "" --content @- < /tmp/fleet-it-fail.yml
expect_eq "and from stdin" "$code" 0
fleet_as itadmin inventory create --name local --kind managed --format ini --content localhost --json
inventory=$(get "$out" 'a["id"]')
fleet_as itadmin template create --name greet --project-id "$project" --playbook site.yml --inventory-id "$inventory" \
    --survey '[{"name": "greeting", "type": "text", "required": true}]'
expect_eq "a template takes its survey as JSON" "$code" 0
fleet_as itadmin template create --name broken --project-id "$project" --playbook fail.yml --inventory-id "$inventory"

# --- launched by an operator -----------------------------------------------------------
fleet_as itoperator launch greet -a greeting=from-the-terminal
expect_eq "an operator launches a template and follows it to its end" "$code" 0
expect_eq "the output is printed as it comes" "$(printf '%s' "$out" | grep -c 'greeting is from-the-terminal')" 1
expect_eq "then how it went" "$(printf '%s' "$out" | grep -c ': successful, rc 0')" 1
expect_eq "the launch is the operator's, through cli" \
    "$(get "$(admin audit.list)" '[(e["user"], e["role"], e["via"]) for e in a["data"]["entries"] if e["command"] == "run.start"][0]')" \
    "('itoperator', 'operator', 'cli')"
fleet_as itoperator run list --limit 1 --json; run=$(get "$out" 'a["runs"][0]["id"]')
fleet_as itoperator log "$run"
expect_eq "the log of a run can be read again" "$(printf '%s' "$out" | grep -c 'greeting is from-the-terminal')" 1
fleet_as itoperator launch broken
expect_eq "a run that fails is exit status 1" "$code" 1
fleet_as itoperator launch greet
expect_eq "a required answer left out, with no terminal to ask, is a usage error" "$code" 2
fleet_as itoperator launch greet -a greeting=x --limit 'all,!localhost' --detach
expect_eq "detached, a launch returns at once" "$code" 0

# --- roles -----------------------------------------------------------------------------
fleet_as itoperator template create --name x --project-id "$project" --playbook site.yml --inventory-id "$inventory"
expect_eq "an operator cannot create a template" "$code" 3
fleet_as itviewer run list
expect_eq "a viewer lists runs" "$code" 0
fleet_as itviewer log "$run"
expect_eq "but does not read their output" "$code" 3
fleet_as itnobody whoami
expect_eq "a user in no group is told why" "$code:$(printf '%s' "$out" | grep -c cockpit-fleet-viewer)" "3:1"
fleet_as itviewer help; seen=$(printf '%s' "$out" | grep -c '  template create')
fleet_as itadmin help; seen=$seen$(printf '%s' "$out" | grep -c '  template create')
expect_eq "help lists what the role may do" "$seen" "01"

# --- secrets ---------------------------------------------------------------------------
fleet_as itadmin credential create --name vault --kind vault --json; vault=$(get "$out" 'a["id"]')
fleet_as itadmin secret vault vault_password <<EOF2
it-secret-value
EOF2
expect_eq "an administrator sets a secret from stdin" "$code" 0
fleet_as itadmin credential list --json
expect_eq "and it is there" "$(get "$out" '[c for c in a["credentials"] if c["id"] == "'"$vault"'"][0]["secrets"]["vault_password"]')" True
fleet_as itoperator secret vault vault_password <<EOF2
it-secret-value
EOF2
expect_eq "an operator cannot" "$code" 3
expect_eq "the value is in no record" "$(journalctl --since -10min -o cat | grep -c it-secret-value)$(grep -c it-secret-value "$DB")" "00"

# --- names, completion, an archive from a file -------------------------------------------------
fleet_as itoperator inventory get local --json
expect_eq "a name stands for an id" "$code $(get "$out" 'a["name"]')" "0 local"
fleet_as itoperator inventory get nothing
expect_eq "a name nothing has is said" "$code $(printf '%s' "$out" | grep -c "no inventory 'nothing'")" "3 1"
expect_eq "completion offers the names of templates" \
    "$(sudo -u itoperator fleet --complete launch gr)" greet
expect_eq "and nothing to whoever has no role" "$(sudo -u itnobody fleet --complete launch gr)" ""
mkdir -p /tmp/fleet-it-archive
printf -- '- hosts: all\n  gather_facts: false\n  tasks: []\n' > /tmp/fleet-it-archive/site.yml
tar -C /tmp/fleet-it-archive -czf /tmp/fleet-it-archive.tar.gz site.yml
chmod a+r /tmp/fleet-it-archive.tar.gz
fleet_as itadmin project create --name shipped --kind archive
fleet_as itadmin project upload shipped --archive /tmp/fleet-it-archive.tar.gz --json
expect_eq "an archive goes up from a file" "$code" 0
upload_run=$(get "$out" 'a["run_id"]')
expect_run "and is unpacked" "$upload_run" 60 successful
rm -rf /tmp/fleet-it-archive /tmp/fleet-it-archive.tar.gz

# --- projects at a terminal --------------------------------------------------------------
# A project written in the interface, cloned, changed with git and pushed back.
cd /tmp
sudo -u itadmin -H git config --global user.name itadmin
sudo -u itadmin -H git config --global user.email itadmin@localhost
fleet_as itadmin project clone cli /tmp/fleet-it-clone
expect_eq "a managed project is cloned" "$code $([ "$(cat /tmp/fleet-it-clone/site.yml)" = "$(cat /tmp/fleet-it-site.yml)" ] && echo same)" "0 same"
printf -- '- hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.debug: msg=pushed\n' > /tmp/fleet-it-clone/pushed.yml
chown -R itadmin /tmp/fleet-it-clone
sudo -u itadmin -H git -C /tmp/fleet-it-clone add pushed.yml
sudo -u itadmin -H git -C /tmp/fleet-it-clone commit -q -m "Add pushed.yml"
fleet_as itadmin project push /tmp/fleet-it-clone
expect_eq "its commits are pushed back" "$code $(printf '%s' "$out" | grep -c 'pushed: the project is at')" "0 1"
expect_eq "as if written in the interface" "$(get "$(admin file.history "{\"id\": \"$project\"}")" 'a["data"]["commits"][0]["subject"]')" "Add pushed.yml"
fleet_as itoperator project clone cli /tmp/fleet-it-other
expect_eq "an operator does not clone" "$code" 3
fleet_as itadmin file write "$project" --path later.yml --expected "" --content 'x: 1'
fleet_as itadmin project pull /tmp/fleet-it-clone
expect_eq "what the interface wrote is pulled" "$code $(test -f /tmp/fleet-it-clone/later.yml && echo here)" "0 here"
rm -rf /tmp/fleet-it-clone /tmp/fleet-it-other

# A directory of local projects as the guide says: written by the
# administrators, read by the service through an ACL, by nobody else.
install -d -m 2770 -g cockpit-fleet-admin /srv/fleet-it/roots
setfacl -m u:cockpit-fleet:rX -m d:u:cockpit-fleet:rX /srv/fleet-it/roots
sed -i 's|^local_roots = .*|local_roots = ["/srv/fleet-it", "/srv/fleet-it/roots"]|' "$CONFIG"
sudo -u itadmin sh -c 'umask 022 && mkdir /srv/fleet-it/roots/mine && printf -- "- hosts: all\n  gather_facts: false\n  tasks: []\n" > /srv/fleet-it/roots/mine/site.yml'
fleet_as itadmin project create --name mine --kind local --path /srv/fleet-it/roots/mine --json
mine=$(get "$out" 'a["id"]')
fleet_as itadmin template create --name mine --project-id mine --playbook site.yml --inventory-id local --json
fleet_as itoperator launch mine
expect_eq "a playbook written there is launched" "$code" 0
expect_eq "the self-check knows the convention" \
    "$(get "$(admin selfcheck)" '[c["status"] for c in a["data"]["checks"] if c["id"] == "project_roots"][0]')" warn
expect_eq "and says what is not as it should be" \
    "$(get "$(admin selfcheck)" '"/srv/fleet-it:" in [c["detail"] for c in a["data"]["checks"] if c["id"] == "project_roots"][0]')" True
expect_denied "nobody else reads it" sudo -u itnobody ls /srv/fleet-it/roots/mine

# --- what is installed beside it ---------------------------------------------------------
expect_eq "completion knows the commands" "$(fleet --complete run sta)" start
expect_ok "completion is installed for bash" test -f /usr/share/bash-completion/completions/fleet
expect_ok "and the manual" sh -c "man -w fleet >/dev/null 2>&1 || test -f /usr/share/man/man1/fleet.1.gz"

rm -f /tmp/fleet-it-site.yml /tmp/fleet-it-fail.yml
finish
