#!/bin/sh
# fleet-mcp: the backend as tools for an agent, under the role of the user who
# starts it and the ceiling of config.toml. Talks to it as an MCP client would,
# on its standard input and output, and over ssh with a key that can do nothing
# else.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe
MCP=/usr/libexec/cockpit-fleet/fleet-mcp
SRC=/srv/fleet-it

wipe
KEYS=$(mktemp -d)
# The ssh login leaves a session of the test account: without ending it the
# account cannot be removed.
cleanup() { loginctl terminate-user itoperator 2>/dev/null && sleep 2; wipe; rm -rf "$KEYS"; }
trap cleanup EXIT
prepare

# mcp USER MESSAGE...: the answers of fleet-mcp, started by that user, to an
# initialize followed by these JSON-RPC messages, one per line.
mcp() {
    user=$1; shift
    {
        printf '%s\n' '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"it-client","version":"1"}}}'
        printf '%s\n' '{"jsonrpc":"2.0","method":"notifications/initialized"}'
        for message in "$@"; do printf '%s\n' "$message"; done
    } | sudo -u "$user" "$MCP" 2>&1
}
# answer TEXT ID EXPRESSION: python EXPRESSION on the answer "a" with that id
answer() {
    printf '%s\n' "$1" | python3 -c "
import json, sys
for line in sys.stdin:
    try:
        a = json.loads(line)
    except ValueError:
        continue
    if a.get('id') == $2:
        print($3)" 2>/dev/null
}
call() {  # call ID TOOL ARGUMENTS-JSON: a tools/call message
    printf '{"jsonrpc":"2.0","id":%s,"method":"tools/call","params":{"name":"%s","arguments":%s}}' "$1" "$2" "$3"
}
LIST='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

mkdir -p "$SRC/site"
cat > "$SRC/site/site.yml" <<'YAML'
- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.copy:
        content: "{{ greeting }}"
        dest: /tmp/fleet-it-marker
        mode: "0644"
    - ansible.builtin.command: /bin/false
      when: break_it | default(false) | bool
YAML
chmod -R a+rX "$SRC"
project_id=$(get "$(admin project.create "{\"name\": \"site\", \"kind\": \"local\", \"path\": \"$SRC/site\"}")" 'a["data"]["id"]')
inventory_id=$(get "$(admin inventory.create '{"name": "self", "kind": "managed", "format": "ini", "content": "self ansible_connection=local\n"}')" 'a["data"]["id"]')
template_id=$(get "$(admin template.create "{\"name\": \"Say hello\", \"project_id\": \"$project_id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory_id\", \"survey\": [{\"name\": \"greeting\", \"type\": \"text\", \"required\": true}, {\"name\": \"break_it\", \"type\": \"boolean\"}]}")" 'a["data"]["id"]')
admin template.create "{\"name\": \"Needs a secret\", \"project_id\": \"$project_id\", \"playbook\": \"site.yml\", \"inventory_id\": \"$inventory_id\", \"survey\": [{\"name\": \"token\", \"type\": \"secret\", \"required\": true}]}" > /dev/null
tool="launch_say_hello_$(printf '%s' "$template_id" | tail -c 6)"

# --- off by default ---------------------------------------------------------------
out=$(mcp itoperator "$LIST")
expect_eq "the handshake works without the backend" "$(answer "$out" 0 'a["result"]["serverInfo"]["name"]')" cockpit-fleet
expect_eq "no tool while agents are not enabled" "$(answer "$out" 1 '"not enabled" in a["error"]["message"]')" True
out=$(mcp itoperator "$(call 2 run_list '{}')")
expect_eq "and no call" "$(answer "$out" 2 'a["result"]["isError"]')" True
expect_eq "the attempt is in the audit log, with where it came from" \
    "$(get "$(admin audit.list)" '[(e["command"], e["outcome"], e["via"]) for e in a["data"]["entries"] if e["via"]][0]')" "('mcp.tools', 'denied', 'mcp:it-client')"

# --- enabled, with the ceiling at operator -----------------------------------------
printf '[mcp]\nenabled = true\n' >> "$CONFIG"
out=$(mcp itoperator "$LIST")
names=$(answer "$out" 1 '" ".join(sorted(t["name"] for t in a["result"]["tools"]))')
case " $names " in *" run_result run_start "*) pass "an operator's agent gets the operator's tools" ;; *) fail "an operator's agent gets the operator's tools: $names" ;; esac
case " $names " in *" $tool "*) pass "a template is a tool" ;; *) fail "a template is a tool: $names" ;; esac
case " $names " in *" credential_create "*|*" run_adhoc "*|*" file_write "*|*launch_needs_a_secret*) fail "an operator's agent is offered too much: $names" ;; *) pass "no admin tool, and no template that needs a secret" ;; esac
expect_eq "the template's questions are the tool's parameters" \
    "$(answer "$out" 1 '[t["inputSchema"]["properties"]["answers"]["required"] for t in a["result"]["tools"] if t["name"] == "'"$tool"'"][0]')" "['greeting']"
