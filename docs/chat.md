# The chat

A page of Fleet, "Assistant" in its tabs, where a user talks to an
assistant that works on the controller through Fleet itself: it reads
what that user may read, launches what that user may launch, within the
profile the user chose for the session, and asks before the actions that
profile does not let through. This page is for the administrator who
turns it on; how it is built: [architecture/chat.md](architecture/chat.md).

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
   #max_role = "admin"     # and never with more than this role
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
  its 114 packages with its hash, so nothing else can be installed, and no
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

The keys of `[chat]`, with their defaults (the rest of this page says
what each does):

```toml
[chat]
#max_sessions = 2            # open at once, all users: one takes about 360 MB
#profiles = ["observer", "assisted", "diagnostic", "autonomous", "administrator"]
#default_profile = "assisted"
#autonomous_runs = 30        # launches per conversation in autonomous
#autonomous_dry_run = false  # autonomous: templates in check mode only
#autonomous_writes = 20      # changes to the documents per conversation
#unattended = false          # allow "Without questions" in administrator
#[chat.agents.NAME]          # command, label, login, terminal
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
says "Login successful"; press Enter there, or the first `fleet
assistant` asks its questions of first use again; what it shows
afterwards is for its own use at a terminal: Ctrl+C leaves it. The login
is kept in the user's home (`~/.claude`). Until it is made the page says so, with this command and a
button that opens the terminal in the same tab: once logged in, open
Fleet again from Cockpit's menu and send the message again.

## What a user gets

- The role they have in Fleet, lowered to `[mcp] max_role` (`admin`):
  set it to `operator` for an administrator's assistant to be an
  operator's, since the output of a run is written by the nodes.
- Within that, the profile of the session, among `[chat] profiles`
  (all five as installed; list fewer to keep the others from users).
  The backend refuses what is outside it; `fleet-mcp` started by hand
  gets `default_profile`.

  | Profile | May | Asked |
  |---|---|---|
  | `observer` | read | changes to the documents |
  | `assisted` (default) | also launch and cancel what is defined: templates, workflows, checks, schedules on or off | launches and cancels, with "always" for the conversation |
  | `diagnostic` | also ad-hoc commands of `[mcp] adhoc_modules` (change nothing), without become or `{{ }}` in their arguments; scans of a subnet allowed by `[network]` ([security.md](security.md)), for administrators of Fleet | as `assisted`; those ad-hoc commands unasked, each scan every time |
  | `autonomous` | as `assisted`, held by the backend: at most `autonomous_runs` launches per conversation (or what the user chose at the start, and more allowed from the page), none on an inventory in a maintenance window, no acknowledging, no maintenance window, no schedule turned on, dry runs only with `autonomous_dry_run`; at most `autonomous_writes` changes to the documents | nothing |
  | `administrator` | whatever the role allows: ad-hoc commands, changes to templates, inventories and the rest, files, projects, credentials, deletes; only for administrators of Fleet with `max_role = "admin"` (for anyone else, a name that promises more than the role) | launches as `assisted`; scans, ad-hoc commands, changes, files, deletes every time, never "always" |

  Every profile reads, searches and writes the documents (Resources,
  Documents); a change to them is asked, with "always", but in
  `autonomous`; deleting one every time, but in `autonomous`. Questions
  are a convenience: the backend decides again, whatever the answer.
- The user picks the profile when starting a session and may change it
  within the conversation ("Change the profile"): the agent starts again
  with the same conversation, and "always" answers are asked again.
  Before the first session in `diagnostic`, `autonomous` or
  `administrator` the page tells what it may do and its risks (the nodes
  can steer an assistant through what they write), and the user accepts
  them once, in the audit log (`chat.accept`); until then the backend
  refuses such a session. Enabling a profile is the administrator's
  decision; accepting its risks, each user's.
- "Without questions", in `administrator` only and only where `[chat]
  unattended = true` (off as installed): the switch in the header of the
  session turns every question off but those about deleting, as the mode
  of Claude Code that bypasses permissions, for the conversations of that session until it is
  turned off, the profile changes or the page is reloaded. Its ad-hoc
  commands then start without a confirmation, marked "unattended" in the
  run's options; changes are made at once; a delete (of a template, an
  inventory, a credential, a file, a document and the rest) is still
  asked about, every time. Files still go to
  a draft a person merges, a template that needs approval still waits for
  a person, and "Stop everything" cancels its runs. Each user accepts its
  risks once (`chat.accept`), and each turning on or off is in the audit
  log (`chat.unattended`). Not at a terminal.
- Above the message box the page shows the runs the assistant launched
  in the conversation and not ended, each a link to its page. In
  `autonomous`, how many of those it may ("Runs launched in this
  conversation: 30 of 30"); once spent, the agent stops and says so, and
  "Allow 30 more" (`autonomous_runs` more each time; role operator) lets
  it go on. In any profile, "Stop everything" stops the answer and cancels
  those runs, queued, running or waiting for an approval, workflow steps
  included. These, and the runs chosen at the start, are the user's
  decisions, in the audit log (`chat.limit_runs`, `chat.grant_runs`,
  `chat.cancel_runs`); an agent cannot call them.
- A run that has ended, and a check whose result is not ok (in the checks
  of a host and in Problems), have a button "Assistance": it writes in the
  chat a request to investigate it, which the user edits and sends; nothing
  is sent by itself. `diagnostic` is suggested for a new session, when it
  is enabled.
- An ad-hoc command the assistant asks for, other than one of `[mcp]
  adhoc_modules` without become and with no template (`{{ }}`) in its
  arguments, does not start until the user confirms
  it (unless "Without questions" is on): "Allow once" when the page asks,
  or later above the message box, in the runs waiting for approval, or
  `fleet approval approve`. The assistant cannot confirm it, an answer
  written in the chat confirms nothing, and it expires as approvals do.
- When runs the assistant launched end after it has answered (a command
  confirmed later, a long playbook), the page tells it in a message marked
  "Fleet", which it answers with how they went: there is no need to write
  to it that a run has ended. The page does this while it is open, in any
  tab of Fleet; runs stopped with "Stop everything" are not told. Each such
  message is a turn of the model, sent to its provider as any other.
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
`/etc/cockpit-fleet/assistant.md`, and last the index of the skills, if
there are any (see "Skills" below). Write there what every assistant of this
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

## Skills

What a project, its collections and this controller know of their own
work (how a role is driven, the conventions of the labs, how a report is
written) reaches the assistant as *skills*, in the format of Claude's: a
directory with a `SKILL.md` whose frontmatter names it and says when to use
it.

```
.fleet/skills/lab-maintenance/SKILL.md     in the tree of a project
.fleet/skills/task-strings/SKILL.md        in a collection a project installs
/etc/cockpit-fleet/skills/site/SKILL.md    on this controller
skills/patching/SKILL.md                   in the documents
```

```markdown
---
name: lab-maintenance
description: Maintaining a lab: wake the PCs, update them, shut them down,
  and write the report.
