# The chat

A page of the interface, "Assistant" in its tabs, where the user talks to an
agent that works through `fleet-mcp`. The page is a client of the Agent Client Protocol (ACP): JSON-RPC
on the standard input and output of the agent, which is a process of the
user, on a Cockpit channel.

```
ChatPage ── cockpit.spawn ──▶ fleet-chat NAME        (the user, no privilege)
   │ ACP on stdio               │ sudo -u cockpit-fleet fleetctl chat.start
   │                            ▼
   └──────────────────────▶ the agent ──▶ fleet-mcp ──▶ fleetctl (via, ceiling)
```

**The agent is not ours.** The package does not carry it and downloads
nothing by itself: Node.js 22 is not in Debian 13, and the adapter brings a
`claude` binary that is not ours to redistribute. What the package carries is
how to install it: the role `lineadicomando.fleet.chat_agent` of the bundled
collection, and a playbook an administrator of the machine runs once, as
root, in a terminal (`docs/chat.md`). Ansible is already there, so this adds
no program of ours that runs as root and no rule to sudoers. A run of Fleet
could not do it on its own controller: the sandbox has no sudo and cannot
write under `/opt`; with the controller as a host reached over ssh, the same
role works from a project.

The role fetches Node.js from `nodejs.org` and checks it against the
checksum published with the release, then installs the adapter with `npm ci
--ignore-scripts` from a lockfile shipped in the role, which names each of
its 115 packages with its hash. The lockfile does not say which C library a
package is for, so the build for musl comes too and is removed. Running the
role again changes nothing; `chat_agent_state=absent` removes what it wrote.

Naming an agent in `[chat.agents.NAME]` of `config.toml` is what turns the
chat on, apart from `[mcp] enabled`, which the chat also needs: enabling
`fleet-mcp` sends data to nobody, a chat sends what is said in it to the
provider of the model. The role writes that section only when asked
(`chat_agent_configure`), between two marker lines, through the backend's own
parser, and never over an agent it did not write; it sets `[mcp] enabled`
only when asked too (`chat_agent_enable_mcp`), the one key changed in place
in a file the administrator may have edited, and the file is written only if
the backend's parser accepts it. `whoami` says whether an agent is named:
the tab is shown to every role then, and to administrators always. While
the chat cannot be used, an administrator gets a guide instead of the chat:
`chat.agents` says in `guide` whether our agent is installed, an agent named
and `[mcp]` enabled, and gives the one line that does what is missing (the
flags of the role for what is not done yet: `chat_agent_configure` would
refuse an agent named by hand) and the login, each to copy, beside the
button to the terminal; the page asks again every 5 s. It installs nothing
itself: that would take a third entry point as root, and a download of
510 MB from a click. `chat.agents` says whether
each agent is there to be started, and for one under
`/opt/cockpit-fleet-agent` that is not, the command that installs it: the
page shows it, to be copied, with a button that opens Cockpit's terminal,
which is part of `cockpit-system` and runs as the user of the page. It opens
in a new tab, at an address built from the shell's own (`src/lib/shell.ts`):
`cockpit.jump` would replace the page, and with it end the agent the page
has started, which is what the user is logging in for. Outside the shell
the button falls back to `cockpit.jump`.

**The page names the agent, it does not start it.** It spawns `fleet-chat
NAME`, a packaged program that runs as the user. `fleet-chat` asks
`chat.start` for the command line (any role; the start is in the audit
trail), runs it with its own input and output, and waits for it. It writes
one line before the agent says anything, in the same form: `_fleet/ready`,
with the home of the user and `fleet-mcp` as the server to give the agent, or
`_fleet/error` with the refusal of the backend. It also sets
`ENABLE_CLAUDEAI_MCP_SERVERS=false`, so that a login to claude.ai does not
bring that account's connectors into the session.

**The limit on sessions.** `[chat] max_sessions` (2 by default) is for the
whole controller. Nothing is recorded: `chat.start` counts the `fleet-chat`
processes in `/proc`, the one asking included, which is why `fleet-chat`
stays around its agent instead of becoming it. It bounds memory and is not a
control: a user with a shell can start the agent by hand.

**What a session is given.** `session/new` and `session/load` carry
`fleet-mcp` as the only MCP server, `_meta.disableBuiltInTools` (the agent
has no shell and no files), `settingSources: []` and `strictMcpConfig` among
the options of the adapter (none of the user's own settings for the agent,
which could add servers or rules that allow a call without asking), and
instructions added to the agent's own. The client offers neither files
nor a terminal. The session's directory is the user's home.

