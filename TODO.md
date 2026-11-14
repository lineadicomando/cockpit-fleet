# cockpit-fleet — plan

Status and what is left. What is done: `CHANGELOG.md` and
`docs/architecture/` (index `docs/architecture.md`; read only the file at
hand). Decisions: `docs/decisions.md`. Known limits: `docs/limits.md`.
Acceptance of each phase: `docs/architecture/acceptance.md`. The state of
the lab's VMs on this workstation: `CLAUDE.local.md` (not tracked).

## Status (14 November 2026)

Phases 0–13 closed; 0.2.0 released on 2 November. Under `[Unreleased]`
(next release 0.3.0, `make next-version`, only when the user asks): runs
in series, assistant profiles (12), the store (13), secret variables (15),
host keys by host, the six grouped tabs. Phase 15 lacks only its check in a
browser; phase 14 is designed, not begun.

To resume: `git status`, `git log origin/main..` (not pushed), `make
check`. Each backend module's docstring says what it is; `cli.py` is the
registry of commands, `db.py` the schema (28 migrations).

Tests (14 November): 659 backend, 171 frontend. `ct-test`: `store` on
AlmaLinux and Debian (14 November); runs, negative, cli, inventories and
checks on the three, `ct-lifecycle` on AlmaLinux (14 November); the last
full run (9 November) passed but `cmdb` on Fedora in a container
(netavark "invalid namespace path", since 1 November). Run two
distributions' `ct-test` one after the other.

## Phase 15: secret variables — to close

Design: `architecture/jobs.md` ("Secret variables and the sets of an
inventory"), `architecture/export.md`; decisions: `decisions.md`, "Sets of
variables", "Vault files". Done and tested (unit, `ct-test`).

1. [ ] In a browser on a VM (`deb13`, skill `vm-lab`): the set's form
   (secret variables added, replaced, removed), the inventory's sets, the
   template's merged view showing them hidden; then the acceptance in
   `architecture/acceptance.md` and phase 15 out of this file.

## Phase 14: starters

Ready-made content a new managed project, managed inventory or playbook
starts from (`decisions.md`, "Starters"): the word is **starter** (code
`starter.*`; Italian "punto di partenza", never "modello": "template" is
the job template's and Ansible's). From the package and the
administrator; static (files copied as they are); what is made from one
keeps no link to it; offered where things are created, no page of its own.

Where it fits: `project.create` kind `managed` starts an empty repository
(`files.init_repository`); `inventory.create` kind `managed` takes
`content` and `files` (`_write_main`, `resources.py`); "New file" is
`PathModal` in `src/components/FilesPage.tsx`, saved by
`file.write`/`file.change`. Packaged data: `/usr/share/cockpit-fleet`
(`SHARE_DIR` in the `Makefile`, `paths.py`).

1. [ ] Ask the user: which starters ship (a Linux project, a Windows one
   shaped like `ansible-win_edulab`, an inventory with `group_vars`,
   playbooks); does an administrator's starter of the same name replace the
   package's, or do both show?
2. [ ] Layout: `/usr/share/cockpit-fleet/starters/` and
   `/etc/cockpit-fleet/starters/`, each with `projects/NAME/`,
   `inventories/NAME/` and `playbooks/NAME.yml`; a title and a description
   per starter (a small metadata file, or the first comment); `paths.py`,
   `Makefile`, `debian/`, the spec.
3. [ ] Backend: `starter.list` (admin, class read), read with the limits of
   `files.py` (paths checked, no links, files ≤ 1 MB, a cap on the tree),
   English titles of the package's in `po/`; a `starter` parameter of
   `project.create` and `inventory.create` (managed only), copied into the
   first commit; tests in `test_cli.py` style, the class in
   `test_agents.py`.
4. [ ] Page: "Empty" or a starter in "New project" and "New inventory";
   "New playbook from…" beside "New file"; strings in `po/it.po`.
5. [ ] Docs and checks: `architecture/projects.md`, `docs/install.md`
   (adding one's own), `CHANGELOG.md` (Added), `fleet help`; `files.sh`
   extended, `make ct-test` on the three (packaging changes).

## Open questions for the user

- Should `inventory.create`/`inventory.update` (class config) write a
  managed inventory through a draft too (today `main`, in `limits.md`)?
- Is "Store" right in the Italian interface, or another word ("Deposito")?

## Future

Sets of variables chosen for an ad-hoc command (today it gets the
inventory's only). Instructions per project for the assistant. External
triggers (git webhooks, auto-remediation); `ansible-pull` for very large
fleets; secrets on OpenBao or Bitwarden; export to Prometheus or InfluxDB;
execution environments in containers; other controller distributions. A
limit on the rows of state changes (off by default). Store: credentials
for downloads, parts of it kept for some projects, transfers over SSH
through `fleet` (`fleet store put --file -` from stdin, `fleet store ls`, a
folder kept in step by digests; not SFTP or FTP, `decisions.md`).
