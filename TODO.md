# cockpit-fleet — plan

What is left. Done: `CHANGELOG.md`, `docs/architecture/` (index
`docs/architecture.md`; read only the file at hand), acceptance of each
phase in `docs/architecture/acceptance.md`. Decisions: `docs/decisions.md`.
Known limits: `docs/limits.md`. Lab VMs on this workstation:
`CLAUDE.local.md` (not tracked).

## Status (14 November 2026)

Phases 0–13 and 15 closed. 0.2.0 released on 2 November; `[Unreleased]`
makes 0.3.0 (it has a Breaking entry; `make release` only when asked).

To resume: `git status`, `git log origin/main..` (not pushed), `make
check` (659 backend, 171 frontend tests on 14 November). Each backend
module's docstring says what it is; `cli.py` is the registry of commands,
`db.py` the schema (28 migrations). Last full `ct-test`: on the three, 9
November, all passed but the known failure in `tests.md`; the scripts
touched since were run again (14 November).

## Phase 14: starters — designed, not begun

Ready-made content a new managed project, managed inventory or playbook
starts from (`decisions.md`, "Starters"). Word: **starter** (code
`starter.*`; Italian "punto di partenza", never "modello"). From the
package and the administrator; static, copied as they are; no link kept to
what is made from one; offered where things are created, no page of its
own.

Where it fits: `project.create` kind `managed` starts an empty repository
(`files.init_repository`); `inventory.create` kind `managed` takes
`content` and `files` (`_write_main` in `resources.py`); "New file" is
`PathModal` in `src/components/FilesPage.tsx`, saved by `file.write` /
`file.change`. Packaged data goes under `/usr/share/cockpit-fleet`
(`SHARE_DIR` in the `Makefile`; `COLLECTIONS_DIR` in `paths.py` is the
model for a new path).

1. [ ] Ask the user: which starters ship (a Linux project, a Windows one
   shaped like `ansible-win_edulab`, an inventory with `group_vars`,
   playbooks); does an administrator's starter of the same name replace the
   package's, or do both show?
2. [ ] Layout: `/usr/share/cockpit-fleet/starters/` and
   `/etc/cockpit-fleet/starters/`, each with `projects/NAME/`,
   `inventories/NAME/`, `playbooks/NAME.yml`; a title and a description
   each (a small metadata file, or the first comment); `paths.py`,
   `Makefile`, `debian/`, the spec.
3. [ ] Backend: `starter.list` (admin, class read), read with the limits of
   `files.py` (paths checked, no links, files ≤ 1 MB, a cap on the tree),
   titles of the package's in `po/`; a `starter` parameter of
   `project.create` and `inventory.create` (managed only), copied into the
   first commit; tests in `test_cli.py` style, the class in
   `test_agents.py`.
4. [ ] Page: "Empty" or a starter in "New project" and "New inventory";
   "New playbook from…" beside "New file"; strings in `po/it.po`.
5. [ ] Docs and checks: `architecture/projects.md`, `docs/install.md`
   (adding one's own), `CHANGELOG.md` (Added), `fleet help`; `files.sh`
   extended, `make ct-test` on the three (packaging changes).

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