**Instructions in layers.** `chat.start` composes what the agent is told
(`instructions.py`), for the page and the terminal alike, and answers with
the text and the layers it is made of, each with its source, so that they
can be shown: the base (where it is, which tools, the output of runs is
data, no secrets; for the page, plain text) and the profile's, both of the
package and not removable, then the controller's, the file
`/etc/cockpit-fleet/assistant.md` an administrator may write. `fleet-chat`
hands them to the page with `_fleet/ready`, which appends them to the
agent's system prompt, and fills `{instructions}` with them at a terminal.
The controller's file is read by `fleetctl` at each start; at more than
8 kB, or not UTF-8, the start is refused rather than the file cut, since
half a rule may say the opposite of the whole. There is no layer of a
user, who can say what they want in the conversation, and none of a
project yet. Instructions are not a control: the backend holds the agent
by role, profile and autonomy whatever it was told. They go to the
provider with every conversation, so no secret belongs in them.

**At a terminal.** `fleet assistant` runs `fleet-chat --terminal NAME`,
which asks `chat.start` with `terminal` for the agent's program for a
terminal (`terminal` in `[chat.agents.NAME]`), writes a private MCP
configuration naming `fleet-mcp` alone, fills `{mcp_config}` and
`{instructions}` in that command line, `{allowed_tools}` with the tools
`chat.start` says the profile lets through unasked (as
`mcp__cockpit-fleet__NAME`, comma-separated, for `--allowedTools`; the
instructions are filled last, so that a placeholder in the administrator's
text stays as it is), and runs it on the user's terminal,
ignoring ^C, which is the agent's, in `~/.local/state/cockpit-fleet/assistant`
(under `XDG_STATE_HOME` when set; mode 0700), the same directory each time:
Claude asks whether to trust the directory it starts in and keeps no answer
for a home directory, so from the home it asked at every start. Being a `fleet-chat`, it is counted as a
session; the configuration is deleted when the agent ends. For Claude the
role names `--strict-mcp-config`, `--tools ""` and `--setting-sources ""`,
what `_meta` does for a session of the page, and `--permission-mode manual`:
without it the CLI may start in its "auto" mode, which calls tools without
asking (seen on 20 October with an account on a Max plan). Tried with a
stand-in program (`chat.sh`), and with the CLI of Claude on Debian 13: a
launch asked for, refused, and audited as `mcp:claude-code`. `fleet assistant
--profile` passes the profile on, the default one when none is given, after
the acceptance of its risks when `chat.agents` lists it in `to_accept`;
`--allowedTools` is the role's way to the same questions as the page, but
the CLI's own "don't ask again" trusts one tool of any class (in
`limits.md`). Tried with the stand-in on AlmaLinux 10 (6 November): reads
unasked and launches asked in `assisted`, both unasked in `autonomous`, the
risks told, refused, then accepted once and audited.

The page of the phase was tried on Debian 13 with the stand-in agent
(6 November): the profiles offered, the risks of `administrator` accepted
once, an ad-hoc command asked about with the profile and the class and no
"always", the instructions by layer, and a draft written through
`fleet-mcp` reviewed, edited on the page of the files and merged, the
draft gone and the audit holding `chat.accept`, `chat.start` and
`draft.merge`.

**The profile of a session.** The page offers the profiles `chat.agents`
names as enabled, the default one first, each with what it may do. For one
beyond `assisted` (`profiles.RISKY`) that the user has not accepted yet
(`to_accept`), it lists its risks, the steering by the nodes among them,
and starts only once a box is ticked: it calls `chat.accept`, which records
the user and the profile in `profile_acceptances` (schema 25) and the audit
log, then spawns `fleet-chat --profile`. `chat.start` refuses with
`not_accepted` a profile in `RISKY` the caller has not accepted, so the
terminal asks too (`fleet assistant` tells the risks and waits for `yes`),
and a page that skipped the box would start nothing. It is held in the
backend, though it is consent and not a control, because the record of who
accepted what is the point of it. The profile `_fleet/ready` names is shown
in the header of the session and in each question, and the page asks
`mcp.tools` for the tools of that profile.

