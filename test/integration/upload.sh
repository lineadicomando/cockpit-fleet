#!/bin/sh
# A project uploaded as an archive: unpacked in the sandbox, its digest as the
# revision, hostile archives refused as a whole, and a run from it.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

WORK=/tmp/fleet-it-upload
cleanup() { wipe; rm -rf "$WORK"; }
trap cleanup EXIT
cleanup
prepare
mkdir -p "$WORK"

upload() {  # upload USER PROJECT FILE: the answer of project.upload
    python3 -c 'import base64,json,sys; json.dump({"id": sys.argv[1], "filename": sys.argv[2], "data": base64.b64encode(open(sys.argv[2], "rb").read()).decode()}, sys.stdout)' "$2" "$3" > "$WORK/params.json"
    sudo -u "$1" sudo -n -u cockpit-fleet "$FLEETCTL" project.upload < "$WORK/params.json" 2>&1
    rm -f "$WORK/params.json"
}
log() { cat "/var/lib/cockpit-fleet/runs/$1/stdout.log" 2>/dev/null; }

project=$(admin project.create '{"name": "shipped", "kind": "archive"}')
id=$(get "$project" 'a["data"]["id"]')
home=/var/lib/cockpit-fleet/projects/$id
expect_eq "an archive project is created without files" "$(get "$project" 'a["data"]["kind"], a["data"]["revision"]')" "archive "
expect_eq "it says so when its files are asked for" "$(get "$(admin file.list "{\"id\": \"$id\"}")" 'a["error"]["code"]')" conflict

# --- a project as people pack it: one directory, a link, git's own files -----------------
mkdir -p "$WORK/site-1.0/roles/common/tasks" "$WORK/site-1.0/.git"
cat > "$WORK/site-1.0/site.yml" <<'YAML'
- name: Leave a marker
  hosts: all
  connection: local
  gather_facts: false
  roles:
    - web
YAML
cat > "$WORK/site-1.0/roles/common/tasks/main.yml" <<'YAML'
- name: Write the marker
  ansible.builtin.copy:
    content: "from the archive\n"
    dest: "{{ lookup('ansible.builtin.env', 'HOME') }}/marker"
    mode: "0644"
YAML
ln -s common "$WORK/site-1.0/roles/web"
echo '[core]' > "$WORK/site-1.0/.git/config"
tar -C "$WORK" -czf "$WORK/site.tar.gz" site-1.0
digest=$(sha256sum "$WORK/site.tar.gz" | cut -d' ' -f1)

expect_eq "operator cannot upload" "$(get "$(upload itoperator "$id" "$WORK/site.tar.gz")" 'a["error"]["code"]')" forbidden
sent=$(upload itadmin "$id" "$WORK/site.tar.gz")
run_id=$(get "$sent" 'a["data"]["run_id"]')
expect_eq "fleetctl answers with the digest of what it received" "$(get "$sent" 'a["data"]["sha256"]')" "$digest"
expect_run "the archive is unpacked by a job, in the sandbox" "$run_id" 60 successful
expect_eq "the digest is the revision of the project" "$(get "$(admin project.list)" 'a["data"]["projects"][0]["revision"]')" "$digest"
expect_ok "the single directory of the archive is not kept" test -f "$home/tree/site.yml"
expect_eq "a link inside the project is kept as a link" "$(readlink "$home/tree/roles/web")" common
expect_denied "git's own files are left out" test -e "$home/tree/.git"
expect_eq "nothing of the job is left but its record" "$(ls "/var/lib/cockpit-fleet/runs/$run_id" | sort | tr '\n' ' ')" "result.json spec.json stdout.log "
expect_eq "the files are listed, and cannot be changed here" \
    "$(get "$(admin file.list "{\"id\": \"$id\"}")" 'sorted(f["path"] for f in a["data"]["files"]), a["data"]["editable"]')" \
    "['roles/common/tasks/main.yml', 'site.yml'] False"
expect_eq "the upload is in the audit log without its content" \
    "$(get "$(admin audit.list)" 'any(e["command"]=="project.upload" and e["outcome"]=="ok" and "\"data\": \"***\"" in e["params"] for e in a["data"]["entries"])')" True

# --- a run from the project -----------------------------------------------------------
inventory=$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "localhost\n"}')
template=$(admin template.create "{\"name\": \"shipped\", \"project_id\": \"$id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$(get "$inventory" 'a["data"]["id"]')\"}")
run=$(ctl itoperator run.start "{\"template_id\": \"$(get "$template" 'a["data"]["id"]')\"}")
play=$(get "$run" 'a["data"]["id"]')
expect_run "a template runs a playbook of the archive, through the link" "$play" 120 successful
expect_eq "the playbook did its work" "$(get "$(admin run.get "{\"id\": \"$play\"}")" 'a["data"]["stats"]["localhost"]["changed"]')" 1
expect_eq "the run records the digest it ran" "$(get "$run" 'a["data"]["revision"]')" "$digest"

