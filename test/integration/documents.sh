#!/bin/sh
# The documents with the package installed: their tree and repository made
# by fleetctl as the service, with git as it is installed (and SELinux,
# where it is enforcing); roles from the groups; a text written, replaced
# with the digest read, moved, deleted and restored, each a commit of the
# caller; an attachment put and read back as the page does it (the
# parameters, then the bytes; the path in argv), out of the history; the
# rendered page; fleet doc put and get at a terminal; a text put there by
# hand committed as the service's; and a run that does not see them.
# It DELETES the database, the runs and the documents: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

WORK=/tmp/fleet-it-documents
DOCS=/var/lib/cockpit-fleet/documents
REPO=/var/lib/cockpit-fleet/documents.git
cleanup() {
    wipe
    userdel -r itviewer 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT
cleanup
prepare
useradd -m -G cockpit-fleet-viewer itviewer
mkdir -p "$WORK"
chmod 755 "$WORK"

put() {  # put USER PATH FILE [EXTRA-JSON]: the answer of doc.put, the file after the line
    size=$(stat -c %s "$3")
    { printf '{"path": "%s", "size": %s%s}\n' "$2" "$size" "${4:+, $4}"; cat "$3"; } |
        sudo -u "$1" sudo -n -u cockpit-fleet "$FLEETCTL" doc.put 2>&1
}
write() {  # write USER PATH CONTENT [EXPECTED]
    ctl "$1" doc.write "$(python3 -c 'import json, sys; print(json.dumps({"path": sys.argv[1], "content": sys.argv[2], "expected": sys.argv[3]}))' "$2" "$3" "${4:-}")"
}
history() {  # history [PATH]: "author:subject" of each change, newest first
    if [ -n "${1:-}" ]; then params="{\"path\": \"$1\"}"; else params='{}'; fi
    get "$(ctl itviewer doc.history "$params")" '" | ".join(c["author"] + ":" + c["subject"] for c in a["data"]["commits"])'
}

# --- a text, its history -----------------------------------------------------------------
first=$(write itoperator reports/web01.md "# web01

Disk full on /var.
")
expect_eq "an operator writes a text" "$(get "$first" 'a["ok"]')" True
expect_eq "the tree and its repository are the service's, closed to others" \
    "$(stat -c '%U %a' "$DOCS") $(stat -c '%U' "$REPO") $(test -e "$DOCS/.git" && echo git-inside || echo apart)" \
    "cockpit-fleet 700 cockpit-fleet apart"
expect_eq "a viewer may not write" "$(get "$(write itviewer a.md x)" 'a["error"]["code"]')" forbidden
read=$(ctl itviewer doc.read '{"path": "reports/web01.md"}')
expect_eq "a viewer reads it, with its digest" "$(get "$read" 'a["data"]["sha256"] == "'"$(get "$first" 'a["data"]["sha256"]')"'"')" True
expect_eq "a replacement without the digest is refused" "$(get "$(write itoperator reports/web01.md changed)" 'a["error"]["code"]')" conflict
second=$(write itadmin reports/web01.md "# web01

Disk full on /var: logs vacuumed.
" "$(get "$read" 'a["data"]["sha256"]')")
expect_eq "with it, it goes through" "$(get "$second" 'a["ok"]')" True
expect_eq "each change is the caller's in the history" "$(history reports/web01.md)" \
    "itadmin:Write reports/web01.md | itoperator:Create reports/web01.md"
expect_eq "the history is git's, as the service reads it" \
    "$(as_fleet git --git-dir="$REPO" log --format=%an --max-count=1 2>&1)" itadmin
diff=$(ctl itviewer doc.diff "{\"revision\": \"$(get "$second" 'a["data"]["revision"]')\"}")
expect_eq "a change shown as a diff" "$(get "$diff" '"+Disk full on /var: logs vacuumed." in a["data"]["text"]')" True

# --- attachments, through fleetctl as the page does it ------------------------------------
head -c 300000 /dev/urandom > "$WORK/graph.png"
digest=$(sha256sum "$WORK/graph.png" | cut -d' ' -f1)
answer=$(put itoperator reports/graph.png "$WORK/graph.png" '"source": "reports/web01.md"')
expect_eq "an attachment is put, its digest taken on the way" "$(get "$answer" 'a["data"]["sha256"]')" "$digest"
expect_eq "it is not in the history" "$(as_fleet git --git-dir="$REPO" ls-files 2>&1)" reports/web01.md
expect_eq "its row says where it belongs" \
    "$(get "$(ctl itviewer doc.list)" '[(f["origin"], f["source"], f["created_by"]) for f in a["data"]["files"] if f["path"] == "reports/graph.png"]')" \
    "[('upload', 'reports/web01.md', 'itoperator')]"
sudo -u itviewer sudo -n -u cockpit-fleet "$FLEETCTL" doc.get reports/graph.png > "$WORK/back.png"
expect_eq "a viewer reads it back, the path in argv" "$(sha256sum "$WORK/back.png" | cut -d' ' -f1)" "$digest"
expect_eq "a viewer may not put one" "$(get "$(put itviewer x.png "$WORK/graph.png")" 'a["error"]["code"]')" forbidden

# --- the rendered page ----------------------------------------------------------------------
printf '{"path": "reports/web01.md", "content": "# T\\n\\n<script>x</script> ![g](graph.png) [j](javascript:x) [o](other.md)\\n"}' > "$WORK/render.json"
rendered=$(ctl itviewer doc.render "$(cat "$WORK/render.json")")
expect_eq "rendered: raw HTML escaped, the image embedded, javascript: dropped, a document's link made fleet:doc" \
    "$(get "$rendered" '("<script" not in a["data"]["html"], a["data"]["images"], "javascript" in a["data"]["html"], "fleet:doc/reports/other.md" in a["data"]["html"])')" \
    "(True, 1, False, True)"

# --- move, delete, restore ------------------------------------------------------------------
expect_eq "a folder moved takes its text and attachment" \
    "$(get "$(ctl itoperator doc.move '{"path": "reports", "to": "2026/reports"}')" 'a["ok"]') $(ls "$DOCS/2026/reports" | tr '\n' ' ')" \
    "True graph.png web01.md "
revision=$(get "$second" 'a["data"]["revision"]')
ctl itoperator doc.delete '{"path": "2026"}' > /dev/null
expect_eq "deleted, the tree is empty" "$(find "$DOCS" -mindepth 1 | wc -l)" 0
restored=$(ctl itoperator doc.restore "{\"path\": \"reports/web01.md\", \"revision\": \"$revision\", \"to\": \"2026/reports/web01.md\"}")
expect_eq "a text comes back from the history" "$(get "$restored" 'a["ok"]') $(tail -n 1 "$DOCS/2026/reports/web01.md")" \
    "True Disk full on /var: logs vacuumed."

# --- by hand, and at a terminal ---------------------------------------------------------------
printf '# By hand\n' > "$DOCS/by-hand.md"
chown cockpit-fleet: "$DOCS/by-hand.md"
write itoperator after.md "after" > /dev/null
expect_eq "a text put there by hand is the service's, before the next change" \
    "$(history | cut -d'|' -f1-2 | sed 's/ $//')" "itoperator:Create after.md | cockpit-fleet:Changes made in the tree by hand"
printf 'notes\n' > "$WORK/notes.txt"
chmod 644 "$WORK/notes.txt"
expect_eq "fleet doc put at a terminal" \
    "$(cd "$WORK" && sudo -u itoperator fleet --json doc put --file notes.txt --path notes/notes.txt 2>&1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])' 2>&1)" notes/notes.txt
expect_eq "fleet doc get at a terminal" "$(sudo -u itviewer fleet doc get --path notes/notes.txt --output - 2>&1)" notes

# --- a run does not see them -------------------------------------------------------------------
mkdir -p /srv/fleet-it/look
cat > /srv/fleet-it/look/site.yml <<'YAML'
- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.stat: {path: /var/lib/cockpit-fleet/documents/after.md}
      register: text
    - ansible.builtin.stat: {path: /var/lib/cockpit-fleet/documents.git}
      register: repo
    - ansible.builtin.assert:
        that: [not text.stat.exists, not repo.stat.exists]
YAML
chown -R itadmin /srv/fleet-it
project=$(get "$(admin project.create '{"name": "look", "kind": "local", "path": "/srv/fleet-it/look"}')" 'a["data"]["id"]')
inventory=$(get "$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "localhost ansible_connection=local ansible_python_interpreter=/usr/bin/python3\n"}')" 'a["data"]["id"]')
template=$(get "$(admin template.create "{\"name\": \"look\", \"project_id\": \"$project\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory\"}")" 'a["data"]["id"]')
run=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template\"}")" 'a["data"]["id"]')
expect_run "a run does not see the documents nor their history" "$run" 300 successful

finish
