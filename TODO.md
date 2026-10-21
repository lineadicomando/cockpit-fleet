# cockpit-fleet — plan

Cockpit plugin that runs Ansible from the browser (Semaphore UI style) and
collects, stores and notifies on data from the nodes, with Ansible only. Not
a replacement for Icinga: nothing in real time, alerts "reasonably timely".

## Status (22 October 2026)

Phases 0–7 closed, the terminal's ideas done and seen live. **Phase 8
under way**, all of it done but the interface at 500 hosts and the 24
hours of the load test: time series (schema 12, `metrics.py`),
maintenance (schema 13, `maintenance.py`), the charts
(`MetricHistory.tsx`, `src/lib/metrics.ts`), the sizing of cycles (schema
14, table `cycles`), cycles that skip and fall behind (schema 15, table
`cycle_lag`, events `late` and `caught_up`, `[checks] late_after`), the
health of the system (`health.py`, `system.health`, the first section of
the System tab, `src/lib/health.ts`), the catalog pipelined
(`runner.check_env`), and the load test (`test/integration/load.sh`,
`load-sample.py`). All pushed (`864854c`), CI green.

**The load test so far** (numbers in `docs/architecture.md`, Checks, "At
scale"): on a Debian machine with 2 vCPUs and 4 GB, every 10 minutes. The
24 hours with 500 hosts were started on 21 October at 14:05 and stopped
after 10 hours by the shutdown of the host that runs the VM: they are to
be done again. Those 10 hours (package of `76a98b9`):

- **The cycles hold**: 1289, all successful, none skipped, no group
  behind, the oldest result 1.13 intervals old; 20 cycles per interval,
  one at a time, done in ~3 minutes; memory never under 2.8 GB
  available, no swap. At 1000 hosts (30 min, earlier) the last cycle
  waited 266 s of the 300 allowed: beyond that, `max_concurrent = 2`.
- **`state.list` fails at 500 hosts already**: 2.5 s at p95 and 3.9 s at
  most, against the 2 s and 5 s of `load.sh` (in the first 1.6 h it had
  passed). It returns every state, 5030 here, and is slowest while a
  cycle's results are taken in. `host.list` passes, close (1.6 s at p95,
  3.1 s at most); `run.list`, `metric.query`, `system.health` under 0.5 s
  at p95. A 24-hour run on this code would fail on it again.
