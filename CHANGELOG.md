# Changelog

Changes that a user or an administrator would notice, newest first. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html):
before 1.0.0, a minor version may change what an earlier one did. New
entries go under `[Unreleased]`, under the heading of their kind.

## [Unreleased]

### Added

- Host keys by host: the page groups the trusted keys of each host (or the
  hosts by inventory, or none), filters by inventory, by trust and by text
  (host, name in an inventory, fingerprint), and lists the SSH hosts of
  inventories that have no trusted key, which runs would refuse. Hosts are
  selected to check their keys again, to export them as a `known_hosts`
  file, or to stop trusting them all at once: `hostkey.remove` and
  `hostkey.scan` take `hosts`, and `hostkey.export` (viewers) gives the
  lines of `known_hosts`.
- Secret variables in a set of variables: their values are encrypted as a
  credential's and can never be read back; a run gets them as extra
  variables, used as written, and they are masked in its output. An
  inventory takes sets of variables too, so that its own files and the
  playbooks use a secret by name (`ansible_password: "{{ lab_password }}"`,
  a license key): every run on it takes them before its template's sets,
  and so do ad-hoc commands and checks. Administrators set the values on
  the Variables page or with `fleet secret --varset SET VARIABLE`; an
  export carries their names, and they are entered again after an import.
  A run stops when a secret variable it takes has no value.
- The store: files on the controller that playbooks copy to nodes, on a
  page of their own (Store) and with `fleet store put` and `fleet store
  read`. Files of any size are uploaded from the browser, resumed when an
  upload stops, and downloaded straight to the browser's downloads; a
  playbook finds the store in the directory named by `fleet_store`, and
  what it adds or replaces there (a `get_url`, a `fetch`) is kept in the
  store when the run ends, put down to the run. Viewers download, operators
  and administrators also upload, move and delete. Every run can read the
  store: it is not for secrets. A limit in `[store] max_gb`; files put in
  `/var/lib/cockpit-fleet/store` by hand appear too, and the digest of each
  file is taken in the background.
- Downloads from URLs into the store, queued from the Store page or with
  `fleet store fetch`: a job in the sandbox of a run fetches the file (http
  or https, through `[network] proxy`), takes up a dropped connection where
  it stopped, checks the digest when one is given, and the page shows its
  progress; downloads have a queue of their own, `[store] max_downloads`
  at once, beside the playbooks. The controller's own addresses and
  link-local ones are refused.
- The Store page works as a file manager: search in a folder or in the
  whole store, columns to sort by, many entries selected and moved or
  deleted together, drag and drop onto folders and onto the path above,
  cut and paste (Ctrl+X, Ctrl+V), rename (F2), a menu on the right click
  and the menu key, a panel of details with the path to use in a playbook,
  folders uploaded from the desktop, one question for names already there
  (replace, keep both, skip), and a move undone from its notice. The
  folder of a download from a URL is chosen from those of the store.
  Entries are also copied (Ctrl+C, "Copy to…", a drag with Ctrl), and the
  page shows by itself what runs or hands put in the store.
- `store.copy`: copies within the store, made at once up to 1 GiB in all
  (on XFS and Btrfs sharing the blocks of the file), by a job beside the
  downloads above that. `store.move` and `store.delete` take many paths in
  one call, all or nothing, with one record in the audit trail;
  `store.list` answers "unchanged" to the token of an earlier listing;
  `store.archive` writes files and directories of the store out as one
  tar, streamed: nothing of it is held whole.
- Folders of the store, or many files, downloaded as one tar from the Store
  page; the editor of a playbook completes the paths of the store after
  `{{ fleet_store }}/`.
- A file downloaded from a URL keeps it as its source: it is checked
  again with a conditional request (ETag, Last-Modified), from the Store
  page or `store.refresh`, and every day, week or month if asked
  (`store.track`), and downloaded again only when it changed there. A
  download that ended short is resumed where it stopped, for a week.

- What Ansible writes for a task on the controller (the whole download of a
  `get_url` delegated to it, say) is kept on the disk of the run, not in
  memory: a download larger than half the memory no longer fails.
- Runs launched in series: a job template or an ad-hoc command launched
  with "Start after the runs already launched on this inventory" waits in
  the queue while another run is running on its inventory, or one launched
  before it is queued there, whatever their outcome; it takes no place in
  the queue meanwhile. Runs waiting for approval and check cycles do not
  hold it back. The box is ticked by default in the interface; the
  command line and the API launch in series only when asked. The page of runs says what it waits for (`serial` of
  `run.start` and `run.adhoc`, `waiting_for` of `run.get` and `run.list`,
  `fleet launch --serial`).
