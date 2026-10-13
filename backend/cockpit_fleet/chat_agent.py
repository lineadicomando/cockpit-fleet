"""fleet-chat: starts the agent of a chat session, as the user of the page.

The page gives a name; the command line comes from config.toml through
fleetctl, which also says no when too many sessions are open. The agent
inherits the standard input and output, where it speaks the Agent Client
Protocol with the page: this program only stays around it, so that an open
session can be counted, and ends with it.

What the page must know before the agent says anything comes as a
notification in the same form, one JSON object on a line: "_fleet/ready"
with what to give the agent as its only MCP server, or "_fleet/error".
"""

import json
import os
import signal
import subprocess
import sys

from .mcp import Unreachable, fleetctl
from .paths import LIBEXEC_DIR

# The account an agent is logged in with may carry connectors of its own:
# this keeps those of claude.ai out of a session whose only tools are ours.
_ENVIRONMENT = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}


def _notify(sink, method: str, params: dict) -> None:
    sink.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params},
                          ensure_ascii=False) + "\n")
    sink.flush()


def main(argv: list[str], sink=sys.stdout, call=fleetctl, popen=subprocess.Popen) -> int:
    if len(argv) != 2:
        print("usage: fleet-chat AGENT", file=sys.stderr)
        return 2
    try:
        answer = call("chat.start", {"agent": argv[1]})
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
        "mcp": {"name": "cockpit-fleet", "command": str(LIBEXEC_DIR / "fleet-mcp"),
                "args": [], "env": []},
    })
    return child.wait()
