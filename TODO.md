# cockpit-fleet — plan

Status and what is left. Decisions: `docs/decisions.md`; known limits:
`docs/limits.md`; how each part works and how each phase was accepted:
`docs/architecture.md` (2600 lines: list its sections with `grep -n '^## '
docs/architecture.md` and read only the one needed).

## Status (6 November 2026)

Phases 0–11 closed; 0.2.0 released on 2 November. Phase 12 (profiles of
the assistant) is built, documented and tested but for the trial with the
real agent below; then it closes. Under `[Unreleased]`: runs in series,
and phase 12. Its commits on `main` are not pushed (`git log
origin/main..`).

To resume: `git status`; `make check`. Each backend module says what it is
in its docstring; `cli.py` is the registry, `db.py` the schema (25).

Tests on 6 November: unit tests (582 backend, 133 frontend); full
`ct-test` on the three, all passed but `cmdb` on Fedora (only in a
container: netavark "invalid namespace path", as on 1 November); the page
in a browser on `deb13` with `fake-agent.py`. Run two distributions'
`ct-test` one after the other, not together.

## Next: close phase 12

Where it is: decisions in `docs/decisions.md` ("Assistant profiles");
design in `docs/architecture.md` ("Agents", "Approvals", "The chat");
for administrators `docs/chat.md`. Code: `profiles.py` (classes, the table
of questions), `autonomy.py`, `approvals.py`, `drafts.py`,
`instructions.py`, `chat.py` (`chat.accept`, `chat.start`), `terminal.py`
(`fleet assistant --profile`, `fleet draft diff`); `src/components/
ChatPage.tsx`, `Drafts.tsx`, `src/lib/chat.ts`.

1. [ ] **Trial with Claude** (the user logs in; agreed to do it in a later
   session). Prepare `cockpit-fleet-deb13` (skill `vm-lab`): `reset`,
   `deploy`, add the lab user to `cockpit-fleet-admin`, run the role
   `lineadicomando.fleet.chat_agent` with `chat_agent_configure=true
   chat_agent_enable_mcp=true` (about 510 MB), set `[mcp] max_role =
   "admin"` and `[chat] profiles` to all five, make a managed project with
   a playbook, a managed inventory (`self ansible_connection=local`) and a
   template. Then the user runs `/opt/cockpit-fleet-agent/bin/claude
   /login` as the lab user. Try, and note in `architecture.md`:
   - the page: each profile's start (risks accepted once), questions with
     profile and class, "always" for launches, an ad-hoc command in
     `administrator` asked every time and then waiting for confirmation,
     the agent writing a playbook to a draft, reviewed and merged;
   - `fleet assistant --profile assisted|autonomous`: that
     `--allowedTools` works beside `--permission-mode manual` (reads
     unasked, launches asked in `assisted`, unasked in `autonomous`).
2. [ ] Acceptance of phase 12 in `architecture.md` ("Acceptance of the
   phases"); update `CLAUDE.local.md` with the state of the VM.

Open question for the user: should `inventory.create`/`inventory.update`
(class config) write a managed inventory through a draft too? Today they
write `main` (in `limits.md`).

## Future

Sets of variables: secret variables in a set (encrypted as credentials
are, kept off the page and out of export), and sets for ad-hoc commands.
Instructions per project for the assistant. External triggers (git
webhooks, auto-remediation); `ansible-pull` for very large fleets; secrets
on OpenBao or Bitwarden; export to Prometheus or InfluxDB; execution
environments in containers; other controller distributions. A limit on the
rows of state changes (off by default).
