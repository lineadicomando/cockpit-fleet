# cockpit-fleet — plan

Cockpit plugin that runs Ansible from the browser (Semaphore UI style) and
collects, stores and notifies on data from the nodes, with Ansible only. Not
a replacement for Icinga: nothing in real time, alerts "reasonably timely".

Decisions: [docs/decisions.md](docs/decisions.md). Known limits:
[docs/limits.md](docs/limits.md). How each closed phase was accepted:
`docs/architecture.md`, last section.

## Status (5 November 2026)

Phases 0–11 closed; 0.2.0 released on 2 November (sets of variables, the
assistant's setup guide). Since then, under `[Unreleased]`: runs launched in
series on an inventory (5 November). **Under way:** phase 12, below; the
classes of commands, the profiles and the limits of autonomy in the
backend, the modes of approval, an agent's ad-hoc command waiting for a
confirmation, done (6 November); next the drafts of files.

Last test runs: on 6 November the unit tests (557 backend, 130 frontend)
and `ct-test` of `negative mcp chat cli` on AlmaLinux and Debian;
on 1 November `make ct-test` of every script on Debian and AlmaLinux, and
on the three before that day's fixes (`cmdb` fails on Fedora only in a
container: `podman network connect`, netavark "invalid namespace path");
on 2 November `sandbox files upload inventories runs` on the VMs of Fedora
44 and AlmaLinux 10, and on 24 October `privileges sandbox roles runs
workflows schedules` on the three; `windows.sh` last on 20 October. Run two
distributions' `ct-test` one after the other: together they make
`checks.sh` miss its times.

