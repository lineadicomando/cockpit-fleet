# cockpit-fleet — plan

A Cockpit plugin that runs Ansible from the browser (in the style of Semaphore
UI) and that collects, stores and notifies on data from the nodes using Ansible
only. It does not replace Icinga: nothing in real time, alerts that are
"reasonably timely".

## Status

Phases 0, 1 and 2 were closed on 8 October 2026, **phase 3 on 13 October** (surveys
and the view of differences done on 9 October, the editor in the interface and
archive uploads on 10 October, Galaxy on 11 October, ad-hoc commands on
13 October). The acceptance test of the phase passed on 13 October (see
phase 3). `main` is ahead of `origin`: nothing was pushed.

Phase 3 was closed on 13 October with its last four items: inventory
plugins, help on modules, masking of secrets, export and import.

**Next step: phase 3b** (agents), then 4.

To resume: read `CLAUDE.md` (rules, commands, test environment), then
`docs/architecture.md` (what exists and why). This file only says what is missing.

What works today: projects (git or a local directory), inventories, credentials
with encrypted secrets, trusted host keys, projects written in the interface with
git history and syntax checks, projects uploaded as an archive, collections
installed from the requirements file, templates with surveys, ad-hoc commands,
inventory plugins, help on modules, export and import of the configuration,
secrets masked in the output, runs with a queue,
cancellation, live output, a view by task and the outcome per host; roles from
Unix groups and the audit trail. Tried on Linux nodes (containers) and on Windows
over SSH.

State of the test VM `deb13-samba-dc`: the snapshot `baseline_fleet` (redone on
11 October, a child of `baseline`) is where tests start from: Debian 13 with
`cockpit`, `cockpit-fleet` at commit `124f131`, `ansible`, `ansible-lint`,
`sshpass`, `podman` and the `fleet-node` image already built; no data and no test
accounts. It has neither the Samba AD DC (`baseline` is a Debian with no Cockpit
and no Samba) nor the secret `it-win-key`, to be created again before
`windows.sh`.

The VM, shut off, is at the snapshot: a test needs `make vm-deb-deploy` first, to
install the current code. On 11 October, with the Galaxy package, these passed
there: `galaxy.sh` (with `IT_GALAXY_NET=yes`), `privileges.sh`, `roles.sh`,
`sandbox.sh`, `runs.sh` (without the git part: `IT_GIT_URL` not set), `files.sh`
(without `ansible-lint`, missing at the time) and `upload.sh`; `multihost.sh` and
`windows.sh` were not run again. On 13 October, with the ad-hoc package:
`adhoc.sh`, `privileges.sh`, `roles.sh`, `sandbox.sh` and `runs.sh`. `runs.sh`,
`sandbox.sh` and `adhoc.sh` want `IT_SSH_USER` (`maint`) and `IT_SSH_PASSWORD`.
`windows.sh` was not run again.

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
| Name of `fleetctl` | Stays `fleetctl`, with `fleet-mcp` next to it: they are in `/usr/libexec/cockpit-fleet`, out of the `PATH`, and sharing a name with FleetDM's CLI creates no ambiguity |
| Scheduling | A single static timer that runs `fleetctl scheduler tick` every minute, not a timer per schedule (root must not write units from user input; queue and sharding are needed anyway); `OnCalendar` expressions |
| Retention | Raw data 14 days, hourly 90 days, daily 2 years, run logs 90 days |
| Who approves | Group `cockpit-fleet-approver` |

## Open points (a default applies, to be confirmed before the phase named)

- **Phase 3b, chat in Cockpit**: decided after the ACP trial on the VM; the agent
  runs as the user, with no shell and no access to files, with `fleet-mcp` alone,
  and sends data (inventories, output) to the provider of the model: opt-in by
  the admin.

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
  at. The key made for the test is still authorised on `win11-pc00`; its
  private half was deleted.
- Inventory plugins: no credential for them (a cloud token), and one written
  in the interface is called `hosts.yml`, which some plugins refuse; only
  built-in plugins were tried.
- Masking: other encodings of a secret are not recognised, and secrets the
  run decrypts by itself (a vaulted variable of the inventory) are not known.