out=$(mcp itadmin "$LIST")
expect_eq "an administrator's agent is held to the ceiling" \
    "$(answer "$out" 1 'any(t["name"] in ("credential_create", "run_adhoc") for t in a["result"]["tools"])')" False

# --- launch a template as a tool, read how it went ---------------------------------
out=$(mcp itoperator "$(call 2 "$tool" '{"answers": {"greeting": "hello from an agent"}}')")
run_id=$(answer "$out" 2 'a["result"]["structuredContent"]["id"]')
expect_run "an agent launches a template" "$run_id" 90 successful
expect_eq "with its answers" "$(get "$(admin run.get "{\"id\": \"$run_id\"}")" 'a["data"]["options"]["answers"]["greeting"]')" "hello from an agent"
out=$(mcp itoperator "$(call 3 run_result "{\"id\": \"$run_id\", \"wait\": 30}")")
expect_eq "run_result says how it went, by host and by task" \
    "$(answer "$out" 3 '(lambda r: (r["done"], r["status"], r["stats"]["self"]["changed"], [t["changed"] for t in r["tasks"]]))(a["result"]["structuredContent"])')" "(True, 'successful', 1, [1, 0])"
out=$(mcp itoperator "$(call 2 "$tool" '{"answers": {"greeting": "x", "break_it": true}}')" )
run_id=$(answer "$out" 2 'a["result"]["structuredContent"]["id"]')
out=$(mcp itoperator "$(call 3 run_result "{\"id\": \"$run_id\", \"wait\": 50}")")
expect_eq "a failure is told with what the node said" \
    "$(answer "$out" 3 '(lambda r: (r["status"], r["tasks"][-1]["failed"], "self" in r["tasks"][-1]["problems"]))(a["result"]["structuredContent"])')" "('failed', 1, True)"
expect_eq "the launch is recorded as the user's, through the agent" \
    "$(get "$(admin audit.list)" '[(e["user"], e["outcome"], e["via"]) for e in a["data"]["entries"] if e["command"] == "run.start"][0]')" "('itoperator', 'ok', 'mcp:it-client')"
expect_ok "and is in the journal with FLEET_VIA" sh -c "journalctl -t cockpit-fleet FLEET_VIA=mcp:it-client FLEET_COMMAND=run.start --since -5min -o cat | grep -q 'itoperator via mcp:it-client run.start: ok'"

# --- what an agent cannot do --------------------------------------------------------
out=$(mcp itoperator "$(call 2 "$tool" '{"answers": {"greeting": "x"}, "template_id": "other", "via": "interface"}')")
expect_eq "a tool's own parameters cannot be replaced" "$(answer "$out" 2 'a["result"]["isError"]')" False
out=$(mcp itadmin "$(call 2 credential_create '{"name": "x", "kind": "vault"}')")
expect_eq "an admin tool is refused under the ceiling" "$(answer "$out" 2 'a["error"]["code"] if "error" in a else a["result"]["isError"]')" -32602
out=$(printf '%s' '{"name": "x", "kind": "vault", "via": "mcp:it-client"}' | sudo -u itadmin sudo -n -u cockpit-fleet "$FLEETCTL" credential.create)
expect_eq "also when asked of fleetctl directly" "$(get "$out" 'a["error"]["code"]')" forbidden
expect_eq "and the refusal is in the audit log" \
    "$(get "$(admin audit.list)" '[(e["user"], e["role"], e["outcome"], e["via"]) for e in a["data"]["entries"] if e["command"] == "credential.create"][0]')" "('itadmin', 'operator', 'denied', 'mcp:it-client')"
out=$(mcp itoperator "$LIST" "$(call 2 run_log "{\"id\": \"$run_id\"}")" "$(call 3 config_export '{}')" "$(call 4 credential_list '{}')")
if printf '%s' "$out" | grep -qi 'ciphertext\|BEGIN OPENSSH\|\.cred"'; then fail "a secret is in an answer"; else pass "no secret in the answers"; fi
expect_eq "there is no tool for secrets" "$(answer "$out" 1 'any("secret" in t["name"] for t in a["result"]["tools"])')" False

# --- raising the ceiling ------------------------------------------------------------
sed -i 's/^\[mcp\]$/[mcp]\nmax_role = "admin"/' "$CONFIG"
out=$(mcp itadmin "$LIST" "$(call 2 run_adhoc "{\"inventory_id\": \"$inventory_id\", \"limit\": \"all\", \"module\": \"ansible.builtin.command\", \"free_form\": \"id -un\"}")")
expect_eq "with the ceiling at admin, ad-hoc commands are a tool" "$(answer "$out" 1 'any(t["name"] == "run_adhoc" for t in a["result"]["tools"])')" True
expect_run "and an agent runs one" "$(answer "$out" 2 'a["result"]["structuredContent"]["id"]')" 90 successful
out=$(mcp itoperator "$LIST")
expect_eq "the user's own role still holds" "$(answer "$out" 1 'any(t["name"] == "run_adhoc" for t in a["result"]["tools"])')" False

# --- over ssh, with a key that reaches fleet-mcp and nothing else ----------------------
ssh-keygen -q -t ed25519 -N '' -f "$KEYS/agent"
home=$(getent passwd itoperator | cut -d: -f6)
install -d -m 700 -o itoperator -g itoperator "$home/.ssh"
printf 'restrict,command="%s" %s\n' "$MCP" "$(cat "$KEYS/agent.pub")" > "$home/.ssh/authorized_keys"
chown itoperator:itoperator "$home/.ssh/authorized_keys"; chmod 600 "$home/.ssh/authorized_keys"
over_ssh() {  # over_ssh COMMAND: what that key gets when it asks for COMMAND
    ssh -i "$KEYS/agent" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR itoperator@127.0.0.1 "$1" 2>&1
}
out=$(printf '%s\n%s\n' '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"clientInfo":{"name":"over-ssh"}}}' "$LIST" | over_ssh "$MCP")
expect_eq "an agent reaches its tools over ssh" "$(answer "$out" 1 'any(t["name"] == "run_start" for t in a["result"]["tools"])')" True
out=$(printf '%s\n' '{"jsonrpc":"2.0","id":7,"method":"ping"}' | over_ssh "id; cat /etc/passwd")
expect_eq "whatever that key asks for, it gets fleet-mcp" "$(answer "$out" 7 'a["result"]')" "{}"
if printf '%s' "$out" | grep -q 'uid=\|root:'; then fail "the key ran a command of its own"; else pass "the key runs no command of its own"; fi

finish