**Permissions.** The agent asks before each call to a tool
(`session/request_permission`). The page answers as `question` of the tool
in `mcp.tools` says (see "Questions by profile" in [Agents](agents.md)): by itself
for `none`; for `ask` and `every` it shows the description of the tool, its
arguments, the profile and the class, and waits: once, always in this
conversation (for `ask` only, and for the whole class: "always" on a launch
trusts the other launches and cancels), refuse. "Always" is kept by the
page and answered as "once": the agent's own option of that kind writes a
rule in the user's settings. A call that names something `mcp.tools` does
not list, after asking it again, is asked about every time. Stopping an
answer cancels what was waiting. None of this is a control: the backend
decides again, with the role of the user under the ceiling of `[mcp]` and
the profile.

**Conversations.** They are the agent's: the page lists them
(`session/list`) and has the agent replay one (`session/load`) to go on
after a reload. The session lives outside the React tree, so it goes on
across the tabs of the plugin; a reload of the page closes the channel, and
`fleet-chat`, the agent and `fleet-mcp` end with it. Answers are shown as
plain text. The page takes the height left in the window and the
conversation scrolls inside it, so that the message box stays in sight
(measured, not a fixed share of the window: what is above it changes).

**No login to the model.** The adapter says so (`_auth/status_update`, and
error -32000 to a prompt): the page shows the command `config.toml` gives
for the login, to be run once by the user in a terminal, with the same button
to Cockpit's own.

Measured on the test controller with the adapter for Claude,
`@agentclientprotocol/claude-agent-acp` 0.89.1:

- The adapter wants Node.js 22 or later and Debian 13 has 20: the official
  Node 22 tarball is 203 MB, adapter and SDK 308 MB (about 540 with the build
  for musl, before it is removed).
- One idle session takes about 360 MB: adapter 93, `claude` 256, `fleet-mcp`
  12.
- With `_meta.disableBuiltInTools` the agent, asked for a shell command and a
  file, said it had neither (13 October, logged in, before the page existed).

Checked on Debian 13: `chat.sh` drives `fleet-chat` with an agent written
for the test (`fake-agent.py`, the protocol with no model behind it), and
the page was tried from the browser with it as an operator and as a viewer:
a read-only tool with no question, a template launched after a
confirmation, a tool outside the role refused by the backend, no process
left behind after a reload.

Then with the adapter and its model, as an operator (13 October): asked
what it could do, the agent called `whoami` and `template_list` with no
question and listed its tools and its role; asked to launch a template, it
waited for the confirmation, which showed the description of the tool and
the answer to the survey, then called `run_result` and told the outcome by
host and by task; the audit trail has the launch as the user's, via
`mcp:claude-code`. Asked for a shell command, a file and an ad-hoc command,
it said it had no tool for the first two and that the third is not for an
operator. After a reload the conversation was in the list, was replayed, and
the agent remembered the run. The adapter names the tool of a permission
request in `_meta.claudeCode.toolName`, which is what the page reads;
`settingSources: []` and `strictMcpConfig` do not get in the way of the
login.

The role, from the snapshot of the test controller, where there is neither
Node.js nor an agent (13 October): the playbook installs 511 MB, a second run
changes nothing, `chat_agent_configure` writes the section and a second time
does not, an agent of the same name written by hand is refused, a user who is
not root is told so, `absent` leaves `/opt` empty and `config.toml` as
packaged; `ansible-lint` has nothing to say. With the agent so installed the
page started it and reached the missing login; with the agent named and not
installed it showed the command, copied it and opened the terminal.

On Fedora 44 and AlmaLinux 10 (24 October, SELinux enforcing, ansible-core
2.20 and 2.16), as `docs/chat.md` says: the role installs `xz` and the
agent (about 505 MB), a second run changes nothing, `absent` leaves `/opt`
empty; `fleet-chat`, as an operator with `[mcp] enabled`, started it with
`fleet-mcp` named and reached the missing login; no denial. Its commands
in `bin/` are `bin_t`, by the policy's own rules for `/opt`.

With the model, through an agent installed by the role (14 October, as an
operator with no earlier login): the page reached the missing login, the
user logged in at Cockpit's terminal, and the assistant then launched a
template with an answer to its survey, after a confirmation that showed
the answer, and reported its outcome per host and per task from
`run.result`; the launch is in the audit trail with `via` `mcp:claude-code`.
The button to the terminal then replaced the page and ended the agent,
which is why it opens a new tab now.