- Export and import: nothing is updated or deleted by an import; schedules and
  notification rules will have to join the document when they exist.
- Help on modules: like the completion, only what is installed on the
  controller.
- `selfcheck` leaves `/var/lib/cockpit-fleet/.ansible` behind (`ansible
  --version` with the account's home).

## Phase 3 — Complete runner

- [x] **Surveys**: text, number, choice, boolean, secret; tried on the VM
      (`privileges.sh`, `roles.sh`, `runs.sh`, `sandbox.sh`) and from the interface
- [x] **View of differences** per host: the "Differences" tab of a run; tried on
      the VM (`runs.sh` and from the interface)
- [x] **Editor in the interface**: the project as a local git repository, an
      automatic commit with the Cockpit user as author, `--syntax-check` on save,
      `ansible-lint` if present; tried on the VM (`files.sh` and from the
      interface). CodeMirror editor with highlighting, indentation and completion
      of keywords, modules and parameters
- [x] **Archive upload**: tar or zip from the browser, extracted by a sandboxed job
      with its own checks (path traversal, links, devices, limits), revision =
      sha256; tried on the VM (`upload.sh` and from the interface, 19 MB in 1.7 s)
- [x] **Galaxy**: the collections of the requirements file installed by a sandboxed
      job, kept next to the project by digest of the file, copied into runs; tried
      on the VM (`galaxy.sh`, also from a public git repository)
- [x] **Ad-hoc commands**: admin only, allowlist of modules in `config.toml`, run
      as a generated playbook of one task (arguments in a file, not in argv);
      tried on the VM (`adhoc.sh` and from the interface)
- [x] **Dynamic inventories**: YAML plugins and directories of sources;
      executable scripts off by default; tried on the VM (`inventories.sh`)
- [x] Help on modules (`module.help`): description, parameters, examples and
      return values, in a dialog
- [x] Masking of known secrets in stored logs, in the journal and in the
      events; tried on the VM (`runs.sh`)
- [x] Export and import of the configuration, without secrets; tried on the VM
      (`runs.sh`)

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

After ad-hoc commands and the acceptance of phase 3. The commands of the later
phases (schedules, notifications) become tools by themselves, from the registry.

- [ ] Declarative parameters in `@command`, from which to generate the
      `inputSchema` of tools (today `Params` reads them imperatively)
- [ ] `fleet-mcp` in `/usr/libexec/cockpit-fleet`: JSON-RPC on stdio (`initialize`,
      `tools/list`, `tools/call`), one tool per command, `sudo -n fleetctl` with
      argv
- [ ] `[mcp]` in `config.toml`: off by default, role ceiling; tools above the
      ceiling are not listed and `fleetctl` refuses them anyway
- [ ] Audit: column `via` (migration) and `FLEET_VIA` in the journal, from the
      field `fleet-mcp` adds to the JSON; with `via` present `fleetctl` applies
      the ceiling
- [ ] Documentation: an ssh key dedicated to the agent with a forced command on
      `fleet-mcp`; without it, `via` and the ceiling are a declaration (to be
      written in `docs/architecture.md` too); as an alternative, a Unix account
      for the agent
- [ ] **Templates as tools**: name and description from the template, schema from
      the survey; a template with a required secret field is not published,
      optional ones stay out of the schema
- [ ] A tool to wait for the end of a run and read its outcome per host and its
      view by task; the raw output, in chunks, only when asked for explicitly
- [ ] Ad-hoc commands as tools (the equivalent of `run_powershell` and
      `run_tasks`)
- [ ] Integration test `mcp.sh`: roles, ceiling, refusals, `via` in the audit
      trail, no secret in the responses, a key with a forced command that reaches
      `fleet-mcp` only; tried from Claude Code over ssh
- [ ] **ACP trial** on the VM: the agent kept alive by a `cockpit.spawn` channel,
      resuming a session, the Claude Code adapter on Debian 13
- [ ] Chat page (only if the trial holds): several agents, confirmation for every
      tool that changes something, permission requests in the interface

Done when: from Claude Code, over ssh, an operator launches a template of
`ansible-win_edulab` as a tool and reads its outcome, and the same request with
an admin tool is refused and ends up in the audit trail.

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
