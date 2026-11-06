#!/usr/bin/python3 -I
"""The program of an agent for a terminal, for the tests of fleet assistant:
it reads the MCP configuration it is given, calls whoami through the server
it names, and says what it found and what it was started with."""

import json
import os
import subprocess
import sys

args = sys.argv[1:]
config = json.load(open(args[args.index("--mcp-config") + 1], encoding="utf-8"))
servers = config["mcpServers"]
server = next(iter(servers.values()))
messages = [
    {"jsonrpc": "2.0", "id": 0, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "fake-terminal"}}},
    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}},
]
done = subprocess.run([server["command"], *server.get("args", [])], capture_output=True, text=True,
                      input="".join(json.dumps(m) + "\n" for m in messages), timeout=120, check=False)
role = "none"
for line in done.stdout.splitlines():
    answer = json.loads(line)
    if answer.get("id") == 1 and "result" in answer and not answer["result"]["isError"]:
        role = json.loads(answer["result"]["content"][0]["text"])["role"]
print(f"servers={','.join(servers)} role={role} tty={sys.stdin.isatty()}"
      f" no-tools={args[args.index('--tools') + 1] == ''} user={os.environ.get('USER')}"
      f" told={'cockpit-fleet' in args[-1]} rules={'IT-RULE' in args[-1]}")
