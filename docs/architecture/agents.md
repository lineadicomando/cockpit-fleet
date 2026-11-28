# Agents

`fleet-mcp` is the backend as a Model Context Protocol server: JSON-RPC on
its standard input and output, one message per line, tools only. A user
starts it, on the controller or as the command of an ssh connection, and it
runs as that user with no privilege of its own. Every tool is one call to
`fleetctl` through sudo, as the interface makes it: one way into the
backend, the same roles and audit trail. Standard library only; it listens
on nothing.

```
agent ── JSON-RPC on stdio ──▶ fleet-mcp          (the user, e.g. over ssh)
                                 │ sudo -u cockpit-fleet fleetctl COMMAND
                                 │   < {…arguments, "via": "mcp:<client>"}
                                 ▼
                               fleetctl            role = min(user's, ceiling)
```

**The ceiling.** `[mcp]` has `enabled` (false) and `max_role` (`admin`).
`fleet-mcp` adds `via` to every call; `fleetctl`, finding it, refuses
everything while agents are not enabled and lowers the caller's role to
the ceiling, never raises it; the audit record and the journal
(`FLEET_VIA`) say where the call came from. Why an administrator may set
it below the person's role: the output of a run is written by the nodes,
and an agent that reads it and may also write files and launch anything
can be steered by what a node prints.

Only a `via` that is `mcp` or starts with `mcp:` is an agent's; any other,
such as the `cli` of `fleet` ([The terminal](terminal.md)), is only
recorded. `via` is a declaration that can only restrict: a user with a
shell calls `fleetctl` without it, with no more than they could do by hand.
It binds an agent only when the agent reaches nothing but `fleet-mcp`: a
key of its own with a forced command (`mcp.sh` tries it),

```
restrict,command="/usr/libexec/cockpit-fleet/fleet-mcp" ssh-ed25519 AAAA… agent
```

or, stricter and with no setting, a Unix account in the role group it
should have.

**Tools.** `mcp.tools` gives `fleet-mcp` one tool per command the caller's
role allows under the ceiling (`run.start` becomes `run_start`): the first
lines of its docstring as description, its declared parameters as
`inputSchema`. Not tools (`tool=False`): what moves files or archives
(`project.upload`, `file.bundle`/`import`/`push`/`download`, `store.*` but
listing, `doc.put`/`get`), what is too large (`module.list`,
`run.events`), what only the backend or the page calls (`mcp.tools`,
`chat.*`, `run.reconcile`, `scheduler.tick`, `notification.deliver`,
`doc.render`), and a person's decisions (`approval.approve`/`reject`,
`draft.merge`/`discard`, `skill.approve`). `_through` refuses a call of
any of them from an agent. No tool for secrets: `fleet-secret` is not a
command of `fleetctl`; an agent may create a credential, a person enters
its value.

**Classes of tools.** Every command declares what it lets an agent do
(`agent=` in `@command`, `cli.AGENT_CLASSES`), from the least to the most:
`read`; `run` (start, stop and follow what is defined: launches, cancels,
checks, schedules on or off, acknowledgements, maintenance windows, a
syntax check, an inventory read again, a notification retried); `scan`
(`netscan.start`, [The network](network.md)); `adhoc` (`run.adhoc`);
`config` (create and change templates, inventories, schedules, rules,
channels, checks, sets of variables, workflows, what a scan adds to an
inventory); `code` (files of projects, projects, credentials, host keys,
the store's writes, `config.import`, and every delete but
`maintenance.delete`, of `run`, and `doc.delete`); and
apart, `doc`: changes to the documents ([Documents](documents.md)),
allowed in every profile, since a text is not run and has its history.
Undeclared, a command that changes nothing is `read`, one that changes
something `run` for an operator and `config` for an administrator. A
template's tool is `run`. `CLASS_TABLE` in `test_agents.py` holds the
table, so moving a command is a decision.

**Profiles of the assistant** (`profiles.py`): sets of classes.

