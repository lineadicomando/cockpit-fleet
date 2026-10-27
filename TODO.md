# cockpit-fleet — plan

Cockpit plugin that runs Ansible from the browser (Semaphore UI style) and
collects, stores and notifies on data from the nodes, with Ansible only. Not
a replacement for Icinga: nothing in real time, alerts "reasonably timely".

## Status (25 October 2026)

Phases 0–9 closed (how each was accepted: the last section of
`docs/architecture.md`); the terminal's ideas done and seen live. Phase 9
(approvals and workflows) closed on 24 October: `sandbox roles runs
workflows` on the VMs of the three, no SELinux denial on Fedora 44 and
AlmaLinux 10; CI green after a test stopped needing `systemd-analyze`.

**Next:** the last item of phase 10, publication on GitHub: its Actions
workflow, the license confirmed, a history with no secrets or real
environments. 0.1.0 is tagged; everything else of phase 10 is done.

Phase 8 brought time series (schema 12, `metrics.py`), maintenance (schema
13, `maintenance.py`), the charts (`MetricHistory.tsx`,
`src/lib/metrics.ts`), the sizing of cycles (schema 14, table `cycles`),
cycles that skip and fall behind (schema 15, table `cycle_lag`, events
`late` and `caught_up`), the health of the system (`health.py`,
`system.health`, `src/lib/health.ts`), the catalog pipelined
(`runner.check_env`), runs in `system-cockpitfleet.slice` (`CPUWeight=20`)
and the load test (`test/integration/load.sh`, `load-sample.py`). Its
target, 500 hosts with ten checks every 10 minutes for 24 hours, passed
on a Debian machine of 2 vCPUs and 4 GB: no backlog, every call of the
interface under 0.4 s at p95 (numbers in `docs/architecture.md`, Checks,
"At scale").

