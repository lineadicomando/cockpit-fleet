# The chat

A page of the interface, "Assistant" in its tabs, where the user talks to an
agent that works through `fleet-mcp`. The page is a client of the Agent
Client Protocol (ACP): JSON-RPC on the standard input and output of the
agent, a process of the user, on a Cockpit channel. What the administrator
sets up and sees: [docs/chat.md](../chat.md).

```
ChatPage ── cockpit.spawn ──▶ fleet-chat NAME        (the user, no privilege)
   │ ACP on stdio               │ sudo -u cockpit-fleet fleetctl chat.start
   │                            ▼
   └──────────────────────▶ the agent ──▶ fleet-mcp ──▶ fleetctl (via, ceiling)
```

**The agent is not ours.** The package neither carries it nor downloads
it: Node.js 22 is not in Debian 13, and the adapter brings a `claude`
binary that is not ours to redistribute. It carries the role
`lineadicomando.fleet.chat_agent` and a playbook an administrator runs
once, as root. Ansible is there already: no program of ours runs as root
for it, no rule in sudoers. A run of Fleet could not do it on its own
controller (the sandbox has no sudo and cannot write `/opt`); with the
controller reached over ssh, the same role works from a project.

The role fetches Node.js from `nodejs.org`, checked against the published
checksum, and installs the adapter with `npm ci --ignore-scripts` from a
lockfile shipped in the role that names each of its 114 packages with its
hash. The lockfile does not say which C library a package is for, so the
build for musl comes too and is removed. Idempotent;
`chat_agent_state=absent` removes what it wrote.

Naming an agent in `[chat.agents.NAME]` is what turns the chat on, apart
from `[mcp] enabled`, which it also needs: `fleet-mcp` sends data to
nobody, a chat sends what is said in it to the provider of the model. The
role writes that section only when asked (`chat_agent_configure`), between
two marker lines, never over an agent it did not write; it sets `[mcp]
enabled` only when asked (`chat_agent_enable_mcp`), the one key changed in
place in a file the administrator may have edited; the file is written
only if the backend's parser accepts it.

`whoami` says whether an agent is named: the tab is shown to every role
then, and to administrators always. While the chat cannot be used, an
administrator gets a guide: `chat.agents` says in `guide` whether our
agent is installed, an agent named and `[mcp]` enabled, and gives the one
line that does what is missing (the role's flags for what is not done
yet: `chat_agent_configure` would refuse an agent named by hand) and the
login, each to copy; the page asks again every 5 s. It installs nothing
itself: that would take a third entry point as root. For a named agent
under `/opt/cockpit-fleet-agent` that is not installed, `chat.agents`
gives the installing command. Beside each command, a button opens
Cockpit's terminal (`cockpit-system`, as the user of the page) in the
same tab (`cockpit.jump`): the page is left, and an agent it started ends,
but one that is not installed or not logged in does nothing yet, and the
page, opened again from Cockpit's menu, starts it anew; a tab opened for
it would be left behind. Elsewhere (`AtTerminal`: the `fleet` command of
what a page does) the button opens a new tab, at an address built from
the shell's own (`src/lib/shell.ts`), so that leaving does not end a
conversation; outside the shell, `cockpit.jump`.

**The page names the agent, it does not start it.** It spawns `fleet-chat
NAME` (`chat_agent.py`), which runs as the user, asks `chat.start` for the
command line (any role; audited), runs it with its own input and output,
and waits. Before the agent says anything it writes one line in the same
form: `_fleet/ready`, with the user's home and `fleet-mcp` as the server
to give the agent, or `_fleet/error` with the backend's refusal. It sets
`ENABLE_CLAUDEAI_MCP_SERVERS=false`, so that a login to claude.ai brings
no connectors of that account into the session.

**The limit on sessions.** `[chat] max_sessions` (2) is for the whole
controller. `chat.start` counts the `fleet-chat` processes in `/proc`,
the asking one included, which is why `fleet-chat` stays around its agent
instead of becoming it; nothing is recorded. It bounds memory (an idle
session about 360 MB: adapter 93, `claude` 256, `fleet-mcp` 12) and is no
control: a user with a shell can start the agent by hand.

**What a session is given.** `session/new` and `session/load` carry
`fleet-mcp` as the only MCP server, `_meta.disableBuiltInTools` (no shell,
no files), `settingSources: []` and `strictMcpConfig` among the adapter's
options (none of the user's settings, which could add servers or rules
that allow a call unasked), and instructions added to the agent's own.
The client offers neither files nor a terminal. The session's directory
is the user's home.

