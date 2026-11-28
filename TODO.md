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
backend, 257 frontend tests). Under way: the list of workflows (below);
`sandbox` with run D on `deb13` (Integration).

## Minor changes decided

- **The list of workflows as the one of templates** (28 November; done,
  unit tests, docs; `decisions.md` "Workflows list"). Left:
  - seen in a browser (`make browser-up`, a throwaway scenario): the
    toolbar's width, the steps of a long workflow, the groups, the
    preview in the form, the tooltip of an approval;
  - `make ct-test TESTS=workflows` on the three (`workflow.list` changed).

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
last three on `fed44` (SELinux enforcing, no denial). Left: `sandbox`
with run D on `deb13` (its data of the closes kept until then).

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
