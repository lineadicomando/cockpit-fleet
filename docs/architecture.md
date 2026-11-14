# Architecture

How cockpit-fleet is put together on the controller, why, and what was
measured before building on it: one file per area in
[architecture/](architecture/). The plan is in [TODO.md](../TODO.md); the
decisions in [decisions.md](decisions.md). Read only the file of the area at
hand.

| File | What it covers |
|---|---|
| [overview.md](architecture/overview.md) | The parts and how a run flows; the security model: privilege boundaries (why no polkit rule), secrets, the run sandbox |
| [backend.md](architecture/backend.md) | `fleetctl`: the registry of commands, roles and rights, the audit |
| [jobs.md](architecture/jobs.md) | Runs and the other jobs, `fleet-run`, the queue and lanes, runs in series; what a launch is given: host keys, keys with a passphrase, vaults, sets of variables and secret variables, limits, ad-hoc commands, surveys, secret answers, masking |
| [projects.md](architecture/projects.md) | Projects (git, managed, uploaded), Galaxy, syntax checks, the files page and editor, `ansible.cfg`, help on modules, inventories (file, managed) |
| [store.md](architecture/store.md) | The store: hard links and intake, uploads, downloads to the browser and from URLs, copies, archives, sources, the page |
| [schedules.md](architecture/schedules.md) | The timer and `scheduler.tick`, missed times, overlap |
| [approvals.md](architecture/approvals.md) | Approvals and their modes |
| [workflows.md](architecture/workflows.md) | Workflows: steps, `set_stats`, failure, resume |
| [notifications.md](architecture/notifications.md) | Channels, rules, the outbox and its delivery |
| [checks.md](architecture/checks.md) | The catalog, `fleet_result`, cycles, states, sizing, falling behind, measures at scale |
| [inventory-of-hosts.md](architecture/inventory-of-hosts.md) | CMDB, updates, drift |
| [time-series.md](architecture/time-series.md) | Metrics and their aggregates |
| [maintenance.md](architecture/maintenance.md) | Retention and `maintenance.tidy` |
| [health.md](architecture/health.md) | `system.health` |
| [export.md](architecture/export.md) | Export and import of the configuration |
| [agents.md](architecture/agents.md) | `fleet-mcp`, classes of commands, profiles, questions by profile, autonomy, drafts |
| [terminal.md](architecture/terminal.md) | The command `fleet`, projects at a terminal |
| [chat.md](architecture/chat.md) | `fleet-chat`, ACP, sessions, the page of the assistant, instructions |
| [events.md](architecture/events.md) | The callback and `events.jsonl` |
| [windows.md](architecture/windows.md) | Windows nodes over SSH |
| [packaging.md](architecture/packaging.md) | `.deb`, `.rpm`, versions, other distributions |
| [interface.md](architecture/interface.md) | Language, accessibility, navigation, long lists |
| [tests.md](architecture/tests.md) | Unit, frontend and integration tests, containers and the lab |
| [acceptance.md](architecture/acceptance.md) | What each closed phase had to show, and how it was shown |
