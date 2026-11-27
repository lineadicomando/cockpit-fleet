# cockpit-fleet — plan

What is left. How each closed phase was shown:
`docs/architecture/acceptance.md`; this workstation and its lab VMs:
`CLAUDE.local.md` (not tracked).

## Status (26 November 2026)

Phases 0–19 closed; 20–24 built, each waiting only for the close below.
Phases 21–24 were the last that add features (`decisions.md`, "Freeze of
features"): then fixes, minor changes and field use only, toward 1.0.0.
Last release 0.4.0 (18 November); `[Unreleased]` of `CHANGELOG.md` has
what came since. `make release` only when asked.

To resume: `git status`, `git log origin/main..`; `make check` (864
backend, 224 frontend tests). Each backend module's docstring says what it
is; `cli.py` is the registry of commands, `db.py` the schema (38
migrations). Under way: the scenarios (below), tooling of the tests.

## Built, waiting for their close

Each is described in the file named; what remains is the close.

- **20, documents** (`architecture/documents.md`): the user, with Claude as
  the assistant: a report of a run written in a folder, edited by hand, the
  agent's version restored, printed to PDF from the page.
- **21, inventory chosen at launch** (`architecture/inventories.md`, "The
  inventory chosen at launch"). Shown by the scenario `labs-launch` on
  `deb13` (26 November): `wu-run` from the page on `aula-1` (Windows Update
  on `win11`, 1 h 14 min, a restart), `wol` on `aula-2` (the PC off,
  failed as it should), a schedule made in the page for `aula-2` beside
  `aula-1`'s, the workflow launched on `aula-2`. What remains: the workflow
  launched on `aula-1` to its end; the agent choosing the inventory (the
  user, with Claude).
- **22, check cycles in view** (`checks.md`, "Following the cycles";
  `architecture/checks.md`): the scenario `checks-cycles` goes through it in
  a container (26 November). What remains: the user follows the cycles of
  the labs' checks on a controller.
- **23, skills of projects** (`architecture/agents.md`, "Skills";
  `chat.md`, "Skills"): the skills are pushed in `ansible-win_edulab`
  (`win-edulab-operations`, `-inventory`, `-reports`) and in
  `ansible-collection-win_workman` (`win-workman`, its catalog written by
  `tools/fleet_skills.py`, in its tag v0.9.2, which `requirements.yaml`
  of win_edulab pins). What remains: on a controller, the project
  synchronised and its collections installed again, and the work of a
  lab's maintenance done from the chat with those skills, without going
  back to the repository.
- **24, skills in the documents** (`architecture/documents.md`, "Skills
  of the documents"; `chat.md`, "Skills"): shown in containers and in a
  browser on `deb13` (26 November). What remains: the user, with Claude as
  the assistant, asks it to propose a skill for a task that recurs,
  approves it in the page, and a new session uses it.

## Decided, to do

Found by the scenarios (26 November), decided on 27 November.

- **The list of checks following the cycles of the timer** (to choose).
  `ChecksPage` polls `check.list` every 5 s only while a cycle holds a
  check (`checking`, or an active `last_cycle`): a cycle the tick of the
  scheduler queues (each minute, `cockpit-fleet-scheduler.timer`) shows
  only once the page is loaded again. Ways found:
  - a slow poll while nothing runs (30 s) beside the 5 s one, paused
    while the tab is hidden (`visibilitychange`, as `StorePage` does):
    a few lines in the page, at most 30 s late, two calls of `fleetctl` a
    minute with the page open and seen; `HostsPage` polls every 15 s,
    `ApprovalsPage` every 5 s, always;
  - a reload at the next due time: `check.list` gives each check the
    earliest `next_at` of its states, the page reloads just after the
    tick that follows it; no call while nothing is due, but blind to a
    cycle someone else starts ("Run now", an agent, `fleet`) and to the
    end of a maintenance;
  - systemd's signals on D-Bus (`cockpit.dbus`, `Subscribe`; the end of
    `cockpit-fleet-scheduler.service`, a new `cockpit-fleet-run@` unit):
    at once and free while idle, and would serve Runs, Templates and the
    Overview too; but a second channel from the page beside `fleetctl` (a
    change of design, `architecture/overview.md`), tied to the names of
    the units, to be tried under SELinux.

  Proposed: the first now, a fix within the freeze, the pause of a hidden
  tab in `usePolling` so that every page that polls has it; the third,
  if wanted, under Future.
- **The last run of a template under an inventory** (to fix). Grouped by
  inventory, a template is listed under each inventory it allows, and its
  "Last run" is its last on any (`_last_runs`, `resources.py`): under
  `aula-2` a run on `aula-1`. A row speaks of what it is listed under, as
  its Launch does already:
  - `template.list` and `template.get` give `last_runs` by inventory
    beside `last_run` (`runs.inventory_id` is the inventory chosen at
    launch: no migration), and `last_run` its `inventory_id`;
  - under an inventory, "Last run" is the last there; with none, "Never
    here" and, muted, the last elsewhere with its inventory, so that the
    history is not hidden; the sort by last run and the filter by outcome
    take the same (a template failed on `aula-1` and good on `aula-2`
    shows, filtering failures, under the first alone);
  - grouped by project, by outcome or not at all, the last on any, with
    its inventory beside it when it is not the one of the column;
  - tests in `test/templates.test.ts`, the page's and `template.list`'s;
    `architecture/inventories.md` updated.

## Integration

Last runs, 26 November. Containers, the three distributions: `runs`,
`schedules`, `workflows`, `negative`, `checks`, `chat`, `mcp`, `cli`,
`documents`, `store`, `sandbox` (its run D in containers only); `netscan`
on Debian; `make ct-lifecycle` on Debian (from 0.3.0). VMs: `netscan` on
`deb13`, `alma10`, `fed44` (`IT_NETSCAN_SUBNET`, `win11` up), `windows` on
`deb13` with `win11`; `runs`, `workflows`, `schedules` on `fed44` (SELinux,
no denial); `sandbox` on `deb13` and `alma10` before run D. Earlier:
`files` in containers.

## Future

Not planned as phases; after the freeze, only if field use shows one
blocking (`decisions.md`, "Freeze of features").

- Ad-hoc commands: sets of variables chosen for one (today only the
  inventory's).
- External triggers (git webhooks, auto-remediation); `ansible-pull` for
  very large fleets.
- Secrets on OpenBao or Bitwarden; export to Prometheus or InfluxDB.
- Execution environments in containers; other controller distributions.
- A limit on the rows of state changes (off by default).
- Store: credentials for downloads, parts kept for some projects.
- Overview: a filter by inventory.
