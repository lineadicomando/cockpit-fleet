# cockpit-fleet — plan

What is left. Done: `CHANGELOG.md`, `docs/architecture/` (index
`docs/architecture.md`; read only the file at hand), acceptance of each
phase in `docs/architecture/acceptance.md`. Decisions: `docs/decisions.md`.
Known limits: `docs/limits.md`. Lab VMs on this workstation:
`CLAUDE.local.md` (not tracked).

## Status (14 November 2026)

Phases 0–13 and 15 closed, 14 under way. 0.2.0 released on 2 November; `[Unreleased]`
makes 0.3.0 (it has a Breaking entry; `make release` only when asked).

To resume: `git status`, `git log origin/main..` (not pushed), `make
check` (669 backend, 171 frontend tests on 14 November). Each backend
module's docstring says what it is; `cli.py` is the registry of commands,
`db.py` the schema (28 migrations). Last full `ct-test`: on the three, 9
November, all passed but the known failure in `tests.md`; the scripts
touched since were run again (14 November).

## Phase 14: starters — under way

Ready-made content a new managed project, managed inventory or playbook
starts from (`decisions.md`, "Starters"; design in
`architecture/projects.md`, "Starters"). Done: `starters.py`
(`starter.list`, `starter` of `project.create` and `inventory.create`),
the package's in `starters/` (Linux and Windows of each kind), "Start from"
in "New project", "New inventory" and "New file", docs, `files.sh`;
`ct-test` of `files inventories negative` passed on the three (14 November).

1. [ ] On a VM of the lab, in the browser: the three dialogs, in Italian
   too; a Windows project from its starter on `win11` (`ping.yml`, with
   `ansible.windows` installed by its requirements file).
2. [ ] Close the phase: acceptance in `architecture/acceptance.md`.

## Open questions for the user

- Should `inventory.create`/`inventory.update` write a managed inventory
  through a draft too? (Today straight to `main`; in `limits.md`, Agents.)
- "Store" in the Italian interface, or another word ("Deposito")?

## Future

Sets of variables chosen for an ad-hoc command (today only the
inventory's). Instructions per project for the assistant. External
triggers (git webhooks, auto-remediation); `ansible-pull` for very large
fleets; secrets on OpenBao or Bitwarden; export to Prometheus or InfluxDB;
execution environments in containers; other controller distributions. A
limit on the rows of state changes (off by default). Store: credentials
for downloads, parts kept for some projects, transfers over SSH through
`fleet` (`fleet store put --file -`, `fleet store ls`, a folder kept in
step by digests; not SFTP or FTP, `decisions.md`).
