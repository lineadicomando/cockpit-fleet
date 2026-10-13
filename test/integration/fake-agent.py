#!/usr/bin/python3 -I
"""An agent for the tests of the chat: it speaks the Agent Client Protocol as
the adapter for Claude does, with no model behind it.

What it does with a prompt is decided by its first word:

    call TOOL [JSON]   asks permission for that tool of the session's MCP
                       server, as the adapter does, then calls it for real
    login              answers that it has no login to its model
    env NAME           says the value of that variable of its environment
    anything else      is said back, in two pieces

Conversations are kept in ~/.fleet-fake-agent, so that they can be listed
and loaded by a later process.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

STORE = Path.home() / ".fleet-fake-agent"
OPTIONS = [
    {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
    {"optionId": "allow-with-updates", "name": "Yes, and don't ask again", "kind": "allow_always"},
    {"optionId": "reject", "name": "No", "kind": "reject_once"},
]
sessions: dict[str, dict] = {}
counter = 0


def write(message: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()


def read() -> dict | None:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def update(session_id: str, body: dict, keep: bool = True) -> None:
    write({"method": "session/update", "params": {"sessionId": session_id, "update": body}})
    if keep:
        session = sessions[session_id]
        session["updates"].append(body)
        session["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        STORE.mkdir(mode=0o700, exist_ok=True)
        (STORE / f"{session_id}.json").write_text(json.dumps(session))


def text(session_id: str, kind: str, said: str, keep: bool = True) -> None:
    update(session_id, {"sessionUpdate": kind, "content": {"type": "text", "text": said}}, keep)


def mcp_call(server: dict, tool: str, arguments: dict) -> tuple[bool, str]:
    """One tools/call to the MCP server of the session, started for it."""
    messages = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "fake-agent"}}},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}},
    ]
    done = subprocess.run([server["command"], *server.get("args", [])], capture_output=True,
                          input="".join(json.dumps(m) + "\n" for m in messages), text=True,
                          timeout=120, check=False)
    for line in done.stdout.splitlines():
        answer = json.loads(line)
        if answer.get("id") == 1:
            if "error" in answer:
                return False, answer["error"]["message"]
            result = answer["result"]
            return not result["isError"], result["content"][0]["text"]
    return False, done.stderr.strip() or "no answer from the MCP server"


def ask(session_id: str, call_id: str, tool: str, arguments: dict) -> bool:
    """Ask the client whether the tool may be used, and wait for its answer."""
    global counter
    counter += 1
    write({"id": counter, "method": "session/request_permission", "params": {
        "sessionId": session_id, "options": OPTIONS,
        "toolCall": {"toolCallId": call_id, "title": tool, "rawInput": arguments,
                     "_meta": {"claudeCode": {"toolName": f"mcp__cockpit-fleet__{tool}"}}}}})
    while True:
        message = read()
        if message is None:
            sys.exit(0)
        if message.get("id") == counter and "method" not in message:
            outcome = message.get("result", {}).get("outcome", {})
            return outcome.get("optionId") == "allow-once"


def prompt(session_id: str, said: str) -> dict:
    session = sessions[session_id]
    # Kept, not said: the client shows what the person wrote by itself.
    session["updates"].append({"sessionUpdate": "user_message_chunk",
                               "content": {"type": "text", "text": said}})
    session.setdefault("title", said[:60])
    word, _, rest = said.partition(" ")
    if word == "login":
        return {"error": {"code": -32000, "message": "Authentication required"}}
    if word == "env":
        text(session_id, "agent_message_chunk", f"{rest}={os.environ.get(rest, '')}")
    elif word == "call":
        tool, _, raw = rest.partition(" ")
        arguments = json.loads(raw) if raw.strip() else {}
        call_id = f"call-{uuid.uuid4().hex[:8]}"
        update(session_id, {"sessionUpdate": "tool_call", "toolCallId": call_id, "title": tool,
                            "status": "pending", "rawInput": arguments,
                            "_meta": {"claudeCode": {"toolName": f"mcp__cockpit-fleet__{tool}"}}})
        if ask(session_id, call_id, tool, arguments):
            ok, answer = mcp_call(session["mcp"], tool, arguments)
            update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id,
                                "status": "completed" if ok else "failed",
                                "content": [{"type": "content",
                                             "content": {"type": "text", "text": answer}}]})
            text(session_id, "agent_message_chunk",
                 f"{tool} answered." if ok else f"{tool} failed: {answer}")
        else:
            update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id,
                                "status": "failed"})
            text(session_id, "agent_message_chunk", f"{tool} was not allowed.")
    else:
        half = len(said) // 2
        text(session_id, "agent_message_chunk", "You said: " + said[:half])
        text(session_id, "agent_message_chunk", said[half:])
    return {"result": {"stopReason": "end_turn"}}


def handle(message: dict) -> dict:
    method, params = message["method"], message.get("params", {})
    if method == "initialize":
        return {"result": {"protocolVersion": 1, "authMethods": [], "agentCapabilities": {
            "loadSession": True, "sessionCapabilities": {"list": {}}}}}
    if method == "session/new":
        # A test that sends all its lines at once names the session itself.
        session_id = (params.get("_meta") or {}).get("testSessionId") or str(uuid.uuid4())
        sessions[session_id] = {"sessionId": session_id, "cwd": params["cwd"], "updates": [],
                                "mcp": params["mcpServers"][0]}
        return {"result": {"sessionId": session_id}}
    if method == "session/list":
        found = [json.loads(path.read_text()) for path in STORE.glob("*.json")] if STORE.is_dir() else []
        return {"result": {"sessions": [{"sessionId": one["sessionId"], "cwd": one["cwd"],
                                         "title": one.get("title", ""),
                                         "updatedAt": one.get("updatedAt", "")} for one in found]}}
    if method == "session/load":
        path = STORE / f"{params['sessionId']}.json"
        if not path.is_file():
            return {"error": {"code": -32002, "message": "no such session"}}
        session = json.loads(path.read_text())
        session["mcp"] = params["mcpServers"][0]
        sessions[session["sessionId"]] = session
        for body in session["updates"]:
            update(session["sessionId"], body, keep=False)
        return {"result": {}}
    if method == "session/prompt":
        return prompt(params["sessionId"], params["prompt"][0]["text"])
    return {"error": {"code": -32601, "message": f"method not found: {method}"}}


while True:
    incoming = read()
    if incoming is None:
        break
    if "method" in incoming and "id" in incoming:
        write({"id": incoming["id"], **handle(incoming)})