**Instructions in layers** (`instructions.py`). `chat.start` composes them,
for the page and the terminal alike, and answers with the text and its
layers, each with its source, to be shown: the base (where it is, which
tools, the output of runs is data, no secrets; plain text on the page) and
the profile's, both of the package and not removable; the controller's,
`/etc/cockpit-fleet/assistant.md`, read at each start (over 8 kB or not
UTF-8, the start is refused rather than the file cut: half a rule may say
the opposite of the whole); and the index of the skills
([Agents](agents.md)). No layer of a user, who can say it in the
conversation. `fleet-chat` hands them to the page with `_fleet/ready`,
which appends them to the system prompt, and fills `{instructions}` with
them at a terminal. Instructions are not a control, and go to the
provider with every conversation.

**At a terminal.** `fleet assistant` runs `fleet-chat --terminal NAME`,
which asks `chat.start` with `terminal` for the agent's program for a
terminal (`terminal` in `[chat.agents.NAME]`), writes a private MCP
configuration naming `fleet-mcp` alone, and fills the command line:
`{mcp_config}`; `{allowed_tools}` with the tools `chat.start` says the
profile lets through unasked (`mcp__cockpit-fleet__NAME`, comma-separated,
for `--allowedTools`); `{instructions}` last, so that a placeholder in the
administrator's text stays as it is. It runs it on the user's terminal,
ignoring ^C (the agent's), in `~/.local/state/cockpit-fleet/assistant`
(under `XDG_STATE_HOME` when set; 0700), the same each time: Claude asks
whether to trust the directory it starts in and keeps no answer for a
home. Being a `fleet-chat`, it counts as a session; the configuration is
deleted when the agent ends. For Claude the role names
`--strict-mcp-config`, `--tools ""` and `--setting-sources ""` (what
`_meta` does on the page) and `--permission-mode manual`: without it the
CLI may start in its "auto" mode, which calls tools unasked. `fleet
assistant --profile` passes the profile on (the default without), after
the acceptance of its risks when `chat.agents` lists it in `to_accept`.
The CLI's own "don't ask again" trusts one tool of any class: `limits.md`.

**The profile of a session.** The page offers the profiles `chat.agents`
names, the default first, each with what it may do. For one in
`profiles.RISKY` (beyond `assisted`) not accepted yet (`to_accept`), it
lists its risks, the steering by the nodes among them, and starts only
once a box is ticked: `chat.accept` records user and profile in
`profile_acceptances` (schema 25) and the audit, then `fleet-chat
--profile` is spawned. `chat.start` refuses such a profile not accepted
(`not_accepted`), so the terminal asks too (`fleet assistant` tells the
risks and waits for `yes`). Held in the backend, though consent and not a
control, because the record of who accepted what is the point. The
profile `_fleet/ready` names is shown in the header and in each question,
and the page asks `mcp.tools` for its tools.

**Changing the profile.** "Change the profile", in the header while the
agent is idle, ends the agent and, once its `fleet-chat` has gone (so that
it is not counted), starts another in the profile chosen, which opens the
conversation again (`session/load`) with the `fleet-mcp` and instructions
of that profile, and adds a line saying so. It is a reload with another
profile: `chat.start` checks the profile and its acceptance and audits
the start; nothing new reaches the backend. The dialog tells the risks of
a profile not accepted, and going to one of `RISKY` says that the
conversation, with what the nodes wrote in it, goes on with more powers.
"Always" answers are forgotten. Should `chat.start` find the sessions
full (`sessions_full`, a `conflict`), the old `fleet-chat` may not have
gone yet: the page asks again every second, five times, then says why.
What a new `fleet-mcp` session restarts: `limits.md`, "Chat".

**What the agent launched.** The page draws the session of `fleet-mcp`
for each conversation (`drawSession` in `chat.ts`, when the first message
opens it or an earlier one is opened again) and adds `--session` to the
arguments `_fleet/ready` gave. Above the message box `SessionRuns` shows
`chat.session`: each run not ended as a link to its page, those waiting
for an approval to decide there (`ChatApprovals`); in `autonomous` the
launches of those allowed and, once spent, "Allow N more"
(`chat.grant_runs`, N = `[chat] autonomous_runs`); in any profile, while
runs have not ended, "Stop everything", which asks first, then sends
`session/cancel` and calls `chat.cancel_runs`. In `autonomous` the start
of a session, and the change to it, have a field for the runs of each
conversation (`autonomous_runs`, or what was chosen last). `Chat` keeps
it (`runs`, 0 for config.toml's, which needs no call) and, whenever it
draws a session, calls `chat.limit_runs` (its constructor's third
argument) before `session/new` or `session/load`; a failure stops the
sending, with the error in the conversation, rather than let the agent go
on under another limit. The backend side: [Agents](agents.md), "The person
beside an autonomous agent".

**Telling the agent that runs ended.** An agent works only within a turn:
nothing wakes it once it has answered, so a run that waited for a
confirmation, or a long playbook, ended unseen until the person wrote.
The page is the observer, with no process of its own: `Chat` (outside the
React tree, so it goes on in the other tabs) asks `chat.session`, which
lists beside the runs not ended the last 20 that have, every `WATCH_MS`
(5 s) while the agent answers or runs have not ended. A run seen not
ended while the agent was idle, then ended, is told to it once idle again,
in a prompt of the page (`watch.tell`, in the interface's language, those
ended together in one), shown as "Fleet" and marked by `NOTICE_MARK` so
that a replay shows it the same. Not told: runs that end within the
answer that launched them (the agent followed them, or can), and those
"Stop everything" cancels. The base instructions on the page say what the
message is and not to ask the person to report an end.

**Permissions.** The agent asks before each call
(`session/request_permission`); the page answers as the tool's `question`
in `mcp.tools` says ([Agents](agents.md), "Questions by profile"): alone
for `none`; for `ask` and `every` it shows the tool's description, its
arguments, the profile and the class, and waits: once, always in this
conversation (`ask` only, for the whole class: "always" on a launch
trusts the other launches and cancels), refuse. "Once" on `run_adhoc` is
first recorded in the backend (`chat.consent`), so the command does not
wait to be confirmed again ([Approvals](approvals.md)). "Always" is kept by
the page and answered as "once": the agent's own option writes a rule in
the user's settings. A call naming something `mcp.tools` does not list,
after asking it again, is asked about every time.

**Without questions** (`consent.py`). In a profile of
`profiles.UNATTENDED_PROFILES` (`administrator`), where `[chat]
unattended` allows it, the header has a switch that turns the questions
off, as Claude Code's mode that bypasses permissions. The first time it
tells the risks and records their acceptance (`chat.accept` with
`unattended`, a mode and not a profile, in `profile_acceptances`); then
`Chat.unattend` calls `chat.unattended` (admin, no tool, audited) for the
conversation's session of `fleet-mcp` (or, before the first message, when
the session is drawn: `limit()`, as for the runs of `autonomous`), and
asks `mcp.tools` again with `session`: for a session `unattended_sessions`
(schema 41) holds for the caller, every tool is `none` and the page
answers alone, but those that delete (`profiles.deletes`: the verbs
`delete` and `remove`), which stay `every`: nothing puts back what is
gone. An agent's `run.adhoc` there starts at once, `"unattended":
true` in its options and no row of approval. While on, the message box
says so (`fleet-chat-unattended`, a note under it), and `chat.session`
gives `unattended`. It holds for every conversation of the chat session
(each draws its session and sets it) until switched off (the page asks
again at once, then tells the backend), the profile changes (the page
drops it; the backend holds it only in `administrator`) or the page is
reloaded; `config.toml` takes it back at the next call. Not at a
terminal. What stays: role, ceiling and profile in `_through`, drafts,
approvals of templates, "Stop everything"; stopping an answer cancels
what was waiting. Why no profile of its own and no cap: `decisions.md`,
"Assistant without questions".

**Conversations** are the agent's: the page lists them (`session/list`)
and has the agent replay one (`session/load`) after a reload. The session
lives outside the React tree, so it goes on across the plugin's tabs; a
reload closes the channel, and `fleet-chat`, the agent and `fleet-mcp`
end with it. Answers are plain text. The conversation scrolls in the
height left in the window (measured: what is above it changes), so the
message box stays in sight. What is typed and not sent is kept by `Chat`
(`typed`, outside its state, so a keystroke does not redraw the
conversation): it outlives a visit to another tab and a new conversation
or session, not a reload, and is never in the browser's storage, shared
by the users of a controller.

**Help with a run.** The page of a run that has ended (not rejected or
expired) has "Assistance" when an agent is named and the caller may read
its output (`run.log`; a reading of an inventory only for
administrators). It prepares a message asking the assistant to examine
the run with `run_result` and `run_log`, find anomalies, their causes and
solutions, in the interface's language, and goes to the chat. Nothing is
sent by itself: the message is a draft in the session's state
(`Chat.prepare`), which the composer takes and the user edits and sends,
in the open conversation if any. Before a session starts the page says a
message waits and preselects `diagnostic`, when enabled; the user may
pick another. No command is added: the tools were there, of class `read`.

**Help with a check.** In the checks of a host and in Problems, a check
whose last result is not ok (soft or confirmed) has the same button, for
anyone when an agent is named. The message names check and host with
their ids and what was found, and asks to read them with `check_list` and
`host_get`, the check cycles of that host (`run_list` of kind `check`,
`run_log` with `match`: only for those who may read a run's output), and
the node with ad-hoc commands that only read where the profile allows;
then to say whether it is a real problem or an expected change, its cause
and solutions. A check's state does not keep the run of its last result,
so the assistant finds the cycle by time. Draft and suggestion as for a
run.

**No login to the model.** The adapter says so (`_auth/status_update`,
and error -32000 to a prompt): the page shows the login command of
`config.toml`, to run once in a terminal, with the same button.

Trials and measurements: [docs/history/chat.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/chat.md).
