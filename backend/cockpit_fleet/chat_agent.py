"""fleet-chat: starts the agent of a chat session, as the user of the page.

The page gives a name; the command line comes from config.toml through
fleetctl, which also says no when too many sessions are open. The agent
inherits the standard input and output, where it speaks the Agent Client
Protocol with the page: this program only stays around it, so that an open
session can be counted, and ends with it.

What the page must know before the agent says anything comes as a
notification in the same form, one JSON object on a line: "_fleet/ready"
with what to give the agent as its only MCP server, or "_fleet/error".

With --terminal, for fleet assistant, it runs the agent's own program for a
terminal instead, on the terminal of the user, with fleet-mcp as its only
server in a file of its own that is gone when the agent ends. It is counted
as a session all the same.

With --profile, the session is in that profile of the assistant; without,
in the default one. fleetctl says whether it is enabled, and fleet-mcp is
started with it. fleetctl also composes the instructions the agent is
given (instructions.py): the page gets them, and their layers, with
"_fleet/ready"; at a terminal they take the place of {instructions}, and
the tools the profile lets through with no question take the place of
{allowed_tools}, for the agent's own list of tools it may call unasked.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile

from .mcp import Unreachable, fleetctl
from .paths import LIBEXEC_DIR

# The account an agent is logged in with may carry connectors of its own:
# this keeps those of claude.ai out of a session whose only tools are ours.
_ENVIRONMENT = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
MCP_NAME = "cockpit-fleet"


def allowed_tools(names: list[str]) -> str:
    """The tools of fleet-mcp the profile lets through with no question, as
    the agent names them: one word, comma-separated."""
    return ",".join(f"mcp__{MCP_NAME}__{name}" for name in names)


def terminal_argv(command: list[str], mcp_config: str, instructions: str,
                  allowed: str = "") -> list[str]:
    """The program of an agent for a terminal, with its placeholders filled."""
    # The instructions last: text of the administrator's may hold a placeholder.
    return [word.replace("{mcp_config}", mcp_config).replace("{allowed_tools}", allowed)
            .replace("{instructions}", instructions) for word in command]


def workspace() -> str:
    """The directory of the agent at a terminal: the same one each time.

    Claude asks whether to trust the directory it starts in, and keeps no
    answer for a home directory: here it asks once, and the conversations of
    fleet assistant are together, to be resumed. Where it cannot be made, the
    current directory.
    """
    base = os.environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".local", "state")
    path = os.path.join(base, "cockpit-fleet", "assistant")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError:
        return os.getcwd()
    return path


def _start(name: str, profile: str) -> dict:
    """The parameters of chat.start."""
    return {"agent": name, **({"profile": profile} if profile else {})}


def mcp_args(answer: dict) -> list[str]:
    """The arguments of fleet-mcp for the session chat.start answered for."""
    return ["--profile", answer["data"]["profile"]]


def terminal(name: str, call=fleetctl, run=subprocess.run, profile: str = "") -> int:
    """fleet-chat --terminal NAME: the agent on the user's terminal."""
    try:
        answer = call("chat.start", {**_start(name, profile), "terminal": True, "via": "cli"})
    except Unreachable as error:
        print(f"fleet assistant: {error}", file=sys.stderr)
        return 3
    if not answer["ok"]:
        print(f"fleet assistant: {answer['error']['message']}", file=sys.stderr)
        return 3
    directory = tempfile.mkdtemp(prefix="fleet-assistant-")
    try:
        config = os.path.join(directory, "mcp.json")
        with open(config, "w", encoding="utf-8") as sink:
            json.dump({"mcpServers": {MCP_NAME: {"type": "stdio",
                                                 "command": str(LIBEXEC_DIR / "fleet-mcp"),
                                                 "args": mcp_args(answer)}}}, sink)
        # ^C is the agent's, which has the terminal: this one only waits for it.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return run(terminal_argv(answer["data"]["command"], config,
                                     answer["data"]["instructions"],
                                     allowed_tools(answer["data"].get("allowed_tools", []))),
                       env={**os.environ, **_ENVIRONMENT}, cwd=workspace(), check=False).returncode
        except OSError as error:
            print(f"fleet assistant: cannot start the agent: {error}", file=sys.stderr)
            return 3
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _notify(sink, method: str, params: dict) -> None:
    sink.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params},
                          ensure_ascii=False) + "\n")
    sink.flush()


def main(argv: list[str], sink=sys.stdout, call=fleetctl, popen=subprocess.Popen,
         run=subprocess.run) -> int:
    words = argv[1:]
    at_terminal = words[:1] == ["--terminal"]
    words = words[at_terminal:]
    profile = ""
    if len(words) == 3 and words[0] == "--profile" and words[1]:
        profile, words = words[1], words[2:]
    if len(words) != 1:
        print("usage: fleet-chat [--terminal] [--profile PROFILE] AGENT", file=sys.stderr)
        return 2
    if at_terminal:
        return terminal(words[0], call, run, profile)
    try:
        answer = call("chat.start", _start(words[0], profile))
    except Unreachable as error:
        _notify(sink, "_fleet/error", {"code": "unreachable", "message": str(error)})
        return 1
    if not answer["ok"]:
        _notify(sink, "_fleet/error", answer["error"])
        return 1
    try:
        child = popen(answer["data"]["command"], env={**os.environ, **_ENVIRONMENT})
    except OSError as error:
        _notify(sink, "_fleet/error", {"code": "unavailable",
                                       "message": f"cannot start the agent: {error}"})
        return 1
    for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(number, lambda *_: child.terminate())
    _notify(sink, "_fleet/ready", {
        "agent": answer["data"]["agent"],
        "cwd": os.path.expanduser("~"),
        "profile": answer["data"]["profile"],
        "instructions": answer["data"]["instructions"],
        "layers": answer["data"]["layers"],
        "mcp": {"name": MCP_NAME, "command": str(LIBEXEC_DIR / "fleet-mcp"),
                "args": mcp_args(answer), "env": []},
    })
    return child.wait()
