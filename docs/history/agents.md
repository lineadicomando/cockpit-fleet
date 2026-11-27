# Agents: history

Archive: what was tried, measured and found while building agents; not
needed to work on it. The design is in
[architecture/agents.md](../architecture/agents.md).

**`fleet-mcp` over ssh** (`mcp.sh` on Debian 13, with a client written for
the test, and Claude Code 2.1 with a forced-command key): five turns to
launch a template, wait for it and report host and tasks.

**Why `run_log` gives an agent 16 KiB** (18 November): the page's chunk of
256 KiB, with its colours, came to some 61,000 characters for a log of
54 KB, past what Claude Code takes from a tool (about 25,000 tokens);
Claude Code then put it in a file the agent had no tool to read.
