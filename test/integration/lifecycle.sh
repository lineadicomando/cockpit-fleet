#!/bin/sh
# The life of the package on a controller: an older version installed and
# used, an upgrade with a run going, removal, installing again, and purge
# (on Debian; on Fedora and AlmaLinux, what dnf leaves). Run as root on a
# controller where cockpit-fleet is NOT installed, with the two packages
# named by IT_PKG_OLD and IT_PKG_NEW: scripts/ct-lifecycle.sh does that.
set -u
. "$(dirname "$0")/lib.sh"
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 2; }
[ -f "${IT_PKG_OLD:-}" ] && [ -f "${IT_PKG_NEW:-}" ] || { echo "set IT_PKG_OLD and IT_PKG_NEW to two packages" >&2; exit 2; }
[ -e /usr/libexec/cockpit-fleet ] && { echo "cockpit-fleet is installed already" >&2; exit 2; }

if command -v dpkg >/dev/null; then
    debian=yes
    install_package() { DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o Dpkg::Options::=--force-confold "$1" >/dev/null; }
    remove_package() { DEBIAN_FRONTEND=noninteractive apt-get remove -y -q cockpit-fleet >/dev/null; }
    installed_version() { dpkg-query -W -f '${db:Status-Abbrev} ${Version}' cockpit-fleet 2>/dev/null | sed -n 's/^ii *//p'; }
    # Debian's container images forbid packages to start services; a
    # machine has no such file, and the timer must start as it would there.
    if grep -qs 'exit 101' /usr/sbin/policy-rc.d; then rm /usr/sbin/policy-rc.d; fi
else
    debian=no
    install_package() { dnf install -y -q --disablerepo='*' "$1" >/dev/null; }
    remove_package() { dnf remove -y -q cockpit-fleet >/dev/null; }
    installed_version() { rpm -q --qf '%{VERSION}' cockpit-fleet 2>/dev/null | grep -v 'not installed'; }
fi
version_of() {  # the version a package file says
    if [ "$debian" = yes ]; then dpkg-deb -f "$1" Version; else rpm -qp --qf '%{VERSION}' "$1" 2>/dev/null; fi
}
schema() { db "PRAGMA user_version"; }
active() { systemctl is-active --quiet "$1"; }
runs_active() { systemctl list-units --no-legend --state=active 'cockpit-fleet-run@*' | grep -c . ; }
# The service account's files: anything left owned by root would lock it out.
owned_by_root() { find /var/lib/cockpit-fleet -user root | grep -c . ; }

cleanup() {
    for user in itadmin itoperator; do userdel -r "$user" 2>/dev/null; done
    rm -rf /srv/fleet-it
}
trap cleanup EXIT