- Profiles of the assistant, held by the backend: `observer` (reads),
  `assisted` (also launches and cancels what is defined), `diagnostic` (also
  ad-hoc commands of modules that change nothing, `[mcp] adhoc_modules`),
  `autonomous` (as `assisted` for now) and `administrator` (whatever the
  role allows). The administrator enables them in `[chat] profiles` and
  names the one an agent gets by default (`default_profile`); `fleet-mcp
  --profile` and `fleet-chat --profile` start a session in one. The audit
  log records the profile of each call made by an agent and the session of
  `fleet-mcp` it came through, and `mcp.tools` gives each tool its class.
- Limits of an agent acting without asking (profile `autonomous`): at most
  `[chat] autonomous_runs` launches in a session, none on an inventory
  with a maintenance window open, no acknowledging a problem and no
  maintenance window opened or closed, and with `[chat]
  autonomous_dry_run` dry runs only.

- Modes of approval for a job template and a step of a workflow: none, a
  confirmation, which who launched the run may give too (a checkpoint on
  their own run, or between two steps), or another person, as before
  (`approval`: `none`, `confirm`, `other`). A run of a schedule in confirm
  waits for another person, and an agent never confirms. `[approvals]
  admins_exempt = false` makes an administrator need someone else to
  approve their own run. Existing templates and steps that needed an
  approval wait for another person, as before.

- An ad-hoc command an agent asks for waits until the person it acts for
  confirms it, unless its module changes nothing (`[mcp] adhoc_modules`,
  without become); the list of runs waiting for approval shows the
  command. The agent cannot confirm it.

- Drafts: what an agent writes in a project or inventory written in the
  interface goes to a draft of its session, a git branch beside the files,
  which no run reads; templates, schedules and checks keep using what a
  person wrote. `draft.list` and `draft.diff` show the drafts, what each
  changes and the syntax checks of it; `draft.merge` takes all or some of
  its files in as one commit of the person, and `draft.discard` throws it
  away. Agents can do neither. The file commands take `draft` to read or
  edit one.
- Instructions of the assistant in layers: a base and a part for the
  profile of the session, from the package, then the controller's own, from
  `/etc/cockpit-fleet/assistant.md` if an administrator writes one (up to
  8 kB). `chat.start` gives them, as one text and by layer, to the page and
  to `fleet assistant` alike.
- The assistant's page: the profile chosen at the start, with what it may
  do; for `diagnostic`, `autonomous` and `administrator`, their risks
  accepted once by each user (`chat.accept`, recorded in the audit log;
  `chat.start` refuses with `not_accepted` until then). The profile is
  shown in the header and in each question; questions follow the class of
  the tool, "always" trusts a class for the conversation, and ad-hoc
  commands, changes and files are asked about every time. The
  instructions the assistant was given can be read there. Drafts waiting
  for review are told on the page and on the files of their project, to be
  reviewed, merged in whole or in part, discarded, or edited first.
- `fleet assistant --profile`: the session in a profile, its risks told and
  accepted at the first one. The agent's program for a terminal gets the
  tools the profile lets through with no question (`{allowed_tools}` in
  `terminal` of `[chat.agents.NAME]`, which the role
  `lineadicomando.fleet.chat_agent` writes when it is run again; without
  it every call is asked about). `fleet draft diff` shows a draft as a
  person reads it, with the command that merges what was shown, and the
  draft commands take the name of a project or inventory.

### Changed

- The page's eighteen tabs are now six, each with its own row of tabs
  below: Overview, Nodes (hosts, assets, checks, host keys), Automation
  (runs, job templates, workflows, schedules), Resources (projects,
  inventories, store, variables, credentials), Administration
  (notifications, system, audit log) and Assistant. Links and bookmarks
  are unchanged. Searching Cockpit's menu for a section ("workflow",
  "credential", "pianificazione") opens it directly.

### Fixed

- With Cockpit 370 or later, uploading a project archive, or any large
  input from the page (a long file saved in the editor, a long message to
  the chat), no longer closes the Cockpit session ("Disconnected"): the
  page sends it in pieces of 64 KiB, under the limit cockpit-ws now has on
  a message from the browser.
- `run.result` gave a run waiting for approval as done: it is not, and the
  answer says in which mode it waits; `fleet result --wait` waits for it.
- The syntax check of a playbook no longer reports an `internal-error` of
  ansible-lint on every playbook with ansible-core 2.19 (Debian 13,
  Fedora 44): ansible-lint's own syntax check, which has no inventory, is
  no longer made to fail for it.

### Security

- Reading an inventory left in the run's directory, until the run was
  deleted, everything `ansible-inventory --list` gives: its variables, and
  in clear those of a file encrypted with Ansible Vault. The listing now
  stays in the run's runtime directory, gone with it, and only hosts,
  groups and how a host is reached are written; the listings that older
  versions left are removed on upgrade.

