# cockpit-fleet — plan

Cockpit plugin that runs Ansible from the browser (Semaphore UI style) and
collects, stores and notifies on data from the nodes, with Ansible only. Not
a replacement for Icinga: nothing in real time, alerts "reasonably timely".

## Status (23 October 2026)

Phases 0–8 closed (how each was accepted: the last section of
`docs/architecture.md`); the terminal's ideas done and seen live. All
pushed (`2cb9996`), CI green. **Next: phase 9**, workflows and approvals
(below); nothing of it exists yet.

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
`cli.py` the registry, `auth.py` the roles, `db.py` schema 15), templates
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

Last test runs, all at `3c42c70` (the code of phase 8's close; `2cb9996`
changed only the docs): every script but `windows`, `measure-events` and
`load` on machines of the three distributions (Fedora 44 and AlmaLinux 10
on 21 October at `76a98b9`, SELinux enforcing, no denial; Debian 13 on 23
October), `make ct-test` of `sandbox privileges runs checks` on the
three. `windows.sh` last on 20 October, on Debian against Windows 11. The
health section and the charts seen in a browser on Debian only.

## Decisions

Detail and reasons in `docs/architecture.md`. All confirmed by the user,
except the rows that say "to confirm".

| Topic | Decision |
|---|---|
| Name | `cockpit-fleet`, menu "Fleet", collection `lineadicomando.fleet`, command `fleet`; `fleetctl` stays the backend's (out of `PATH`) |
| License, repo | `LGPL-2.1-or-later`, to confirm at publication; a private Gitea, GitHub once stable |
| Backend | No daemon, no port: units on demand; account `cockpit-fleet`; root only in `fleet-unit`, `fleet-secret` |
| Roles | Groups `cockpit-fleet-admin`, `-operator`, `-viewer`; `-approver` in phase 9. Viewer: outcomes, not output. Operator: launches, narrows hosts, dry run, answers surveys |
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
| Terminal | `fleet` for the three roles, `via: cli`; every command as `fleet AREA VERB --param`, names for ids, shortcuts `launch log result secret assistant help`, `project clone/pull/push`; secrets never on the command line; English only until a field-tested version |
| Projects at a terminal | Admins only: `local_roots` dir of group `cockpit-fleet-admin` 2770 + ACL for the service, checked by `selfcheck`; managed projects as git bundles, push only as a fast forward of files the editor could write |
| Schedules | One timer, `scheduler.tick` each minute; `OnCalendar` with time zone; admin edits, operator switches and runs now; answers stored, never secrets; missed > 5 min; overlap skip/queue/cancel |
| Notifications | Telegram, webhook, SMTP, Matrix; outbox delivered by `cockpit-fleet-notify.service` via a path unit; one secret per channel; rules by scope (all, project, template, schedule, inventory, group, check); failed/changed/successful/canceled runs, check problem/recovery/reminder, check cycles late/caught_up (scopes all and inventory) |
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

## Open point

- The chat's agent role on Fedora and AlmaLinux (before phase 10): installs
  `xz` there, never run on either (on Debian it now runs, 20 October).

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
- CMDB: zypper never had an update to find; dnf without `become` refetches
  metadata into the account's cache; a security-update notification end to
  end not seen.
- Terminal: survey choices not pickable or completed; pushed commits keep
  the authors of the clone (who pushed is audited).

## Phase 9 — Workflows and approvals

To decide with the user before starting: whether the approver is a fourth
role in the order viewer < operator < admin (`auth.ROLES`) or a group that
adds a right to any role; whether anyone may approve their own launch (the
"done when" says another user); what a workflow step may override of its
template (limit, survey answers). Each new group needs `sysusers`,
`sudoers` (`fleetctl` only) and `GROUP_ROLES` in `auth.py`.

- [ ] Approvals: critical template, run `pending_approval`, a second user of
      `cockpit-fleet-approver` (sysusers, sudoers), expiry, notification, audit
- [ ] Workflows: graph of templates on success/failure/always, `set_stats`
      between steps, editor and view
- [ ] Cancel, and resume from a failed step

Done when: a three-step workflow with an approval at the second stops, is
approved by another user and completes.

## Phase 10 — Release 0.1.0

- [ ] Security review: root helpers, sandbox, sudoers, archives, git, bundles
- [ ] Automated negative tests: injection, traversal, role escalation, secrets
- [ ] Accessibility; complete Italian (PatternFly's "Input", "Kebab toggle", "Copyable input", "Details" of an expandable row)
- [ ] Docs: installation, security model, writing a check, backup
- [ ] Upgrade and removal (`purge` too), `.deb` and `.rpm`
- [ ] `.rpm` built in CI (runner is Debian)
- [ ] Screenshots, `CHANGELOG`, tag `v0.1.0` (version is `0.0.1` in
      `package.json`, `debian/changelog`, the spec)
- [ ] GitHub Actions workflow; publication with license confirmed and a
      history with no secrets or real environments

## Future

External triggers (git webhooks, auto-remediation); `ansible-pull` for very
large fleets; secrets on OpenBao or Bitwarden; export to Prometheus or
InfluxDB; execution environments in containers; other controller
distributions.
