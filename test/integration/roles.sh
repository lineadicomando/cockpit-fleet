#!/bin/sh
# Roles come from Unix groups and are enforced by the backend, whatever the
# frontend shows. Creates four temporary accounts and removes them at the end.
set -u
. "$(dirname "$0")/lib.sh"
require_root

FLEETCTL=/usr/libexec/cockpit-fleet/fleetctl
SECRET=/usr/libexec/cockpit-fleet/fleet-secret
USERS="itviewer itoperator itadmin itnobody"

cleanup() {
    for user in $USERS; do userdel -r "$user" 2>/dev/null; done
    "$SECRET" delete it-role 2>/dev/null
}
trap cleanup EXIT
cleanup

useradd -m -G cockpit-fleet-viewer itviewer
useradd -m -G cockpit-fleet-operator itoperator
useradd -m -G cockpit-fleet-admin itadmin
useradd -m itnobody

ctl() {  # ctl USER COMMAND [JSON]: what that user gets from fleetctl
    printf '%s' "${3:-}" | sudo -u "$1" sudo -n -u cockpit-fleet "$FLEETCTL" "$2" 2>&1
}
field() {  # field JSON-TEXT PYTHON-EXPRESSION on the parsed answer "a"
    printf '%s' "$1" | python3 -c "import json,sys; a=json.load(sys.stdin); print($2)" 2>/dev/null
}
expect_eq() {  # expect_eq DESCRIPTION GOT EXPECTED
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got: '$2')"; fi
}

for pair in itviewer:viewer itoperator:operator itadmin:admin; do
    user=${pair%%:*}; role=${pair##*:}
    answer=$(ctl "$user" whoami)
    expect_eq "$user has the $role role" "$(field "$answer" 'a["data"]["role"]')" "$role"
    expect_eq "$user is identified by name" "$(field "$answer" 'a["data"]["user"]')" "$user"
done

expect_eq "viewer may run the self-check" "$(field "$(ctl itviewer selfcheck)" 'a["ok"]')" True
expect_eq "viewer is refused the audit log" "$(field "$(ctl itviewer audit.list)" 'a["error"]["code"]')" forbidden
expect_eq "operator is refused the audit log" "$(field "$(ctl itoperator audit.list)" 'a["error"]["code"]')" forbidden
expect_eq "operator is refused the configuration" "$(field "$(ctl itoperator config.show)" 'a["error"]["code"]')" forbidden
expect_eq "admin reads the configuration" "$(field "$(ctl itadmin config.show)" 'a["ok"]')" True

log=$(ctl itadmin audit.list '{"limit": 20}')
expect_eq "admin reads the audit log" "$(field "$log" 'a["ok"]')" True
expect_eq "the viewer's refused attempt is in the audit log" \
    "$(field "$log" 'any(e["user"]=="itviewer" and e["command"]=="audit.list" and e["outcome"]=="denied" for e in a["data"]["entries"])')" True
expect_ok "the refused attempt is in the journal too" \
    sh -c "journalctl -t cockpit-fleet --since=-2min -o json --no-pager | grep -q '\"FLEET_USER\":\"itviewer\".*\"FLEET_OUTCOME\":\"denied\"\\|\"FLEET_OUTCOME\":\"denied\".*\"FLEET_USER\":\"itviewer\"'"

expect_denied "account in no role group cannot reach fleetctl" sudo -u itnobody sudo -n -u cockpit-fleet "$FLEETCTL" whoami
expect_denied "fleetctl refuses to run without sudo" sudo -u itadmin "$FLEETCTL" whoami
expect_denied "fleetctl refuses to run as root directly" "$FLEETCTL" whoami
expect_denied "a role group does not allow other commands as the service account" sudo -u itadmin sudo -n -u cockpit-fleet /usr/bin/id
expect_eq "SUDO_UID cannot be forged by the caller" \
    "$(field "$(printf '' | sudo -u itviewer env SUDO_UID=0 sudo -n -u cockpit-fleet "$FLEETCTL" whoami 2>&1)" 'a["data"]["role"]')" viewer

expect_ok     "admin stores a secret"      sh -c "printf value | sudo -u itadmin sudo -n $SECRET set it-role"
expect_ok     "storing a secret is in the journal, without the value" \
    sh -c "journalctl -t cockpit-fleet --since=-2min -o cat --no-pager | grep -q 'itadmin secret.set: it-role'"
expect_denied "operator cannot store a secret" sh -c "printf value | sudo -u itoperator sudo -n $SECRET set it-role2"
expect_denied "viewer cannot list secrets"     sudo -u itviewer sudo -n "$SECRET" list

expect_eq "database is private to the service account" "$(stat -c '%U %a' /var/lib/cockpit-fleet/fleet.db)" "cockpit-fleet 600"
expect_eq "self-check finds no problem" "$(field "$(ctl itviewer selfcheck)" 'sorted(c["id"] for c in a["data"]["checks"] if c["status"]=="fail")')" "[]"

finish