---

# Maintaining a lab
...
```

- The name is the directory's: lower case letters, digits and hyphens, 64
  at most; a frontmatter that says another name, or no description, leaves
  the directory out. The description is what the assistant chooses by:
  say *when* to use the skill, in a sentence or two (1024 characters at
  most).
- Each session is told the index, names and descriptions, after the
  controller's instructions; it reads a skill's text, and the `.md` or
  `.txt` files beside it that the text points to, with `skill_read` when
  the work at hand is what the description says. A text is 64 kB at most;
  the index, past 16 kB, says that the rest is in `skill_list`.
- They are read as the project is now, when a session starts: a change
  reaches the next one. A link is never followed, on the way or at the end.
- Every role reads them, viewers included, and they go to the provider of
  the model like the instructions: no password, address or name that
  should not leave the controller. They guide the assistant; what it may
  do is held by its role and profile, whatever a skill says.
- At a terminal, `fleet skill list` and `fleet skill read --source ...
  --name ...` show what the assistant sees.
- The skills of the documents (Resources, Documents, the folder `skills`)
  are written and corrected there like any document, with their history,
  and only their texts (`.md`) count. The assistant may propose one, for a
  task that recurs: it writes it there and says so. A skill whose last
  change the assistant made waits for a person: it is not in the index,
  nor read with `skill_read`, until an operator or an administrator opens
  it and chooses "Approve", after reading it or the changes since the last
  approved version, or saves it themselves. The approval is in the history
  of the documents and in the audit log (`skill.approve`; `fleet skill
  approve --name NAME --revision ID` at a terminal, the revision that
  `fleet skill list` gives); an agent can never give it.

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
end, naming `fleet-mcp` as the only server; `{allowed_tools}` the tools
the profile lets through with no question, as the page would,
comma-separated; `{instructions}` those of the session. The options keep
the agent to the tools of `fleet-mcp`, with none of its own and none of
the user's settings. The rest is as on the page: the role under `[mcp]
max_role`; the profile (`--profile`, or the default), whose risks
`fleet assistant` tells at the first session and waits for `yes`; the
start in the audit log (`chat.start`, with `terminal`); a session counted
in `max_sessions`. Other calls are asked in the agent's own way: its
"don't ask again" trusts one tool for the session, whatever its class,
which the page never does for ad-hoc commands, changes and files; a line
without `{allowed_tools}` makes it ask about every call. No "Without
questions", "Allow more" or "Stop everything" there.

It starts in `~/.local/state/cockpit-fleet/assistant`, the same each
time: Claude asks there, the first time, whether to trust it (answer yes:
its own tools are off, whatever the question says); conversations held at
a terminal are there, resumed with `/resume`. `fleet assistant NAME`
chooses among agents, `--profile diagnostic` the profile; an agent
without `terminal` is for the page only. Installed by hand, add the line
to its section. A controller set up before this line, or before
`{allowed_tools}`, gets it by running the playbook again with
`chat_agent_configure=true`, which replaces the section it wrote (remove
one written by hand first).
