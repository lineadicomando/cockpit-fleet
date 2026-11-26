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
  `tools/fleet_skills.py`), but the collection's came after its tag v0.9.1:
  it needs a new tag and `requirements.yaml` of win_edulab pointing to it.
  Then, on a controller, the project synchronised and its collections
  installed again, and the work of a lab's maintenance done from the chat
  with those skills, without going back to the repository.
- **24, skills in the documents** (`architecture/documents.md`, "Skills
  of the documents"; `chat.md`, "Skills"): shown in containers and in a
  browser on `deb13` (26 November). What remains: the user, with Claude as
  the assistant, asks it to propose a skill for a task that recurs,
  approves it in the page, and a new session uses it.

## To look at

Found by the scenarios (26 November), not yet decided:

- Text the backend writes stays English in the Italian interface: titles
  of check cycles ("laboratorio: 4 hosts", `checks.py`), a workflow's error
  ("accensione: failed").
- The list of checks polls only while a cycle holds a check: a cycle of
  the timer shows only once the page is loaded again.
- Under the group of an inventory, the list of templates shows a
  template's last run on any inventory.

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
