# cockpit-fleet — plan

What is left. Elsewhere: `CHANGELOG.md` (what changed), `docs/decisions.md`
(what was decided), `docs/limits.md` (known limits),
`docs/architecture.md` (index of `docs/architecture/`, one file per area;
read only the one at hand), `docs/architecture/acceptance.md` (how each
phase was closed), `CLAUDE.local.md` (lab VMs on this workstation, not
tracked).

## Status (15 November 2026)

Phases 0–15 closed; no phase under way. 0.2.0 released on 2 November;
`[Unreleased]` makes 0.3.0 (it has a Breaking entry; `make release` only
when asked).

To resume: `git status`, `git log origin/main..` (not pushed: push only
when asked, `git push origin main`), `make check` (673 backend, 171
frontend tests). Each backend module's docstring says what it is; `cli.py`
is the registry of commands, `db.py` the schema (28 migrations). Last full
`make ct-test` on the three: 15 November, after the `.yaml` change, all
passed but the known failure of `cmdb.sh` on Fedora (`tests.md`).

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
- Store: credentials for downloads, parts kept for some projects,
  transfers over SSH through `fleet` (`fleet store put --file -`, `fleet
  store ls`, a folder kept in step by digests; not SFTP or FTP,
  `decisions.md`).
