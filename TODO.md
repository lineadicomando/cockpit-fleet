# cockpit-fleet — plan

What is left. Elsewhere: `CHANGELOG.md` (what changed), `docs/decisions.md`
(what was decided), `docs/limits.md` (known limits),
`docs/architecture.md` (index of `docs/architecture/`, one file per area;
read only the one at hand), `docs/architecture/acceptance.md` (how each
phase was closed), `CLAUDE.local.md` (this workstation and its lab VMs,
not tracked).

## Status (16 November 2026)

Phases 0–17 closed; phase 18 under way (below). 0.3.0 released on 15
November (its packages on GitHub, published by the job "release" at
the tag); `make release` only when asked.

To resume: `git status`; `git log origin/main..` for what is not pushed
(push only when asked: `git push origin main`); `make check` (701 backend,
176 frontend tests). Each backend module's docstring says what it is;
`cli.py` is the registry of commands, `db.py` the schema (31 migrations).
Integration last run on 15 November, at phase 17: `files` and
`ct-lifecycle` on AlmaLinux, `files`, `negative` and `checks` on Debian
and Fedora, all passed (every script on AlmaLinux before it); the known
failure of `cmdb.sh` on Fedora in a container is in
`architecture/tests.md`.

## Phase 18: the Overview as a dashboard (under way)

Decided on 16 November: statistics and charts on the Overview, from what
is already kept; a window of 14 days by default (7 or 30 to choose); the
hosts failing most and the time to recover included unless they cost more
than about 200 ms at 500 hosts; "Your access" and "Installation" in one
compact card at the bottom; no filter by inventory (later).

- [x] `overview.get` (viewer): hosts, states, open problems, runs by day
  and outcome, success rate, durations, recent failures, templates and
  hosts failing most, queue, next schedules and maintenance windows,
  updates, compliance, systems, uptime, problems and recoveries by day,
  time to recover, notifications, cycles behind; days in the browser's
  time zone. Tests; measured at 500 hosts.
- [x] The page: a row of figures, then cards with charts (lazy chunk),
  each leading to its section; read again every 30 s; logic in
  `src/lib/overview.ts`, tested.
- [x] Strings in Italian; `CHANGELOG.md`; `docs/decisions.md`,
  `docs/architecture/dashboard.md` and the index, `docs/limits.md`.
- [x] `make check`, `make ct-test` on AlmaLinux (`negative`); the page in
  a browser on `deb13` as a viewer, with the data `checks.sh` leaves
  (`IT_KEEP=yes`), in the dark theme; Lighthouse accessibility 100.
- [ ] Look at it as an operator (only the card of access differs), and
  in the light theme; then close the phase.

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
