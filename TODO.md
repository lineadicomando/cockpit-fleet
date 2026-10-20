# cockpit-fleet — plan

Cockpit plugin that runs Ansible from the browser (Semaphore UI style) and
collects, stores and notifies on data from the nodes, with Ansible only. Not
a replacement for Icinga: nothing in real time, alerts "reasonably timely".

## Status (20 October 2026)

Phases 0–7 closed, the terminal's ideas done and seen live. **Phase 8
under way**: time series (schema 12, `metrics.py`), maintenance (schema
13, `maintenance.py`), the charts (`MetricHistory.tsx`,
`src/lib/metrics.ts`), the sizing of cycles (schema 14, table `cycles`),
cycles that skip and fall behind (schema 15, `cycle_lag`, events `late`
and `caught_up`) and the health of the system (`health.py`,
`system.health`, a section of the System tab) done, committed up to
`94a0e0e`; pushed up to `17b945b`.

**Next: SSH connections and facts, then the load test** (below). The
health section was not yet looked at in a browser: do it on a container
controller (as with the charts) or at the close of the phase.

Then, in phase 8:

- SSH and facts: within a run `ControlMaster=auto`, `ControlPersist=60s`,
  sockets in the run's runtime directory (`runner.py`,
  `ANSIBLE_SSH_CONTROL_PATH_DIR`), gone when it ends. Reusing connections
  between cycles means a socket outliving a run: weigh it against rule 8 of
  `CLAUDE.md` with the user before building anything. The checks' playbook
  has `gather_facts: false` (host facts come from the checks), so "fact
  caching" likely comes to nothing: say so rather than add it.
- Load: `test/integration/measure-events.sh` (hosts as local aliases of the
  controller, local connection) is where a load script starts from;
  measured ~0.6 s per host on 2 vCPUs, CPU-bound. At the target, 14 days
  of raw values are about 500 MB. The "done when" needs a controller with
  4 GB and 24 hours: a machine, not a container.

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
told behind and back on time). Phase 8 not yet on machines.
On machines (SELinux enforcing, no denial): every script at `3dc0e38` on
the three; `cmdb.sh` at `a26bd69` on the three; `windows.sh` on Debian
against Windows 11 the same day and again on 20 October. Not yet on
machines: the schedules fix (`d7708be`); the terminal's changes only by
hand on Debian, not `cli.sh`, and not on Fedora or AlmaLinux. At the
close of phase 8, on the three machines: every script (they cover all of
the above), the charts and the health page in a browser, the load test.
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
| Notifications | Telegram, webhook, SMTP, Matrix; outbox delivered by `cockpit-fleet-notify.service` via a path unit; one secret per channel; rules by scope (all, project, template, schedule, inventory, group, check); failed/changed/successful/canceled runs, check problem/recovery/reminder |
| Time series | Narrow `WITHOUT ROWID` tables keyed by series and time; hourly and daily aggregates upserted as values arrive (UTC days), so retention only deletes; `metric.query` for viewers; charts under a check on the page of a host, `@patternfly/react-charts` in a chunk of its own |
| Sizing of cycles | `fleet-run` reads `memory.peak` and `cpu.stat` of its cgroup into `result.json`; table `cycles`, 30 days; forks fit `[checks] memory_mb` (0: half the controller's) shared by `2 × max_concurrent`, hosts per shard what they get through in half the shortest interval; `shard_size` and `forks` stay ceilings, sizing only lowers |
| System health | `system.health` for viewers, like `selfcheck` (no configuration in it beyond the memory budget); a section of the System tab |
| Cycles late | A due state held by a cycle is skipped, not doubled, and counted; `late` after `[checks] late_after` due times skipped in a row by a group, `caught_up` when one of its cycles ends with none; rules of scope all or inventory |
| Checks | Catalog + playbooks ending with `fleet_result`; one task per host, `checks.py` or `win_checks.ps1` by shell; thresholds at ingest; cycles as `check` jobs with their own limit, sharded by inventory/credential/become; soft/hard states, flapping, ack, maintenance; viewer sees, operator acks, admin configures |
| CMDB, updates, drift | Kinds `cmdb`, `updates` and `compliance` (a playbook in `--check --diff`), slow: own cycles, `[checks] slow_timeout`, own dispatch lane; snapshot + changes tables; hardware change = warning for one cycle; viewer sees CMDB, drift diffs operator+ |
| Retention | All in `maintenance.tidy` at each tick: metrics raw 14 d, hourly 90 d, daily 2 y (`[metrics]`); run output 90 d (`[runs] keep_days`, row kept); outbox 90 d, check cycles 1 d (what they took 30 d), CMDB changes 365 d; runs, state changes, audit kept. Incremental auto-vacuum, a quarter free before giving back; a limit on state changes later (phase 10), off by default |
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
  thresholds per check, not per host; `state.list` returns everything;
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
  seen in unit tests and containers only, not with many hosts.
- Falling behind: counted per inventory and group, not per credential or
  `become` within them; a group turned off while behind is never told back
  on time (its row goes after 30 days); a slow playbook of the user shares
  the lane of the other checks and makes them skip too.
- Health: the last day only, no history; seen through `fleetctl` in a
  container, not yet in a browser.
- Charts: on the page of a host only, per check; no zoom, no chosen dates,
  no thresholds drawn; seen in a browser on a container only, with values
  made up for the past.
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
- [ ] Fact caching, SSH connections reused between cycles
- [x] A cycle that does not end before the next is skipped and reported
- [x] "System health" page: cycle durations, queue, database size
- [ ] Load tests with 200, 500, 1000 simulated hosts (measured: ~0.6 s per
      host on 2 vCPUs, CPU-bound; give the controller 4 GB)

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
- [ ] Accessibility; complete Italian (PatternFly's "Input", "Kebab toggle", "Copyable input")
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