| Profile | Classes |
|---|---|
| `observer` | read, doc |
| `assisted` | read, run, doc |
| `diagnostic` | read, run, scan, adhoc-diagnostic, doc |
| `administrator` | all |

`adhoc-diagnostic` is the class of a call, not of a command: `run.adhoc`
with a module of `[mcp] adhoc_modules`, without become, and no `{{`, `{%`
or `{#` in its arguments (Ansible would read them as templates, and a
lookup runs a command on the controller, in the run). `fleet-chat
--profile` asks `chat.start` whether the profile is enabled (`[chat]
profiles`) and starts `fleet-mcp --profile`, which adds it to every call
beside `via`. In `_through`, after the ceiling, a call outside the
profile is refused and audited; a profile not enabled is refused; no
profile gets `default_profile`. A profile is offered only to an agent
whose role (the caller's under the ceiling) it does more for than one
below it (`profiles.LEAST_ROLE`): `diagnostic` and `administrator` from
admin, `assisted` from operator, `observer` from viewer. A viewer
launches nothing and writes no document, so of the profiles enabled it
gets one, `observer` or else `assisted` (`FALLBACK`); an operator runs no
ad-hoc command and scans nothing. Below that role a profile is refused
there, left out of `chat.agents` (and of its risks to accept), refused by
`chat.accept` and `chat.start`, and the default falls to `assisted` (or
the first profile the role may have). So a profile, like `via`, only
restricts. `mcp.tools` lists the tools of the
profile only, `run_adhoc` in `diagnostic` with an enum of its modules and
no `become`. Audit column `profile` (schema 22), journal `FLEET_PROFILE`.
`autonomous`, a profile until 0.5, is an alias (`profiles.ALIASES`) that
`fleet-mcp` started by hand may still name: `_through` makes it `assisted`
in the mode `autonomous` for every call, where `[chat] autonomous` allows
it; `chat.start` refuses it, so page and terminal never start one.

**Questions by profile.** `profiles.question` gives, for a profile, a
class and the mode of the session, how a person is asked: `none`
(reading; `run` and `doc` in the mode `autonomous`, which autonomy holds;
`adhoc-diagnostic` in `diagnostic`; everything but deletes in the mode
`unattended`), `every` (never trusted for a conversation: `scan`,
`adhoc`, `config`, `code` in any profile, and `doc.delete`,
`ALWAYS_ASKED_COMMANDS`, but in a mode) or `ask` ("always" trusts the
class for the conversation). `mcp.tools` gives it with each tool, for the
page; `chat.start` gives the `none` tools for the agent's program at a terminal.
One table, so page and terminal cannot drift; a floor, not a control:
`_through` decides each call again. `mcp.tools` with `session` gives the
questions of the mode the caller turned on in that session.

**Modes without questions** (`profiles.MODES`). A person turns the
questions off for a session of `fleet-mcp` with `chat.unattended`
(operator, no tool, audited), naming its profile: a row of
`unattended_sessions` (schema 41) with that profile (schema 42). The mode
is the profile's, `autonomous` in `assisted` (`[chat] autonomous`) and
`unattended` in `administrator` (`[chat] unattended`), each accepted once
by its name (`chat.accept`, in `profile_acceptances`). `_through` sets
`ctx.mode` (`autonomy.mode`) when the session's row names the profile of
the call and config.toml still allows its mode: so a row turned on in one
profile is nothing in another, and an agent, which cannot call
`chat.unattended`, cannot turn one on. In `autonomous` launches and
documents are asked about no more, held by autonomy (below); in
`unattended` nothing is asked but what deletes (`profiles.deletes`: the
verbs `delete` and `remove`), every time, since nothing puts back what is
gone, and an agent's ad-hoc command does not wait for its person
(`consent.py`). The audit records the mode beside the profile (column
`mode`, schema 42; journal `FLEET_MODE`). Turning it on or off needs no
new agent: the page asks `mcp.tools` again ("Without questions",
[The chat](chat.md)).

**Autonomy.** In the mode `autonomous` (`profiles.AUTONOMOUS`),
`dispatch` hands the call, once the role allows it, to `autonomy.hold`:
nothing that silences a problem (`state.ack`, `state.unack`,
`maintenance.*`); no `schedule.enable`, whose runs would outlive the
session and its count; launches (`autonomy.LAUNCHES`: templates, ad-hoc,
workflows and their resume, schedules, checks) only with a session, at
most `[chat] autonomous_runs`, none on an inventory with a maintenance
window open, and with `autonomous_dry_run` templates in check mode and no
ad-hoc command, workflow or schedule (they have no dry run); changes to the documents
(`autonomy.DOCUMENT_CHANGES`) only with a session, at most `[chat]
autonomous_writes`, with no grant (a person writes beside the agent
instead). The session (16 hex digits) is drawn by `fleet-mcp` and added to
every call; the audit records it (column `session`, schema 23) and both
counts come from there: no counter to keep in step, none an agent can
reset. Another `fleet-mcp` is another session, which whoever can start one
could do by hand anyway. A run that needs approval still waits for a
person: an agent has no rights.

**The person beside an agent acting without asking** (schema 35). The chat page draws
the session for each conversation and gives it to `fleet-mcp`
(`--session`), to follow what the agent launched. Naming a session gains
nothing: `fleetctl` takes any session an agent's call names, and counts
and grants are by session and user. `chat.session` (viewer) gives the
launches counted, those allowed and the runs not ended.
`chat.limit_runs` (operator, schema 36) sets the base of a session and
user in `session_limits`, in place of `[chat] autonomous_runs`: what the
user chose when turning the questions off, lower or higher, set before
the mode is (and for each later conversation, before the agent is told
anything); no ceiling of the administrator's, since grants have none.
`chat.grant_runs` (operator) adds to it in `session_grants`
(`autonomy.allowed`). `chat.cancel_runs` (operator) cancels the user's
runs of the session not ended, by `runs.session`, set when an agent's call
queues a template's run, an ad-hoc command, a workflow run or a scan
(`runs.launch`, `run.adhoc`, `workflows._start`, `netscan.start`). Not
tagged: the steps of a workflow run (`jobs.cancel` of the workflow run
cancels them, and a step queued by `reconcile` during another session's
call must not count as that session's), and check cycles (`check.run`
plans every check due). The three are no tools: allowing more is a
person's decision. When its runs are spent the refusal, and the profile's
instructions, tell the agent to say so and wait; the page offers "Allow N
more", N being `[chat] autonomous_runs`. Why a limit and not a stop
button alone: [decisions.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/decisions.md), "Assistant profiles".

**Drafts.** An agent never writes to `main` of a managed project or
inventory: its `file.write`, `file.change`, `file.delete`, `file.move`,
`file.restore`, and the files `inventory.create` and `inventory.update`
write in a managed inventory (main file, files given, a starter's), go to
the branch `drafts/SESSION`, made at the first write from where `main` is
then (`files.place`). It is checked out in `drafts/SESSION/tree` beside
the project's `tree`, with its own index (`GIT_INDEX_FILE`), and committed
with `write-tree`, `commit-tree` and `update-ref`: `main`, its index and
`HEAD` are never touched; a write that fails before the first commit
leaves no draft. No run reads a draft (templates, schedules, checks and
inventory readings copy `main`), but a syntax check of it, a run whose
`options` name it; collections and inventories wait for `main`. While its
draft exists an agent reads it (`file.list`, `file.read`, `file.history`,
`file.check`), so the digest it quotes is the draft's; another session's
draft it may read by naming it (`draft`), never write. Without a session
an agent's write is refused. The row of an inventory (name, path, sets of
variables) is no file and changes at once, so an agent may not change the
format of a managed inventory (it names the main file, which would not
match `main` until a merge). An inventory an agent creates has nothing in
`main` until then; its answer names the draft.

`drafts.py` is the person's side (admin): `draft.list` (with how many
files each changes and how many `main` changed too), `draft.diff` (path by
path, the unified diff from where the draft started, the last syntax check
of each file), `draft.merge` and `draft.discard` (`code`, no tools: a
program does not approve its own code). A merge quotes the revision
reviewed and takes all or some paths into `main` through `change()`, one
commit of the person, with what any change of `main` brings (checks,
collections, inventories read again); the draft goes once `main` has all
of it. File by file: a file goes in if `main` still has it as the draft
found it (the merge base), is skipped if `main` has it as the draft does,
and is a conflict, refused, if both changed it; a person settles it by
editing the draft or `main`. Directories a merge empties go, unless the
draft keeps them. Not line by line: a person reviews whole files, and
git's merge would write conflict markers into a playbook.

