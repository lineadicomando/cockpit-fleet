# The chat

A page of Fleet where a user talks to an assistant that works on the
controller through Fleet itself: it reads what that user may read, launches
what that user may launch, and asks before every action that changes
something. How it is built is in [architecture.md](architecture.md), "The
chat"; this page is for the administrator who turns it on.

## What leaves the controller

The assistant is a language model at its provider. Everything said in a
conversation is sent there: what the user writes, and what the tools of
Fleet answer, which means the names of projects, inventories, hosts,
templates and credentials, the outcome of runs task by task with the error
messages of the nodes and, when the user asks for it, the output of a run.
Secrets are not among the tools: no value of a credential can be read, and
those handed to a run are masked in its output, as a net and not as a
guarantee. Do not turn the chat on where that is not acceptable.

## Turning it on

Two settings in `/etc/cockpit-fleet/config.toml`, both off in a new
installation:

```toml
[mcp]
enabled = true          # agents may reach the backend, as the user who starts them
#max_role = "operator"  # and never with more than this role

[chat]
#max_sessions = 2       # for all users: a session takes about 360 MB

[chat.agents.claude]
command = ["/opt/cockpit-fleet-agent/node/bin/node",
           "/opt/cockpit-fleet-agent/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js"]
label = "Claude"
login = "/opt/cockpit-fleet-agent/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude /login"
```

An agent is any program that speaks the Agent Client Protocol on its
standard input and output. The "Chat" tab appears, after a reload, for every
role once an agent is named. The agent runs as the user who opens the page.

## Installing the adapter for Claude

The package does not install it: it needs Node.js 22, which Debian 13 does
not have, and brings its own `claude` binary. Once, as root, for all users
(about 510 MB under `/opt/cockpit-fleet-agent`):

```sh
NODE=node-v22.23.3-linux-x64.tar.xz
cd /tmp
wget "https://nodejs.org/dist/v22.23.3/$NODE"
echo "df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de  $NODE" | sha256sum -c -
install -d /opt/cockpit-fleet-agent/node
tar -C /opt/cockpit-fleet-agent/node --strip-components=1 -xJf "$NODE"
PATH=/opt/cockpit-fleet-agent/node/bin:$PATH \
    npm install --prefix /opt/cockpit-fleet-agent --no-fund --no-audit \
    @agentclientprotocol/claude-agent-acp@0.89.1
```

The versions are the ones the chat was tried with. To update, install
another version of the adapter in the same way; `package-lock.json` in that
directory says what is there.

## The login of each user

The model wants a login, which is the user's own and is made once, in a
terminal on the controller (Cockpit has one), as that user:

```sh
/opt/cockpit-fleet-agent/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude /login
```

It gives an address to open in a browser and takes the code shown there. The
login is kept in the user's home (`~/.claude`). Until it is made the page
says so, with the command set as `login` in `config.toml`.

## What a user gets

- The role they have in Fleet, lowered to `[mcp] max_role`: with the default,
  an administrator's assistant is an operator's. The reason is in
  `config.toml`: the output of a run is written by the nodes.
- Every start of a session is in the audit log (`chat.start`), and so is
  everything the assistant changes, as the user's own action with `mcp:` and
  the name of the agent in the "via" column.
- The agent runs while the page is open and ends when it is closed or
  reloaded. Conversations are kept by the agent in the user's home and can be
  continued from the page.
