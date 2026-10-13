#!/bin/sh
# The chat: fleet-chat starts the agent config.toml names, as the user of the
# page, and nothing else; the limit on open sessions; what the agent reaches
# through fleet-mcp. The agent is fake-agent.py, which speaks the protocol
# with no model behind it.
# It DELETES the database, the runs and the projects: set IT_WIPE=yes to accept.
set -u
. "$(dirname "$0")/lib.sh"
require_root
require_wipe
CHAT=/usr/libexec/cockpit-fleet/fleet-chat
AGENT=/usr/local/lib/fleet-it/fake-agent

wipe
cleanup() {
    pkill -f "^/usr/bin/python3 -I $CHAT" 2>/dev/null
    for user in itviewer itnobody; do userdel -r "$user" 2>/dev/null; done
    rm -rf /usr/local/lib/fleet-it
    wipe
}
trap cleanup EXIT
prepare
useradd -m -G cockpit-fleet-viewer itviewer
useradd -m itnobody
install -D -m 755 "$(dirname "$0")/fake-agent.py" "$AGENT"

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}'
NEW='{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[{"name":"cockpit-fleet","command":"/usr/libexec/cockpit-fleet/fleet-mcp","args":[],"env":[]}],"_meta":{"testSessionId":"it"}}}'
YES='{"jsonrpc":"2.0","id":1,"result":{"outcome":{"outcome":"selected","optionId":"allow-once"}}}'
NO='{"jsonrpc":"2.0","id":1,"result":{"outcome":{"outcome":"selected","optionId":"reject"}}}'
# chat USER AGENT LINE...: what fleet-chat writes when it is given these lines
chat() {
    user=$1; agent=$2; shift 2
    for line in "$@"; do printf '%s\n' "$line"; done | sudo -u "$user" "$CHAT" "$agent" 2>&1
}
# line TEXT CONDITION EXPRESSION: EXPRESSION on the first message "m" that meets CONDITION
line() {
    printf '%s\n' "$1" | python3 -c "
import json, sys
for text in sys.stdin:
    try:
        m = json.loads(text)
    except ValueError:
        continue
    if $2:
        print($3)
        break" 2>/dev/null
}
said() {  # said TEXT: what the agent said, all of it
    printf '%s\n' "$1" | python3 -c "
import json, sys
out = ''
for text in sys.stdin:
    try:
        m = json.loads(text)
    except ValueError:
        continue
    u = m.get('params', {}).get('update', {})
    if u.get('sessionUpdate') == 'agent_message_chunk':
        out += u['content']['text']
print(out)"
}
# talk USER TEXT [ANSWER]: one prompt in a new session; ANSWER is for a permission asked
talk() {
    chat "$1" fake "$INIT" "$NEW" \
        "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"session/prompt\",\"params\":{\"sessionId\":\"it\",\"prompt\":[{\"type\":\"text\",\"text\":\"$2\"}]}}" \
        ${3:+"$3"}
}

# --- off until an agent is named -----------------------------------------------------
expect_eq "no chat tab by default" "$(get "$(ctl itoperator whoami)" 'a["data"]["chat"]')" False
out=$(chat itoperator fake "$INIT")
expect_eq "nothing starts while no agent is named" "$(line "$out" 'm.get("method") == "_fleet/error"' 'm["params"]["code"]')" unavailable
printf '[chat]\nmax_sessions = 2\n[chat.agents.fake]\ncommand = ["%s"]\nlabel = "Fake"\nlogin = "fake login"\n[chat.agents.gone]\ncommand = ["/nowhere/agent"]\n' "$AGENT" >> "$CONFIG"
expect_eq "the tab is there once one is" "$(get "$(ctl itviewer whoami)" 'a["data"]["chat"]')" True
out=$(ctl itviewer chat.agents)
expect_eq "but the chat needs agents enabled" "$(get "$out" '"[mcp]" in a["data"]["unavailable"]')" True
out=$(chat itoperator fake "$INIT")
expect_eq "and nothing starts without them" "$(line "$out" 'm.get("method") == "_fleet/error"' 'm["params"]["code"]')" unavailable
printf '[mcp]\nenabled = true\n' >> "$CONFIG"

# --- what the page learns, and what it starts -------------------------------------------
out=$(ctl itviewer chat.agents)
expect_eq "a viewer learns which agents there are" "$(get "$out" '[(x["name"], x["label"], x["login"]) for x in a["data"]["agents"]][0]')" "('fake', 'Fake', 'fake login')"
expect_eq "and not how they are started" "$(printf '%s' "$out" | grep -c fleet-it)" 0
expect_eq "nothing is in the way" "$(get "$out" '(a["data"]["unavailable"], a["data"]["open_sessions"], a["data"]["max_sessions"])')" "('', 0, 2)"