In the interface (`Drafts.tsx`) an administrator is told of waiting
drafts on the page of the assistant (looked at again after each answer)
and on the files of the project or inventory. The review shows
`draft.diff`, every change chosen but conflicts, and merges what is chosen
with the revision shown. "Edit the draft" opens the files at
`projects/ID/drafts/SESSION`, where every file command carries `draft`:
the person's saves and syntax checks go to the draft; importing an archive
and downloading, which touch `main`, are left out. `fleet draft diff`
writes the same review as text, ending with the `fleet draft merge` line
quoting the revision; draft commands take the name of a managed project
or inventory.

**Skills** (`skills.py`). What a project, its collections and the
controller teach the assistant, as data the backend reads, never code it
runs: `.fleet/skills/NAME/SKILL.md` in a project's tree, in each
collection under `collections/<digest>/ansible_collections/NS/NAME/`, and
`/etc/cockpit-fleet/skills/NAME/SKILL.md`; and `skills/NAME/` of the
documents, which an agent may write and whose last change by an agent
waits for a person's `skill.approve` ([Documents](documents.md)). Claude's
format, so one file serves Claude Code and the assistant; a dot directory
survives `ansible-galaxy collection build`. The frontmatter is read for
`name` and `description` only (plain, quoted, or a `>`/`|` block), not as
YAML; the name must be the directory's. `chat.start` adds the index as a
layer after the controller's (`instructions.layers(..., skills=)`), 16 kB
at most, the rest left to `skill.list`; `skill.read` (viewer, `read`)
gives `SKILL.md` or a `.md`/`.txt` beside it, 64 kB at most, each step
opened with `O_NOFOLLOW` (a project's tree may hold links, and fleetctl
can read the database). Sources: `controller`, `project:ID`,
`collection:ID:NS.NAME`, `documents`. Two tools whatever the number of
skills, so the list of tools does not grow.

