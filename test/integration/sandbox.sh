#!/bin/sh
# What a playbook can and cannot reach from inside a run unit, and how
# secrets get to it. Optional SSH part: set IT_SSH_USER and IT_SSH_PASSWORD
# to an account of this machine that accepts password logins.
set -u
. "$(dirname "$0")/lib.sh"
require_root

RUNS=/var/lib/cockpit-fleet/runs
SECRETS=/etc/cockpit-fleet/secrets
SECRET=/usr/libexec/cockpit-fleet/fleet-secret
A=itsba B=itsbb C=itsbc

cleanup() {
    for id in $A $B $C; do
        systemctl stop "cockpit-fleet-run@$id.service" 2>/dev/null
        systemctl reset-failed "cockpit-fleet-run@$id.service" 2>/dev/null
        rm -rf "${RUNS:?}/$id"
    done
    for name in it-alpha it-beta it-ssh; do "$SECRET" delete "$name" 2>/dev/null; done
}
trap cleanup EXIT
cleanup

new_run() {  # new_run ID
    install -d -m 0700 "$RUNS/$1" "$RUNS/$1/project" "$RUNS/$1/creds"
    printf 'localhost ansible_connection=local\n' > "$RUNS/$1/project/hosts"
}
start_run() {  # start_run ID: hand over to the service account and start
    chown -R cockpit-fleet:cockpit-fleet "$RUNS/$1"
    as_fleet sudo -n /usr/libexec/cockpit-fleet/fleet-unit start "$1"
}
wait_run() {  # wait_run ID SECONDS
    n=0
    while systemctl is-active --quiet "cockpit-fleet-run@$1.service" && [ "$n" -lt "$2" ]; do
        sleep 1; n=$((n + 1))
    done
}
succeeded() {  # succeeded ID: the unit ran and ansible-playbook exited 0
    [ "$(systemctl show -P ExecMainStatus "cockpit-fleet-run@$1.service")" = 0 ] &&
        [ "$(systemctl show -P Result "cockpit-fleet-run@$1.service")" = success ] &&
        grep -q 'PLAY RECAP' "$RUNS/$1/stdout.log"
}
probe() { sed -n "s/^$1=//p" "$RUNS/$A/probe.out" 2>/dev/null; }

# --- secrets -----------------------------------------------------------------
printf 'alpha-value' | "$SECRET" set it-alpha
printf 'beta-value'  | "$SECRET" set it-beta
expect_ok     "secret is stored"                      test -s "$SECRETS/it-alpha.cred"
if grep -q 'alpha-value' "$SECRETS/it-alpha.cred"; then fail "stored secret is plaintext"; else pass "stored secret is not plaintext"; fi
expect_ok     "service account reads the ciphertext"  as_fleet test -r "$SECRETS/it-alpha.cred"
expect_denied "service account cannot write the store" as_fleet touch "$SECRETS/x.cred"
expect_denied "service account cannot decrypt"        as_fleet systemd-creds decrypt --name= "$SECRETS/it-alpha.cred" -
expect_denied "fleet-secret rejects a path as name"   "$SECRET" set ../x
expect_denied "fleet-secret rejects an empty value"   sh -c "printf '' | $SECRET set it-empty"

# --- run B: holds another secret while A probes --------------------------------
new_run $B
cp "$SECRETS/it-beta.cred" "$RUNS/$B/creds/connection_password"
cat > "$RUNS/$B/project/site.yml" <<'YAML'
- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.wait_for:
        timeout: 90
YAML
printf '{"playbook": "site.yml", "inventory": "hosts"}\n' > "$RUNS/$B/spec.json"
start_run $B

# --- run A: probes its sandbox ---------------------------------------------------
new_run $A
cp "$SECRETS/it-alpha.cred" "$RUNS/$A/creds/connection_password"
cat > "$RUNS/$A/project/probe.sh" <<EOF2
own=/run/credentials/cockpit-fleet-run@$A.service
other=/run/credentials/cockpit-fleet-run@$B.service
try() { name=\$1; shift; if "\$@" >/dev/null 2>&1; then echo "\$name=yes"; else echo "\$name=no"; fi; }
echo "user=\$(id -un)"
echo "own_secret=\$(cat \$own/fleet_connection_password 2>&1)"
try other_secret      cat \$other/fleet_connection_password
try list_other_creds  ls \$other
# Every run uses the same account: without a PID namespace of its own, the
# other run's files are reachable through /proc/PID/root.
stolen=no
for p in /proc/[0-9]*; do
    cat "\$p/root\$other/fleet_connection_password" >/dev/null 2>&1 && stolen=yes
done
echo "proc_steal=\$stolen"
echo "foreign_procs=\$(ps -eo args= 2>/dev/null | grep -c '[w]ait_for')"
try read_store        ls $SECRETS
try decrypt_copy      systemd-creds decrypt --name= $RUNS/$A/creds/connection_password -
echo "state_dir=\$(ls /var/lib/cockpit-fleet | tr '\n' ' ')"
echo "runs_dir=\$(ls $RUNS | tr '\n' ' ')"
try write_own_dir     touch $RUNS/$A/written
touch /var/lib/cockpit-fleet/written-outside 2>/dev/null
try write_etc         touch /etc/x
try write_usr         touch /usr/x
try read_home         ls /home/
try read_shadow       cat /etc/shadow
try dbus_socket       test -e /run/dbus/system_bus_socket
try systemd_private   test -e /run/systemd/private
try systemctl_start   systemctl start cockpit-fleet-run@$B.service
try sudo_fleet_unit   sudo -n /usr/libexec/cockpit-fleet/fleet-unit start $B
try setuid_su         su -c true
echo "tmp=\$(ls -A /tmp | grep -v '^ansible' | tr '\n' ' ')"
try dns               getent hosts deb.debian.org
EOF2
cat > "$RUNS/$A/project/site.yml" <<'YAML'
- hosts: localhost
  gather_facts: true
  tasks:
    - ansible.builtin.shell: sh {{ playbook_dir }}/probe.sh > {{ playbook_dir }}/../probe.out 2>&1
    - ansible.builtin.debug:
        msg: "{{ ansible_facts.distribution }} {{ ansible_facts.memtotal_mb }}"
