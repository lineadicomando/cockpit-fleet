# cockpit-fleet — plan

What is left. Elsewhere: `CHANGELOG.md` (what changed), `docs/decisions.md`
(what was decided), `docs/limits.md` (known limits),
`docs/architecture.md` (index of `docs/architecture/`, one file per area;
read only the one at hand), `docs/architecture/acceptance.md` (how each
phase was closed), `CLAUDE.local.md` (this workstation and its lab VMs,
not tracked).

## Status (15 November 2026)

Phases 0–16 closed; none under way: the user picks the next one from
Future. 0.3.0 released on 15 November (its packages on GitHub, published
by the job "release" at the tag); `make release` only when asked.

To resume: `git status`; `git log origin/main..` for what is not pushed
(push only when asked: `git push origin main`); `make check` (683 backend,
172 frontend tests). Each backend module's docstring says what it is;
`cli.py` is the registry of commands, `db.py` the schema (28 migrations).
Integration last run on 15 November: every script on AlmaLinux, `store`
and `negative` on Debian and Fedora, all passed; the known failure of
`cmdb.sh` on Fedora in a container is in `architecture/tests.md`.

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