**Templates as tools.** Each job template is also a tool,
`launch_<name>_<id>`, for whoever may launch it: its description says what
it runs and where; its parameters are the survey's questions, typed and
bounded as the survey is, the limit, the dry run, and `inventory_id` when
it allows more than one inventory (an enum, the description naming each).
What the tool fixes and `via` are set by `fleet-mcp` after the agent's
arguments, so a call cannot replace them. A template that cannot start
without a secret is not offered; an optional secret question is left out.
Skills and templates stand in for the MCP servers a project may carry,
which are never started: they would run Ansible outside the sandbox.

**Reading a run.** `run.result` gives the status, the outcome per host
and, task by task, how many hosts went each way and what was said where
one failed (500 characters); with `wait` it answers when the run ends or
after 50 s at most, so an agent follows a run one call at a time. Built
from the events, not the output. The output is `run_log`, whose
description says to read `run_result` first, then search, and read it all
only when asked. For an agent it answers 16 KiB at a time, cut at a line,
without colour codes, with `size`: the page's 256 KiB chunk overflows what
Claude Code takes from a tool (giving the agent `Read` for the file it
then writes is ruled out: it reads any file of the user's). With `match`
(any caller): the lines that hold a text, any
case, from `offset`, `context` lines around each (2, at most 10), groups
split by `--`, at most 100 matches of 500 characters; `more` and `offset`
say where to go on.

Trials: [docs/history/agents.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/agents.md).
