# cockpit-fleet — plan

A Cockpit plugin that runs Ansible from the browser (in the style of Semaphore
UI) and that collects, stores and notifies on data from the nodes using Ansible
only. It does not replace Icinga: nothing in real time, alerts that are
"reasonably timely".

## Status

Phases 0, 1, 2, 3, 3b (agents and the chat, "Assistant" in the interface),
4 (schedules) and 5 (notifications) are closed, the last on 15 October
2026. On 14 October `fleet` was written, the backend for a person at a terminal
(`docs/architecture.md`, "The terminal"); the rest of what the terminal
could give is under "The terminal" below.

On 15 October the controller was extended to Fedora 44 and AlmaLinux 10:
an `.rpm` (`make rpm`) and two test VMs, every integration script passing
on both (`docs/architecture.md`, "Other distributions"), and the minimum of
ansible-core went down to 2.16, AlmaLinux's, for both packages and
`selfcheck`. What is left of it is under "Open points", in the leftovers and
in phase 10.

**Next step: phase 6** (data collection and checks). The ideas left for
the terminal are an alternative the user may pick first.

To resume: read `CLAUDE.md` (rules, commands, test environment), then
`docs/architecture.md` (what exists and why). This file only says what is
missing. `docs/chat.md` is the administrator's guide to the chat.

What works today: projects (git, a local directory, written in the interface
with git history and syntax checks, uploaded as an archive), collections
installed from the requirements file, inventories (a file, a directory of
sources, the file of a plugin), credentials with encrypted secrets, trusted
host keys, templates with surveys, ad-hoc commands, runs with a queue,
cancellation, live output with the secrets masked, a view by task, the
differences and the outcome per host; help on modules, export and import of
the configuration; roles from Unix groups and the audit trail; agents through
`fleet-mcp`, under a role ceiling, and a chat page that starts one for the
user; schedules of templates (`OnCalendar` with a time zone, overlap,
catching up, priority in the queue); notifications of the end of runs to
Telegram, webhooks, mail and Matrix, with rules, retries, grouping and a
limit; a self-check of the controller; an overview, the audit trail as a
page; the interface in English and Italian; the command `fleet` for a
terminal, with completion for bash and a manual.
Tried on Linux nodes (containers) and on Windows over SSH.

State of the test VM `deb13-samba-dc` (shut off): the package as of commit
`7e5a0fb` (the close of phase 5). Its `config.toml` is the packaged one plus
`[mcp] enabled = true`, `[notifications] url = "https://VM:9090"`
and the chat's agent, so a later deploy stops at dpkg's question: install
with `apt-get install -o Dpkg::Options::=--force-confold`. Throwaway
accounts `tadmin`, `toperator` and `tviewer`, one per role group (their
password was random and is lost: set a new one with `chpasswd` as root).
The data of those checks: the project `notify-demo` (written in the
interface, `fail.yml` and `ok.yml` on localhost), the inventory `local`,
the templates `fail-demo` and `ok-demo`, the schedule `nightly-fail`
(daily). Channels: three to stand-ins (`hook-renamed`, `mail-test`,
`matrix-test`, on ports 8025 and 2525 of `notify-sinks.py`, which is not
running after a boot, so they end given up) and three to real services of
the user, kept on purpose for the checks of phase 6: one for mail (
an account of its own), `telegram` and `matrix` (a bot and an account of
their own). Rules on every failed run send to all of them: **a failed run
on this VM reaches the user's phone and mailbox**; turn the rules off
before tests that fail on purpose. Going back to `baseline_fleet` removes
all of it. To try the chat again: a test account in a role group, and the
user logs it in to Claude at Cockpit's terminal
(`/opt/cockpit-fleet-agent/bin/claude /login`); log it out (`claude auth
logout`) before removing the account.

The snapshot `baseline_fleet` (redone on 13 October, a child of `baseline`) is
where tests start from: Debian 13 with `cockpit`, `cockpit-fleet` as of commit
`1bb7a37`, `ansible`, `ansible-lint`, `sshpass`, `podman` and the `fleet-node`
image already built; no data and no test accounts, and no Samba AD DC. It has
neither Node.js nor an agent for the chat. After a revert, `make
vm-deb-deploy` installs the current code.

