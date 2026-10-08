#!/bin/sh
# Runs against several Linux nodes (containers, see nodes.sh): a key with a
# passphrase, host keys trusted for a whole inventory, two vaults, privilege
# escalation, a node that goes away and a node whose host key changes.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe

NODES=${IT_NODES:-5}
HERE=$(cd "$(dirname "$0")" && pwd)
KEYS=$(mktemp -d)
SRC=/srv/fleet-it

cleanup() { wipe; sh "$HERE/nodes.sh" down 2>/dev/null; rm -rf "$KEYS"; }
trap cleanup EXIT
wipe; sh "$HERE/nodes.sh" down 2>/dev/null
prepare

ssh-keygen -q -t ed25519 -N 'node pass phrase' -C fleet-it -f "$KEYS/key"
cp "$KEYS/key.pub" "$KEYS/authorized_keys"; chmod 644 "$KEYS/authorized_keys"; chmod 755 "$KEYS"
sh "$HERE/nodes.sh" up "$NODES" "$KEYS/authorized_keys"

# --- project: two vaults, a loop, privilege escalation ---------------------------
mkdir -p "$SRC/site/vars"
printf 'dev-pass\n' > "$KEYS/dev"; printf 'prod-pass\n' > "$KEYS/prod"
printf 'dev_secret: from-dev-vault\n' > "$SRC/site/vars/dev.yml"
printf 'prod_secret: from-prod-vault\n' > "$SRC/site/vars/prod.yml"
ansible-vault encrypt --vault-id "dev@$KEYS/dev" "$SRC/site/vars/dev.yml" >/dev/null
ansible-vault encrypt --vault-id "prod@$KEYS/prod" "$SRC/site/vars/prod.yml" >/dev/null
cat > "$SRC/site/site.yml" <<'YAML'
- hosts: nodes
  gather_facts: false
  vars_files:
    - vars/dev.yml
    - vars/prod.yml
  tasks:
    - ansible.builtin.command: id -un
      register: who
      changed_when: false
    - ansible.builtin.copy:
        content: "{{ who.stdout }} {{ dev_secret }} {{ prod_secret }}"
        dest: /root/fleet-marker
        mode: "0600"
    - ansible.builtin.command: "test {{ item }} != bad"
      loop: [good, bad]
      changed_when: false
      ignore_errors: true
YAML
chmod -R a+rX "$SRC"

cred=$(admin credential.create '{"name": "nodes", "kind": "machine", "username": "ansible"}')
set_secret "$cred" private_key < "$KEYS/key"
printf 'node pass phrase' | set_secret "$cred" key_passphrase
dev=$(admin credential.create '{"name": "dev vault", "kind": "vault", "vault_id": "dev"}')
prod=$(admin credential.create '{"name": "prod vault", "kind": "vault", "vault_id": "prod"}')
printf 'dev-pass' | set_secret "$dev" vault_password
printf 'prod-pass' | set_secret "$prod" vault_password

hosts='[nodes]\n'
i=1
while [ "$i" -le "$NODES" ]; do
    hosts="$hosts""node$i ansible_host=$(sh "$HERE/nodes.sh" address "$i")\\n"
    i=$((i + 1))
done
inventory=$(admin inventory.create "{\"name\": \"nodes\", \"kind\": \"managed\", \"format\": \"ini\", \"content\": \"$hosts\"}")
inventory_id=$(get "$inventory" 'a["data"]["id"]')
parse=$(admin inventory.parse "{\"id\": \"$inventory_id\"}")
expect_eq "inventory is read" "$(wait_status "$(get "$parse" 'a["data"]["run_id"]')" 60)" successful
expect_eq "inventory has every node" "$(get "$(admin inventory.get "{\"id\": \"$inventory_id\"}")" 'len(a["data"]["hosts"])')" "$NODES"

# --- host keys of the whole inventory ---------------------------------------------
trust() {  # trust SCAN-JSON [REPLACE]: add every key that is not trusted yet
    printf '%s' "$1" | python3 -c '
import json, subprocess, sys
replace = len(sys.argv) > 2
for key in json.load(sys.stdin)["data"]["keys"]:
    if key["state"] == "trusted" or (key["state"] == "changed" and not replace):
        continue
    params = json.dumps({**{k: key[k] for k in ("host", "key_type", "key")}, "replace": replace})
    subprocess.run(["sudo", "-u", "itadmin", "sudo", "-n", "-u", "cockpit-fleet", sys.argv[1], "hostkey.add"],
                   input=params, text=True, capture_output=True, check=True)' "$FLEETCTL" ${2:+replace}
}
scan=$(admin hostkey.scan "{\"inventory_id\": \"$inventory_id\"}")
expect_eq "one scan answers for every node" "$(get "$scan" 'len({k["host"] for k in a["data"]["keys"]}), a["data"]["unanswered"]')" "$NODES []"
expect_eq "every key is new" "$(get "$scan" '{k["state"] for k in a["data"]["keys"]}')" "{'new'}"
trust "$scan"
expect_eq "after confirmation every key is trusted" \
    "$(get "$(admin hostkey.scan "{\"inventory_id\": \"$inventory_id\"}")" '{k["state"] for k in a["data"]["keys"]}')" "{'trusted'}"

