# cockpit-fleet — plan

A Cockpit plugin that runs Ansible from the browser (in the style of Semaphore
UI) and that collects, stores and notifies on data from the nodes using Ansible
only. It does not replace Icinga: nothing in real time, alerts that are
"reasonably timely".

## Status

Phases 0, 1 and 2 were closed on 8 October 2026 and phase 3 on 13 October.
**In progress: phase 3b** (agents): `fleet-mcp` is done and accepted, the ACP
trial held, the chat page is what is left. `main` is level with `origin`.

**Next step: the chat page** (see phase 3b), on the user's confirmation; it
was decided on 13 October how its agent gets on a controller (see Decisions,
"Agent of the chat"). Phase 4 follows.

To resume: read `CLAUDE.md` (rules, commands, test environment), then
`docs/architecture.md` (what exists and why). This file only says what is missing.

What works today: projects (git, a local directory, written in the interface
with git history and syntax checks, uploaded as an archive), collections
installed from the requirements file, inventories (a file, a directory of
sources, the file of a plugin), credentials with encrypted secrets, trusted
host keys, templates with surveys, ad-hoc commands, runs with a queue,
cancellation, live output with the secrets masked, a view by task, the
differences and the outcome per host; help on modules, export and import of
the configuration; roles from Unix groups and the audit trail; agents through
`fleet-mcp`, under a role ceiling. Tried on Linux nodes (containers) and on
Windows over SSH.

State of the test VM `deb13-samba-dc`: **it is not at its snapshot**, on
purpose. It has the package of commit `afc2f06` and, for the chat page, the
account `acpop` (operator, password locked) with Node.js 22, the ACP adapter
and a login to Claude in its home, a few test data and `[mcp]` enabled. Enter
as `maint` and use `sudo -u acpop -H`. Revert to `baseline_fleet` when the chat
page is done, or sooner to remove the login; a new login then takes the user
at a terminal.

The snapshot `baseline_fleet` (redone on 11 October, a child of `baseline`) is
where tests start from: Debian 13 with `cockpit`, `cockpit-fleet` at commit
`124f131`, `ansible`, `ansible-lint`, `sshpass`, `podman` and the `fleet-node`
image already built; no data and no test accounts, and no Samba AD DC. After a
revert, `make vm-deb-deploy` installs the current code.