### Deprecated

- `needs_approval` of `template.create`, `template.update` and the steps of
  `workflow.create` and `workflow.update`: use `approval`. It is still
  taken (true is `other`) and given, and an export carries both.

### Breaking

- An agent gets the tools of its profile, `assisted` unless configured:
  with `[mcp] max_role = "admin"`, ad-hoc commands, changes to templates,
  inventories and the rest, files, projects and credentials now need
  `administrator` among `[chat] profiles` and chosen, or as
  `default_profile`.

## [0.2.0] - 2026-11-02

### Added

- Sets of variables, in a new Variables tab: named objects of extra
  variables that job templates take, several in order, before their own
  extra variables (a later one wins), so that a value common to many
  templates is written once (`varset.list`, `create`, `update`, `delete`;
  `variable_set_ids` of a template). The form of a template shows what a
  run gets and where each variable comes from; a run records the sets it
  took; export and import carry them. Not for secrets: every role sees
  them.
- The Assistant tab is shown to administrators while the chat is not set
  up, with what is missing and the two lines to paste in Cockpit's
  terminal: one that installs the agent and turns the chat on, one for the
  login; it shows the chat once nothing is missing.
- The playbook `lineadicomando.fleet.chat_agent` takes
  `-e chat_agent_enable_mcp=true`, which sets `enabled = true` in `[mcp]`
  of `config.toml`.
- Files of a project written in the interface can be changed several at a
  time, as one commit made whole or not at all (`file.change`: write,
  move, copy and delete, of files and directories); a directory is
  renamed, moved or deleted with everything in it (`file.move`,
  `file.delete`); a file or a directory is put back as it was at an
  earlier revision (`file.restore`); a file is downloaded, a directory as
  a `.tar.gz` (`file.download`, any kind of project). Files that are not
  text, up to 1 MB, can be uploaded and kept, and are shown as such.
- The page of the files is a file manager: folders are created, renamed,
  moved, copied and deleted with what they hold; files and folders are
  dragged onto a folder to move them, and files from the desktop to upload
  them; several are selected at once; files are uploaded and downloaded, a
  folder as an archive; several files stay open in tabs with their changes
  while another tab of Fleet is shown; the tree is filtered by name; a file
  is put back as it was after an earlier change. The tree is used with the
  keyboard too (arrows, Enter, Space, F2, Delete). Folders are kept when
  empty, made so or left so by a move or a delete (`file.change` takes
  `mkdir`), as an empty `.gitkeep` that the page does not show; an
  imported archive keeps its empty folders too.
- The syntax of an inventory written in the interface is checked when one
  of its files is saved, imported or pushed, and on request from the
  editor ("Check syntax", `file.check`): it is read as for its hosts, but
  any source or variables file that cannot be read fails the check.
- `fleet inventory clone`, `pull` and `push` work on an inventory written
  in the interface as `fleet project` does on a project; a push reads it
  again.
- An archive (tar or zip) can be imported into a project or inventory
  written in the interface, into one of its directories, as one commit
  (`file.import`); it is unpacked in the sandbox like an uploaded project,
  and links are refused.
- An inventory that is a file of a project can be used by templates,
  ad-hoc commands and checks of other projects: the run gets a copy of the
  inventory's directory, with its `group_vars` and `host_vars`, beside the
  playbooks of its own project.
- An inventory that is a file of a project is read again when a change
  touches its files, its `group_vars` or `host_vars`, and after the
  project is fetched or uploaded again.

- Rules of notifications can tell when the check cycles of an inventory
  cannot be prepared (`unprepared`, "Cannot start"), which leaves their
  checks unknown and so told about by no rule on problems, and when they
  can again (`prepared`); the System tab shows such a group with the
  reason.
- The Checks tab filters its checks by text, state, inventory, kind and
  whether they are enabled, groups them by inventory (the default), kind
  or worst state, with a button to open or close every group, and sorts
  them by name, interval or state; maintenance
  windows that have ended are hidden unless asked for. The browser
  remembers these choices between tabs and visits.
- The Job templates tab does the same: filters by text, project,
  inventory, a feature (an approval, questions, scheduled or not) and the
  outcome of the last run; groups by project (the default), inventory or
  last run; and shows each template's last run, its schedules and whether
  it asks for an approval or questions. `template.list` gives each
  template its `last_run`.

### Changed

- The extra variables of a job template must have names Ansible takes for
  a variable (letters, digits and `_`, not starting with a digit): a
  template with another name, which Ansible refuses at run time, is
  refused when saved.