The two RPM controllers, `fed44-fleet` and `alma10-fleet` (shut off, see
`CLAUDE.md`), have the package of the RPM work (Alma that of commit `8888816`,
Fedora one built just before the minimum went to 2.16, which changes
nothing there), the `fleet-node` image and no data. On Alma the package in
`baseline_fleet` predates that minimum, so its `selfcheck` reports
`ansible`: after a revert, install the current `.rpm`.

Integration tests, all passing when last run, on 15 October on Debian 13,
Fedora 44 and AlmaLinux 10.2 alike: every script but `windows.sh` and
`measure-events.sh`; `runs.sh` without the git part (`IT_GIT_URL` not set)
and `galaxy.sh` without the network (`IT_GALAXY_NET` not set). `windows.sh`
was last run before phase 3 and wants the secret `it-win-key` made again
(see `CLAUDE.md`); Windows was tried by the two acceptance tests instead. The
role that installs the chat's agent has no script: it was tried by hand on
Debian, on 13 and 14 October, as `docs/architecture.md` tells.

`runs.sh`, `sandbox.sh` and `adhoc.sh` want `IT_SSH_USER` (`maint`) and
`IT_SSH_PASSWORD`.

## Decisions

| Topic | Decision |
|---|---|
| Name | `cockpit-fleet`, menu "Fleet", collection `lineadicomando.fleet` |
| License | `LGPL-2.1-or-later` (as `cockpit-samba-dc`), to be confirmed again on publication |
| Repository | a private Gitea; GitHub once it is stable |
| Backend | No daemon and no port: systemd units started on demand |
| Identity | System account `cockpit-fleet`; root only in `fleet-unit` and `fleet-secret` |
| Roles | Groups `cockpit-fleet-admin`, `-operator`, `-viewer` |
| Viewer | Sees the state of runs and their outcome per host, not the output |
| Operator | Launches templates; may narrow the hosts and ask for a dry run, not widen the limit; gives variables only by answering the survey |
| Secrets | `systemd-creds`; only PID 1 decrypts them, for the single run |
| Secrets in a survey | The operator hands them to `fleet-secret answer`, which chooses the name and returns a single-use ticket; the run consumes it |
| Ansible | The distribution's package `ansible-core`, 2.16 or later (AlmaLinux 10 has 2.16, unsupported upstream but kept by Red Hat), decided on 15 October: the integration scripts are run on AlmaLinux at every phase; extra collections in the project |
| A project's `ansible.cfg` | Not used directly: only the keys in an allowlist pass |
| Sources of projects | Git, local directory, written in the interface, uploaded archive |
| Collections from Galaxy | For every kind of project; installed by a sandboxed job next to the tree (`projects/<id>/collections/<sha256 of the requirements file>`), never inside it; copied into every run; without them a playbook does not start |
| Uploaded archive | Travels as base64 in the JSON on the stdin of `fleetctl` (20 MB, at most 50); extracted in the sandbox; revision = sha256; links out of the tree, hard links and devices make the whole archive refused |
| Ad-hoc commands | Admin only; one module by its full name, among those in `[adhoc] modules` of `config.toml` (default: ping, facts, `service_facts`, `package_facts`, `command`, `shell`, and on Windows ping, facts, `win_command`, `win_shell`, `win_powershell`; empty turns them off); run as a generated playbook, so module and arguments are in a file and events work as for any run |
| Inventory sources | A file, a directory of sources or the YAML file of a plugin, always of a project and read in the sandbox; executable scripts off unless `[inventories] scripts = true`; an inventory nothing can read fails the job |
| Masking | The secrets handed to a run are replaced in its output, in the journal and in the events, as they are and as JSON escapes them; a net, not a guarantee |
| Export and import | One JSON document, by name, with no secret and no project files; import creates what is missing through the usual commands and replaces nothing |
| Projects written in the interface | A git repository next to the tree, one commit per change, admin only |
| Editor | CodeMirror 6 in a shadow root (Cockpit's CSP stays as it is) |
| Syntax check | A sandboxed job when a playbook is saved; `ansible-lint` reports, it does not fail the check |
| Notifications | Telegram, generic webhook, SMTP, Matrix |
| Delivery of notifications | `cockpit-fleet-notify.service`, started by a path unit on `notify.wake`, which the backend writes; retries woken by the tick of the scheduler. No root code: `fleet-unit` was not extended |
| Secrets of channels | One per channel, `n-<id>-token`, kept by `fleet-secret` in `secrets/notify/`, the only directory the delivery unit has decrypted |
| What is told about | Playbook runs and ad-hoc commands that end failed (or in error), with changes, successful or canceled; scope: every run, a project, a template or a schedule. Not the jobs of projects |
| Group as scope of a rule | Not now: a run has no group. Groups of hosts come with the checks (phase 6) |
| Approval requested | An event of phase 9, when approvals exist |
| Data from the nodes | Health checks, CMDB, updates, drift |
| Checks | Built-in catalog + user playbooks with a result contract |
| History | SQLite: state, state changes, time series with downsampling |
| Scale | Over 200 nodes: intervals of 5-15 min, one connection per host, sharding |
| Managed nodes | Anything Ansible supports; Windows over SSH only |
| Controller | Debian 13 (`.deb`), Fedora 44 and AlmaLinux 10 (`.rpm`, one spec), decided on 15 October; SELinux enforcing, with no policy of ours |
| External triggers | Out of scope, future development |
| Agents (MCP) | `fleet-mcp` on stdio, launched by the user (also over ssh): every tool is a call to `fleetctl`, so roles and audit stay the same; no HTTP; standard library only |
| Secrets and agents | No tool for `fleet-secret`: the agent creates the credential, a person enters the value from the interface |
| MCP servers of projects | Not started (they launch Ansible outside the sandbox, with vaults and keys in clear): templates with a survey are published as tools by `fleet-mcp` |
| Chat | Stays in `cockpit-fleet`, it does not become a separate package; `cockpit-samba-dc` does not hook into it: a Samba AD DC is managed from here like any other Ansible project (the collection of `ansible-win_edulab` or a dedicated one), which the plugin knows nothing about |
| Role ceiling of agents | `[mcp] enabled = false` and `max_role = "operator"` in `config.toml`, which the administrator may raise to `admin`: the output of a run is written by the nodes, and an admin agent that reads it can then write files and launch runs |
| Template with a required secret | Not published as a tool, to begin with |
| Run output for agents | The tool gives the outcome per host and the view by task; the raw output only when asked for explicitly |
| Audit of agents | `fleet-mcp` declares `via` (and the name of the client) in the JSON on stdin; `fleetctl` records it in the audit trail and in the journal and, when it is there, applies the ceiling. Declaring it can only restrict. Over ssh it binds only with a dedicated key and a forced command on `fleet-mcp` in `authorized_keys`; otherwise it is a declaration, not a control. A stricter variant, with no code: a Unix account for the agent |
| Switch of the chat | Naming an agent in `[chat.agents.NAME]` of `config.toml`, apart from `[mcp] enabled`, which the chat also needs: enabling `fleet-mcp` sends data to nobody, the chat does. The tab is shown only when an agent is named |
| Agent of the chat | Not in the packages, which download nothing by themselves: Node.js 22 is not in Debian 13 nor in AlmaLinux 10 and the `claude` binary is not ours to redistribute. The package carries a role and a playbook (`lineadicomando.fleet.chat_agent`) that an administrator of the machine runs once, as root, in a terminal: Node.js by checksum, the adapter from a lockfile with the hash of every package. `config.toml` names the command (`[chat.agents.NAME]`), which the playbook writes only when asked; any ACP agent can be named there. No program of ours runs as root for this and sudoers does not change. Discarded: an installation per user driven by the page, a second package, a third-party apt repository, driving `claude` without ACP, a shell recipe in the documentation (it was the first form) |
| Name of the chat in the interface | "Assistant": "Chat" reads as a chat between people, and "agent" already means two other things here. Only what the user reads: `[chat]`, `fleet-chat` and the `chat.*` commands keep their names |
| Terminal for the chat | Cockpit's own, which is in `cockpit-system` and runs as the user of the page: the page shows the command to install the agent, or to log in, with a copy button and one that opens it. No terminal of ours in the page |
| Start of the chat's agent | The page names the agent; `fleet-chat`, a packaged program with no privilege, gets the command line from `chat.start` and runs it. No command line reaches the browser |
| Login of the chat's agent | Each user logs in once at a terminal on the controller; the page says when the login is missing and shows the command `config.toml` gives. No login driven by the page |
| Sessions of the chat | `[chat] max_sessions`, 2 by default, for the whole controller; counted from the running `fleet-chat` processes, a bound on memory and not a control |
| Permissions in the chat | Read-only tools of `fleet-mcp` go without a question, everything else is confirmed; "always" lasts a conversation and is kept by the page, never written in the agent's settings |
| Name of `fleetctl` | Stays `fleetctl`, with `fleet-mcp` next to it: they are in `/usr/libexec/cockpit-fleet`, out of the `PATH`, and sharing a name with FleetDM's CLI creates no ambiguity |
| Name of the command for the terminal | `fleet`, confirmed on 14 October. No package of Debian (trixie, sid), Fedora 44 or AlmaLinux 10 (with EPEL) installs `bin/fleet`, its completion or its manual (Fedora and Alma checked on 15 October); the CLI of Rancher Fleet, installed by hand in `/usr/local/bin`, would shadow it, and JetBrains Fleet's launcher is for desktops. `fleetctl` was weighed and discarded: it is the backend's own name, and FleetDM's CLI. A second name, `cockpit-fleet`, as a link, was proposed for that case and not made |
| Terminal | `fleet` in `/usr/bin`, for admins, operators and viewers: a client of `fleetctl` through sudo like the page and `fleet-mcp`, with `"via": "cli"`, which is recorded and is not an agent's (no ceiling). Every command of the registry as `fleet AREA VERB --param value`, plus shortcuts (`launch`, `log`, `result`, `secret`, `help`); five commands meant for programs are not listed and still answer; tables for a person, `--json` for a program |
| Secrets at the terminal | Never on the command line: asked for with no echo, or read from stdin, and handed to `fleet-secret` on its stdin |
| Scheduling | A single static timer that runs `fleetctl scheduler.tick` every minute, not a timer per schedule (root must not write units from user input; queue and sharding are needed anyway); `OnCalendar` expressions read by `systemd-analyze`, with a time zone |
| Who manages schedules | As templates: an administrator creates, changes and deletes; an operator turns one on or off and runs it now; a viewer sees them (confirmed on 14 October) |
| Whose a scheduled run is | The schedule's: `created_by` is `schedule:NAME`, the tick runs as `system`; the audit trail has who made, changed, switched or ran a schedule, not one record per tick. "Run now" is the operator's (confirmed on 14 October) |
| Schedule of a template with a survey | The answers are stored with the schedule and checked when it is saved and at each launch; never a secret, so a template with a required secret question cannot be scheduled (confirmed on 14 October) |
| Missed times | More than 5 minutes late is missed; per schedule, either recorded as missed or launched once for all the times lost |
| Overlap | Per schedule: skip (default), queue, cancel the previous run |
| Retention | Raw data 14 days, hourly 90 days, daily 2 years, run logs 90 days; to be made in phase 8: nothing is pruned yet but the outbox of notifications |
| Who approves | Group `cockpit-fleet-approver`, to be added in phase 9 |

## Open points (a default applies, to be confirmed before the phase named)

- **The chat's agent on Fedora and AlmaLinux** (before phase 10): the role
  installs `xz` there instead of `xz-utils`, but was not run on either.

## Leftovers of the closed phases

- A git token over https was never tried against a real private repository (it
  needs a dedicated token); public https and ssh with a key are tried.
- The directories of runs that started (`stdout.log`, `events.jsonl`) are
  never deleted: the cleanup
  comes with retention (phase 8).
- Whoever enters with Cockpit's administrative access without being in a group
  appears in the audit trail as `root`.
- Integration tests only run by hand on the VM, not in CI.
- A run that fails leaves its unit `cockpit-fleet-run@ID` failed in systemd
  (`fleet-run` exits with the playbook's code): Cockpit marks "Services" red
  and the system is `degraded` until a reboot or `reset-failed`. The outcome
  is already in `result.json`. Kept on 15 October as coherent with systemd;
  what may change it: a monitor of the controller that alerts on a
  `degraded` system or failed units (Icinga's `check_systemd`), and units
  piling up with a schedule that fails often. `CollectMode=inactive-or-failed`
  on the unit would unload them once finished, keeping the failure in the
  journal (the backend only asks `is-active`); untried.
- An inventory has one vault credential only (templates have more than one).
- Editor: files are renamed and deleted one at a time, not a directory; changing
  tab with unsaved changes loses them without warning; the syntax check gets no
  vault password; every check appears in the list of runs.
- Completion: it only suggests the modules installed on the controller, not
  those of the collections inside the project or installed for it (`ansible-doc`
  must run in the sandbox: the `galaxy` job can do it, leaving the list next to
  the collections); it does not suggest the sub-parameters of a module; a task
  file outside `tasks/` or `handlers/` is treated as a playbook.
- Upload: the archive is not kept (its sha256 can be verified, it cannot be
  downloaded again); no progress bar; over 50 MB another transport would be
  needed.
- Galaxy: public sources only (no credential for git sources, no private Galaxy
  server or token); the roles listed in the requirements file are not installed;
  a local project does not notice by itself that the file changed (it says "not
  installed", the installation has to be asked for); no time limit on the job.
- Ad-hoc commands: the interface takes the arguments as a line only (the
  backend also takes a mapping); arguments are stored in clear, so there is no
  way to give a secret; the list of modules has no wildcard for a whole
  collection.
- Acceptance with `ansible-win_edulab`: its vault file is not in its git
  repository, so the project was an archive (the tracked files plus the vault
  of `school`), not a git project; a git project has no way to get such a file.
  The inventory sets `ansible_password` and its own `ansible_ssh_common_args`
  (no host key checking: ours come first on the command line and win, checked
  by reading the code of the ssh plugin, not by a test). Whether the node let
  the run in by the key of the credential or by that password was not looked
  at. The keys made for the two acceptance tests are still authorised on
  `win11-pc00` (comment `cockpit-fleet-test`); their private halves were
  deleted.
- Inventory plugins: no credential for them (a cloud token), and one written
  in the interface is called `hosts.yml`, which some plugins refuse; only
  built-in plugins were tried.
- Masking: other encodings of a secret are not recognised, and secrets the
  run decrypts by itself (a vaulted variable of the inventory) are not known.
- Export and import: nothing is updated or deleted by an import.
- Help on modules: like the completion, only what is installed on the
  controller.
- Agents: every `tools/list` while agents are not enabled leaves a denial in
  the audit trail; `run.result` holds a `fleetctl` process for up to 50 s; most
  parameters (171 of 210, counted on 15 October) have no description, so only their type and
  bounds reach the schema; the user manual for setting up an agent is the section
  of `docs/architecture.md`, there is no page for it in the interface.
- Chat: answers are plain text, Markdown is not rendered; a conversation
  cannot be deleted or renamed from the page; the model and the effort of
  the agent cannot be chosen; the standard error of the agent is kept by
  Cockpit for the whole session, to show its end when it fails; the limit on
  sessions can be filled by one user; a tool the role does not have is asked
  about as "not one of your tools" and then refused by the backend; with the
  model it was tried as an operator only (as a viewer, with the test agent);
  with no login the page says so only after a first message, not at the
  start, when `~/.claude` already exists;
  below 320 px of height for the chat the page scrolls again.
- Agent of the chat: the role was tried on x86_64 only (the checksum for
  arm64 is there, untried); the versions of Node.js and of the adapter are fixed
  in the role and move only with a release of the package; `selfcheck` says
  nothing about the chat; no integration script runs the playbook, which
  downloads the whole agent; the role used from a project, on a controller
  reached over ssh, and the installation behind a proxy were not tried.
- AlmaLinux 10 has neither `ansible` nor `ansible-lint`, even with EPEL
  (which has some single collections, `ansible-collection-*`): the syntax
  check runs without lint there, and a project brings in its requirements
  file any collection beyond `ansible.builtin`, `ansible.windows` included.
  SELinux was tried in enforcing mode by the scripts only, not by a person
  at the interface.
- `selfcheck` leaves `/var/lib/cockpit-fleet/.ansible` behind (`ansible
  --version` with the account's home).
- Schedules: the builder of the interface knows a few shapes (every few
  minutes, hourly, daily, weekdays, weekly, monthly), anything else is typed;
  no end date and no window in which a schedule may run; the list of runs
  cannot be filtered by schedule and the overview says nothing of them;
  a time that ends in `error` because the calendar could not be read leaves
  the schedule with no next time until it is saved or switched again; the
  tick runs `fleetctl` every minute even with no schedule (it also collects
  runs and starts queued ones, which is what makes a reboot lose nothing).
  A failed scheduled run is told about only through a rule.

## Phase 3 — Complete runner (closed on 13 October)

Surveys, the view of differences, the editor in the interface, archive
uploads, collections from Galaxy, ad-hoc commands, inventory plugins, help on
modules, masking of secrets, export and import: all described in
`docs/architecture.md`, and what they leave to be desired is in the leftovers
above.

Done when: `ansible-win_edulab` runs from cockpit-fleet with no change to the
repository, with its vault as a credential. **Met on 13 October**, from the
interface, on `win11-pc00`: the project uploaded as an archive, the two
collections of its `requirements.yaml` installed from GitHub, the inventory
`inventories/school/hosts.yaml` read with the vault credential, host keys
trusted from the inventory, `playbooks/win_wm.yaml` launched by an operator
with a survey (`t = ping,sysinfo`, 14 tasks, 14 s), an ad-hoc
`ansible.windows.win_command`, and `playbooks/shutdown.yaml`, which has a task
delegated to the controller.

## Phase 3b — Agents (MCP) (closed on 13 October)

The commands of the later phases (schedules, notifications) become tools by
themselves, from the registry.

Done on 13 October and described in `docs/architecture.md` ("Agents"):
declared parameters and their schemas, `fleet-mcp`, the role ceiling and `via`
in the audit trail, templates as tools, `run.result`, ad-hoc commands as a
tool, `mcp.sh`, and the trial of the Agent Client Protocol on which the chat
rests.

Done when: from Claude Code, over ssh, an operator launches a template of
`ansible-win_edulab` as a tool and reads its outcome, and the same request with
an admin tool is refused and ends up in the audit trail. **Met on 13 October**
on `win11-pc00`, the project set up as for the acceptance of phase 3: Claude
Code 2.1, over ssh with a key forced on `fleet-mcp`, as an operator, launched
`playbooks/win_wm.yaml` through its `launch_*` tool with `t = ping,sysinfo`,
waited with `run_result` and reported the host and the tasks (7 turns). Asked
for an ad-hoc command, it found no tool for it and said which role it had; the
same request sent to `fleetctl` with `via`, as the operator and as an
administrator under the ceiling, was refused and is in the audit trail and in
the journal.

The chat page, not part of that criterion, was written on 13 October and is
described in `docs/architecture.md` ("The chat") and, for the administrator,
in `docs/chat.md`. Tried from the interface on the VM with `fake-agent.py`,
as an operator and as a viewer, and with the adapter for Claude and its
model, as an operator: a template launched after a confirmation and its
outcome read, a conversation resumed after a reload.

## The terminal (started on 14 October)

Done: the command `fleet` (point 1 of the brainstorm), with completion for
bash and a manual, tried by `cli.sh` and by hand at a terminal. Ideas left,
in the order they were weighed, none decided:

- [ ] Projects at the terminal: a convention for `[projects] local_roots`
      (a group that writes, `cockpit-fleet` that reads) and its guide; for
      a managed project, a read-only clone of `repo.git` for administrators
      (it touches "an operator does not read files": decide first)
- [ ] `fleet assistant`: the chat's agent at a terminal, with `fleet-mcp` as
      its only tools and the same ceiling
- [ ] From the page to the terminal: next to a run, a template or a project,
      the `fleet` command that does the same, to copy, with the button to
      Cockpit's terminal (which cannot be handed a command to run)

What `fleet` leaves to be desired: completion knows commands and options,
not the names of templates, runs or credentials; `project upload` wants
the archive as one base64 string (`--data @-` after `base64 -w0`, not tried),
there is no option that takes the file itself; answers to a survey
are typed by `-a` as the questions say; the prompt shows the choices of a
question, but they cannot be picked from a list nor completed; English only.

## Phase 4 — Scheduling (closed on 14 October)

Schedules of templates with `OnCalendar` expressions and a time zone, the
timer and its tick, conversion of a cron line, priority in the queue,
overlap, catching up, random delay, the page with a builder and the next
times, export and import: described in `docs/architecture.md`
("Schedules"), and what they leave to be desired is in the leftovers above.

Done when: ten schedules on the same minute respect the concurrency limit and
none is lost after a reboot. **Met on 14 October** on the VM: `schedules.sh`
launched ten schedules from one tick with `max_concurrent = 2`, never more
than two running, highest priority first. For the reboot, the VM was shut
down with one run running and two queued, before the time of two schedules,
one that catches up and one that does not, and started again eight minutes
later. The first tick after the boot recorded the run cut by the shutdown
(failed: its unit was stopped), started the next queued one, launched the
schedule that catches up once and recorded the other as missed; both were
planned for the next day. The page was looked at in the browser: the list,
the builder, the preview and the conversion of a cron line.

## Phase 5 — Notifications (closed on 15 October)

Outbox, the unit and its path unit, the four channels, secrets, test
message, proxy, rules, events, messages in two languages with a link,
grouping and limit, export and import, `fleet secret --channel`: described
in `docs/architecture.md` ("Notifications").

Done when: a failed run reaches the four channels and an unreachable channel is
retried without blocking the others. **Met on 14 October** on the VM, with
stand-ins (`notify.sh`, `notify-sinks.py`): one failed run reached the
Telegram Bot API, the webhook, the mail server and the Matrix homeserver in
one delivery; a fifth channel, refused, was tried again by the tick and got
it once its server was up, about 90 s later; the tokens were in neither the
database nor the journal.

Done on 15 October in the browser, in Italian, with the stand-ins of
`notify-sinks.py` on the VM and Telegram's real API with a wrong token: the
page as an administrator (a channel of each kind created, edited with its
secret kept, tested and deleted with its rules; a rule of each scope created,
edited, turned off and deleted; a refused test shown as "To be tried again"
with `HTTP 401`, and "Try again" on one marked given up by hand), as an
operator and as a viewer (lists only); the check "Notifications" in
"System"; a failed run and a scheduled one reaching the channels their rules
name, in the channel's language, with a link `/fleet#/runs/ID` that opens
the run; `fleet secret --channel` and `fleet notification list` (which now
shows state, event and channel). No token in the database or the journal.
The same day, real services: a Telegram bot (a test and a failed run
reached the phone; plain text is enough for now) and Mailu, with an account
of its own, over TLS on 465 (587 is closed from outside), and a Matrix room
on matrix.org with an account of its own (a room of version 12, whose id
has no server: the check of the id was too strict and was fixed).

The decisions taken alone were confirmed on 15 October (a path unit and no
change to `fleet-unit`; no "group" scope until phase 6; the area names
`channel`, `rule`, `notification`); a real webhook was skipped, the
stand-in is enough.

What it leaves to be desired: one message per run, never per host; the
message is plain text (no Markdown or HTML for Telegram, Matrix or mail);
mail never goes through the proxy; a user name for mail without TLS is
refused, so a password never travels in clear; channels are tried one after
the other in a delivery, so one that does not answer delays the rest by at
least 15 s (a timeout per step: connection, STARTTLS and login for mail;
the lookup of a name has none); the link's host name is looked up once per
delivery when `[notifications] url` is not set, which can take seconds
where the name is not in DNS (AlmaLinux); the
list of notifications has no filter in the page (the command has one);
no notification when a schedule misses a time or cannot launch. Seen in
the browser on 15 October: a test refused for good (a wrong token, HTTP
401) is tried again for 24 hours like any notification; the default body
of a webhook has `$text` only, not the title, so a run's message there does
not say it failed; `fleet channel list` does not show whether the secret is
set; the SMTP form shows the password field with no security too (the
backend refuses it). A mail channel with a user name and no password
is `secret_required: no`, so it looks complete; `fleet channel update`
wants the name again even to change only the settings.

## Phase 6 — Data collection and checks

To decide first, with the user (nothing is settled; no code exists yet:
no tables for hosts or results in `db.py`, no `plugins/modules` in the
collection):

- How a check cycle is a job: a new kind for `fleet-run`, and how it shares
  `[runs] max_concurrent` and the queue with playbook runs
- What starts the cycles: the tick of `cockpit-fleet-scheduler.timer`, a
  timer of its own, or schedules
- How the host registry is filled: parsing the inventories (the
  `inventory` job) or a run that gathers facts
- The tables of hosts, checks, assignments, states and results, and their
  migrations
- Who sees, acknowledges and assigns checks (viewer, operator, admin)

- [ ] **Host registry** from the inventories, identity `(inventory, hostname)`,
      platform from the facts
- [ ] **Result contract**: `check`, `state` (0-3), `summary`, `metrics[]`
      (`name`, `value`, `unit`, `min`, `max`), `details`; module
      `lineadicomando.fleet.fleet_result` for the user's checks
- [ ] **Catalog** with one implementation per platform and one schema only:
      reachability, disk and inodes, CPU and memory, services, uptime and reboot
      required, certificate expiry, clock
- [ ] **Measured constraint**: on Windows every task costs 4-5 s. All the checks
      of a node in a single task with a single script
- [ ] Thresholds evaluated on the controller at ingest, changeable without a new
      collection
- [ ] Assignment of checks to groups, with interval and parameters
- [ ] **Planning**: checks that are due grouped by host, hosts in shards, one run
      per shard, `strategy: free`, timeout per task
- [ ] **State engine**: soft/hard after N attempts, an unreachable host that
      suppresses the alerts of its checks, flapping
- [ ] Acknowledgement and maintenance windows
- [ ] Notifications on state change, recovery, reminder
- [ ] Interface: counters, list of problems, host × check grid, host detail

Done when: "disk space" gives comparable results on Debian and Windows, and a
full test disk produces a notification within two intervals.

## Phase 7 — Inventory, updates, drift

- [ ] **CMDB**: facts, packages, services; last snapshot per host, normalised
      columns, history of changes
- [ ] CMDB interface: filterable table, host detail, distribution by OS, CSV/JSON
      export
- [ ] **Updates**: apt, dnf, zypper, Windows Update in search-only mode; security
      ones kept apart; a link to a template that applies them
- [ ] **Drift**: a "compliance" template in `--check --diff` at intervals; the diff
      is kept
- [ ] Notifications: security updates, non-compliant host, hardware change

Done when: the CMDB shows Linux and Windows together and a manual change to a
managed file appears as drift at the next cycle.

## Phase 8 — Time series and scale

- [ ] Narrow metrics table, hourly and daily aggregates, retention
- [ ] Maintenance: downsampling, deletion (of run directories too), vacuum
- [ ] Charts (`@patternfly/react-charts`)
- [ ] Shards and forks sized on measured duration and RAM
- [ ] Fact caching, reuse of SSH connections between cycles
- [ ] A cycle that does not end before the next one is skipped and reported
- [ ] "System health" page: duration of cycles, queue, size of the database
- [ ] Load tests with 200, 500, 1000 simulated hosts. **Measured constraint**:
      about 0.6 s per host on 2 vCPUs, the limit is the controller's CPU. The VM
      has 2 GB: bring it to 4 for these tests

Done when: 500 simulated hosts with 10 checks every 10 minutes run for 24 hours
with no backlog and a responsive interface.

## Phase 9 — Workflows and approvals

- [ ] **Approvals**: critical template, run in `pending_approval`, confirmation by
      a second user in the group `cockpit-fleet-approver` (to be added to sysusers
      and sudoers), expiry, notification, audit
- [ ] **Workflows**: a graph of templates with edges on success, failure, always;
      variables between steps (`set_stats`); editor and view of the execution
- [ ] Cancelling and resuming from a failed step

Done when: a workflow of three steps with an approval at the second stops, is
approved by another user and completes.

## Phase 10 — Release 0.1.0

- [ ] Security review: root helpers, sandbox, sudoers, archive extraction, git
- [ ] Automated negative tests: injection, path traversal, role escalation,
      reading of secrets (many already exist in `test/integration/`)
- [ ] Accessibility; complete Italian translation (seen on 15 October: the
      number inputs of PatternFly are named "Input", the menus of rows
      "Kebab toggle", in English)
- [ ] Documentation: installation, security model, writing a check, backup
- [ ] Upgrade and removal of the package tried, `purge` included; for the `.rpm` too
- [ ] The `.rpm` built in CI (the runner is Debian: rpmbuild there, or a second runner)
- [ ] Screenshots, `CHANGELOG`, tag `v0.1.0`; the version is still `0.0.1`
      in `package.json`, `debian/changelog` and the spec
- [ ] Workflow for GitHub Actions (the current one is for Gitea only)
- [ ] Publication on GitHub: license confirmed again, history with no secrets and
      no details of real environments

## Future developments

External triggers (git webhooks, auto-remediation on state change); pull mode
(`ansible-pull`) for very large fleets; secrets on external backends (OpenBao,
Bitwarden); export to Prometheus or InfluxDB; execution environments in
containers; controller on other distributions than Debian 13, Fedora 44 and
AlmaLinux 10.
