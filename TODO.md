# cockpit-fleet — plan

What is left. The other documents are listed in `CLAUDE.md`; how each
closed phase was shown is in `docs/architecture/acceptance.md`; this
workstation and its lab VMs in `CLAUDE.local.md` (not tracked).

## Status (18 November 2026)

Phases 0–18 closed; the user picks the next (see "Future"). Last
release 0.4.0 (18 November), with the Overview of phase 18. `make
release` only when asked.

To resume: `git status`, `git log origin/main..`; `make check` (735
backend, 194 frontend tests). Each backend module's docstring says what
it is; `cli.py` is the registry of commands, `db.py` the schema (33
migrations).

Integration since the review of 16 November: `make ct-test` on AlmaLinux
(`runs`, `checks`, `store`, `files`, `schedules`, `cli`, `mcp`, `chat`,
`negative`), and `chat`, `mcp` on Debian and Fedora
after the changes of 18 November; `make ct-lifecycle` on Debian; on the
VMs `deb13` and `alma10`: `sandbox`, `runs`, `files`, `store`, `chat`,
`schedules`, `negative`. The rest on Fedora not since phase 17. The
known failure of `cmdb.sh` on Fedora in a container: `architecture/tests.md`.

## What the review of 16 November left

Left by the user for another session; checked against the code on 18
November, all still open.

Fix:

- [ ] `jobs.reconcile`: `fcntl.flock(LOCK_EX)` with no timeout; a
  `fleetctl` suspended (Ctrl-Z) holding it stops every page and dispatch.
- [ ] `fleet-secret answer` logs the ticket's name to the journal before
  a run spends it: a journal reader with the operator role can spend it.
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
- [ ] A store folder is drawn whole, without paging.
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
