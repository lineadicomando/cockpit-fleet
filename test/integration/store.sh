#!/bin/sh
# The store: a file put through fleetctl as the browser sends it (the
# parameters, then the bytes) and read back as a Cockpit link reads it (the
# path in argv), roles, and a run that gets the store as hard links: get_url
# that finds a file, replaces one, adds one; copy and fetch; a delete and a
# write in place; what it wrote taken in when it ends; Ansible's temporary
# files of the controller on disk; a file of root's left out; the digest of
# a file put there by hand taken by the tick; downloads from URLs, from a
# container node, and those to the controller's own addresses refused;
# many entries moved and deleted in one call, copies, one of them a job;
# a folder and a file downloaded as one tar.
# It DELETES the database, the runs and the store: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

HERE=$(dirname "$0")
WORK=/tmp/fleet-it-store
STORE=/var/lib/cockpit-fleet/store
WWW_PORT=8099
cleanup() {
    [ -n "${www:-}" ] && kill "$www" 2>/dev/null
    sh "$HERE/nodes.sh" down 2>/dev/null
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

# --- many at once, and copies ----------------------------------------------------------------
same() {  # same FILE FILE: "same" when their bytes are
    [ "$(sha256sum < "$1")" = "$(sha256sum < "$2")" ] && echo same
}
mkdir -p "$STORE/many/a" "$STORE/many/b"
echo one > "$STORE/many/a/one.txt"
echo two > "$STORE/many/b/two.txt"
chown -R cockpit-fleet: "$STORE/many"
moves='{"moves": [{"path": "many/a", "to": "many/c"}, {"path": "many/b/two.txt", "to": "many/two.txt"}]}'
expect_eq "a viewer may not move" "$(get "$(ctl itviewer store.move "$moves")" 'a["error"]["code"]')" forbidden
expect_eq "an operator moves two entries in one call" "$(get "$(ctl itoperator store.move "$moves")" 'len(a["data"]["moved"])')" 2
expect_eq "  they are there" "$(cat "$STORE/many/c/one.txt" "$STORE/many/two.txt" | tr '\n' ' ')" "one two "
expect_eq "  one record in the audit log" "$(get "$(admin audit.list)" 'sum(e["command"] == "store.move" and e["user"] == "itoperator" for e in a["data"]["entries"])')" 1
bad='{"moves": [{"path": "many/c", "to": "many/d"}, {"path": "many/two.txt", "to": "apps/setup.msi"}]}'
expect_eq "nothing moves when one cannot" "$(get "$(ctl itoperator store.move "$bad")" 'a["error"]["code"]')" conflict
expect_eq "  the first stays where it was" "$(test -d "$STORE/many/c" && echo kept)" kept
copies='{"copies": [{"path": "many/c", "to": "many/c2"}, {"path": "apps/setup.msi", "to": "many/setup.msi"}]}'
expect_eq "a small copy is made at once" "$(get "$(ctl itoperator store.copy "$copies")" 'a["data"]["copied"]')" "['many/c2', 'many/setup.msi']"
expect_eq "  a file of its own, the same bytes" "$(same "$STORE/many/setup.msi" "$STORE/apps/setup.msi"):$(test "$(stat -c %i "$STORE/many/setup.msi")" != "$(stat -c %i "$STORE/apps/setup.msi")" && echo own)" "same:own"
expect_eq "  its origin and digest" "$(listed "f['many/setup.msi']['origin'] + ' ' + str(f['many/setup.msi']['sha256'])")" "copy $digest"
head -c 1100000000 /dev/zero > "$STORE/many/large.img"
chown cockpit-fleet: "$STORE/many/large.img"
answer=$(ctl itoperator store.copy '{"copies": [{"path": "many/large.img", "to": "many/large-copy.img"}]}')
run=$(get "$answer" 'a["data"]["run_id"]')
expect_run "a copy over 1 GiB is a job, in the sandbox of a run" "$run" 300 successful
expect_eq "  the copy is there, whole" "$(same "$STORE/many/large.img" "$STORE/many/large-copy.img")" same
expect_eq "  in the list of downloads and copies" "$(get "$(ctl itoperator store.downloads)" 'a["data"]["downloads"][0]["kind"], a["data"]["downloads"][0]["status"]')" "copy successful"
expect_eq "  its links are gone with the job" "$(test -e "/var/lib/cockpit-fleet/runs/$run/copy" && echo left || echo gone)" gone
token=$(get "$(ctl itviewer store.list)" 'a["data"]["token"]')
expect_eq "a listing answers unchanged to its token" "$(get "$(ctl itviewer store.list "{\"since\": \"$token\"}")" 'a["data"].get("unchanged")')" True
sudo -u itviewer sudo -n -u cockpit-fleet "$FLEETCTL" store.archive many/c2 many/setup.msi > "$WORK/out/a.tar"
expect_eq "a viewer downloads a folder and a file as one tar, the paths in argv" "$(python3 -c 'import sys, tarfile; print(" ".join(sorted(m.name for m in tarfile.open(sys.argv[1]))))' "$WORK/out/a.tar")" "c2 c2/one.txt setup.msi"
if sudo -u itviewer sudo -n -u cockpit-fleet "$FLEETCTL" store.archive many/c2 nothing > "$WORK/out/none.tar" 2>/dev/null; then
    fail "an archive of something missing is an error"
else
    expect_eq "an archive of something missing is an error, with nothing written" "$(stat -c %s "$WORK/out/none.tar")" 0
fi
expect_eq "many deleted in one call" "$(get "$(ctl itoperator store.delete '{"paths": ["many", "many/c2"]}')" 'a["data"]["deleted"]')" "['many']"
expect_eq "  then the token is another" "$(get "$(ctl itviewer store.list "{\"since\": \"$token\"}")" 'a["data"].get("unchanged")')" None

# --- downloads from URLs ------------------------------------------------------------------
: > "$WORK/keys"
sh "$HERE/nodes.sh" up 1 "$WORK/keys"
node=$(sh "$HERE/nodes.sh" address 1)
podman exec fleet-node-1 sh -c 'mkdir -p /srv/www && head -c 268435456 /dev/urandom > /srv/www/big.iso'
big_sha=$(podman exec fleet-node-1 sha256sum /srv/www/big.iso | cut -d' ' -f1)
podman exec -d fleet-node-1 python3 -m http.server -d /srv/www 8099
python3 - "$node" <<'PY'
import sys, time, urllib.request
for _ in range(60):
    try:
        urllib.request.urlopen(f"http://{sys.argv[1]}:8099/", timeout=2).close()
        break
    except OSError:
        time.sleep(1)
PY
fetch() {  # fetch USER URL PATH [SHA256]: the answer of store.fetch
    ctl "$1" store.fetch "{\"url\": \"$2\", \"path\": \"$3\", \"sha256\": \"${4:-}\"}"
}
expect_eq "a viewer may not queue a download" "$(get "$(fetch itviewer "http://$node:8099/big.iso" x)" 'a["error"]["code"]')" forbidden
expect_eq "nor anyone from file:" "$(get "$(fetch itoperator file:///etc/shadow x)" 'a["error"]["code"]')" bad_request
run=$(get "$(fetch itoperator "http://$node:8099/big.iso" iso/big.iso "$big_sha")" 'a["data"]["run_id"]')
expect_run "an operator downloads 256 MB from a node into the store" "$run" 300 successful
expect_eq "it is there, whole" "$(sha256sum "$STORE/iso/big.iso" 2>/dev/null | cut -d' ' -f1)" "$big_sha"
expect_eq "it came from the URL" "$(listed "f['iso/big.iso']['origin'] + ' ' + str(f['iso/big.iso']['sha256'])")" "url $big_sha"
expect_eq "the list of downloads says so" "$(get "$(ctl itoperator store.downloads)" 'a["data"]["downloads"][0]["status"], a["data"]["downloads"][0]["size"]')" "successful 268435456"
run=$(get "$(fetch itoperator "http://$node:8099/big.iso" iso/wrong.iso "$(printf '0%.0s' $(seq 64))")" 'a["data"]["run_id"]')
expect_run "a file with another digest fails" "$run" 300 failed
expect_eq "and is not kept" "$(test -e "$STORE/iso/wrong.iso" && echo kept || echo gone)" gone
own=$(python3 -c 'import socket,sys; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect((sys.argv[1], 9)); print(s.getsockname()[0])' "$node")
for url in "http://127.0.0.1:9090/" "http://$own:9090/" "http://169.254.169.254/latest/meta-data/"; do
    run=$(get "$(fetch itoperator "$url" refused.html)" 'a["data"]["run_id"]')
    expect_run "a download from $url is refused" "$run" 120 failed
    expect_eq "  where it connects" "$(grep -c 'may not connect' "/var/lib/cockpit-fleet/runs/$run/stdout.log")" 1
done
expect_eq "the downloads have a queue of their own" "$(get "$(admin system.health)" '[q["lane"] for q in a["data"]["queue"]]')" "['runs', 'downloads', 'checks', 'slow']"

finish
