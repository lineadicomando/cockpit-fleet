# cockpit-fleet — plan

What is left. The other documents are listed in `CLAUDE.md`; how each
closed phase was shown is in `docs/architecture/acceptance.md`; this
workstation and its lab VMs in `CLAUDE.local.md` (not tracked).

## Status (16 November 2026)

Phases 0–17 closed, phase 18 under way (below). Last release 0.3.0 (15
November, packages on GitHub); `make release` only when asked.

To resume: `git status`; `git log origin/main..` for what is not pushed
(push only when asked: `git push origin main`); `make check` (708 backend,
184 frontend tests). Each backend module's docstring says what it is;
`cli.py` is the registry of commands, `db.py` the schema (31 migrations).

Integration: every script on AlmaLinux and `files`, `negative`, `checks`
on Debian and Fedora at phase 17 (15 November); since then (values
replaced at launch, schema 30; workflows of a project or inventory,
schema 31; the Overview) only `negative` on AlmaLinux. The known failure
of `cmdb.sh` on Fedora in a container is in `architecture/tests.md`.

## Phase 18: the Overview as a dashboard (under way)

Decided: `decisions.md`, row "Overview"; design and measures:
`architecture/dashboard.md`; limits: `limits.md`, "Overview".

Done: `overview.get` and its tests (measured at 500 hosts), the page
(`OverviewPage.tsx`, `OverviewCharts.tsx`, logic in `src/lib/overview.ts`,
tested), Italian strings, changelog and docs; `make check`, `make ct-test`
on AlmaLinux (`negative`); in a browser on `deb13` as a viewer with the
data `checks.sh` leaves (`IT_KEEP=yes`), dark theme, Lighthouse
accessibility 100.

- [ ] Look at it as an operator (only the card of access differs) and in
  the light theme.
- [ ] Close: an entry in `acceptance.md` from the line above; this
  section out of the plan.

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
