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

Until it is set up, the "Assistant" tab is shown to administrators of Fleet
only, and says what is missing with the lines to paste in a terminal, each
with a button to copy it and one that opens Cockpit's terminal ("Terminal"
in its menu, which runs as whoever is logged in). The page looks again every
few seconds and shows the start of the chat once nothing is missing. The
steps are these:

1. An administrator of the machine (one who may use `sudo`) installs the
   agent, names it in `config.toml` and lets agents reach the backend, with
   one line:

   ```sh
   sudo env ANSIBLE_COLLECTIONS_PATH=/usr/share/cockpit-fleet/collections ansible-playbook -i localhost, -c local lineadicomando.fleet.chat_agent -e chat_agent_configure=true -e chat_agent_enable_mcp=true
   ```

   The page leaves out `chat_agent_configure` when an agent is named
   already, and `chat_agent_enable_mcp` when `[mcp]` is enabled already.
   `chat_agent_enable_mcp` sets `enabled = true` in `[mcp]` of
   `/etc/cockpit-fleet/config.toml` and nothing else; without it, set it by
   hand:

   ```toml
   [mcp]
   enabled = true          # as the user who starts them
   #max_role = "operator"  # and never with more than this role
   ```

2. Each user logs in to the model, once, as their own user and without
   `sudo` (see below):

   ```sh
   /opt/cockpit-fleet-agent/bin/claude /login
   ```

Once an agent is named in `config.toml`, the tab appears for every role,
after a reload. The agent runs as the user who opens the page.

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

  Without it, the playbook prints these lines for you to add. It refuses to
  write over an agent of the same name that it did not write.
- With `-e chat_agent_enable_mcp=true`, `enabled = true` in `[mcp]` of
  `config.toml`: the line there is changed, or the commented default, or the
  key is added under `[mcp]`, or `[mcp]` at the end of the file. The file is
  written only if the backend accepts it. Removing the agent leaves `[mcp]`
  as it is, since other agents may use it.

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
- Within that, what the profile of the assistant allows (`[chat] profiles`,
  `default_profile`): `observer` reads; `assisted`, the default, also
  launches and cancels what is defined (templates, workflows, checks,
  schedules turned on or off); `diagnostic` also runs ad-hoc commands of the
  modules in `[mcp] adhoc_modules`, which change nothing, without become;
  `autonomous` is as `assisted`, held by the backend since nobody confirms
  its calls: at most `[chat] autonomous_runs` launches in a session, none on
  an inventory with a maintenance window open, no acknowledging a problem
  and no maintenance window opened or closed, and with
  `autonomous_dry_run` dry runs only; `administrator` gets whatever the
  role allows: ad-hoc commands, changes to templates, inventories and the
  rest, files, projects, credentials, deletes. Only `observer` and
  `assisted` are enabled as installed. The backend refuses what is outside
  the profile, and `fleet-mcp` started by hand gets the default one.
- An ad-hoc command the assistant asks for, other than one of `[mcp]
  adhoc_modules`, does not start until the user confirms it, in the list of
  runs waiting for approval or with `fleet approval approve`; the assistant
  cannot confirm it, and it expires as approvals do.
- Every start of a session is in the audit log (`chat.start`), and so is
  everything the assistant changes, as the user's own action with `mcp:` and
  the name of the agent in the "via" column and the profile beside it.
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
