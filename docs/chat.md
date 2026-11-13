# The chat

A page of Fleet, "Assistant" in its tabs, where a user talks to an assistant that works on the
controller through Fleet itself: it reads what that user may read, launches
what that user may launch, within the profile the user chose for the
session, and asks before the actions that profile does not let through.
How it is built is in [architecture/chat.md](architecture/chat.md); this page is for the administrator who turns it on.

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
says "Login successful"; press Enter there, or the first `fleet assistant`
asks its questions of first use again, and what it shows afterwards is for
its own use at a terminal: Ctrl+C leaves it. The login is kept in the user's home
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
- The user picks the profile when starting a session, among those enabled.
  Before the first session in `diagnostic`, `autonomous` or `administrator`
  the page tells what it may do and its risks (the nodes can steer an
  assistant through what they write, and in these profiles it does more
  with it), and the user accepts them, once: the acceptance is in the audit
  log (`chat.accept`), and until then the backend refuses to start a
  session in that profile. Enabling a profile in `config.toml` is the
  administrator's decision; accepting its risks is each user's.
- What the page asks about follows the profile: in `observer` nothing,
  since nothing changes; in `assisted` every launch or cancel, with "always"
  to trust launches for the rest of the conversation; in `diagnostic` the
  same, and its ad-hoc commands go unasked; in `autonomous` nothing, the
  backend holds it instead; in `administrator` every change, and ad-hoc
  commands, changes to what is defined and files every time, never
  "always". These questions are a convenience: the backend decides again,
  whatever the answer.
- An ad-hoc command the assistant asks for, other than one of `[mcp]
  adhoc_modules`, does not start until the user confirms it, in the list of
  runs waiting for approval or with `fleet approval approve`; the assistant
  cannot confirm it, and it expires as approvals do.
- Files the assistant writes in a project or inventory written in Fleet
  (profile `administrator`) go to a draft of its session, which no run
  uses. The page of the assistant and the files of the project say when a
  draft waits: an administrator reviews it (what changes, the syntax
  checks), edits it on the page of the files if needed, and merges it,
  whole or a file at a time, or discards it. At a terminal, `fleet draft
  list`, `fleet draft diff`, `merge` and `discard` do the same. The
  assistant can do neither.
- Every start of a session is in the audit log (`chat.start`), and so is
  everything the assistant changes, as the user's own action with `mcp:` and
  the name of the agent in the "via" column and the profile beside it.
- The agent runs while the page is open and ends when it is closed or
  reloaded. Conversations are kept by the agent in the user's home and can be
  continued from the page.

## What the assistant is told

Before the first message, the agent is given instructions in layers: a base
and a part for its profile, which come with the package and cannot be
removed, then the controller's own, if there is a file
`/etc/cockpit-fleet/assistant.md`. Write there what every assistant of this
controller should know: the inventories not to touch, who to ask about what,
the conventions of the projects. Plain text or Markdown, up to 8 kB, in
UTF-8; it is read at the start of each session, so a change applies to the
next one. A file larger than that, or not text, stops every session from
starting, with a message that says so. The layers are shown on the
user's page as well, under "Instructions the assistant was given".

Instructions guide the assistant, they do not hold it: what it may do is
decided by the role, the profile and `[chat]`, whatever it was told. And
they are sent to the provider with every conversation: put no password,
address or name in them that should not leave the controller.

## At a terminal

`fleet assistant` starts the same agent in a terminal on the controller (an
SSH session, or Cockpit's terminal), for a user who works at the command
line. It runs the agent's own program for a terminal, which the playbook
names in `config.toml` beside the one of the page:

```toml
terminal = ["/opt/cockpit-fleet-agent/bin/claude", "--mcp-config", "{mcp_config}",
            "--strict-mcp-config", "--tools", "", "--setting-sources", "",
            "--permission-mode", "manual", "--allowedTools", "{allowed_tools}",
            "--append-system-prompt", "{instructions}"]
```

`{mcp_config}` becomes a file, private to the session and deleted at its
end, that names `fleet-mcp` as the only server; `{allowed_tools}` the
tools the profile of the session lets through with no question, as the
page would (reading, for example), comma-separated; `{instructions}` the
instructions of the session (see "What the assistant is told"). The options keep the agent to the tools of
`fleet-mcp`, with none of its own and none of the user's settings, as in the
page. What the user gets is the same: the role under `[mcp] max_role`, the
profile, `--profile` or the default one, with its risks to accept at the
first session (`fleet assistant` tells them and waits for `yes`), the
start in the audit log (`chat.start`, with `terminal`), a session counted in
`[chat] max_sessions`. The agent asks about the other calls in its own way:
its "don't ask again" trusts one tool for the session, whatever its class,
which the page never does for ad-hoc commands, changes and files. A line
without `{allowed_tools}` makes it ask about every call. It starts in a directory of its own,
`~/.local/state/cockpit-fleet/assistant`, the same each time: Claude asks
there, the first time, whether to trust it (answer yes: its own tools are
off, whatever the question says), and the conversations held at a terminal
are there, to be resumed with `/resume`. With more than one agent, `fleet assistant NAME`
chooses, and `fleet assistant --profile diagnostic` the profile; an agent without `terminal` is for the page only. When it is
installed by hand, add the line to its section; a controller set up before
this line existed, or before `{allowed_tools}`, gets it by running the playbook again with
`chat_agent_configure=true`, which replaces the section it wrote (a section
written by hand is to be removed first).
