# Agents

`fleet-mcp` is the backend as a Model Context Protocol server: JSON-RPC on
its standard input and output, one message per line, tools only. A user
starts it, on the controller or as the command of an ssh connection, and it
runs as that user with no privilege of its own. Every tool is one call to
`fleetctl` through sudo, exactly what the interface does, so there is still
one way into the backend, and roles and the audit trail are the same. It
uses the standard library only and listens on nothing.

```
agent ── JSON-RPC on stdio ──▶ fleet-mcp          (the user, e.g. over ssh)
                                 │ sudo -u cockpit-fleet fleetctl COMMAND
                                 │   < {…arguments, "via": "mcp:<client>"}
                                 ▼
                               fleetctl            role = min(user's, ceiling)
```

**The ceiling.** `[mcp]` in `config.toml` has `enabled`, off by default, and
`max_role`, `operator` by default. `fleet-mcp` adds `via` to the JSON of every
call. When `fleetctl` finds it, it refuses everything while agents are not
enabled, and lowers the caller's role to the ceiling for that call, never
raises it; the audit record and the journal (`FLEET_VIA`) say where the call
came from. The reason for a ceiling below what the person may do: the output
of a run is written by the nodes, and an agent that reads it and may also
write files and launch anything can be steered by what a node prints.

Only a `via` that is `mcp` or starts with `mcp:` is an agent's; any other
value, such as the `cli` of the command `fleet` (see [The terminal](terminal.md)), is
recorded and changes nothing else. Saying `via` can only restrict, and that
is all it is: a declaration. A user
with a shell can call `fleetctl` without it, with their whole role, which is
no more than they could do by hand. It binds an agent only when the agent
cannot reach anything but `fleet-mcp`: a key of its own in `authorized_keys`
with a forced command,

```
restrict,command="/usr/libexec/cockpit-fleet/fleet-mcp" ssh-ed25519 AAAA… agent
```

which gets `fleet-mcp` whatever it asks for (`mcp.sh` tries), or, stricter
and with no setting at all, a Unix account for the agent in the role group it
should have.

**Tools.** `mcp.tools` gives `fleet-mcp` what to offer: one tool per command
the caller's role allows under the ceiling (`run.start` becomes `run_start`),
with the first lines of its documentation as description and its declared
parameters as `inputSchema`. Tools above the ceiling are not listed, and
`fleetctl` refuses them anyway. A few commands are not tools: the upload of an
archive, the list of all modules and the raw events, which are too large to be
useful to an agent, and those only the backend calls. There is no tool for
secrets, because `fleet-secret` is not a command of `fleetctl`: an agent may
create a credential, a person enters its value.

**Classes of tools.** Every command declares what it lets an agent do, one
of five classes from the least to the most: `read`; `run` (start, stop and
follow work already defined: launches, cancels, checks, schedules turned on
or off, acknowledgements, maintenance windows, a syntax check); `adhoc` (any
module on the nodes); `config` (create and change templates, inventories,
schedules, rules, channels, checks, sets of variables, workflows); `code`
(the files of projects, projects themselves, credentials, host keys,
`config.import`, and every delete). The default comes from the declaration:
a command that changes nothing reads, one that changes something is `run`
for an operator and `config` for an administrator; the others say theirs
(`agent=` in `@command`). `mcp.tools` gives each tool its class, a
template's being `run`. The profiles of the assistant are made of these
classes; a test holds the table of them, so moving a command to another
class is a decision.

**Profiles of the assistant.** A profile is a set of classes
(`profiles.py`): `observer` reads, `assisted` and `autonomous` also run,
`diagnostic` also `adhoc-diagnostic`, which is not the class of a command
but of a call (`run.adhoc` with a module of `[mcp] adhoc_modules`, without
become), `administrator` has them all. `fleet-chat --profile` asks
`chat.start` whether the profile is enabled (`[chat] profiles`) and starts
`fleet-mcp --profile`, which adds it to every call beside `via`. In
`_through`, after the ceiling lowers the role, a call whose class the
profile does not allow is refused and audited; a call with no profile gets
`default_profile`, one with a profile not enabled is refused. So the
profile, like `via`, can only restrict: naming another gains nothing that
the default does not give, and leaving `via` out is what a person does by
hand with the same role. `mcp.tools` lists only the tools of the profile,
`run_adhoc` in `diagnostic` with the enum of its modules and without
`become`. The audit has a `profile` column (schema 22), and the journal
`FLEET_PROFILE`.

**Questions by profile.** How a person is asked about a call is the
backend's too: `profiles.question` gives, for a profile and a class, `none`
(made unasked: reading; `run` in `autonomous`, which autonomy holds;
`adhoc-diagnostic` in `diagnostic`), `every` (asked every time, never
trusted for a conversation: `adhoc`, `config`, `code`, in any profile) or
`ask` (asked, and "always" trusts the class for the conversation).
`mcp.tools` gives it with each tool, for the page, and `chat.start` gives
the names of the tools that are `none`, for the agent's program at a
terminal. One table, so that the page and the terminal cannot drift apart;
and a floor, not a control, since `_through` decides each call again.

