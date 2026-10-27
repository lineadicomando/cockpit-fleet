# Changelog

Changes that a user or an administrator would notice, newest first. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html):
before 1.0.0, a minor version may change what an earlier one did. New
entries go under `[Unreleased]`, under the heading of their kind.

## [Unreleased]

### Changed

- The paths of a project are offered as one types, for inventories and
  playbooks: its files and directories, narrowed down as one types; a path
  that is not listed is still taken. `group_vars` and `host_vars` are never
  offered.

### Fixed

- A local project with files or directories the service account cannot read
  (logs written with mode 0600, say) failed every run and the reading of its
  inventories; they are now left out of the copy, and the run's output
  begins with the paths left out.
- A new inventory whose reading failed kept its dialog open, so that saving
  again made it twice; the dialog now closes and says the inventory was
  saved but not read.
- The message box of the assistant's page went partly below the bottom of
  the window.

## [0.1.0] - 2026-10-25

The first release: a controller on Debian 13, Fedora 44 or AlmaLinux 10 runs
Ansible from the Cockpit web console and watches the nodes through Ansible
alone. How to install it is in [docs/install.md](docs/install.md).

### Running playbooks

- Projects from git (https or ssh, a branch or a tag), a directory of
  the controller, written in the browser (a git repository with the history
  of each change, syntax checks and `ansible-lint` when installed), or an
  uploaded archive; collections from a project's requirements file.
- Inventories: a file or directory of a project, an inventory plugin, or one
  written in the browser, in YAML or INI; inventory scripts off unless the
  administrator allows them.
- Credentials for machines (key, key passphrase, password, `become`
  password), vaults and git sources; trusted SSH host keys, scanned from the
  hosts.
- Job templates with surveys (text, number, choice, yes or no, secret), a
  host pattern, tags, extra variables, forks, verbosity and `become`; dry
  runs with differences; launches on fewer hosts than the template's.
- Runs with live output, events by host and task, differences, a queue with
  a limit, cancellation; ad-hoc commands among the modules the administrator
  allows.
- Approvals: a template may make each of its runs wait until someone else
  approves it.
- Workflows: steps that follow each other on success, failure or always, pass
  on what `set_stats` set, may ask for an approval, and can be resumed from
  the step that failed.
- Schedules with systemd calendar expressions and time zones, missed times
  caught up or not, and what to do when a run is still going.
- Export and import of a configuration by name, without secrets.

### Watching the fleet

- Checks on a group of an inventory: reachability, disk, processor, memory,
  services, uptime and reboot, certificates, clock; on Linux and on Windows
  over SSH, with the same metrics.
- Checks written as a playbook ending with `lineadicomando.fleet.fleet_result`,
  and compliance checks: a playbook run in `--check --diff`, its changes the
  drift.
- Soft and hard states, flapping, acknowledgements, maintenance windows,
  thresholds applied on the controller.
- The inventory of hosts: hardware, packages, services and their changes,
  updates available, a search of packages across hosts, export as CSV or
  JSON.
- Time series of every metric, with hourly and daily aggregates, and charts
  on the page of a host.
- Cycles sized from what earlier ones took, and told when they fall behind;
  the health of the controller on the System tab.

### Notifications

- Telegram, webhooks, mail and Matrix, in English or Italian, with rules by
  scope (everything, a project, template, schedule, inventory, group or
  check) and by event (runs failed, changed, successful, canceled or waiting
  for approval; problems, recoveries and reminders of checks; cycles late).
- Messages grouped within a window, a limit per hour, retries.

### Agents and the terminal

- `fleet`, the command for a terminal, for every role: the backend's commands
  as `fleet AREA VERB`, names for identifiers, completion, a manual
  (`man fleet`), and projects cloned, changed with git and pushed back.
- `fleet-mcp`: the backend as tools for an agent (Model Context Protocol),
  under the role of the user and a ceiling set by the administrator; off
  unless enabled.
- An assistant in the page, an agent started for the user who opens it,
  which asks before each action that changes something; installed by a role
  of the bundled collection ([docs/chat.md](docs/chat.md)).

### Security

- No daemon and no listening port; runs in sandboxed systemd units under an
  unprivileged account, isolated from each other; two small root helpers.
- Secrets encrypted with `systemd-creds`, decrypted by PID 1 for the one run
  that needs them, never shown again.
- Roles from Unix groups (viewer, operator, administrator) and a right to
  approve; every change and every refusal in an audit log and the journal.
- Negative tests over every command of the backend; a review of the root
  helpers, the sandbox, sudoers, archives, git and bundles.
  See [docs/security.md](docs/security.md).

### Packaging

- `.deb` for Debian 13, `.rpm` for Fedora 44 and AlmaLinux 10, checked with
  lintian and rpmlint; upgrades keep everything, removal stops the runs
  going, purge on Debian removes the data.
- The interface in English and Italian, accessible to screen readers.

[Unreleased]: https://github.com/lineadicomando/cockpit-fleet/compare/v0.1.0...main
[0.1.0]: https://github.com/lineadicomando/cockpit-fleet/releases/tag/v0.1.0
