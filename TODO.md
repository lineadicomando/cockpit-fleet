# cockpit-fleet — plan

What is left. How each closed phase was shown:
`docs/architecture/acceptance.md`; this workstation and its lab VMs:
`CLAUDE.local.md` (not tracked).

## Status (27 November 2026)

Phases 0–19 closed; 20–24 built, each waiting only for its close below.
21–24 were the last to add features (`decisions.md`, "Freeze of
features"): then fixes, minor changes and field use, toward 1.0.0. Last
release 0.4.0 (18 November); since then: `[Unreleased]` of `CHANGELOG.md`.
`make release` only when asked.

To resume: `git status`, `git log origin/main..`; `make check` (876
backend, 244 frontend tests). Each backend module's docstring says what it
is; `cli.py` the registry of commands, `db.py` the schema (41
migrations). Under way: the closes of 20–24 on the lab's VMs, then
the integration there (`sandbox` run D, SELinux).

## To study

- **The names of `overview.md` and `dashboard.md`** (`docs/architecture/`).
  The first is "Overview and security model" (the parts, a run's flow,
  privilege boundaries, secrets, the sandbox: what `CLAUDE.md`'s rules
  cite), the second "The Overview", the first tab of the page
  (`overview.get`): a search for one finds the other. To weigh, before
  renaming anything:
  - the names: `security-model.md` for the first (then what of its
    section on the parts and the flow: there, in `backend.md`, or a short
    file of its own), `overview-page.md` or as it is for the second,
    whose command and component are `overview`; `docs/security.md`, the
    administrator's, must not be taken for it;
  - what cites them: 7 references to `overview.md` in 5 files
    (`CLAUDE.md`, the index, `checks.md`, `interface.md`,
    `docs/security.md`), 5 to `dashboard.md` in 4, `docs/history/dashboard.md`
    beside it, the sections cited by name ("Secrets");
  - what breaks outside: links from the installed documents and from
    released packages and the README on GitHub to the old paths;
  - whether a line at the top of each ("not the page: see …") is enough.

## Built, waiting for their close

What each is: the file named. What remains:

- **20, documents** (`architecture/documents.md`): the user, with Claude
  as the assistant: a report of a run written in a folder, edited by hand,
  the agent's version restored, printed to PDF from the page.
- **21, inventory chosen at launch** (`architecture/inventories.md`):
  scenario `labs-launch` on `deb13` showed the rest (26 November). Left:
  the workflow launched on `aula-1` to its end; the agent choosing the
  inventory (the user, with Claude).
  A template with no default inventory (27 November, `decisions.md`):
  built; `ct-test` passed but for the known failures of containers
  (`architecture/tests.md`); left, seen in a browser (its form, the
  launch, a schedule and a workflow that must choose).
- **22, check cycles in view** (`checks.md`, "Following the cycles"):
  scenario `checks-cycles` in a container. Left: the user follows the
  cycles of the labs' checks on a controller.
- **23, skills of projects** (`architecture/agents.md`, "Skills"): pushed
  in `ansible-win_edulab` (`win-edulab-operations`, `-inventory`,
  `-reports`) and in `ansible-collection-win_workman` (`win-workman`, tag
  v0.9.2, pinned by win_edulab's `requirements.yaml`). Left: on a
  controller, the project synchronised, its collections installed again,
  a lab's maintenance done from the chat with those skills alone.
- **24, skills in the documents** (`architecture/documents.md`, "Skills
  of the documents"): shown in containers and on `deb13`. Left: the user,
  with Claude, has it propose a skill for a recurring task, approves it in
  the page, and a new session uses it.
- **Assistant without questions** (27 November, admitted under the
  freeze, `decisions.md`; `architecture/chat.md`): one consent per ad-hoc
  command, and "Without questions" in `administrator` (`[chat]
  unattended`). Shown: `ct-test` (`mcp chat adhoc negative`) on the three;
  the user with Claude on a Windows node, the switch on (runs marked
  `unattended`) and off (one "Allow once", approval "confirmed in the chat
  page"). Nothing left: to `acceptance.md` at the next close.

## Integration

Last runs, 26 November. Containers, the three distributions: `runs`,
`schedules`, `workflows`, `negative`, `checks`, `chat`, `mcp`, `cli`,
`documents`, `store`, `sandbox` (its run D in containers only); Debian
only: `netscan`, `make ct-lifecycle` (from 0.3.0). VMs: `netscan` on the
three (`win11` up), `windows` on `deb13`; `runs`, `workflows`,
`schedules` on `fed44` (SELinux, no denial); `sandbox` on `deb13` and
`alma10` before run D. Earlier: `files` in containers.

## Future

After the freeze, only if field use shows one blocking.

- Ad-hoc commands: sets of variables chosen for one (today the inventory's).
- External triggers (git webhooks, auto-remediation); `ansible-pull` for
  very large fleets.
- Secrets on OpenBao or Bitwarden; export to Prometheus or InfluxDB.
- Execution environments in containers; other controller distributions.
- A limit on the rows of state changes (off by default).
- Store: credentials for downloads, parts kept for some projects.
- Overview: a filter by inventory.
