# cockpit-fleet — plan

What is left. Other documents: `CLAUDE.md`; how each closed phase was shown:
`docs/architecture/acceptance.md`; this workstation and its lab VMs:
`CLAUDE.local.md` (not tracked).

## Status (25 November 2026)

Phases 0–18 closed; 19 and 20 built, each waiting only for its close
below. Last release 0.4.0 (18 November); `[Unreleased]` of `CHANGELOG.md`
has what came since. `make release` only when asked.

To resume: `git status`, `git log origin/main..`; `make check` (831
backend, 212 frontend tests). Each backend module's docstring says what it
is; `cli.py` is the registry of commands, `db.py` the schema (37
migrations).

## Phase 19: network discovery

Decided 20 November (`decisions.md`, "Network discovery"); what exists:
`architecture/network.md` (scan, inventory from a scan, MAC addresses,
`netscan.probe`, `net_probe` from a node). `netscan.sh` passes in
containers on the three distributions.

- [ ] Close: `win_net_probe` on `win11` (on the workstation that has it);
  `netscan.sh` with `IT_NETSCAN_SUBNET` on the lab's network with `win11`
  up (NetBIOS, its MAC address); on `alma10` and `fed44` the same, and
  whether ICMP is allowed as installed (Debian 13 allows it: `deb13`, 20
  November); the page in a browser.

## Phase 20: documents

Decided 25 November (`decisions.md`, "Documents"); what exists:
`architecture/documents.md` (`documents.py`, `docrender.py`, the class
`doc`, `FileManager` shared with the store, `DocumentsPage`). `documents.sh`
passes in containers on the three distributions; the page was tried in
Chrome on `deb13` (25 November), all but printing.

- [ ] Close (the user does it, 25 November): with Claude
  as the assistant, a report of a run written in a folder, edited by
  hand, the agent's version restored, printed to PDF from the page.

## Integration since the review of 16 November

`make ct-test` on AlmaLinux: `runs`, `checks`, `store`, `files`,
`schedules`, `cli`, `mcp`, `chat`, `negative`; on Debian `chat`, `mcp`,
`documents`, `cli`, `store`; on Fedora `chat`, `mcp`, `documents`;
`make ct-lifecycle` on Debian. On the VMs `deb13` and `alma10`: `sandbox`,
`runs`, `files`, `store`, `chat`, `schedules`, `negative`. The rest on
Fedora not since phase 17. `cmdb.sh` fails on Fedora in a container (known:
`architecture/tests.md`).

## What the review of 16 November left

Left by the user for another session; checked against the code on 18
November, all still open.

Fix:

- [ ] `jobs.reconcile`: `fcntl.flock(LOCK_EX)` with no timeout; a
  `fleetctl` suspended (Ctrl-Z) holding it stops every page and dispatch.
  The documents' lock (`documents._locked`) is the same.
- [ ] `fleet-secret answer` logs the ticket's name to the journal before a
  run spends it: a journal reader with the operator role can spend it.
- [ ] `store.archive` (`storage.py`) opens every file before the first
  byte: past ~1000 files `EMFILE`, and the browser saves an empty tar.
- [ ] Upgrade scriptlets (`debian/cockpit-fleet.postinst`,
  `packaging/cockpit-fleet.spec`) delete every `runs/*/inventory.json`,
  also of a reading ended and not collected: tie it to the old version.

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

Not planned as phases yet; the user picks the next one.

- Ad-hoc commands: sets of variables chosen for one (today only the
  inventory's).
- Assistant: instructions per project.
- External triggers (git webhooks, auto-remediation); `ansible-pull` for
  very large fleets.
- Secrets on OpenBao or Bitwarden; export to Prometheus or InfluxDB.
- Execution environments in containers; other controller distributions.
- A limit on the rows of state changes (off by default).
- Store: credentials for downloads, parts kept for some projects.
- Overview: a filter by inventory.
