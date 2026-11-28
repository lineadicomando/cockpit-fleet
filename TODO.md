# cockpit-fleet — plan

What is left. How each closed phase was shown:
`docs/architecture/acceptance.md`; a developer's workstation and lab VMs:
`CLAUDE.local.md` (not tracked).

## Status (28 November 2026)

Phases 0–24 closed, and the assistant without questions. 21–24 were the
last to add features (`decisions.md`, "Freeze of features"): since then
fixes, minor changes (refinements of what exists) and field use, toward
1.0.0. Last release 0.4.0 (18 November); since then `[Unreleased]` of
`CHANGELOG.md`. `make release` only when asked.

To resume: `git status`, `git log origin/main..`; `make check` (881
backend, 266 frontend tests). Under way: the review of the interface
(below).

## Minor changes decided

- **A review of the interface** (28 November, the frontend only): a first
  block done (nothing typed lost without asking, launches and the end of
  a key's trust asked first, notices and lists that tell the truth, the
  Overview's problems, a run's template and inventory, a check's details,
  the chat that stays where it is read, wording; seen in a browser on
  `deb13`, with lists on a narrow screen fixed); unit tests, docs. Left,
  by impact: drilling down from a check to its hosts, its cycles and its
  changes; relative times and stale results; the recap of a run below its
  output; the problems of an unreachable host folded; the approval dialog
  of the chat with names; clearing a credential's secret; the audit's
  objects and filters (backend).

## To study

- **The names of `overview.md` and `dashboard.md`** (`docs/architecture/`):
  "Overview and security model" (what `CLAUDE.md`'s rules cite) and "The
  Overview" page (`overview.get`); a search for one finds the other.
  Before renaming, weigh: `security-model.md` for the first (and where its
  parts and flow go), `overview-page.md` or as it is for the second, not
  to be taken for `docs/security.md`; the references (7 to `overview.md`
  in 5 files, 5 to `dashboard.md` in 4, `docs/history/dashboard.md`,
  sections cited by name); links from installed documents, released
  packages and the README on GitHub; or only a line at the top of each
  ("not the page: see …").

## Integration

Last runs, 26 and 27 November. Containers, the three distributions:
`runs`, `schedules`, `workflows`, `negative`, `checks`, `chat`, `mcp`,
`cli`, `documents`, `store`, `sandbox` (with run D); Debian only:
`netscan`, `make ct-lifecycle` (from 0.3.0); earlier `files`. VMs:
`netscan` on the three (`win11` up), `windows` on `deb13`; at `0.4.0+82`
`sandbox` with run D, `runs`, `workflows`, `schedules` on `alma10`, the
last three on `fed44` (SELinux enforcing, no denial); 28 November at
`0.4.0+96` `workflows` in containers on the three, `sandbox` with run D
on `deb13`.

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
