# cockpit-fleet — plan

Status and what is left. Decisions: `docs/decisions.md`; known limits:
`docs/limits.md`; how each part works and how each phase was accepted:
`docs/architecture.md` (2500 lines: list its sections with `grep -n '^## '
docs/architecture.md` and read only the one needed).

## Status (6 November 2026)

Phases 0–11 closed; 0.2.0 released on 2 November. Under `[Unreleased]`:
runs in series on an inventory, and phase 12 up to the instructions in
layers. Phase 12's commits on `main` are not pushed (`git log
origin/main..`).
**Next:** phase 12, the integration (below).

To resume: `git status`; `make check`. Each backend module says what it is
in its docstring; `cli.py` is the registry, `db.py` the schema (25).

Tests on 6 November: unit tests (582 backend, 133 frontend); full
`ct-test` on the three, all passed but `cmdb` on Fedora (only in a
container: netavark "invalid namespace path", as on 1 November); the page
in a browser on `deb13` with `fake-agent.py`. Not yet with the adapter and
its model. Run two distributions' `ct-test` one after the other, not
together.

## Phase 12 — Profiles of the assistant

Decided with the user on 5 November: what an agent may do is held by the
backend, by classes of commands and profiles, because the output of runs is
written by the nodes and an agent that reads it can be steered by a node.

**Built** (`docs/architecture.md`, "Agents", "Approvals", "The chat"):
classes of commands (`agent=` in `@command`, `cli.AGENT_CLASSES`,
`CLASS_TABLE` in `test_agents.py`); five profiles (`profiles.py`, held in
`cli._through`, `fleet-chat --profile` → `fleet-mcp --profile`); limits of
autonomy (`autonomy.py`, a session drawn by `fleet-mcp`); modes of approval
(`approvals.py`); an agent's ad-hoc command waits for its person unless
diagnostic; drafts (`files.place`, `drafts.py`); instructions in layers
(`instructions.py`, given by `chat.start` and `_fleet/ready`). The page:
the profile chosen at the start, its risks accepted once (`chat.accept`,
table `profile_acceptances`, migration 25; `chat.start` refuses with
`not_accepted`), questions by class (`question()` in `src/lib/chat.ts`),
the instructions shown, drafts reviewed (`src/components/Drafts.tsx`) and
edited on the page of the files (`projects/ID/drafts/SESSION`).

Decided on 6 November along the way (into `decisions.md` when the phase
closes): an autonomous agent launches nothing on an inventory with a
maintenance window open and never calls `state.ack`/`unack` or
`maintenance.*`; a diagnostic ad-hoc command never waits;
`approval.approve`/`reject` need the role operator or the right; a draft
is merged file by file, not line by line (a file changed in `main` too is
a conflict); instructions of the controller too large or not UTF-8 refuse
the session rather than being cut; the acceptance of a profile's risks is
held by the backend (`chat.start` refuses, the terminal must ask too);
"always" trusts a class of tools, not one tool.

Open question for the user: should `inventory.create`/`inventory.update`
(class config) write a managed inventory through a draft too? Today they
write `main` (in `limits.md`).

Questions asked by the page and the terminal, by profile:

| Profile | Classes | Questions |
|---|---|---|
| observer | read | none |
| assisted (default) | read, run | changes |
| diagnostic | read, run, adhoc-diagnostic | adhoc-diagnostic unasked, run asked |
| autonomous | read, run | none (the backend holds it) |
| administrator | all | every change, no "always" for adhoc, config, code |

Kinds of question: every call; changes only (today); trusted by class for
a conversation ("always"); none. adhoc and code are never autonomous.
Risks told: a profile beyond assisted is accepted once by the user after
what it may do, steering by the nodes and what goes to the provider are
told (audited); the active profile is in the header of the session and in
every question.

Steps, each tested before the next:

- [x] Page.
- [x] Terminal: `fleet assistant --profile`, the risks accepted at the
      first session; `{allowed_tools}` from `chat.start` (`profiles.question`,
      which the page reads from `mcp.tools` too); `fleet draft diff` for a
      person. To try with Claude's CLI: that `--allowedTools` works beside
      `--permission-mode manual`.
- [x] Docs.
- [x] Integration scripts: `chat.sh` by profile (page and terminal),
      drafts in `mcp.sh`; `make ct-test` on the three.
- [ ] The page in a browser on a VM with the adapter and its model (the
      user logs in to Claude there), `fleet assistant --profile` with
      Claude's CLI (`--allowedTools` beside `--permission-mode manual`);
      then the acceptance of the phase in `architecture.md`, its decisions
      into `decisions.md`.

## Future

Sets of variables: secret variables in a set (encrypted as credentials
are, kept off the page and out of export), and sets for ad-hoc commands.
Instructions per project for the assistant. External triggers (git
webhooks, auto-remediation); `ansible-pull` for very large fleets; secrets
on OpenBao or Bitwarden; export to Prometheus or InfluxDB; execution
environments in containers; other controller distributions. A limit on the
rows of state changes (off by default).