To resume: `CLAUDE.md` (rules, commands, tests), `CLAUDE.local.md` if
present (this developer's VMs); `git status`, `git log origin/main..`;
`make check`; `make ct-test DISTRO=debian` (then `fedora`, `alma`) around a
change that needs it. Each backend module says what it is in its
docstring; `cli.py` is the registry, `auth.py` roles and rights, `db.py`
the schema (24); pages in `src/components/`, node modules in
`collection/ansible_collections/lineadicomando/fleet/plugins/modules/`.
For phase 12: `cli._through`, `agents.py` (`mcp.tools`), `mcp.py`,
`chat.py`, `chat_agent.py` (`fleet-chat`), `approvals.py`, `files.py`;
`src/lib/chat.ts`, `src/lib/acp.ts`, `ChatPage.tsx`; `docs/chat.md`.

## Phase 12 — Profiles of the assistant

Decided with the user on 5 November. Today the assistant has a ceiling
(`[mcp] max_role`, enforced in `cli._through`) and questions asked by the
page (`src/lib/chat.ts`: read-only tools unasked, the rest confirmed,
"always" per tool) or by the agent's CLI (`--permission-mode manual`).
Giving an agent ad-hoc commands means `max_role = "admin"`, which gives it
files, projects, credentials and `config.import` too; and nothing lets it
work without asking. The output of runs is written by the nodes, so an
agent that reads it and acts unasked can be steered by a node: when nobody
confirms, the backend is the only control left.

**What an agent may call** (the backend): every command declares a class.

| Class | Commands |
|---|---|
| read | lists, gets, `whoami`, `run.result`, `run.log`, CMDB, metrics |
| run | `launch_*`, `workflow.launch`, `run.cancel`, `check.run`, `schedule.run/enable/disable`, `state.ack`, `maintenance.*` |
| adhoc-diagnostic | `run.adhoc` with a module of `[mcp] adhoc_modules` (a subset of `[adhoc] modules` that changes nothing: `ping`, `setup`, `*_facts`, `win_ping`) |
| adhoc | `run.adhoc` with any other module, `become` |
| config | create and update of templates, inventories, schedules, rules, channels, sets of variables, checks, workflows |
| code | `file.*` writes, `project.create/sync/upload`, `credential.*`, `hostkey.add`, `config.import`, every delete |

**When it asks** (the page, the terminal): every call; changes only (as
today); trusted by class for a conversation; autonomous.

| Profile | Classes | Questions |
|---|---|---|
| Observer | read | none |
| Assisted (default, as today) | read, run | changes |
| Diagnostic | read, run, adhoc-diagnostic | adhoc-diagnostic unasked, run asked |
| Autonomous operator | read, run | none, within the limits below |
| Assisted administrator | all | every change, no "always" for adhoc, config, code |

- The administrator enables profiles (`[chat] profiles`,
  `default_profile`); the user picks one at the start of a session. A
  profile only restricts, under the user's role and `[mcp] max_role`:
  `fleet-chat` hands it to `fleet-mcp`, which adds it to every call beside
  `via`; forging it gains nothing; the audit records it.
- adhoc and code are never autonomous, whatever the configuration; still
  no tool for secrets.
- Autonomy bounded by the backend: runs per session (counted from the
  audit), templates with an approval still wait, maintenance windows
  respected; an option of autonomy in dry run only (`check` forced).
- adhoc asked by an agent waits as an approval in the mode "confirm", for
  the person it acts for; agents never approve.
- **Modes of approval**, instead of the yes/no `needs_approval` of a
  template or a workflow step: none; confirm (who launched it may decide
  too, without the right `approve`: a checkpoint on their own run, or
  between steps); another person (approvers and administrators, not who
  launched it: today's `needs_approval`, which migrates to it). A run of a
  schedule in confirm is decided as in another person, and only an
  administrator turns such a schedule on, as now. `[approvals]
  admins_exempt` (true, as now): false makes an administrator need someone
  else in another person. Not breaking.
- **Drafts**: files an agent writes go to a branch of the session, never
  to `main`; templates, schedules and checks run from `main` only; a
  person reviews the diff with its syntax check and merges, by a command
  refused to agents.
- **Risks told**: a profile beyond Assisted is accepted once by the user
  after what it may do, steering by the nodes and what goes to the
  provider are told (audited); the active profile is in the header of the
  session and in every question.
- **Instructions in layers**, composed by `chat.start` for the page and
  the terminal alike (today a constant in `src/lib/chat.ts` and the
  terminal's `{instructions}`): base and profile (the package, not
  removable), controller (an administrator's file under
  `/etc/cockpit-fleet/`, with a size limit); per project later, from a
  file of the project (text written by others when it comes from git);
  none per user (the agent's own settings stay off). Shown to the user;
  not a control; sent to the provider, so no secrets in them.

Each step on its own, tested, before the next:

- [x] Classes of commands: `agent=` in `@command`, a default from `role`
      and `mutating`, set by hand where it differs (ad-hoc, deletes, files,
      credentials); a test that every tool has one; `mcp.tools` gives it.
      `run.adhoc` is `adhoc`; adhoc-diagnostic is decided per call, by the
      module, in the next step
- [x] Profiles in the backend: `[chat] profiles` and `default_profile`,
      `[mcp] adhoc_modules` (a subset of `[adhoc] modules`); the profile
      passed by `fleet-chat` to `fleet-mcp` and added to every call beside
      `via`; `_through` refuses a class outside it, `mcp.tools` lists only
      its tools, the audit records it (schema 22); `negative.sh`, `mcp.sh`
      (`ct-test` of `negative mcp chat cli` on AlmaLinux and Debian, 6 November). The page still asks `mcp.tools` for
      all the tools of the role: it passes the session's profile in the
      step of the page
- [x] Limits of autonomy in the backend: runs per session (counted from
      the audit), templates with an approval still waiting, maintenance
      windows; dry run forced where the profile says so. Decided on 6
      November: no autonomous launch on an inventory with a window open,
      and no `state.ack`/`unack` or `maintenance.*` when autonomous. The
      session is drawn by `fleet-mcp` (audit column, schema 23); `ct-test` of
      `negative mcp chat` on AlmaLinux and Debian
- [x] Modes of approval (above): a migration (schema 24) from
      `needs_approval` to the mode, of templates and of workflow steps;
      `may_decide` by mode, `[approvals] admins_exempt`; schedules in
      confirm decided as another person; an agent never decides, not even
      its own run in confirm; export and import, `fleet`, the
      forms and the page of approvals; `workflows.sh` and `negative.sh`.
      `approval.approve`/`reject` lowered to operator (or the right):
      `may_decide` holds the rest. `needs_approval` deprecated, still
      taken and given; `ct-test` of `workflows negative roles runs cli mcp`
      on AlmaLinux and Debian, the forms not yet seen in a browser
- [x] adhoc asked by an agent waits in the mode confirm, for the
      person it acts for; expiry as the others. A diagnostic one (a module
      of `[mcp] adhoc_modules`, without become) starts, whatever the
      profile; `run.result` gives a waiting run as not done (it said done); `ct-test`
      of `mcp cli adhoc negative` on AlmaLinux and Debian
- [ ] Drafts of files: an agent's `file.write`/`file.change` on a branch of
      the session; templates, schedules and checks from `main` only; a
      merge command refused to agents; the diff and the syntax check of a
      draft; drafts left behind listed and discarded
- [ ] Instructions in layers composed by `chat.start` (the constant of
      `src/lib/chat.ts` and the terminal's `{instructions}` from it): base,
      profile, the administrator's file under `/etc/cockpit-fleet/`, with
      a size limit
- [ ] Page: choice of profile at the start of a session, its risks told and
      accepted once (audited), the profile shown in the header and in each
      question, the policy of questions by class with its floor, "always"
      for a class, the instructions the agent got, the review of a draft
      (diff, accept all or part, discard, edit before merging)
- [ ] Terminal: `fleet assistant --profile`, the profile as options of the
      agent's CLI (permission mode, tools allowed)
- [ ] Docs: `chat.md` (profiles, risks, instructions), `security.md`,
      `architecture.md`, the comments of `config.toml`; `CHANGELOG.md`
- [ ] Integration: `mcp.sh` and `chat.sh` by profile (refusals, limits,
      drafts, the approval of an ad-hoc); `make ct-test` on the three; the
      page in a browser on a VM with the adapter and its model

## Future

Sets of variables: secret variables in a set (encrypted as credentials
are, kept off the page and out of export), and sets for ad-hoc commands.
External triggers (git webhooks, auto-remediation); `ansible-pull` for very
large fleets; secrets on OpenBao or Bitwarden; export to Prometheus or
InfluxDB; execution environments in containers; other controller
distributions. A limit on the rows of state changes (off by
default).
