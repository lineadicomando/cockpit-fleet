# cockpit-fleet — plan

What is left. Other documents: `CLAUDE.md`; how each closed phase was shown:
`docs/architecture/acceptance.md`; this workstation and its lab VMs:
`CLAUDE.local.md` (not tracked).

## Status (26 November 2026)

Phases 0–18 closed; 19–24 built, each waiting only for the close below;
no phase is next. Phases 21–24 were the last that add features
(`decisions.md`, "Freeze of features"): then fixes, minor changes and
field use only, toward 1.0.0. Last release 0.4.0 (18 November);
`[Unreleased]` of `CHANGELOG.md` has what came since. `make release` only
when asked.

To resume: `git status`, `git log origin/main..`; `make check` (856
backend, 214 frontend tests). Each backend module's docstring says what it
is; `cli.py` is the registry of commands, `db.py` the schema (38
migrations).

## Built, waiting for their close

Each is described in the file named; what remains is the close.

- **19, network discovery** (`architecture/network.md`): `win_net_probe` on
  `win11` (on the workstation that has it); `netscan.sh` with
  `IT_NETSCAN_SUBNET` on the lab's network with `win11` up (NetBIOS, its
  MAC address), on `alma10` and `fed44` too, and whether ICMP is allowed as
  installed (Debian 13 allows it); the page in a browser.
- **20, documents** (`architecture/documents.md`): the user, with Claude as
  the assistant: a report of a run written in a folder, edited by hand, the
  agent's version restored, printed to PDF from the page.
- **21, inventory chosen at launch** (`architecture/projects.md`, "The
  inventory chosen at launch"): on a controller `wu-run` and `wol` from the
  page on two inventories, a schedule each, a workflow launched on two, the
  agent choosing the inventory.
- **22, check cycles in view** (`checks.md`, "Following the cycles";
  `architecture/checks.md`): the user follows the cycles of the labs'
  checks on a controller.
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
  of the documents"; `chat.md`, "Skills"): shown in containers (`mcp`,
  `documents`, `chat`, the three distributions) and in a browser on
  `deb13` with an agent's writes made through `fleetctl` (26 November).
  What remains: the user, with Claude as the assistant, asks it to
  propose a skill for a task that recurs, approves it in the page, and a
  new session uses it.

## Integration

Last run, 26 November, in containers on the three distributions: `runs`,
`schedules`, `workflows`, `negative`, `checks`, `chat`, `mcp`, `cli` (on
AlmaLinux `checks` on the VM `alma10` instead: `architecture/tests.md`,
known failures); on the VM `fed44` `runs`, `workflows`, `schedules`
(SELinux, no denial). Earlier: `documents`, `store`, `files` in containers;
`make ct-lifecycle` on Debian; `sandbox` on `deb13` and `alma10`.

## What the review of 16 November left

Left by the user for another session; checked against the code on 18
November. The four to fix were fixed on 26 November.

Fix, or record in `limits.md`:

- [ ] `fleet-run` keeps the unmasked error in `result.json` (no message
  holds a secret today).
- [ ] Two `notify_states` (`checks.py`) together notify twice.
- [ ] `_last_stats` (`jobs.py`) reads the last 256 KiB of `events.jsonl`:
  stats and artifacts lost past ~1900 hosts.
- [ ] `moved_rows` (`storage.py`) can fail on a stale row after
  `store.move` moved the file.
- [ ] Files: a tab kept while its read was pending spins for ever (`kept`
  in `FilesPage.tsx`).
- [ ] Approvals: the decide modal vanishes when another decides
  (`PendingApprovals`).
- [ ] Survey: "None" on an optional choice with a default gives the
  default (`collectAnswers`).
- [ ] Installed docs (`%doc ... docs` in the spec) link to `decisions.md`
  and `TODO.md`, not packaged; the rpm has no `CHANGELOG.md`.
- [ ] `security.md` repeats limits of `limits.md`.

For the user to decide:

- [ ] A playbook can rewrite its `result.json` and turn a failed run into
  a successful one (the run writes its own directory).
- [ ] A store folder is drawn whole, without paging (the documents too:
  the same `FileManager`).
- [ ] No component tests for the frontend.
- [ ] GitHub actions pinned by tag, not by commit.

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
