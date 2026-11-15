# cockpit-fleet — plan

What is left. Elsewhere: `CHANGELOG.md` (what changed), `docs/decisions.md`
(what was decided), `docs/limits.md` (known limits),
`docs/architecture.md` (index of `docs/architecture/`, one file per area;
read only the one at hand), `docs/architecture/acceptance.md` (how each
phase was closed), `CLAUDE.local.md` (this workstation and its lab VMs,
not tracked).

## Status (15 November 2026)

Phases 0–16 closed; phase 17 under way. 0.3.0 released on 15 November (its packages on GitHub, published
by the job "release" at the tag); `make release` only when asked.

To resume: `git status`; `git log origin/main..` for what is not pushed
(push only when asked: `git push origin main`); `make check` (694 backend,
172 frontend tests). Each backend module's docstring says what it is;
`cli.py` is the registry of commands, `db.py` the schema (29 migrations).
Integration last run on 15 November: every script on AlmaLinux, `store`
and `negative` on Debian and Fedora, all passed; the known failure of
`cmdb.sh` on Fedora in a container is in `architecture/tests.md`.

## Phase 17: duplicate, and starters kept as projects and inventories

Decided on 15 November. Managed projects and managed inventories only.

- [x] **Duplicate.** `project.duplicate` and `inventory.duplicate` (admin,
  `id`, `name`): the files of `main` (no draft) as the first commit of a
  new one, `Copy of NAME`, its history not copied (a secret taken out of
  a file stays in old commits). An inventory keeps `format`, `path`, its
  vault and its sets of variables; nothing that uses the original
  (templates, checks, schedules) follows. No syntax check per playbook
  copied. "Duplicate" in the menu of a row, asking the name.
- [x] **Use as starter** ("Usa come punto di partenza"): a reversible flag
  (`starter`, a migration) on a managed project or inventory, which stays
  in its list, is edited as before, and is offered by `starter.list` as a
  third source (`saved/<id>`), within the limits of a tree, not of a
  starter on disk. Enforced by the backend, not only hidden: one so
  marked is refused by templates, ad-hoc commands, checks, file
  inventories of a project so marked, and at launch; the flag is refused
  while something uses it; a starter inventory's hosts stay out of the
  CMDB and of host keys. Carried by `config.export`/`config.import`.
- [ ] Docs (`architecture/projects.md`, `install.md`, `decisions.md`),
  CHANGELOG, `po/it.po`; integration on AlmaLinux at least; the browser
  on a VM at the close.

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