# --- the run ----------------------------------------------------------------------
project_id=$(get "$(admin project.create "{\"name\": \"site\", \"kind\": \"local\", \"path\": \"$SRC/site\"}")" 'a["data"]["id"]')
template=$(admin template.create "{\"name\": \"site\", \"project_id\": \"$project_id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory_id\", \"machine_credential_id\": \"$(get "$cred" 'a["data"]["id"]')\", \"vault_credential_ids\": [\"$(get "$prod" 'a["data"]["id"]')\", \"$(get "$dev" 'a["data"]["id"]')\"], \"become\": true, \"forks\": 10}")
template_id=$(get "$template" 'a["data"]["id"]')
expect_eq "template with two vaults is created" "$(get "$template" 'len(a["data"]["vault_credential_ids"])')" 2

run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")" 'a["data"]["id"]')
expect_run "run on every node succeeds" "$run_id" 180 successful
detail=$(admin run.get "{\"id\": \"$run_id\"}")
expect_eq "every node is in the result, none failed" \
    "$(get "$detail" 'len(a["data"]["stats"]), sum(h["failures"] + h["unreachable"] for h in a["data"]["stats"].values())')" "$NODES 0"
expect_eq "passphrase key, both vaults and become reached the node" \
    "$(podman exec "fleet-node-$NODES" cat /root/fleet-marker)" "root from-dev-vault from-prod-vault"
events="/var/lib/cockpit-fleet/runs/$run_id/events.jsonl"
expect_eq "loop elements have their own events" \
    "$(grep -c '"event": "item_ok"' "$events") $(grep -c '"event": "item_failed"' "$events")" "$NODES $NODES"
if grep -rqE 'node pass phrase|dev-pass|prod-pass|from-dev-vault' "/var/lib/cockpit-fleet/runs/$run_id/"; then
    fail "a passphrase, a vault password or a vaulted value was stored"
else
    pass "no passphrase, vault password or vaulted value is stored"
fi

# --- a git project over ssh, with the same passphrase-protected key -------------------
podman exec -u ansible "fleet-node-$NODES" sh -c '
    git init -q -b main /home/ansible/repo && cd /home/ansible/repo &&
    echo "- hosts: all" > from-git.yml && git add . &&
    git -c user.name=it -c user.email=it@example.org commit -qm init'
source=$(admin credential.create '{"name": "git", "kind": "source", "username": "ansible"}')
set_secret "$source" private_key < "$KEYS/key"
printf 'node pass phrase' | set_secret "$source" key_passphrase
git=$(admin project.create "{\"name\": \"from git\", \"kind\": \"git\", \"url\": \"ssh://ansible@$(sh "$HERE/nodes.sh" address "$NODES")/home/ansible/repo\", \"ref\": \"main\", \"credential_id\": \"$(get "$source" 'a["data"]["id"]')\"}")
git_id=$(get "$git" 'a["data"]["id"]')
run_id=$(get "$(admin project.sync "{\"id\": \"$git_id\"}")" 'a["data"]["run_id"]')
expect_run "git project over ssh is fetched with the key from the agent" "$run_id" 120 successful
expect_eq "its files are there" "$(get "$(admin project.files "{\"id\": \"$git_id\"}")" 'a["data"]["files"]')" "['from-git.yml']"

# --- a node goes away ---------------------------------------------------------------
podman stop -t 1 fleet-node-2 >/dev/null
run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\"}")" 'a["data"]["id"]')
expect_run "run with a node down is recorded as failed" "$run_id" 180 failed
detail=$(admin run.get "{\"id\": \"$run_id\"}")
expect_eq "the node that is down is unreachable, the others are fine" \
    "$(get "$detail" 'a["data"]["stats"]["node2"]["unreachable"], sum(h["unreachable"] + h["failures"] for n, h in a["data"]["stats"].items() if n != "node2")')" "1 0"
podman start fleet-node-2 >/dev/null

# --- a host key changes --------------------------------------------------------------
sh "$HERE/nodes.sh" recreate 1 "$KEYS/authorized_keys"
scan=$(admin hostkey.scan "{\"inventory_id\": \"$inventory_id\"}")
address=$(sh "$HERE/nodes.sh" address 1)
expect_eq "a changed host key is reported as changed" \
    "$(get "$scan" '{k["state"] for k in a["data"]["keys"] if k["host"] == "'"$address"'"}')" "{'changed'}"
changed=$(get "$scan" 'json.dumps({**{f: k[f] for f in ("host", "key_type", "key")}} if (k := [k for k in a["data"]["keys"] if k["state"] == "changed"][0]) else None)')
expect_eq "it is not accepted without an explicit replacement" "$(get "$(admin hostkey.add "$changed")" 'a["error"]["code"]')" conflict
run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\", \"limit\": \"node1\"}")" 'a["data"]["id"]')
expect_run "a run refuses the node whose key changed" "$run_id" 120 failed
trust "$scan" replace
run_id=$(get "$(ctl itoperator run.start "{\"template_id\": \"$template_id\", \"limit\": \"node1\"}")" 'a["data"]["id"]')
expect_run "after the explicit replacement the run succeeds" "$run_id" 120 successful
expect_eq "the caller's limit narrowed the run to one node" \
    "$(get "$(admin run.get "{\"id\": \"$run_id\"}")" 'sorted(a["data"]["stats"])')" "['node1']"

finish
