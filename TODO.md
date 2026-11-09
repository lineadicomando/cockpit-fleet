# cockpit-fleet — plan

Status and what is left. Decisions: `docs/decisions.md`; known limits:
`docs/limits.md`; how each part works and how each phase was accepted:
`docs/architecture.md` (2700 lines: list its sections with `grep -n '^## '
docs/architecture.md` and read only the one needed).

## Status (9 November 2026)

Phases 0–11 closed; 0.2.0 released on 2 November. Under `[Unreleased]`:
runs in series, phase 12 (profiles of the assistant: built, waiting for the
trial below) and phase 13 (the store: points 1–3 done). Commits on `main`
are not pushed (`git log origin/main..`).

To resume: `git status`; `make check`. Each backend module says what it is
in its docstring; `cli.py` is the registry, `db.py` the schema (26).

Tests on 9 November: unit tests (611 backend, 141 frontend); full
`ct-test` on the three after the store, all passed but `cmdb` on Fedora
(only in a container: netavark "invalid namespace path", since 1
November); after the downloads `store`, `negative`, `sandbox` on the three.
Run two distributions' `ct-test` one after the other, not together.

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

## Phase 13: the store

Decided on 9 November (`decisions.md`, "Store"); design and measures in
`architecture.md`, "The store" (hard links, intake, uploads, downloads to
the browser, downloads from URLs and the addresses refused). Code:
`storage.py`, `fetch.py`, `cli.py` (`stream=` commands), `jobs.py`
(`dispatch` makes the farm, `_collect` takes it in, lane `downloads`),
`terminal.py` (`fleet store put/read`); `src/components/StorePage.tsx`,
`src/lib/store.ts`; `test/integration/store.sh`.

1. [x] Prototypes (external channel, binary upload, farm of hard links).
2. [x] The store: commands, farm and intake, page, `fleet store`, docs.
3. [x] Downloads from URLs: job `download`, its lane, progress, cancel.
4. [ ] **Sources**: a file keeps the URL and digest it came from, and is
   refreshed by a conditional GET (ETag, Last-Modified), by hand and from a
   schedule; "try again" then resumes from what arrived.
5. [ ] **Acceptance with `ansible-win_edulab`** (needs `win11` and its own
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
rows of state changes (off by default). Credentials for downloads into the
store; parts of the store kept for some projects.