**Autonomy.** In a profile nobody confirms (`profiles.AUTONOMOUS`),
`dispatch` hands the call, once the role allows it, to `autonomy.hold`:
nothing that silences a problem (`state.ack`, `state.unack`,
`maintenance.*`); launches (templates, ad-hoc, workflows and their resume,
schedules, checks) only with a session, at most `[chat] autonomous_runs` of
them, none on an inventory with a maintenance window open, and with
`autonomous_dry_run` a template's run in check mode and no workflow or
schedule, which have no dry run. The session is drawn by `fleet-mcp` when
it starts and added to every call; the audit records it (column `session`,
schema 23) and the launches are counted from there, so there is no counter
to keep in step and an agent cannot reset it. A fleet-mcp started again is
a new session: what a person with a shell could do by hand anyway. Rights
are taken away from agents, so a run that needs approval waits for a
person.

**Drafts.** An agent never writes to `main` of a managed project or
inventory: `file.write`, `file.change`, `file.delete`, `file.move` and
`file.restore` called by an agent go to the branch `drafts/SESSION` of the
same repository, made at its first write from where `main` is then
(`files.place`). The branch is checked out in a work tree of its own,
`drafts/SESSION/tree` beside the project's `tree`, with its own index
(`GIT_INDEX_FILE`), and committed with `write-tree`, `commit-tree` and
`update-ref`, so `main`, its index and `HEAD` are never touched; a write
that fails before the first commit leaves no draft. No run reads a draft:
templates, schedules, checks and the readings of inventories copy the
project's own tree, which is `main`. What follows a write in a draft is a
syntax check of the draft, a run whose `options` name it; collections and
inventories wait for `main`. While its draft exists an agent reads it
(`file.list`, `file.read`, `file.history`, `file.check`), so the digest it
quotes is the draft's; another session's draft it may read by naming it
(`draft`), never write. Without a session there is nothing to write to,
and an agent's write is refused.

`drafts.py` is the person's side: `draft.list` (every draft, or those of a
project, with how many files each changes and how many main changed too),
`draft.diff` (the changes path by path, the unified diff from where the
draft started, the last syntax check of each file), `draft.merge` and
`draft.discard`. The last two are `code` and not tools, so `_through`
refuses them to an agent: a program does not approve its own code. A
merge quotes the revision of the draft that was reviewed, and takes all
or some of its paths into `main` through `change()`, as one commit of the
person, with what any change of `main` brings (checks, collections,
inventories read again); the draft goes once `main` has all of it. It is
taken file by file, not line by line: a file goes in as the draft has it
if `main` still has it as the draft found it (the merge base of the
branch and `main`), and is skipped if `main` has it as the draft does; a
file changed on both sides is a conflict, refused, which a person settles
by editing the draft (`draft` on the file commands) or `main`. Directories
a merge empties go, unless the draft keeps them. A line-by-line merge was
left: a person reviews whole files anyway, and git's merge would write
conflict markers into a playbook.

In the interface (`src/components/Drafts.tsx`) an administrator is told of
the drafts waiting on the page of the assistant, looked at again after
each answer, and on the files of their project or inventory. The review
shows `draft.diff`, every change chosen but those in conflict, and merges
what is chosen (all, or the paths) with the revision it showed. "Edit the
draft" opens the page of the files at `projects/ID/drafts/SESSION`, where
every file command carries `draft`: what a person saves goes to the draft,
under their name, and its syntax checks check the draft. Importing an
archive and downloading are left out there, since they read and write
`main`. At a terminal `fleet draft diff` writes the same review as text,
ending with the `fleet draft merge` line that quotes the revision shown;
the draft commands take the name of a managed project or inventory.

**Templates as tools.** Each job template is also a tool of its own,
`launch_<name>_<id>`, for whoever may launch it: its description says what it
runs and where, and its parameters are the questions of its survey, typed and
bounded as the survey is, plus the limit and the dry run. The tool fixes the
template: what it fixes and `via` are set by `fleet-mcp` after the agent's
arguments, so a call cannot replace them. A template that cannot start
without a secret is not offered; an optional secret question is left out.
This is what stands in for the MCP servers a project may carry, which are
never started: they would run Ansible outside the sandbox.

**Reading a run.** `run.result` answers with the status of a run, the outcome
per host and, task by task, how many hosts went each way and what was said
where one failed, cut to 500 characters; with `wait` it answers when the run
ends or after at most 50 seconds, so an agent follows a run with one call at
a time and no polling loop of its own. It is built from the events, not from
the output. The raw output stays a separate tool, `run_log`, in chunks, whose
description says to read it only when asked.

Checked on Debian 13 (`mcp.sh`), with a client written for the test, and
from Claude Code 2.1 over ssh with a forced-command key: five turns to launch
a template, wait for it and report host and tasks.