# --- archives that must be refused, whole ---------------------------------------------
python3 - "$WORK" <<'PY'
import io, sys, tarfile, zipfile
work = sys.argv[1]

def tar(name, *members):
    with tarfile.open(f"{work}/{name}", "w:gz") as out:
        for path, kind, value in members:
            info = tarfile.TarInfo(path)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(value)
                out.addfile(info, io.BytesIO(value))
            else:
                info.linkname = value
                out.addfile(info)

good = ("site.yml", tarfile.REGTYPE, b"- hosts: all\n")
tar("bad-dotdot.tgz", good, ("../../escaped", tarfile.REGTYPE, b"evil"))
tar("bad-absolute.tgz", good, ("/var/lib/cockpit-fleet/escaped", tarfile.REGTYPE, b"evil"))
tar("bad-through.tgz", good, ("out", tarfile.SYMTYPE, "../.."), ("out/escaped", tarfile.REGTYPE, b"evil"))
tar("bad-link.tgz", good, ("shadow", tarfile.SYMTYPE, "/etc/shadow"))
tar("bad-chain.tgz", good, ("x/up", tarfile.SYMTYPE, ".."), ("x/out", tarfile.SYMTYPE, "up/.."))
tar("bad-hard.tgz", good, ("copy", tarfile.LNKTYPE, "site.yml"))
tar("bad-device.tgz", good, ("null", tarfile.CHRTYPE, ""))
with zipfile.ZipFile(f"{work}/bad-zip.zip", "w") as out:
    out.writestr("site.yml", "- hosts: all\n")
    out.writestr("../../escaped", "evil")
# 600 MB of zeros in well under one: the limit is on what comes out.
with tarfile.open(f"{work}/bad-bomb.tgz", "w:gz") as out:
    info = tarfile.TarInfo("zeros")
    info.size = 600 * 1024 * 1024
    out.addfile(info, open("/dev/zero", "rb"))
open(f"{work}/bad-text.tgz", "wb").write(b"not an archive\n" * 1000)
PY
for bad in dotdot absolute through link chain hard device zip bomb text; do
    file=$(ls "$WORK"/bad-"$bad".*)
    refused=$(get "$(upload itadmin "$id" "$file")" 'a["data"]["run_id"]')
    expect_run "refused: $bad" "$refused" 120 failed
    [ -e "/var/lib/cockpit-fleet/runs/$refused/checkout" ] && fail "refused: $bad left a tree behind"
    ls "/var/lib/cockpit-fleet/runs/$refused" | grep -q unpack && fail "refused: $bad left its staging area"
done
expect_eq "the run says why" "$(log "$refused" | grep -c 'not a tar or zip archive')" 1
expect_denied "nothing was written outside a run's directory" sh -c "ls -d /var/lib/cockpit-fleet/escaped /var/lib/cockpit-fleet/runs/escaped /var/lib/cockpit-fleet/runs/*/escaped /escaped 2>/dev/null | grep ."
expect_eq "the project keeps the revision it had" "$(db "SELECT revision FROM projects WHERE id = '$id'")" "$digest"
expect_ok "and the files it had" test -f "$home/tree/roles/common/tasks/main.yml"
expect_eq "and shows the reason of the last failure" "$(db "SELECT sync_error LIKE '%not a tar or zip archive%' FROM projects WHERE id = '$id'")" 1

# --- a new archive replaces the tree; the size limit ------------------------------------
mkdir "$WORK/flat" && echo '- hosts: all' > "$WORK/flat/other.yml"
(cd "$WORK/flat" && python3 -m zipfile -c "$WORK/flat.zip" other.yml)
again=$(get "$(upload itadmin "$id" "$WORK/flat.zip")" 'a["data"]["run_id"]')
expect_run "a zip is unpacked too" "$again" 60 successful
expect_eq "the new archive replaces every file" "$(ls "$home/tree" | tr '\n' ' ')" "other.yml "
expect_eq "the failure is forgotten" "$(get "$(admin project.list)" 'a["data"]["projects"][0]["sync_error"]')" ""

head -c $((19 * 1024 * 1024)) /dev/urandom > "$WORK/noise"
echo '- hosts: all' > "$WORK/site.yml"
tar -C "$WORK" -cf "$WORK/large.tar" noise site.yml
start=$(date +%s)
large=$(get "$(upload itadmin "$id" "$WORK/large.tar")" 'a["data"]["run_id"]')
expect_run "an archive just under the limit of 20 MB is taken" "$large" 120 successful
echo "      19 MB: $(($(date +%s) - start)) s from upload to tree"
head -c $((20 * 1024 * 1024 + 1)) /dev/urandom > "$WORK/too-large"
expect_eq "one over the limit is refused before any job" "$(get "$(upload itadmin "$id" "$WORK/too-large")" 'a["error"]["code"]')" bad_request
other=$(get "$(admin project.create '{"name": "edited", "kind": "managed"}')" 'a["data"]["id"]')
expect_eq "only an archive project takes an upload" "$(get "$(upload itadmin "$other" "$WORK/flat.zip")" 'a["error"]["code"]')" bad_request

finish
