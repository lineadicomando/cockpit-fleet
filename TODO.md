# cockpit-fleet — plan

What is left. The other documents are listed in `CLAUDE.md`; how each
closed phase was shown is in `docs/architecture/acceptance.md`; this
workstation and its lab VMs in `CLAUDE.local.md` (not tracked).

## Status (16 November 2026)

Phases 0–17 closed, phase 18 under way. Last release 0.3.0 (15 November);
`[Unreleased]` has the Overview and the fixes of the review of 16
November, so `make next-version` says 0.4.0. `make release` only when
asked.

To resume: `git status`, `git log origin/main..`; `make check` (727
backend, 185 frontend tests). Each backend module's docstring says what
it is; `cli.py` is the registry of commands, `db.py` the schema (32
migrations).

Integration after the review (16 November): `make ct-test` on AlmaLinux
(`runs`, `checks`, `store`, `files`, `schedules`, `cli`, `mcp`, `chat`,
`negative`), `make ct-lifecycle` on Debian; on the VMs `deb13` and
`alma10`: `sandbox`, `runs`, `files`, `store`, `chat`, `schedules`,
`negative`. Fedora not since phase 17. The known failure of `cmdb.sh` on
Fedora in a container is in `architecture/tests.md`.

## Phase 18: the Overview as a dashboard (under way)

Decided: `decisions.md`, row "Overview"; design: `architecture/dashboard.md`;
limits: `limits.md`, "Overview". Done and tested, seen in a browser as a
viewer in the dark theme (Lighthouse accessibility 100).

- [ ] Look at it as an operator (only the card of access differs) and in
  the light theme.
- [ ] Close: an entry in `acceptance.md`; this section out of the plan.

## What the review of 16 November left

Fixed in `fb74fd6`, `c359af7`, `06dbe9a` (see `CHANGELOG.md`). Left, by
the user's choice, for another session. Fix the first four; for the
fifth, fix or record in `limits.md`; the last is for the user to decide.

- [ ] `jobs.reconcile`: `flock` with no timeout; a `fleetctl` suspended
  (Ctrl-Z) while it holds it stops every page and dispatch.
- [ ] `fleet-secret answer` logs the ticket to the journal before it is
  used: a reader of the journal with the operator role can spend it.
- [ ] `store.archive` opens every file before the first byte: over about
  1000 files, `EMFILE`, and the browser saves an empty tar.
- [ ] The upgrade scriptlets (`debian/cockpit-fleet.postinst`,
  `packaging/cockpit-fleet.spec`) delete every `runs/*/inventory.json`,
  that of a reading ended and not yet collected too: tie it to the old
  version.
- [ ] To `limits.md`, or fix: `fleet-run` keeps the unmasked error in
  `result.json` (no message holds a secret today); two `notify_states`
  together send one notification twice; `_last_stats` reads 256 KiB, so
  stats and artifacts are lost past ~1900 hosts; `moved_rows` can fail on
  a stale row after `store.move` moved the file; Files: a tab kept while
  its read was pending spins for ever (`kept` in `FilesPage.tsx`);
  Approvals: the decide modal vanishes when another decides
  (`PendingApprovals`); Survey: "None" on an optional choice with a
  default gives the default (`collectAnswers`); docs installed link to
  `decisions.md` and `TODO.md`, not packaged, and the rpm has no
  `CHANGELOG.md`; `security.md` repeats limits of `limits.md`.
- [ ] To decide: a playbook can rewrite its `result.json` and turn a
  failed run into a successful one (the run writes its own directory);
  a store folder is drawn whole, without paging; no component tests for
  the frontend; GitHub actions pinned by tag, not by commit.

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