Integration tests last run on 13 October, on the code of `afc2f06`: `mcp.sh`,
`privileges.sh`, `roles.sh`, `sandbox.sh`, `runs.sh` (without the git part:
`IT_GIT_URL` not set), `adhoc.sh`, `inventories.sh`, `files.sh`, `upload.sh`,
`galaxy.sh` (with `IT_GALAXY_NET=yes`) and `multihost.sh`, all passing.
`windows.sh` was last run before phase 3 and wants the secret `it-win-key`
made again (see `CLAUDE.md`); Windows was tried by the two acceptance tests
instead. `runs.sh`, `sandbox.sh` and `adhoc.sh` want `IT_SSH_USER` (`maint`)
and `IT_SSH_PASSWORD`.

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
| Ansible | Debian package `ansible-core`; extra collections in the project |
| A project's `ansible.cfg` | Not used directly: only the keys in an allowlist pass |
| Sources of projects | Git, local directory, written in the interface, uploaded archive |
| Collections from Galaxy | For every kind of project; installed by a sandboxed job next to the tree (`projects/<id>/collections/<sha256 of the requirements file>`), never inside it; copied into every run; without them a playbook does not start |
| Uploaded archive | Travels as base64 in the JSON on the stdin of `fleetctl` (20 MB, at most 50); extracted in the sandbox; revision = sha256; links out of the tree, hard links and devices make the whole archive refused |
| Ad-hoc commands | Admin only; one module by its full name, among those in `[adhoc] modules` of `config.toml` (default: ping, facts, `command`, `shell` and their Windows counterparts; empty turns them off); run as a generated playbook, so module and arguments are in a file and events work as for any run |
| Inventory sources | A file, a directory of sources or the YAML file of a plugin, always of a project and read in the sandbox; executable scripts off unless `[inventories] scripts = true`; an inventory nothing can read fails the job |
| Masking | The secrets handed to a run are replaced in its output, in the journal and in the events, as they are and as JSON escapes them; a net, not a guarantee |
| Export and import | One JSON document, by name, with no secret and no project files; import creates what is missing through the usual commands and replaces nothing |
| Projects written in the interface | A git repository next to the tree, one commit per change, admin only |
| Editor | CodeMirror 6 in a shadow root (Cockpit's CSP stays as it is) |
| Syntax check | A sandboxed job when a playbook is saved; `ansible-lint` reports, it does not fail the check |
| Notifications | Telegram, generic webhook, SMTP, Matrix |
| Data from the nodes | Health checks, CMDB, updates, drift |
| Checks | Built-in catalog + user playbooks with a result contract |
| History | SQLite: state, state changes, time series with downsampling |
| Scale | Over 200 nodes: intervals of 5-15 min, one connection per host, sharding |
| Managed nodes | Anything Ansible supports; Windows over SSH only |
| Controller | Debian 13 only |
| External triggers | Out of scope, future development |
| Agents (MCP) | `fleet-mcp` on stdio, launched by the user (also over ssh): every tool is a call to `fleetctl`, so roles and audit stay the same; no HTTP; standard library only |
| Secrets and agents | No tool for `fleet-secret`: the agent creates the credential, a person enters the value from the interface |
| MCP servers of projects | Not started (they launch Ansible outside the sandbox, with vaults and keys in clear): templates with a survey are published as tools by `fleet-mcp` |
| Chat | Stays in `cockpit-fleet`, it does not become a separate package; `cockpit-samba-dc` does not hook into it: a Samba AD DC is managed from here like any other Ansible project (the collection of `ansible-win_edulab` or a dedicated one), which the plugin knows nothing about |
| Role ceiling of agents | `[mcp] enabled = false` and `max_role = "operator"` in `config.toml`, which the administrator may raise to `admin`: the output of a run is written by the nodes, and an admin agent that reads it can then write files and launch runs |
| Template with a required secret | Not published as a tool, to begin with |
| Run output for agents | The tool gives the outcome per host and the view by task; the raw output only when asked for explicitly |
| Audit of agents | `fleet-mcp` declares `via` (and the name of the client) in the JSON on stdin; `fleetctl` records it in the audit trail and in the journal and, when it is there, applies the ceiling. Declaring it can only restrict. Over ssh it binds only with a dedicated key and a forced command on `fleet-mcp` in `authorized_keys`; otherwise it is a declaration, not a control. A stricter variant, with no code: a Unix account for the agent |
| Switch of the chat | Separate from `[mcp] enabled`: enabling `fleet-mcp` sends data to nobody, the chat does |
| Agent of the chat | Installed once by the administrator of the machine, outside the `.deb`; `config.toml` names its command (`[chat]`), and the page starts that and nothing else. The plugin downloads and installs nothing: Node.js 22 and the adapter cannot come from Debian packages, and the `claude` binary is not ours to redistribute. A command in `config.toml` is the administrator's opt-in, and any ACP agent can be named there. A recipe for the Claude adapter goes in the documentation. Discarded: an installation per user driven by the page, a second Debian package, a third-party apt repository, driving `claude` without ACP |
| Name of `fleetctl` | Stays `fleetctl`, with `fleet-mcp` next to it: they are in `/usr/libexec/cockpit-fleet`, out of the `PATH`, and sharing a name with FleetDM's CLI creates no ambiguity |
| Scheduling | A single static timer that runs `fleetctl scheduler tick` every minute, not a timer per schedule (root must not write units from user input; queue and sharding are needed anyway); `OnCalendar` expressions |
| Retention | Raw data 14 days, hourly 90 days, daily 2 years, run logs 90 days |
| Who approves | Group `cockpit-fleet-approver` |

## Open points (a default applies, to be confirmed before the phase named)

- **Phase 3b, login of the chat's agent to its model**: by default each user
  logs in once in a terminal on the controller (`claude /login`), and the page
  only says when a login is missing; the alternative is a login driven by the
  page, which we would have to write.
- **Phase 3b, sessions of the chat at the same time**: by default a limit in
  `config.toml`, since one session takes about 360 MB.

## Leftovers of the closed phases

- A git token over https was never tried against a real private repository (it
  needs a dedicated token); public https and ssh with a key are tried.
- Run directories (`stdout.log`, `events.jsonl`) are never deleted: the cleanup
  comes with retention (phase 8).
- Whoever enters with Cockpit's administrative access without being in a group
  appears in the audit trail as `root`.
- Integration tests only run by hand on the VM, not in CI.
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
- Export and import: nothing is updated or deleted by an import; schedules and
  notification rules will have to join the document when they exist.
- Help on modules: like the completion, only what is installed on the
  controller.
- Agents: every `tools/list` while agents are not enabled leaves a denial in
  the audit trail; `run.result` holds a `fleetctl` process for up to 50 s; the
  descriptions of the parameters are still few (only the types and bounds
  reach the schema); the user manual for setting up an agent is the section
  of `docs/architecture.md`, there is no page for it in the interface.
- `selfcheck` leaves `/var/lib/cockpit-fleet/.ansible` behind (`ansible
  --version` with the account's home).

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

## Phase 3b — Agents (MCP)

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

What is left is the chat page, not part of that criterion:

- [ ] `[chat]` in `config.toml`: off unless it names the command of an agent
      (more than one, each with a name); its own switch, apart from `[mcp]`,
      which it needs enabled; a limit on the sessions open at the same time.
      A backend command gives the page what it may start, to every role that
      may use the chat
- [ ] The page: an ACP client on a `cockpit.spawn` channel, started with
      `fleet-mcp` as the only MCP server, no built-in tool
      (`_meta.disableBuiltInTools`) and `ENABLE_CLAUDEAI_MCP_SERVERS=false`;
      the conversation as it streams, with the calls to tools
- [ ] Permission requests in the interface: every call that changes something
      is confirmed (once, always for the session, refuse), with the tool and
      its arguments in words
- [ ] Sessions: the list, and `session/load` to go on after the page was
      reloaded; the agent ends with the page, which is said in the page
- [ ] When the agent is not logged in to its model: say so and how to log in
- [ ] Documentation: the recipe to install Node.js 22 and the Claude adapter
      once, for all users, with pinned versions; what the chat sends to the
      provider of the model
- [ ] Tried from the interface on the VM, as an operator and as a viewer

## Phase 4 — Scheduling

The limit of concurrent runs and the start of the next one in the queue already
exist.

- [ ] `cockpit-fleet-scheduler.timer` + `fleetctl scheduler tick`
- [ ] `OnCalendar` schedules with a time zone; next execution computed on save and
      at every tick (`systemd-analyze calendar`)
- [ ] Import from 5-field cron; a builder with presets in the interface
- [ ] Catching up on the executions missed while the controller was off, per
      schedule
- [ ] Priority in the queue; overlap policy (skip, queue, cancel the previous one)
- [ ] Random delay to spread the load
- [ ] Interface: list, next executions, enable/disable, "run now"

Done when: ten schedules on the same minute respect the concurrency limit and
none is lost after a reboot.

## Phase 5 — Notifications

- [ ] Outbox in SQLite with retries and backoff; `cockpit-fleet-notify.service`
      sandboxed with outgoing network only
- [ ] Channels Telegram, webhook (JSON template), SMTP, Matrix; standard library
      only
- [ ] Tokens as secrets; a "send a test" button; proxy support
- [ ] Rules: kind of event × scope (project, template, group) × channel
- [ ] Events: run succeeded, failed, with changes, cancelled, approval requested
- [ ] Messages in Italian and English with a link to the Cockpit page
- [ ] Grouping by time window and rate limit

Done when: a failed run reaches the four channels and an unreachable channel is
retried without blocking the others.

## Phase 6 — Data collection and checks

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
- [ ] Accessibility; complete Italian translation
- [ ] Documentation: installation, security model, writing a check, backup
- [ ] Upgrade and removal of the package tried, `purge` included
- [ ] Screenshots, `CHANGELOG`, tag `v0.1.0`
- [ ] Workflow for GitHub Actions (the current one is for Gitea only)
- [ ] Publication on GitHub: license confirmed again, history with no secrets and
      no details of real environments

## Future developments

External triggers (git webhooks, auto-remediation on state change); pull mode
(`ansible-pull`) for very large fleets; secrets on external backends (OpenBao,
Bitwarden); export to Prometheus or InfluxDB; execution environments in
containers; controller on other distributions.
