#!/bin/sh
# Negative tests on the installed package: hostile input to every fleetctl
# command through sudo, as each role (negative-inputs.py); what the caller's
# environment and working directory cannot bring into fleetctl; a secret typed
# where a ticket was expected, refused and recorded nowhere.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

EVIL=/var/tmp/fleet-it-evil
CANARY=/var/tmp/fleet-it-canary
MARK=it-neg-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')

cleanup() {
    wipe
    for user in itviewer itnobody; do userdel -r "$user" 2>/dev/null; done
    "$SECRET" delete it-neg 2>/dev/null
    rm -rf "$EVIL" "$CANARY"
}
trap cleanup EXIT
cleanup
prepare
useradd -m -G cockpit-fleet-viewer itviewer
useradd -m itnobody
started=$(date +%s)

# The canary is the service account's, so that anything fleetctl wrote there would show.
install -d -o cockpit-fleet -g cockpit-fleet -m 0700 "$CANARY"

# --- the caller's environment and directory -----------------------------------------
# A package, a site hook and a startup file that would leave a mark if Python loaded them.
mkdir -p "$EVIL/cockpit_fleet"
for file in cockpit_fleet/__init__.py sitecustomize.py usercustomize.py startup.py; do
    printf 'open("%s/loaded", "w").close()\n' "$CANARY" > "$EVIL/$file"
done
chmod -R a+rX "$EVIL"
chmod 1777 "$CANARY"

answer=$(sudo -u itadmin env PYTHONPATH="$EVIL" PYTHONSTARTUP="$EVIL/startup.py" \
    PYTHONUSERBASE="$EVIL" PYTHONHOME="$EVIL" LD_PRELOAD="$EVIL/x.so" \
    sudo -n -u cockpit-fleet "$FLEETCTL" whoami </dev/null 2>/dev/null)
expect_eq "the caller's Python and loader variables do not reach fleetctl" "$(get "$answer" 'a["ok"]')" True
answer=$(cd "$EVIL" && sudo -u itadmin sudo -n -u cockpit-fleet "$FLEETCTL" whoami </dev/null 2>&1)
expect_eq "nor does a package in the working directory" "$(get "$answer" 'a["ok"]')" True
expect_denied "nothing of them was loaded" test -e "$CANARY/loaded"
expect_denied "a variable cannot be set through sudo" \
    sudo -u itadmin sudo -n -u cockpit-fleet PYTHONPATH="$EVIL" "$FLEETCTL" whoami
expect_denied "nor kept with -E" sudo -u itadmin env PYTHONPATH="$EVIL" sudo -n -E -u cockpit-fleet "$FLEETCTL" whoami
# Asked for by name, sudo may keep a variable; fleetctl's Python (-I) ignores it then.
sudo -u itadmin env PYTHONPATH="$EVIL" sudo -n --preserve-env=PYTHONPATH -u cockpit-fleet \
    "$FLEETCTL" whoami </dev/null >/dev/null 2>&1
expect_denied "nor if kept by name" test -e "$CANARY/loaded"
expect_denied "fleetctl takes one command and nothing more" \
    sh -c "sudo -u itadmin sudo -n -u cockpit-fleet $FLEETCTL whoami extra </dev/null | grep -q '\"ok\": true'"
expect_denied "an account in no group cannot run it" sudo -u itnobody sudo -n -u cockpit-fleet "$FLEETCTL" whoami
chmod 0700 "$CANARY"
chown cockpit-fleet: "$CANARY"
rm -f "$CANARY/loaded"

# --- a secret where a ticket was expected ---------------------------------------------
printf '%s' "$MARK" | "$SECRET" set it-neg
ticket=$(printf '%s' "$MARK" | sudo -u itoperator sudo -n "$SECRET" answer)
mkdir -p /srv/fleet-it/site
printf -- '- hosts: all\n  gather_facts: false\n  tasks: []\n' > /srv/fleet-it/site/site.yml
project=$(get "$(admin project.create '{"name": "site", "kind": "local", "path": "/srv/fleet-it/site"}')" 'a["data"]["id"]')
inventory=$(get "$(admin inventory.create '{"name": "local", "kind": "managed", "content": "all:\n  hosts:\n    localhost:\n"}')" 'a["data"]["id"]')
template=$(get "$(admin template.create "{\"name\": \"t\", \"project_id\": \"$project\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory\", \"survey\": [{\"name\": \"token\", \"type\": \"secret\"}]}")" 'a["data"]["id"]')
expect_eq "a secret typed in clear at launch is refused" \
    "$(get "$(ctl itoperator run.start "{\"template_id\": \"$template\", \"answers\": {\"token\": \"$MARK\"}}")" 'a["error"]["code"]')" bad_request
expect_eq "and as a default of the survey" \
    "$(get "$(admin template.update "{\"id\": \"$template\", \"name\": \"t\", \"project_id\": \"$project\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory\", \"survey\": [{\"name\": \"token\", \"type\": \"secret\", \"default\": \"$MARK\"}]}")" 'a["error"]["code"]')" bad_request
expect_eq "and in the answers of a schedule" \
    "$(get "$(admin schedule.create "{\"name\": \"s\", \"template_id\": \"$template\", \"calendar\": \"daily\", \"answers\": {\"token\": \"$MARK\"}}")" 'a["error"]["code"]')" bad_request
expect_eq "the refusals are in the audit log" \
    "$(get "$(admin audit.list)" 'sum(e["outcome"] == "error" and "token" in e["params"] for e in a["data"]["entries"])')" 3
expect_eq "with the value masked" \
    "$(get "$(admin audit.list)" 'sum("***" in e["params"] for e in a["data"]["entries"] if e["outcome"] == "error")')" 3
expect_eq "a ticket is taken at launch" \
    "$(get "$(ctl itoperator run.start "{\"template_id\": \"$template\", \"answers\": {\"token\": \"$ticket\"}}")" 'a["ok"]')" True
run_id=$(db "SELECT id FROM runs ORDER BY created_at DESC LIMIT 1")
expect_run "and the run goes" "$run_id" 90 successful

contains() {  # contains TEXT FILE...: whether any of the files that exist holds TEXT
    python3 -c "import os, sys; sys.exit(0 if any(sys.argv[1].encode() in open(f, 'rb').read() for f in sys.argv[2:] if os.path.exists(f)) else 1)" "$@"
}
expect_denied "the secret is in no file of the database" contains "$MARK" "$DB" "$DB-wal"
expect_denied "nor in the journal" sh -c "journalctl --since @$started -o export --no-pager | grep -aq '$MARK'"
expect_denied "nor in the files of the runs" grep -rqa "$MARK" /var/lib/cockpit-fleet/runs

# --- hostile input to every command ---------------------------------------------------
python3 -I "$(dirname "$0")/negative-inputs.py" "$CANARY" || FAILED=1

expect_eq "the database is whole" "$(db 'PRAGMA integrity_check')" ok

finish
