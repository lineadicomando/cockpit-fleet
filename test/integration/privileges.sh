#!/bin/sh
# The service account reaches systemd only through fleet-unit, and fleet-unit
# reaches only instances of the run template.
set -u
. "$(dirname "$0")/lib.sh"
require_root

ID=itpriv
UNIT=cockpit-fleet-run@$ID.service
RUN=/var/lib/cockpit-fleet/runs/$ID
FLEET_UNIT=/usr/libexec/cockpit-fleet/fleet-unit
trap 'systemctl stop "$UNIT" 2>/dev/null; systemctl reset-failed "$UNIT" 2>/dev/null; rm -rf "$RUN"' EXIT

# A run that just waits, so that start and stop can both be observed.
install -d -m 0700 "$RUN" "$RUN/project" "$RUN/creds"
cat > "$RUN/project/wait.yml" <<'YAML'
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.wait_for:
        timeout: 120
YAML
printf 'localhost\n' > "$RUN/project/hosts"
printf '{"playbook": "wait.yml", "inventory": "hosts"}\n' > "$RUN/spec.json"
chown -R cockpit-fleet:cockpit-fleet "$RUN"

expect_ok     "start a run through fleet-unit"     as_fleet sudo -n "$FLEET_UNIT" start "$ID"
expect_ok     "run unit is active"                 systemctl is-active "$UNIT"
expect_ok     "stop a run through fleet-unit"      as_fleet sudo -n "$FLEET_UNIT" stop "$ID"

expect_denied "fleet-unit: unknown verb"           as_fleet sudo -n "$FLEET_UNIT" restart "$ID"
expect_denied "fleet-unit: option as run id"       as_fleet sudo -n "$FLEET_UNIT" start --version
expect_denied "fleet-unit: unit name as run id"    as_fleet sudo -n "$FLEET_UNIT" start "x.service"
expect_denied "fleet-unit: path as run id"         as_fleet sudo -n "$FLEET_UNIT" start "../../etc"
expect_denied "fleet-unit: extra argument"         as_fleet sudo -n "$FLEET_UNIT" start "$ID" ssh.service
expect_denied "fleet-unit without sudo"            as_fleet "$FLEET_UNIT" start "$ID"

expect_denied "sudo: any other command"            as_fleet sudo -n /usr/bin/id
expect_denied "sudo: systemctl directly"           as_fleet sudo -n /usr/bin/systemctl start "$UNIT"
expect_denied "sudo: fleet-secret"                 as_fleet sudo -n /usr/libexec/cockpit-fleet/fleet-secret list

expect_denied "systemctl start own run unit"       as_fleet systemctl start "$UNIT"
expect_denied "systemctl start an unrelated unit"  as_fleet systemctl start systemd-tmpfiles-clean.service
expect_denied "transient unit"                     as_fleet systemd-run --quiet --wait /bin/true
expect_denied "transient unit, run-like name"      as_fleet systemd-run --quiet --wait --unit="cockpit-fleet-prepare@x.service" /bin/true
expect_denied "daemon-reload"                      as_fleet systemctl daemon-reload

finish
