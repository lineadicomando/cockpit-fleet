# cockpit-fleet — plan

Status and what is left; what is done is in `CHANGELOG.md` and
`docs/architecture/` (one file per area, index in `docs/architecture.md`:
read only the file at hand). Decisions: `docs/decisions.md`; known limits:
`docs/limits.md`.

## Status (13 November 2026)

Phases 0–12 closed (12 accepted with Claude on 13 November,
`architecture/acceptance.md`); 0.2.0 released on 2 November; the next
release would be 0.3.0 (`make next-version`), made only when the user
asks. Under `[Unreleased]`: runs in series, phase 12 (the profiles of the
assistant), phase 13 (the store, waiting for its acceptance), the six
grouped tabs. Phase 14 (starters) is designed, not begun.

To resume: `git status`; `git log origin/main..` (what is not pushed);
`make check`. Each backend module says what it is in its
docstring; `cli.py` is the registry of commands, `db.py` the schema (27).

Tests on 13 November: unit tests (646 backend, 162 frontend). `ct-test`
of `store` and `negative` on the three on 11 November; the last full
`ct-test` (9 November) passed but `cmdb` on Fedora, in a container only
(netavark "invalid namespace path", since 1 November). Run two
distributions' `ct-test` one after the other.

## Phase 13: close it

Design and measures: `architecture/store.md`; decisions: `decisions.md`,
"Store". Code: backend `storage.py`, `fetch.py` (job `download`),
`copier.py` (job `copy`), `jobs.py` (lane `downloads`), `maintenance.tidy`,
`terminal.py` (`fleet store`); page `StorePage.tsx`, `StoreDialogs.tsx`,
`ContextMenu.tsx`, `RelocateModal.tsx`, `src/lib/store.ts`; tests
`test_storage.py`, `test_copy.py`, `test_fetch.py`, `test/store.test.ts`,
`test/integration/store.sh`.

1. [ ] **Acceptance with `ansible-win_edulab`** (needs `win11` and its own
   VMs: on `inspiron27`). Its store is `win_workman_storage_path`
   (inventory `school`, `group_vars/all/vars.yaml`): set it to
   `"{{ fleet_store }}"`; its roles `get_url` installers delegated to the
   controller (`ansible_collections/lineadicomando/win_workman/roles/
   pkg_utils/tasks/download.yaml`) and `win_copy` them from there; ISOs go
   by hand into `win_iso/`, and the role `win_iso` writes `-unattended.iso`
   beside them; `win_workman_file_copy_pull_root` fetches from nodes. Its
   Veyon keys (`veyon/`) must leave the store (credentials or vault). Show:
   an installer downloaded by a run and kept, an ISO uploaded from the page,
   a file fetched from a node, then the acceptance in
   `architecture/acceptance.md`.
2. [ ] In Firefox and Chrome, by hand: a folder dropped from the desktop
   onto the Store page (the browser tools cannot drop one), and a drag with
   Ctrl that copies.

## Phase 14: starters

Ready-made content a new managed project, managed inventory or playbook
starts from. Decided on 12 November (`decisions.md`, "Starters"): the word
is **starter** (code `starter.*`; Italian "punto di partenza", never
"modello"), because "template" is the job template's (commands, schema,
AWX and Semaphore) and Ansible's (`template`, `.j2`). Provided by the
package and by the administrator; static (files copied as they are, no
parameters, nothing rendered); what is made from one keeps no link to it.
Offered as a choice where things are created, not as a page of its own.

Where it fits today: `project.create` kind `managed` starts an empty
repository (`files.init_repository`, `resources.py`);
`inventory.create` kind `managed` takes `content` and `files`
(`_write_main`); the page's "New file" is `PathModal` in
`src/components/FilesPage.tsx`, saved by `file.write`/`file.change`.
Packaged data goes under `/usr/share/cockpit-fleet` (`SHARE_DIR` in the
`Makefile`, `paths.py`).

1. [ ] Ask the user: which starters ship (a Linux project, a Windows one
   shaped like `ansible-win_edulab`, an inventory with `group_vars`,
   playbooks); does an administrator's starter of the same name replace the
   package's, or do both show?
2. [ ] Layout: `/usr/share/cockpit-fleet/starters/` and
   `/etc/cockpit-fleet/starters/`, each with `projects/NAME/`,
   `inventories/NAME/` (trees) and `playbooks/NAME.yml`; a title and a
   description per starter (a small metadata file, or the first comment);
   `paths.py`, `Makefile`, `debian/`, the spec.
3. [ ] Backend: `starter.list` (role admin, class read), read with the
   limits of `files.py` (paths checked, no links, files ≤ 1 MB, a cap on
   the tree), English titles of the package's in `po/`; a `starter`
   parameter of `project.create` and `inventory.create` (managed only),
   copied into the first commit; tests in `test_cli.py` style, the class
   in `test_agents.py`.
4. [ ] Page: "Empty" or a starter in "New project" and "New inventory";
   "New playbook from…" beside "New file" in the files page; strings in
   `po/it.po`.
5. [ ] Docs and checks: `architecture/projects.md`, `docs/install.md` (adding
   one's own), `CHANGELOG.md` (Added), `fleet help`; `files.sh` extended,
   `make ct-test` on the three (packaging changes).

## Open questions for the user

- Should `inventory.create`/`inventory.update` (class config) write a
  managed inventory through a draft too (today `main`, in `limits.md`)?
- Is "Store" right in the Italian interface, or another word ("Deposito")?

## Future

Sets of variables: secret variables in a set (encrypted as credentials
are, kept off the page and out of export), and sets for ad-hoc commands.
Instructions per project for the assistant. External triggers (git
webhooks, auto-remediation); `ansible-pull` for very large fleets; secrets
on OpenBao or Bitwarden; export to Prometheus or InfluxDB; execution
environments in containers; other controller distributions. A limit on the
rows of state changes (off by default). Store: credentials for downloads,
parts of it kept for some projects, transfers over SSH through `fleet`
(`fleet store put --file -` from stdin, `fleet store ls`, a folder kept in
step by digests; not SFTP or FTP, `decisions.md`).