YAML
printf '{"playbook": "site.yml", "inventory": "hosts", "extra_vars": {"greeting": "hello"}}\n' > "$RUNS/$A/spec.json"
start_run $A
wait_run $A 120

expect_ok "run A finished successfully" succeeded $A
expect_ok "probe ran" test -s "$RUNS/$A/probe.out"

check() {  # check KEY EXPECTED DESCRIPTION
    got=$(probe "$1")
    if [ "$got" = "$2" ]; then pass "$3"; else fail "$3 (got: '$got')"; fi
}
check user             cockpit-fleet  "playbook runs as the service account"
check own_secret       alpha-value    "run receives its own secret, decrypted"
check other_secret     no             "cannot read the secret of a concurrent run"
check list_other_creds no             "cannot list the credentials of a concurrent run"
check proc_steal       no             "cannot reach a concurrent run's secrets through /proc"
check foreign_procs    0              "does not see the processes of a concurrent run"
check read_store       no             "cannot read the secret store"
check decrypt_copy     no             "cannot decrypt ciphertext by itself"
check state_dir        "runs "        "state directory shows only runs/"
check runs_dir         "$A "          "runs/ shows only this run"
check write_own_dir    yes            "can write its own run directory"
if [ -e /var/lib/cockpit-fleet/written-outside ]; then fail "a write outside the run directory reached the state"; else pass "writes outside the run directory are thrown away"; fi
check write_etc        no             "cannot write /etc"
check write_usr        no             "cannot write /usr"
check read_home        no             "cannot read /home"
check read_shadow      no             "cannot read /etc/shadow"
check dbus_socket      no             "no D-Bus system socket"
check systemd_private  no             "no systemd private socket"
check systemctl_start  no             "cannot start a unit with systemctl"
check sudo_fleet_unit  no             "cannot use sudo (NoNewPrivileges)"
check setuid_su        no             "setuid binaries do not elevate"
check tmp              ""             "private /tmp"
check dns              yes            "name resolution works"

expect_ok "events file has host results" grep -q '"event": "host_ok"' "$RUNS/$A/events.jsonl"
expect_ok "events end with stats"        sh -c "tail -n 1 '$RUNS/$A/events.jsonl' | grep -q '\"event\": \"stats\"'"
expect_ok "human-readable log is kept"   grep -q 'PLAY RECAP' "$RUNS/$A/stdout.log"
if grep -rq 'alpha-value' "$RUNS/$A/events.jsonl" "$RUNS/$A/stdout.log"; then fail "secret leaked into run output"; else pass "secret is not in run output"; fi
if [ -e "/run/cockpit-fleet-run-$A" ]; then fail "runtime directory left behind"; else pass "runtime directory removed after the run"; fi
if [ -e "/run/credentials/cockpit-fleet-run@$A.service" ]; then fail "credentials left behind"; else pass "credentials removed after the run"; fi

# --- run C: SSH with a password secret and connection reuse ----------------------
if [ -n "${IT_SSH_USER:-}" ] && [ -n "${IT_SSH_PASSWORD:-}" ]; then
    printf '%s' "$IT_SSH_PASSWORD" | "$SECRET" set it-ssh
    new_run $C
    cp "$SECRETS/it-ssh.cred" "$RUNS/$C/creds/connection_password"
    printf 'self ansible_host=127.0.0.1\n' > "$RUNS/$C/project/hosts"
    ssh-keyscan -T 5 127.0.0.1 2>/dev/null > "$RUNS/$C/known_hosts"
    cat > "$RUNS/$C/project/site.yml" <<'YAML'
- hosts: self
  gather_facts: true
  tasks:
    - ansible.builtin.command: id -un
      register: who
      changed_when: false
    - ansible.builtin.copy:
        content: "{{ who.stdout }}"
        dest: "{{ playbook_dir }}/../remote_user"
      delegate_to: localhost
YAML
    printf '{"playbook": "site.yml", "inventory": "hosts", "user": "%s"}\n' "$IT_SSH_USER" > "$RUNS/$C/spec.json"
    since=$(date '+%Y-%m-%d %H:%M:%S')
    start_run $C
    wait_run $C 120
    expect_ok "SSH run finished successfully" succeeded $C
    expect_ok "SSH run used the password secret" sh -c "[ \"\$(cat $RUNS/$C/remote_user)\" = \"$IT_SSH_USER\" ]"
    logins=$(journalctl --since="$since" --no-pager -o cat | grep -c "Accepted password for $IT_SSH_USER from 127.0.0.1")
    if [ "$logins" -eq 1 ]; then pass "one SSH login for several tasks (ControlPersist)"; else fail "expected one SSH login, saw $logins"; fi

    printf '' > "$RUNS/$C/known_hosts"; rm -f "$RUNS/$C/remote_user"
    start_run $C
    wait_run $C 120
    if [ -e "$RUNS/$C/remote_user" ] || succeeded $C; then fail "unknown host key was accepted"; else pass "unknown host key is refused"; fi
else
    echo "SKIP  SSH checks (IT_SSH_USER / IT_SSH_PASSWORD not set)"
fi

finish
