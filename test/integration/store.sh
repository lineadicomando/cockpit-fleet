#!/bin/sh
# The store: a file put through fleetctl as the browser sends it (the
# parameters, then the bytes) and read back as a Cockpit link reads it (the
# path in argv), roles, and a run that gets the store as hard links: get_url
# that finds a file, replaces one, adds one; copy and fetch; a delete and a
# write in place; what it wrote taken in when it ends; Ansible's temporary
# files of the controller on disk; a file of root's left out; the digest of
# a file put there by hand taken by the tick.
# It DELETES the database, the runs and the store: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

WORK=/tmp/fleet-it-store
STORE=/var/lib/cockpit-fleet/store
WWW_PORT=8099
cleanup() {
    [ -n "${www:-}" ] && kill "$www" 2>/dev/null
    wipe
    userdel -r itviewer 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT
cleanup
prepare
useradd -m -G cockpit-fleet-viewer itviewer
mkdir -p "$WORK/www" "$WORK/out"
chmod 755 "$WORK" "$WORK/out"

put() {  # put USER PATH FILE [REPLACE]: the answer of store.put, the file after the line
    size=$(stat -c %s "$3")
    { printf '{"path": "%s", "size": %s, "replace": %s}\n' "$2" "$size" "${4:-false}"; cat "$3"; } |
        sudo -u "$1" sudo -n -u cockpit-fleet "$FLEETCTL" store.put 2>&1
}
listed() {  # listed PYTHON-EXPRESSION on the files of store.list by path, as "f"
    get "$(ctl itviewer store.list)" "(lambda f: $1)({x['path']: x for x in a['data']['files']})"
}

# --- through fleetctl, as the page does it --------------------------------------------
head -c 3145728 /dev/urandom > "$WORK/setup.msi"
digest=$(sha256sum "$WORK/setup.msi" | cut -d' ' -f1)
answer=$(put itoperator apps/setup.msi "$WORK/setup.msi")
expect_eq "an operator puts a file of 3 MB, its digest taken on the way" "$(get "$answer" 'a["data"]["sha256"]')" "$digest"
expect_eq "it is in the store" "$(sha256sum "$STORE/apps/setup.msi" | cut -d' ' -f1)" "$digest"
expect_eq "a viewer may not" "$(get "$(put itviewer x.bin "$WORK/setup.msi")" 'a["error"]["code"]')" forbidden
expect_eq "nor replace without asking" "$(get "$(put itoperator apps/setup.msi "$WORK/setup.msi")" 'a["error"]["code"]')" conflict
sudo -u itviewer sudo -n -u cockpit-fleet "$FLEETCTL" store.read apps/setup.msi > "$WORK/out/back.msi"
expect_eq "a viewer reads it back, the path in argv" "$(sha256sum "$WORK/out/back.msi" | cut -d' ' -f1)" "$digest"
if sudo -u itviewer sudo -n -u cockpit-fleet "$FLEETCTL" store.read nothing > "$WORK/out/none" 2> "$WORK/out/err"; then
    fail "a missing file is an error"
else
    expect_eq "a missing file is an error, with nothing written" "$(stat -c %s "$WORK/out/none"):$(grep -c 'no such file' "$WORK/out/err")" "0:1"
fi
expect_eq "the read is in the audit log" "$(get "$(admin audit.list)" 'any(e["command"] == "store.read" and e["user"] == "itviewer" for e in a["data"]["entries"])')" True
if sudo -u itviewer sudo -n -u cockpit-fleet "$FLEETCTL" store.read ../fleet.db > /dev/null 2>&1; then
    fail "a path out of the store is refused"
else
    pass "a path out of the store is refused"
fi

# --- a run with the store -----------------------------------------------------------------
head -c 1048576 /dev/urandom > "$WORK/www/present.bin"
head -c 2048 /dev/urandom > "$WORK/www/stale.bin"
truncate -s 64M "$WORK/www/big.bin"
cp "$WORK/www/present.bin" "$STORE/present.bin"
head -c 100 /dev/urandom > "$STORE/stale.bin"
mkdir -p "$STORE/sub/dir"
echo nested > "$STORE/sub/dir/nested.txt"
echo kept > "$STORE/to-delete.txt"
chown -R cockpit-fleet:cockpit-fleet "$STORE"
echo "root's" > "$STORE/roots.txt"
(cd "$WORK/www" && exec python3 -m http.server -b 127.0.0.1 "$WWW_PORT" > /dev/null 2>&1) &
www=$!

mkdir -p /srv/fleet-it/store
cat > /srv/fleet-it/store/site.yml <<YAML
- hosts: localhost
  gather_facts: false
  vars:
    base: http://127.0.0.1:$WWW_PORT
  tasks:
    - name: The store is given
      ansible.builtin.assert:
        that: fleet_store is defined
    - name: Where Ansible keeps its temporary files on the controller
      ansible.builtin.command: findmnt -no FSTYPE -T /var/lib/cockpit-fleet/.ansible
      register: tmp_fs
      changed_when: false
    - name: Present with the right digest
      ansible.builtin.get_url:
        url: "{{ base }}/present.bin"
        dest: "{{ fleet_store }}/present.bin"
        checksum: "sha256:$(sha256sum "$WORK/www/present.bin" | cut -d' ' -f1)"
      register: present
    - name: Present but stale
      ansible.builtin.get_url:
        url: "{{ base }}/stale.bin"
        dest: "{{ fleet_store }}/stale.bin"
        checksum: "sha256:$(sha256sum "$WORK/www/stale.bin" | cut -d' ' -f1)"
    - name: A folder for what is new
      ansible.builtin.file: {path: "{{ fleet_store }}/new", state: directory}
    - name: Absent, and larger
      ansible.builtin.get_url: {url: "{{ base }}/big.bin", dest: "{{ fleet_store }}/new/big.bin"}
    - name: Copied from the store, as to a node
      ansible.builtin.copy: {src: "{{ fleet_store }}/sub/", dest: "/tmp/copied/"}
    - name: Fetched into the store
      ansible.builtin.fetch: {src: /etc/hostname, dest: "{{ fleet_store }}/pulled/", flat: true}
    - name: Deleted from the farm only
      ansible.builtin.file: {path: "{{ fleet_store }}/to-delete.txt", state: absent}
    - name: Written in place
      ansible.builtin.shell: "echo more >> {{ fleet_store }}/sub/dir/nested.txt"
    - name: Root's file is not given
      ansible.builtin.stat: {path: "{{ fleet_store }}/roots.txt"}
      register: roots
    - ansible.builtin.assert:
        that:
          - present is not changed
          - not roots.stat.exists
          - tmp_fs.stdout != "tmpfs"
YAML
chown -R itadmin /srv/fleet-it
project=$(get "$(admin project.create '{"name": "store", "kind": "local", "path": "/srv/fleet-it/store"}')" 'a["data"]["id"]')
inventory=$(get "$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "localhost ansible_connection=local ansible_python_interpreter=/usr/bin/python3\n"}')" 'a["data"]["id"]')
template=$(get "$(admin template.create "{\"name\": \"store\", \"project_id\": \"$project\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory\"}")" 'a["data"]["id"]')
run=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template\"}")" 'a["data"]["id"]')
expect_run "a run uses the store through fleet_store" "$run" 300 successful
expect_eq "the stale file was replaced" "$(sha256sum "$STORE/stale.bin" | cut -d' ' -f1)" "$(sha256sum "$WORK/www/stale.bin" | cut -d' ' -f1)"
expect_eq "the new one was taken in" "$(stat -c %s "$STORE/new/big.bin" 2>/dev/null)" 67108864
expect_eq "the fetched one too" "$(cat "$STORE/pulled/hostname" 2>/dev/null)" "$(cat /etc/hostname)"
expect_eq "the deleted one is still in the store" "$(cat "$STORE/to-delete.txt")" kept
expect_eq "the one written in place has changed" "$(tail -n 1 "$STORE/sub/dir/nested.txt")" more
expect_eq "root's file was said to be left out" "$(grep -c 'not the service account' "/var/lib/cockpit-fleet/runs/$run/stdout.log")" 1
expect_eq "the farm is gone" "$(find "/var/lib/cockpit-fleet/runs/$run/store" /var/lib/cockpit-fleet/store-farms -mindepth 1 2>/dev/null | grep -c .)" 0
expect_eq "what the run wrote is put down to it" "$(listed "[f[p]['origin'] + ':' + f[p]['source'] for p in ('stale.bin', 'new/big.bin', 'pulled/hostname', 'sub/dir/nested.txt')]")" "['run:$run', 'run:$run', 'run:$run', 'run:$run']"
expect_eq "root's file is not for runs" "$(listed "f['roots.txt']['usable']")" False
expect_eq "the run says what it gave back" "$(get "$(ctl itviewer run.get "{\"id\": \"$run\"}")" 'a["data"]["options"]["store"]["counts"]')" "{'added': 2, 'replaced': 1, 'changed': 1, 'skipped': 0}"
expect_eq "the intake is in the audit log" "$(get "$(admin audit.list)" 'any(e["command"] == "store.intake" and e["user"] == "itoperator" for e in a["data"]["entries"])')" True

# --- the digest of what was put there by hand --------------------------------------------
echo "by hand" > "$STORE/hand.txt"
chown cockpit-fleet: "$STORE/hand.txt"
expect_eq "a file put there by hand has no digest yet" "$(listed "f['hand.txt']['sha256']")" None
admin scheduler.tick > /dev/null
expect_eq "the tick takes it" "$(listed "f['hand.txt']['sha256']")" "$(sha256sum "$STORE/hand.txt" | cut -d' ' -f1)"

finish