# --- the older version, used ----------------------------------------------------
install_package "$IT_PKG_OLD"
expect_eq "the older package is installed" "$(installed_version)" "$(version_of "$IT_PKG_OLD")"
expect_ok "its timer is active" active cockpit-fleet-scheduler.timer
useradd -m -G cockpit-fleet-admin itadmin
useradd -m -G cockpit-fleet-operator itoperator
# A change of the configuration, which upgrades and removal must keep.
sed -i -e 's|^#max_concurrent = 4|max_concurrent = 3|' -e 's|^#local_roots = .*|local_roots = ["/srv/fleet-it"]|' "$CONFIG"
mkdir -p /srv/fleet-it/site
printf -- '- hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.ping:\n' > /srv/fleet-it/site/quick.yml
printf -- '- hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.command: sleep 45\n' > /srv/fleet-it/site/slow.yml
project=$(get "$(admin project.create '{"name": "site", "kind": "local", "path": "/srv/fleet-it/site"}')" 'a["data"]["id"]')
inventory=$(get "$(admin inventory.create '{"name": "here", "kind": "managed", "content": "all:\n  hosts:\n    localhost:\n      ansible_connection: local\n"}')" 'a["data"]["id"]')
credential=$(admin credential.create '{"name": "vault", "kind": "vault"}')
printf 'it-vault-password' | set_secret "$credential" vault_password
template() { get "$(admin template.create "{\"name\": \"$1\", \"project_id\": \"$project\", \"playbook\": \"$1.yml\", \"inventory_id\": \"$inventory\"}")" 'a["data"]["id"]'; }
quick=$(template quick)
slow=$(template slow)
expect_eq "a schedule is created" "$(get "$(admin schedule.create "{\"name\": \"nightly\", \"template_id\": \"$quick\", \"calendar\": \"*-*-* 03:00:00\"}")" 'a["ok"]')" True
launch() { get "$(ctl itoperator run.start "{\"template_id\": \"$1\"}")" 'a["data"]["id"]'; }
expect_run "a run of the older version ends" "$(launch "$quick")" 90 successful
old_schema=$(schema)

# --- upgrade, with a run going --------------------------------------------------
going=$(launch "$slow")
sleep 5
expect_eq "a run is going when the upgrade starts" "$(db "SELECT status FROM runs WHERE id = '$going'")" running
install_package "$IT_PKG_NEW"
expect_eq "the newer package is installed" "$(installed_version)" "$(version_of "$IT_PKG_NEW")"
expect_ok "the timer of the scheduler is active" active cockpit-fleet-scheduler.timer
expect_ok "and the path unit of notifications" active cockpit-fleet-notify.path
expect_eq "the configuration as it was changed is kept" "$(grep -c '^max_concurrent = 3' "$CONFIG")" 1
expect_run "the run going through the upgrade ends, and is recorded" "$going" 120 successful
new_schema=$(python3 -c 'import sys; sys.path.insert(0, "/usr/lib/cockpit-fleet"); from cockpit_fleet import db; print(len(db.MIGRATIONS))')
admin whoami >/dev/null
expect_eq "the database is brought to the newer schema ($old_schema to $new_schema)" "$(schema)" "$new_schema"
expect_eq "the templates are there" "$(get "$(admin template.list)" 'sorted(t["name"] for t in a["data"]["templates"])')" "['quick', 'slow']"
expect_eq "the schedule too" "$(get "$(admin schedule.list)" '[s["name"] for s in a["data"]["schedules"]]')" "['nightly']"
expect_eq "the secret is still there" "$(get "$(admin credential.list)" 'a["data"]["credentials"][0]["secrets"]["vault_password"]')" True
expect_eq "the runs of before are listed" "$(get "$(admin run.list)" 'len(a["data"]["runs"])')" 2
expect_run "a run of the newer version ends" "$(launch "$quick")" 90 successful
expect_eq "the self-check finds no problem" "$(get "$(admin selfcheck)" '[c["id"] for c in a["data"]["checks"] if c["status"] == "fail"]')" "[]"
expect_eq "nothing of the service account is root's" "$(owned_by_root)" 0

# --- removal, with a run going --------------------------------------------------
going=$(launch "$slow")
sleep 5
remove_package
expect_eq "removed" "$(installed_version)" ""
expect_eq "the run going was stopped" "$(runs_active)" 0
expect_denied "the timer is gone" active cockpit-fleet-scheduler.timer
expect_denied "the programs are gone" test -e /usr/libexec/cockpit-fleet/fleetctl
expect_denied "the page is gone" test -e /usr/share/cockpit/fleet/index.js
expect_ok "the database is kept" test -s /var/lib/cockpit-fleet/fleet.db
expect_ok "the output of runs too" sh -c 'ls /var/lib/cockpit-fleet/runs/*/stdout.log'
expect_ok "the encrypted secrets too" sh -c 'ls /etc/cockpit-fleet/secrets/*.cred'
if [ "$debian" = yes ]; then
    expect_ok "the configuration too" grep -q '^max_concurrent = 3' "$CONFIG"
else
    expect_ok "the configuration too, as .rpmsave" grep -q '^max_concurrent = 3' "$CONFIG.rpmsave"
    mv "$CONFIG.rpmsave" "$CONFIG"
fi
expect_ok "the account and its groups stay" getent group cockpit-fleet-admin

# --- installed again --------------------------------------------------------------
install_package "$IT_PKG_NEW"
expect_eq "installed again, the templates are back" "$(get "$(admin template.list)" 'len(a["data"]["templates"])')" 2
expect_eq "the configuration is the one changed" "$(grep -c '^max_concurrent = 3' "$CONFIG")" 1
expect_ok "the timer is active again" active cockpit-fleet-scheduler.timer
expect_run "and a run ends" "$(launch "$quick")" 90 successful

# --- purge, or what dnf leaves ------------------------------------------------------
if [ "$debian" = yes ]; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y -q cockpit-fleet >/dev/null
    expect_denied "purge takes the data" test -e /var/lib/cockpit-fleet
    expect_denied "the configuration and the secrets" test -e /etc/cockpit-fleet
    expect_denied "the rules of sudo" test -e /etc/sudoers.d/cockpit-fleet
    expect_denied "and what was in /run" test -e /run/cockpit-fleet
else
    remove_package
    expect_denied "the rules of sudo go with the package" test -e /etc/sudoers.d/cockpit-fleet
    expect_ok "dnf leaves the data, for the administrator to remove" test -s /var/lib/cockpit-fleet/fleet.db
    rm -rf /var/lib/cockpit-fleet /etc/cockpit-fleet /run/cockpit-fleet
fi
expect_eq "no unit of the package is left" "$(systemctl list-units --all --plain --no-legend 'cockpit-fleet*' 'system-cockpitfleet*' | grep -v 'not-found' | awk '{print $1, $3, $4}' | tr '\n' ';')" ""
expect_ok "the account stays, as documented" getent passwd cockpit-fleet

finish