- An inventory written in the interface is a tree of files with a history,
  like a project written there: a file of hosts with `group_vars/` and
  `host_vars/` beside it, vault files, several sources, plugins with the
  names they want. Its files are changed with "Files" on the Inventories
  tab (and `file.*` with its id), each change a commit that reads it again;
  it is read whole, or from a file or directory of it. The text of an
  existing one becomes its `hosts.yml` (or `hosts`) the first time it is
  used. Export and import carry its files. A backup now includes
  `/var/lib/cockpit-fleet/inventories/` (`docs/backup.md`).

- Files of a project written in the interface, or pushed from a clone, may
  be up to 1 MB instead of 512 kB; `file.read` of a file that is not text
  answers with `binary` instead of an error, and `file.list` gives the
  directories and the revision too.

- In Italian, a project or inventory written in the interface is now
  "Interno" rather than "Scritto qui".
- The paths of a project are offered as one types, for inventories and
  playbooks: its files and directories, narrowed down as one types; a path
  that is not listed is still taken. `group_vars` and `host_vars` are never
  offered.
- A package built between two releases has a version of its own, from git:
  `0.1.0+3.gabc1234` for the `.deb`, `0.1.0^3.gabc1234` for the `.rpm`,
  above the last release and below the next, so installing it is an
  upgrade; the Overview tab shows `0.1.0-3-gabc1234`.

### Security

- A `.gitignore` written in a project of the interface kept the files it
  named out of the project's history, diff and push, while runs still
  executed them. Every file written is now committed.
- `docs/security.md` says what the sandbox of a run does not hide, the
  directories of `[projects] local_roots`, and `docs/install.md` how to
  hide them with a drop-in; and that the database, not the journal, is
  the record of the audit, since any local account can write the journal.

### Fixed

- A local project with a directory that can be read but not searched no
  longer fails to launch: its contents are listed as left out at the top
  of the run's output, which now says "collections" when the paths left
  out are under the collections store.
- A run that systemd could not start kept its prepared directory, the
  encrypted secrets included, until the prune; it is removed at once.
- A systemd that does not answer no longer holds `fleetctl` forever: the
  command fails after 30 seconds, and the self-check marks the scheduler
  and the notifications as failed.
- "Check now" while the scheduler is planning a cycle answers "try again"
  instead of queueing a second cycle for the same checks, whose results
  the first one then lost.
- A schedule with overlap "cancel" recorded an error, and lost its
  occurrence, when one of the runs being stopped ended by itself
  meanwhile.
- A run canceled while it waited for approval was later shown as
  "expired" among the decided approvals.
- The Checks and Job templates tabs showed a filter chip on the only
  inventory or project, whose select is not shown; a screen reader named
  the threshold inputs of a check by the metric's internal name, now by
  the measure as shown; the "Become root" and "Enabled" checkboxes of a
  check are grouped under a label, "Options", where there was a blank one.
- The packages install the documentation without the screenshots of the
  README (a megabyte less in `/usr/share/doc`).
- A check cycle whose plan had been rewritten in its run directory (a
  playbook of the user running on the controller could do it) broke the
  reconciling of every run, so the lists of runs and states answered with
  an internal error until the row was fixed by hand. The plan is now
  checked again when read back, and such a cycle ends with its checks
  unknown.
- Exporting assets as CSV or JSON, and the configuration from the System
  tab, did nothing in Firefox: Cockpit's content security policy blocked
  the file. The page's policy now allows it.
- The message box of the Assistant could still fall below the bottom of
  the window: the chat now takes the room left in the page with no
  measuring, and keeps it when the window or what is above it changes.
- A local project under `/home`, `/root`, `/tmp` or `/var/tmp` worked when
  launched from the interface but never from the scheduler, which cannot
  see those directories: scheduled runs and every check cycle failed with
  "is not a directory". Such a project, or a directory of `local_roots`
  there, is now refused with the reason, and the self-check fails on it;
  `fleet(1)` says how to bind-mount a project that has to stay in a home.
  Upgrading a controller with such a project: move or bind-mount it, then
  change its path.
- A local project with files or directories the service account cannot read
  (logs written with mode 0600, say) failed every run and the reading of its
  inventories; they are now left out of the copy, and the run's output
  begins with the paths left out.
- A new inventory whose reading failed kept its dialog open, so that saving
  again made it twice; the dialog now closes and says the inventory was
  saved but not read.

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

[Unreleased]: https://github.com/lineadicomando/cockpit-fleet/compare/v0.2.0...main
[0.2.0]: https://github.com/lineadicomando/cockpit-fleet/releases/tag/v0.2.0
[0.1.0]: https://github.com/lineadicomando/cockpit-fleet/releases/tag/v0.1.0
