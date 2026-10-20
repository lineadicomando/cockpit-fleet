# The chat

A page of Fleet, "Assistant" in its tabs, where a user talks to an assistant that works on the
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

Three steps, the first two for an administrator of the machine. Each is a
command in a terminal, and Cockpit has one ("Terminal" in its menu), which
runs as whoever is logged in.

1. Install the agent, once, for all users:

   ```sh
   sudo env ANSIBLE_COLLECTIONS_PATH=/usr/share/cockpit-fleet/collections \
       ansible-playbook -i localhost, -c local lineadicomando.fleet.chat_agent \
       -e chat_agent_configure=true
   ```

2. Let agents reach the backend, in `/etc/cockpit-fleet/config.toml`:

   ```toml
   [mcp]
   enabled = true          # as the user who starts them
   #max_role = "operator"  # and never with more than this role
   ```

3. Each user logs in to the model, once (see below).

The "Assistant" tab appears, after a reload, for every role once an agent is named
in `config.toml`. The agent runs as the user who opens the page.

## What the playbook does

It is part of the package (the collection `lineadicomando.fleet`, role
`chat_agent`) and installs the ACP adapter for Claude under
`/opt/cockpit-fleet-agent`, about 510 MB. The package itself downloads
nothing: this does, when an administrator runs it.

- **Node.js 22**, which the adapter needs and Debian 13 does not have, from
  `nodejs.org`, checked against the checksum published with the release.
- **The adapter**, `@agentclientprotocol/claude-agent-acp` 0.89.1, from
  `registry.npmjs.org` with `npm ci`: a lockfile in the package names each of
  its 115 packages with its hash, so nothing else can be installed, and no
  install script of a package is run. It brings its own `claude` binary.
- **Two commands** in `/opt/cockpit-fleet-agent/bin`: `claude-agent-acp`, the
  agent, and `claude`, for the login.
- With `-e chat_agent_configure=true`, the agent in `config.toml`, between two
  marker lines:

  ```toml
  [chat.agents.claude]
  command = ["/opt/cockpit-fleet-agent/bin/claude-agent-acp"]
  label = "Claude"
  login = "/opt/cockpit-fleet-agent/bin/claude /login"
  ```

  Without it, the playbook prints these lines for you to add. It never sets
  `[mcp] enabled`, and refuses to write over an agent of the same name that
  it did not write.

Running it again changes nothing; a later version of the package may name a
newer Node.js or adapter, and running it again then updates them. To remove
everything it wrote, add `-e chat_agent_state=absent`. Behind a proxy, add
`https_proxy=…` after `env` in the command (not tried).

A named agent that is not installed shows in the page with this command,
ready to be copied, and a button that opens the terminal.

Other things `[chat]` takes:

```toml
[chat]
#max_sessions = 2       # for all users: a session takes about 360 MB
```

An agent is any program that speaks the Agent Client Protocol on its standard
input and output: another one can be named in the same way, installed by
other means.

To install the agent on other controllers from this one, use the role in a
playbook of a project, with the controllers as hosts and a credential that
may become root:

```yaml
- hosts: controllers
  become: true
  roles:
    - lineadicomando.fleet.chat_agent
```

## The login of each user

The model wants a login, which is the user's own and is made once, in a
terminal on the controller, as that user:

```sh
/opt/cockpit-fleet-agent/bin/claude /login
```

The first time, it asks for a style of text and a way of logging in (an
account with a subscription, or one billed by use), then gives an address to
open in a browser and takes the code shown there. The login is made when it
says "Login successful"; what it shows afterwards is for its own use at a
terminal, and Ctrl+C leaves it. The login is kept in the user's home
(`~/.claude`). Until it is made the page says so, with this command and a
button that opens the terminal in a new tab, so that the assistant already
started stays as it is: once logged in, send the message again.

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

## At a terminal

`fleet assistant` starts the same agent in a terminal on the controller (an
SSH session, or Cockpit's terminal), for a user who works at the command
line. It runs the agent's own program for a terminal, which the playbook
names in `config.toml` beside the one of the page:

```toml
terminal = ["/opt/cockpit-fleet-agent/bin/claude", "--mcp-config", "{mcp_config}",
            "--strict-mcp-config", "--tools", "", "--setting-sources", "",
            "--permission-mode", "manual", "--append-system-prompt", "{instructions}"]
```

`{mcp_config}` becomes a file, private to the session and deleted at its
end, that names `fleet-mcp` as the only server; `{instructions}` a few lines
on where the agent is. The options keep the agent to the tools of
`fleet-mcp`, with none of its own and none of the user's settings, as in the
page. What the user gets is the same: the role under `[mcp] max_role`, the
start in the audit log (`chat.start`, with `terminal`), a session counted in
`[chat] max_sessions`. The agent asks before every call, in its own way; the
answers are not kept. It starts in a directory of its own,
`~/.local/state/cockpit-fleet/assistant`, the same each time: Claude asks
there, the first time, whether to trust it (answer yes: its own tools are
off, whatever the question says), and the conversations held at a terminal
are there, to be resumed with `/resume`. With more than one agent, `fleet assistant NAME`
chooses; an agent without `terminal` is for the page only. When it is
installed by hand, add the line to its section; a controller set up before
this line existed gets it by running the playbook again with
`chat_agent_configure=true`, which replaces the section it wrote (a section
written by hand is to be removed first).