- **Disk**: the database +65 MB a day (raw metrics kept 14 days, so it
  levels off later; state changes and runs' rows are kept for ever); run
  directories +400 MB a day, but those of check cycles are deleted after
  a day (`checks.py`, not `prune_runs`), so they level off at about a
  day's worth: a 24-hour run ends just before the first deletion and
  cannot show it.
- SSH is not settled: 3 nodes are too few to weigh what opening
  connections costs. Connections kept per group stay unbuilt (Decisions,
  "SSH connections") unless a test with tens of SSH nodes says otherwise.

**Next** (proposed on 22 October, to confirm with the user: fix first,
then the 24 hours once, on the code that closes the phase; the other way
is to run the 24 hours again now, which would fail on `state.list`):

1. **Make `state.list` fast at 500 hosts.** It is `checks.state_list`
   (`backend/cockpit_fleet/checks.py`), called only by
   `src/components/HostsPage.tsx` (`filter`: `problems`, `inventory_id`,
   `check_id`, `host_id`) and timed by `load.sh` (line `timed state.list`,
   with no filter: every state, the heaviest call; if the page stops
   making it, time the calls it makes instead). The page's
   default view, "Problems", needs only what is not ok and the counters;
   the "Host" and "Grid" views need every state. Options: page it
   (`limit`/`offset` or a cursor, the counters of the whole selection
   kept), let the views ask only what they show, and look at what makes
   it slow under ingest (`jobs.reconcile` at its start, `_states`
   building one dict per row with maintenance windows per host). Measure
   before and after on a database of 500 hosts (`load.sh` with
   `IT_KEEP=yes`, or the samples' sizes). Then `host.list`, close to the
   limit, the same way if needed.
2. **Then the 24 hours with 500 hosts**, the "done when" of the phase. It
   needs a controller with 4 GB left on for a day (and the host that runs
   it too): deploy the package, copy `test/integration` to it, start
   `load.sh` with `systemd-run` as its header says (`IT_LOAD_HOSTS=500`,
   `IT_LOAD_MINUTES=1440`), read `/var/tmp/fleet-load/*/summary` at the
   end. A run stopped early leaves its samples there but no summary (its
   data is wiped by the next run): `python3 load-sample.py summary
   SAMPLES 600 1200` writes it, read-only, also while the test runs. To
   see the run directories level off, let it go a few hours past a day.
3. **Close the phase on Debian**: every script on the machine of the load
   test, after it (they wipe its data anyway), as on Fedora and AlmaLinux
   (21 October, all passed, below). The health section and the charts
   were seen in a browser during the load test (below).

To resume: read `CLAUDE.md` (rules, commands, tests) and `CLAUDE.local.md`
if present (this developer's VMs and their state); `docs/architecture.md`
says what exists and why, its last section how each phase was accepted.
`git status`, `git log origin/main..`; `make check`; `make ct-test
DISTRO=debian` (then `fedora`, `alma`) around a change that needs it.

Where things are: commands in `backend/cockpit_fleet/` (one module per area;
`cli.py` the registry, `db.py` schema 15), the queue and dispatch of jobs in
`jobs.py`, command lines of jobs in `runner.py`, `selfcheck` and the audit in
`system.py`, the health of the system in `health.py`, checks in `checks.py` and
`catalog.py`, time series in `metrics.py`, retention in `maintenance.py`,
the CMDB in `cmdb.py`, the terminal in `terminal.py`, node modules in
`collection/.../plugins/modules/` (`checks.py`, `win_checks.ps1`), pages in
`src/components/`.

Last test runs: in containers (`make ct-test`) on 16 October, the whole
suite on Debian at `a551049`; `cli`, `chat`, `cmdb` on the three, `checks`
on Debian and AlmaLinux; on 20 October `chat` on Debian and AlmaLinux at
`557dd22`, then `checks` on Debian and AlmaLinux and `runs` on Debian with
the tree of `d7d3819`; the charts (`849303f`) with `checks` on Debian,
then looked at in a browser on a container controller with a month of
made-up values (one line and several, a gap, day to year); the sizing of
cycles (`6f20a5b`) with `checks` on the three and `runs` on Debian, the
memory and processor time read in the sandbox; skipped cycles and health
with `checks` on Debian and AlmaLinux (a playbook of 90 s every 60 s,
told behind and back on time); the catalog pipelined, the tree after
`d0cb93f`, with `checks` and `cmdb` on Debian and AlmaLinux (no temporary
file on a node, with become and without); `load` on Debian (30 hosts
and 2 nodes every 60 s, 12 min). Not run in containers since 16
October: every script but `checks`, `cmdb`, `runs`, `chat` and `load`;
Fedora had only `checks` (the sizing). Phase 8 on a machine: `load.sh`
on Debian at `eaeebc4` (21 October, above), and 10 hours of the 24 at
`76a98b9`; on 21 October every script but
`windows`, `measure-events` and `load` on Fedora 44 and AlmaLinux 10 at
`76a98b9`, all passed, no SELinux denial (`schedules` once the test
created its ten schedules just after a tick: a minute crossed while it
created them gave 17 runs, the scheduler right). Run as root from
`systemd-run`, a script needs `-p
SELinuxContext=unconfined_u:unconfined_r:unconfined_t:s0`: in
`initrc_t`, `ssh-keygen` cannot write in `/run` and the nodes never
start; and the RPM machines needed `acl` for `cli.sh` (installed). In a
browser as a viewer, on Debian with the 500 hosts of the load test: the
health section, the list of hosts, the page of a host and its charts
with real values, all fine.
On machines (SELinux enforcing, no denial): every script at `3dc0e38` on
the three; `cmdb.sh` at `a26bd69` on the three; `windows.sh` on Debian
against Windows 11 the same day and again on 20 October. Not yet on
machines: the schedules fix (`d7708be`); the terminal's changes only by
hand on Debian, not `cli.sh`, and not on Fedora or AlmaLinux. At the
close of phase 8, on the three machines: every script (they cover all of
the above), the charts and the health page in a browser.
Live on Debian, 20 October: Linux and Windows nodes in one Assets list;
"At a terminal" of a launch, a run and an upload, each command copied and
run; the chat's agent installed by its playbook, the page and `fleet
assistant` with Claude. Fixed then: the playbook failed on `os_family`;
`fleet assistant` started in Claude's "auto" mode, calling tools unasked
(now `--permission-mode manual`), and asked to trust the home at every
start (now a directory of its own).

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
| Tests | `make ct-test` on the three after changes that need it; machines at the close of a phase (SELinux, sandbox on its own kernel, browser, Windows) |
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
  thresholds per check, not per host; `state.list` returns everything
  (2.5 s at p95 with 5030 states over 10 h: Next, 1);
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

## Phase 8 — Time series and scale (under way)

- [x] Narrow metrics table, hourly and daily aggregates, retention
- [x] Maintenance: deletion (run directories too), vacuum (downsampling
      needs no pass: aggregates are kept at ingest)
- [x] Charts (`@patternfly/react-charts` 8, a chunk loaded on demand)
- [x] Shards and forks sized on measured duration and RAM
- [x] SSH: the catalog pipelined; connections between cycles discarded or
      left to the load test, no fact cache (nothing gathered)
- [x] A cycle that does not end before the next is skipped and reported
- [x] "System health" page: cycle durations, queue, database size
- [ ] Load tests with 200, 500, 1000 simulated hosts, through real cycles
      (`load.sh`; 200, 500 and 1000 run for an hour or less, 500 for 10
      hours; `state.list` too slow at 500; the 24 hours to do after it)

Done when: 500 simulated hosts with 10 checks every 10 minutes run 24 hours
with no backlog and a responsive interface.

## Phase 9 — Workflows and approvals

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