out=$(chat itoperator fake "$INIT" "$NEW")
expect_eq "fleet-chat says first what to give the agent" "$(printf '%s\n' "$out" | head -n 1 | python3 -c 'import json,sys; m=json.load(sys.stdin); print(m["method"], m["params"]["mcp"]["command"], m["params"]["cwd"])')" "_fleet/ready /usr/libexec/cockpit-fleet/fleet-mcp /home/itoperator"
expect_eq "then the agent answers" "$(line "$out" 'm.get("id") == 1' 'm["result"]["protocolVersion"]')" 1
expect_eq "the start is in the audit log" \
    "$(get "$(admin audit.list)" '[(e["user"], e["outcome"], e["params"]) for e in a["data"]["entries"] if e["command"] == "chat.start"][0]')" "('itoperator', 'ok', '{\"agent\": \"fake\"}')"
expect_eq "the agent runs as the user" "$(said "$(talk itviewer 'env USER')")" "USER=itviewer"
expect_eq "with the connectors of its account kept out" "$(said "$(talk itviewer 'env ENABLE_CLAUDEAI_MCP_SERVERS')")" "ENABLE_CLAUDEAI_MCP_SERVERS=false"

# --- through fleet-mcp, under the ceiling ---------------------------------------------
out=$(talk itadmin 'call whoami' "$YES")
expect_eq "the agent uses a tool when allowed" "$(said "$out")" "whoami answered."
expect_eq "with the role of the user, held to the ceiling" \
    "$(line "$out" 'm.get("params", {}).get("update", {}).get("status") == "completed"' 'json.loads(m["params"]["update"]["content"][0]["content"]["text"])["role"]')" operator
out=$(talk itadmin 'call whoami' "$NO")
expect_eq "and does not when refused" "$(said "$out")" "whoami was not allowed."

# --- what cannot be started -------------------------------------------------------------
out=$(chat itoperator nope "$INIT")
expect_eq "an agent that is not named" "$(line "$out" 'm.get("method") == "_fleet/error"' 'm["params"]["code"]')" not_found
out=$(chat itoperator gone "$INIT")
expect_eq "an agent that is not installed" "$(line "$out" 'm.get("method") == "_fleet/error"' 'm["params"]["code"]')" unavailable
out=$(chat itnobody fake "$INIT")
expect_eq "anything, for a user with no role" "$(line "$out" 'm.get("method") == "_fleet/error"' 'm["params"]["code"]')" unreachable
expect_denied "an agent given as a path" sudo -u itoperator "$CHAT" "$AGENT"

# --- the limit on open sessions --------------------------------------------------------
sleep 60 | sudo -u itoperator "$CHAT" fake > /dev/null 2>&1 &
sleep 60 | sudo -u itviewer "$CHAT" fake > /dev/null 2>&1 &
n=0; while [ "$(pgrep -fc "^/usr/bin/python3 -I $AGENT")" -lt 2 ] && [ "$n" -lt 20 ]; do sleep 0.5; n=$((n + 1)); done
expect_eq "open sessions are counted, for all users" "$(get "$(ctl itviewer chat.agents)" 'a["data"]["open_sessions"]')" 2
out=$(chat itadmin fake "$INIT")
expect_eq "one more than allowed is refused" "$(line "$out" 'm.get("method") == "_fleet/error"' 'm["params"]["code"]')" conflict
expect_eq "and the refusal is in the audit log" \
    "$(get "$(admin audit.list)" '[(e["user"], e["outcome"]) for e in a["data"]["entries"] if e["command"] == "chat.start"][0]')" "('itadmin', 'error')"
pkill -TERM -f "^/usr/bin/python3 -I $CHAT"
n=0; while pgrep -f "^/usr/bin/python3 -I $AGENT" > /dev/null && [ "$n" -lt 20 ]; do sleep 0.5; n=$((n + 1)); done
expect_eq "ending fleet-chat ends its agent" "$(pgrep -fc "^/usr/bin/python3 -I $AGENT")" 0
out=$(chat itadmin fake "$INIT")
expect_eq "and leaves room for another session" "$(line "$out" 'm.get("id") == 1' 'm["result"]["protocolVersion"]')" 1

finish
