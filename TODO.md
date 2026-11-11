# cockpit-fleet — plan

Status and what is left; what is done is in `CHANGELOG.md` and
`docs/architecture.md`. Decisions: `docs/decisions.md`; known limits:
`docs/limits.md`. `architecture.md` is long (2900 lines): list its
sections with `grep -n '^## ' docs/architecture.md` and read only one,
e.g. `sed -n '/^## The store$/,/^## /p' docs/architecture.md`.

## Status (11 November 2026)

Phases 0–11 closed; 0.2.0 released on 2 November; the next release would
be 0.3.0 (`make next-version`), made only when the user asks. Under
`[Unreleased]`: runs in series, phase 12 (built, waiting for the trial
below) and phase 13 (the store: all but its acceptance).

To resume: `git status`; `git log origin/main..` (what is not pushed);
`make check`. Each backend module says what it is in its docstring;
`cli.py` is the registry of commands, `db.py` the schema (27).

Tests on 11 November: unit tests (645 backend, 156 frontend); `ct-test`
of `store` and `negative` on the three after the sources, `sandbox` after
the copies. The last full `ct-test` (9 November) passed but `cmdb` on
Fedora, in a container only (netavark "invalid namespace path", since 1
November). Run two distributions' `ct-test` one after the other.

## Phase 12: close it

Design in `architecture.md` ("Agents", "Approvals", "The chat"); for
administrators `docs/chat.md`. Code: `profiles.py`, `autonomy.py`,
`approvals.py`, `drafts.py`, `instructions.py`, `chat.py`, `terminal.py`;
`src/components/ChatPage.tsx`, `Drafts.tsx`, `src/lib/chat.ts`.

1. [ ] **Trial with Claude** (the user logs in; a later session). On
   `cockpit-fleet-deb13` (skill `vm-lab`): `reset`, `deploy`, the lab user
   in `cockpit-fleet-admin`, the role `lineadicomando.fleet.chat_agent` with
   `chat_agent_configure=true chat_agent_enable_mcp=true` (about 510 MB),
   `[mcp] max_role = "admin"`, `[chat] profiles` all five, a managed project
   with a playbook, a managed inventory (`self ansible_connection=local`), a
   template. The user runs `/opt/cockpit-fleet-agent/bin/claude /login` as
   the lab user. Try, and note in `architecture.md`:
   - the page: each profile's start (risks accepted once), questions with
     profile and class, "always" for launches, an ad-hoc command in
     `administrator` asked every time then waiting for confirmation, a
     playbook written to a draft, reviewed and merged;
   - `fleet assistant --profile assisted|autonomous`: `--allowedTools`
     beside `--permission-mode manual` (reads unasked, launches asked in
     `assisted`, unasked in `autonomous`).
2. [ ] Acceptance of phase 12 in `architecture.md` ("Acceptance of the
   phases"); the state of the VM in `CLAUDE.local.md`.

## Phase 13: close it

Design and measures in `architecture.md`, "The store" (hard links and
intake, uploads, downloads to the browser and from URLs, the page, copies,
archives, sources); decisions in `decisions.md`, "Store". Code:

- backend: `storage.py` (tree, farm and intake, commands, copies, archive,
  sources), `fetch.py` (job `download`), `copier.py` (job `copy`),
  `jobs.py` (lane `downloads`, `_collect`), `maintenance.tidy` (digests,
  sources due), `terminal.py` (`fleet store put/read`), `cli.py` (`stream=`
  commands, paths in argv);
- page: `src/components/StorePage.tsx`, `StoreDialogs.tsx`,
  `ContextMenu.tsx`, `RelocateModal.tsx`; `src/lib/store.ts`; completion of
  `{{ fleet_store }}/` in `src/lib/ansible-source.ts`;
- tests: `backend/tests/test_storage.py`, `test_copy.py`, `test_fetch.py`;
  `test/store.test.ts`; `test/integration/store.sh`.

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
   a file fetched from a node, then the phase's acceptance in
   `architecture.md`.
2. [ ] In Firefox and Chrome, by hand: a folder dropped from the desktop
   onto the Store page (the browser tools cannot drop one: only the reading
   of its entries is tested), and a drag with Ctrl that copies.

Open questions for the user: should `inventory.create`/`inventory.update`
(class config) write a managed inventory through a draft too (today
`main`, in `limits.md`)? Is "Store" right in the Italian interface, or
another word ("Deposito")?

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
