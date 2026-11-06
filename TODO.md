# cockpit-fleet — plan

Cockpit plugin that runs Ansible from the browser and collects, stores and
notifies on data from the nodes, with Ansible only (nothing in real time).
Decisions: [docs/decisions.md](docs/decisions.md); known limits:
[docs/limits.md](docs/limits.md); how each phase was accepted and how each
part works: `docs/architecture.md`.

## Status (6 November 2026)

Phases 0–11 closed; 0.2.0 released on 2 November. Under `[Unreleased]`:
runs in series on an inventory, and steps 1–5 of phase 12 (below). Five
commits of phase 12 on `main` are not pushed (`git log origin/main..`).
**Next:** phase 12, step 6, the drafts of files.

Tests: on 6 November the unit tests (557 backend, 130 frontend) and
`ct-test` of `mcp chat cli adhoc negative workflows roles runs` on
AlmaLinux and Debian; not Fedora, no VM, and the forms of approval not
seen in a browser. Last full `ct-test`: 1 November, on the three (`cmdb`
fails on Fedora only in a container: netavark "invalid namespace path").
Run two distributions' `ct-test` one after the other, not together.

To resume: `CLAUDE.md`, `CLAUDE.local.md` if present; `git status`;
`make check`. Each backend module says what it is in its docstring;
`cli.py` is the registry, `db.py` the schema (24).

## Phase 12 — Profiles of the assistant

Decided with the user on 5 November: what an agent may do is held by the
backend, by classes of commands and profiles, because the output of runs is
written by the nodes and an agent that reads it can be steered by a node.

**Done** (how it works: `docs/architecture.md`, "Agents" and "Approvals"):
classes of commands (`agent=` in `@command`, `cli.AGENT_CLASSES`,
`CLASS_TABLE` in `test_agents.py`); five profiles (`profiles.py`; `[chat]
profiles`, `default_profile`, `[mcp] adhoc_modules`), held in
`cli._through`, passed by `fleet-chat --profile` to `fleet-mcp --profile`,
in the audit (schema 22); limits of autonomy (`autonomy.py`, `[chat]
autonomous_runs`, `autonomous_dry_run`, a session drawn by `fleet-mcp` and
in the audit, schema 23); modes of approval (`approvals.py`: `none`,
`confirm`, `other`, `[approvals] admins_exempt`, schema 24, `needs_approval`
deprecated); an agent's ad-hoc command waits for its person's confirmation
unless diagnostic. Decided on 6 November along the way: an autonomous
agent launches nothing on an inventory with a maintenance window open and
never calls `state.ack`/`unack` or `maintenance.*`; a diagnostic ad-hoc
command never waits; `approval.approve`/`reject` need the role operator or
the right, `may_decide` does the rest.

**Still to build, as decided:**

| Profile | Classes | Questions (page and terminal) |
|---|---|---|
| observer | read | none |
| assisted (default) | read, run | changes |
| diagnostic | read, run, adhoc-diagnostic | adhoc-diagnostic unasked, run asked |
| autonomous | read, run | none (the backend holds it) |
| administrator | all | every change, no "always" for adhoc, config, code |

Questions: every call; changes only (today); trusted by class for a
conversation; none. adhoc and code are never autonomous.

- **Drafts**: files an agent writes go to a branch of the session, never
  to `main`; templates, schedules and checks run from `main` only; a person
  reviews the diff with its syntax check and merges, by a command refused
  to agents.
- **Risks told**: a profile beyond assisted is accepted once by the user
  after what it may do, steering by the nodes and what goes to the provider
  are told (audited); the active profile is in the header of the session
  and in every question.
- **Instructions in layers**, composed by `chat.start` for the page and the
  terminal alike: base and profile (the package, not removable), controller
  (an administrator's file under `/etc/cockpit-fleet/`, size limit); per
  project later; none per user. Shown to the user; not a control; sent to
  the provider, so no secrets in them.

Steps, each tested before the next:

- [ ] Drafts of files: an agent's `file.write`/`file.change` on a branch of
      the session; templates, schedules and checks from `main` only; a
      merge command refused to agents (class `code`, `tool=False`); the
      diff and the syntax check of a draft; drafts left behind listed and
      discarded. Start from `files.py` (a managed project is a work tree
      with its git repository beside it, one commit per change;
      `file.history`, `file.diff`, `file.restore`), `jobs.project_tree` and
      `jobs.snapshot` (what a run copies); the session is `ctx.session`
- [ ] Instructions in layers composed by `chat.start` (today
      `INSTRUCTIONS` in `src/lib/chat.ts` and in `chat_agent.py` for the
      terminal's `{instructions}`)
- [ ] Page: choice of profile at the start (`chat.agents` gives `profiles`
      and `default_profile`; `src/lib/chat-agent.ts` must pass `--profile`
      to `fleet-chat`, and the profile to `mcp.tools`, which today lists
      every tool of the role), its risks told and accepted once (audited),
      the profile in the header and in each question, questions by class
      (`class` of each tool) with its floor, "always" for a class, the
      instructions the agent got, the review of a draft (diff, accept all
      or part, discard, edit before merging)
- [ ] Terminal: `fleet assistant --profile` (`fleet-chat --terminal
      --profile` exists), the profile as options of the agent's CLI
      (permission mode, tools allowed)
- [ ] Docs: `chat.md` (profiles, risks, instructions), `security.md`,
      `architecture.md`, `config.toml`; `CHANGELOG.md`
- [ ] Integration: `chat.sh` by profile, drafts in `mcp.sh`; `make ct-test`
      on the three; the page in a browser on a VM with the adapter and its
      model; then the acceptance of the phase in `architecture.md`, its
      decisions into `decisions.md`

## Future

Sets of variables: secret variables in a set (encrypted as credentials
are, kept off the page and out of export), and sets for ad-hoc commands.
External triggers (git webhooks, auto-remediation); `ansible-pull` for very
large fleets; secrets on OpenBao or Bitwarden; export to Prometheus or
InfluxDB; execution environments in containers; other controller
distributions. A limit on the rows of state changes (off by default).