To resume: read `CLAUDE.md` (rules, commands, tests) and `CLAUDE.local.md`
if present (this developer's VMs and their state); `docs/architecture.md`
says what exists and why. `git status`, `git log origin/main..`; `make
check`; `make ct-test DISTRO=debian` (then `fedora`, `alma`) around a
change that needs it.

Where things are: commands in `backend/cockpit_fleet/` (one module per area;
`cli.py` the registry, `auth.py` the roles and rights, `db.py` schema 17),
approvals in `approvals.py`, workflows in `workflows.py`, templates
and other resources in `resources.py`, launching in `runs.py`, the queue and
dispatch of jobs in `jobs.py`, command lines of jobs in `runner.py`,
`selfcheck` and the audit in `system.py`, the health of the system in
`health.py`, checks in `checks.py` and `catalog.py`, time series in
`metrics.py`, retention in `maintenance.py`, the CMDB in `cmdb.py`,
notifications in `notify.py` and `channels.py`, the terminal in
`terminal.py`, node modules in
`collection/ansible_collections/lineadicomando/fleet/plugins/modules/`
(`checks.py`, `win_checks.ps1`, `fleet_result.py`), pages in
`src/components/`.

Last test runs, on 25 October with the negative tests and their fixes:
unit tests (461 backend, 93 frontend) and ruff clean; `make ct-test` of
every script on the three, `negative` included, all passing but `cmdb` on
Fedora, where `podman network connect` fails in the container (netavark:
"invalid namespace path"), so the interface it adds to a node never
appears: the environment, not the code. On the VMs of the three, last on
24 October (`privileges sandbox roles runs workflows schedules`, no SELinux
denial), before these fixes. `windows.sh` last on 20 October.

## Decisions

Detail and reasons in `docs/architecture.md`. All confirmed by the user,
except the rows that say "to confirm".

| Topic | Decision |
|---|---|
| Name | `cockpit-fleet`, menu "Fleet", collection `lineadicomando.fleet`, command `fleet`; `fleetctl` stays the backend's (out of `PATH`) |
| License, repo | `LGPL-2.1-or-later`, to confirm at publication; a private Gitea, GitHub once stable |
| Backend | No daemon, no port: units on demand; account `cockpit-fleet`; root only in `fleet-unit`, `fleet-secret` |
| Roles | Groups `cockpit-fleet-admin`, `-operator`, `-viewer`, the highest wins; rights apart (`auth.GROUP_RIGHTS`, a command's `right=`). Viewer: outcomes, not output. Operator: launches, narrows hosts, dry run, answers surveys |
| Approvals | `cockpit-fleet-approver` is a right (`approve`) that adds to any role or stands alone, not a fourth role; administrators approve too; nobody approves their own launch but an administrator; agents never approve; the run is prepared at launch and waits as `pending_approval`, expires after `[approvals] expire_hours` (24) |
| Workflows | Runs of kind `workflow`, with no unit, advanced when a step ends (and by `reconcile`); a step may narrow the limit and answer the survey (non-secret answers), may ask for an approval, and gets what earlier steps passed with `set_stats` (`!unsafe`, above the template's vars, as in AWX); nothing else of its template. A failure fails the workflow unless a step "on failure" follows it ("always" does not count); resume makes a new run carrying the steps that succeeded |
| Controller | Debian 13 `.deb`; Fedora 44 and AlmaLinux 10 `.rpm` (one spec); SELinux enforcing with no policy of ours; distro `ansible-core` ≥ 2.16 |
| Tests | `make ct-test` on the three after changes that need it; the lab's VMs at the close of a phase (SELinux, sandbox on its own kernel, browser, Windows) |
| Test VMs | `cockpit-fleet-deb13`, `-fed44`, `-alma10` (controllers), `-win11` (Windows 11 Enterprise Evaluation node), on the libvirt network `cockpit-fleet`; built from scratch by `test/vm/` (cloud images and cloud-init, Windows Setup with an answer file), snapshot `baseline` without the package; `lab.conf` tracked, `.env` per workstation; agents may build and destroy them, never other VMs |
| Secrets | `systemd-creds`, decrypted only by PID 1 per run; survey secrets via `fleet-secret answer` tickets; never on a command line; masked in output |
| Projects | Git, local directory, written in the interface (git repo beside the tree, admin only), uploaded archive (base64 on stdin, 20 MB by default, `[projects] max_upload_mb` up to 50); `ansible.cfg` keys by allowlist; Galaxy collections installed beside the tree, copied into runs |
| Inventories | A file, a directory of sources or a plugin file, of a project, read in the sandbox; scripts off unless `[inventories] scripts` |
| Ad-hoc | Admin only, modules from `[adhoc] modules`, run as a generated playbook |
| Export/import | One JSON by name, no secrets, no files; import creates, never replaces |
| Agents (MCP) | `fleet-mcp` on stdio, each tool a `fleetctl` call; `[mcp] enabled = false`, `max_role = "operator"`; `via` audited, can only restrict; no tool for secrets; templates with a required secret not tools |
| Chat ("Assistant") | Agent named in `[chat.agents.NAME]` (ACP), installed by the role `chat_agent`, not by the package; started by `fleet-chat` as the user; login per user at a terminal; `[chat] max_sessions` 2; in the page read-only tools unasked, the rest confirmed; `fleet assistant` runs the agent's CLI with `--permission-mode manual` (every call confirmed) in `~/.local/state/cockpit-fleet/assistant` |
| Terminal | `fleet` for the three roles and approvers, `via: cli`; every command as `fleet AREA VERB --param`, names for ids, shortcuts `launch log result secret assistant help`, `project clone/pull/push`; secrets never on the command line; English only until a field-tested version |
| Projects at a terminal | Admins only: `local_roots` dir of group `cockpit-fleet-admin` 2770 + ACL for the service, checked by `selfcheck`; managed projects as git bundles, push only as a fast forward of files the editor could write |
| Schedules | One timer, `scheduler.tick` each minute; `OnCalendar` with time zone; admin edits, operator switches and runs now; answers stored, never secrets; missed > 5 min; overlap skip/queue/cancel |
| Notifications | Telegram, webhook, SMTP, Matrix; outbox delivered by `cockpit-fleet-notify.service` via a path unit; one secret per channel; rules by scope (all, project, template, schedule, inventory, group, check); failed/changed/successful/canceled runs (rejected and expired as canceled; workflow runs too), runs waiting for approval (`approval`), check problem/recovery/reminder, check cycles late/caught_up (scopes all and inventory) |
| Time series | Narrow `WITHOUT ROWID` tables keyed by series and time; hourly and daily aggregates upserted as values arrive (UTC days), so retention only deletes; `metric.query` for viewers; charts under a check on the page of a host, `@patternfly/react-charts` in a chunk of its own |
| Sizing of cycles | `fleet-run` reads `memory.peak` and `cpu.stat` of its cgroup into `result.json`; table `cycles`, 30 days; forks fit `[checks] memory_mb` (0: half the controller's) shared by `2 × max_concurrent`, hosts per shard what they get through in half the shortest interval; `shard_size` and `forks` stay ceilings, sizing only lowers |
| System health | `system.health` for viewers, like `selfcheck` (no configuration in it beyond the memory budget); a section of the System tab |
| Cycles late | A due state held by a cycle is skipped, not doubled, and counted; `late` after `[checks] late_after` due times skipped in a row by a group, `caught_up` when one of its cycles ends with none; rules of scope all or inventory |
| SSH connections | Kept within a run only; the catalog pipelined (playbooks of the user as their project says). Kept by the account: discarded (rule 8). Kept by a unit per group: only if the load test shows that opening connections is the limit. No fact cache: checks gather no facts |
| Checks | Catalog + playbooks ending with `fleet_result`; one task per host, `checks.py` or `win_checks.ps1` by shell; thresholds at ingest; cycles as `check` jobs with their own limit, sharded by inventory/credential/become; soft/hard states, flapping, ack, maintenance; viewer sees, operator acks, admin configures |
| CMDB, updates, drift | Kinds `cmdb`, `updates` and `compliance` (a playbook in `--check --diff`), slow: own cycles, `[checks] slow_timeout`, own dispatch lane; snapshot + changes tables; hardware change = warning for one cycle; viewer sees CMDB, drift diffs operator+ |
| Retention | All in `maintenance.tidy` at each tick: metrics raw 14 d, hourly 90 d, daily 2 y (`[metrics]`); run output 90 d (`[runs] keep_days`, row kept); outbox 90 d, check cycles 1 d (what they took 30 d, a group's delay 30 d without a skip), CMDB changes 365 d; runs, state changes, audit kept. Incremental auto-vacuum, a quarter free before giving back; a limit on state changes later (phase 10), off by default |
| Discarded | MCP servers of projects (Ansible outside the sandbox, keys in clear); the chat as its own package, or hooked by `cockpit-samba-dc`; installing the agent per user from the page, from an apt repo or by a shell recipe; driving `claude` without ACP; a timer per schedule (root writing units from input); a second command name for `fleet` (Rancher's CLI in `/usr/local/bin` would shadow it, left as is); unloading failed run units (kept as systemd does, 15 October) |
| Out of scope | External triggers (future) |

## Leftovers (known limits, none blocking)

- Runs: a failed run leaves its unit failed, so the system is `degraded`
  until `reset-failed`
  (`CollectMode=inactive-or-failed` untried); Cockpit's admin access without
  a group is audited as `root`; integration tests not in CI (no privileged
  containers on the runner).
- Projects: a git token over https untried on a real private repo; with
  `ansible-win_edulab` whether the key or the inventory's password logged in
  was not looked at; the
  uploaded archive is not kept, no progress bar; Galaxy public sources only,
  roles of the requirements file not installed, no job timeout; an inventory
  has one vault credential; inventory plugins get no credential.
- Editor: one file at a time; unsaved changes lost on tab change; syntax
  check without vault password, listed among runs; completion and module
  help know only the controller's modules, no sub-parameters.
- Ad-hoc: arguments as one line in the page, stored in clear.
- Masking: other encodings, and secrets a run decrypts itself, not masked.
- Agents: `tools/list` while disabled audits a denial; `run.result` holds a
  process up to 50 s; 199 of 280 parameters have no description.
- Chat: plain text; conversations not deletable; model not choosable; one
  user can fill the sessions; missing login said only after a first message;
  role tried on x86_64 only, versions pinned in the role, no script runs it,
  untried behind a proxy; `fleet assistant` permission answers not kept,
  read-only calls asked too, Claude's question on trusting its directory
  the first time (it says the agent may edit and run files: it may not);
  the page lists Claude's own `/login` among past conversations; the tab
  appears only once the page is reloaded after the agent is named.
- AlmaLinux: no `ansible`/`ansible-lint` (no lint; collections from the
  requirements file); `selfcheck` leaves `/var/lib/cockpit-fleet/.ansible`.
- Schedules: builder knows few shapes; no end date or window; runs not
  filterable by schedule; a calendar read error leaves no next time; a
  missed or failed launch is not notified by itself.
- Notifications: one message per run, plain text; mail never via proxy;
  channels tried in sequence (a dead one delays the rest ≥ 15 s); a refused
  test retried 24 h; the default webhook body has no title; `fleet channel
  update` wants the name again.
- Checks: host facts only from cycles; one `fleet_result` per host;
  thresholds per check, not per host; `state.list` is not paged (the
  grid of the page of hosts asks for every state: 0.38 s at p95 for 5030;
  at many thousands more it will need paging);
  maintenance windows not exported; inventories re-read hourly, each a run;
  `certificates` on a Windows store lists every expired root it keeps, in
  one long summary.
  Untried: pages as a viewer, Windows checks from Fedora/Alma, multi-shard
  cycles on real nodes.
- Time series and maintenance: ingest writes outside a transaction (0.12 s
  for 5000 values, half in one); aggregates by UTC day; a database of
  before phase 8 above 64 MB is never made incremental (none exists); rows
  of runs, state changes and the audit grow without end; deleting run
  output and giving pages back seen in unit tests and containers only.
- Scale: SSH not weighed (3 nodes beside 500 local aliases are too few to
  say what opening connections costs; connections kept per group stay
  unbuilt unless a test with tens of SSH nodes asks for them); beyond
  ~1000 hosts every 10 minutes, `[checks] max_concurrent = 2`; the run
  directories levelling off after a day not seen (the 24 hours end just
  before the first deletion).
- Sizing: per group, whatever checks a shard holds (one task per host, so
  close enough); a host's time is the cycle's spread evenly; the fit of
  memory may come out flat with few cycles, then all of it counts per fork;
  with up to 1000 hosts it kept the configured shard and forks (half the
  interval far off), so lowering them was never seen outside unit tests.
- Falling behind: counted per inventory and group, not per credential or
  `become` within them; a group turned off while behind is never told back
  on time (its row goes after 30 days); a slow playbook of the user shares
  the lane of the other checks and makes them skip too.
- Health: the last day only, no history; three column headers of the
  cycles cut short at 1400 px ("Inve…", "Interv…", "Durata, …").
- Charts: on the page of a host only, per check; no zoom, no chosen dates,
  no thresholds drawn; the labels of the axes faint on the dark theme.
- Interface (25 October): the summary a node writes for a check is English
  ("accounts 2 (warning 2)"), as are the backend's messages; the counts of
  the states on the page of hosts are headings; a page left open across an
  upgrade of the package cannot load the new bundle's chunks until the user
  logs in again (Cockpit lists a package's files when the session starts);
  pages not seen as a viewer or an operator, nor the chat, in this audit;
  the header "Which check" of the tables of states cut short at 1440 px
  beside Cockpit's menu; with a few hours of values a chart of a day is
  mostly empty.
- CMDB: zypper never had an update to find; dnf without `become` refetches
  metadata into the account's cache; a security-update notification end to
  end not seen.
- Approvals: one approver suffices (no quorum); an approval is not
  notified to the approvers by name, only to channels; a pending run shows
  its launch time as "Started".
- Workflows: no schedule launches a workflow; the launch takes no limit or
  answers of its own; the graph is drawn in columns, not with its arrows;
  `set_stats` per host is not passed on; what a step passed on is kept in
  clear and masked only for known secrets; the steps a workflow starts are
  not in the audit (its launch, cancel and resume are); its commands are
  tools of fleet-mcp like any other (deciding an approval is not); no rule
  scope for a workflow; an approval at a step is seen on the page of runs,
  not on the workflow's.
- Terminal: survey choices not pickable or completed; pushed commits keep
  the authors of the clone (who pushed is audited).
- Security review (24 October), left as they are: a run directory swapped
  for a link between `fleet-unit`'s check and the start binds another
  directory (needs the account itself); the backend shares the runs' uid,
  so anything of a run it followed would reach their credentials through
  `/proc/PID/root` (the files of a run are read without following links);
  webhook bodies carry what nodes said unescaped (Slack and Discord markup,
  mentions); `http://` allowed for channels with a secret; webhook URLs and
  template `extra_vars` visible to viewers (neither is for secrets);
  `config.export` and `inventory.get` by an administrator not audited; an
  operator can fill the 500 waiting secret answers for an hour; `fleet`
  shows run output with its colours raw (escape sequences of nodes); unpack
  jobs have no time limit.
- Negative tests (25 October): a value typed in clear for a secret is
  masked in the audit wherever it appears, so a short one masks other text
  of that record too; the fuzzer gives one hostile value at a time, never
  two together; `chat.start`, `state.ack`, `workflow.resume` and the
  deletes of things in use stop before most of their checks in it.

## Phase 10 — Release 0.1.0

- [x] Security review: root helpers, sandbox, sudoers, archives, git, bundles
      (24 October; fixed: PID 1 following links in `creds/`, a limit that
      widened the template's, run files read through links, notifications
      stopped by a host name, races copying a local project and reading
      its files, audit of refusals; what was left is under Leftovers)
- [x] Automated negative tests: injection, traversal, role escalation, secrets
      (25 October; `test_negative.py` over the whole registry and
      `negative.sh`; fixed: a secret typed in clear recorded in the audit,
      `hostkey.add` taking a pattern, `SUDO_UID` read by `int()`, three
      internal errors on malformed input)
- [x] Accessibility; complete Italian (25 October: PatternFly's English
      text replaced through `pf.tsx`, `<html lang>`, tab panels, alert
      titles, labels of number inputs and groups, contrast of the editor's
      placeholder; Lighthouse 100 on every tab, light and dark)
- [x] Docs: installation, security model, writing a check, backup
      (25 October: `docs/install.md`, `security.md`, `checks.md`,
      `backup.md`; the backup and restore tried on the Debian VM)
- [x] Upgrade and removal (`purge` too), `.deb` and `.rpm` (25 October:
      removal stops runs, purge deletes the data on Debian, rpm reloads
      systemd; `make ct-lifecycle` from phase 8's commit passed on the three
      and on the AlmaLinux VM)
- [x] `.rpm` built in CI (25 October: runners for Fedora 44 and AlmaLinux 10
      on the Gitea host, a job each, rpm-build and rpmlint)
- [x] Screenshots, `CHANGELOG`, tag `v0.1.0` (25 October: eight
      screenshots of the Debian VM in `docs/screenshots/`, in the README;
      `CHANGELOG.md`; the version 0.1.0 wherever it is written, the
      collection's included)
- [ ] GitHub Actions workflow; publication with license confirmed and a
      history with no secrets or real environments

## Future

External triggers (git webhooks, auto-remediation); `ansible-pull` for very
large fleets; secrets on OpenBao or Bitwarden; export to Prometheus or
InfluxDB; execution environments in containers; other controller
distributions.
