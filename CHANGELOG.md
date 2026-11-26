# Changelog

Changes that a user or an administrator would notice, newest first. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html):
before 1.0.0, a minor version may change what an earlier one did. New
entries go under `[Unreleased]`, under the heading of their kind.

## [Unreleased]

### Added

- Assistant: skills, what a project, the collections it installs and this
  controller teach it, in the format of Claude's skills
  (`.fleet/skills/NAME/SKILL.md` in a project or a collection,
  `/etc/cockpit-fleet/skills/NAME/SKILL.md`). Each session is told their
  names and descriptions and reads one when its work is what it describes
  (`skill.list`, `skill.read`, `fleet skill`). They are read by every role
  and go to the provider of the model: no secret in them.
- Documents: the history says which changes the assistant made, beside
  the person it acted for.
- Documents: skills in the folder `skills` of the documents
  (`skills/NAME/SKILL.md`), which the assistant may propose for a task
  that recurs and people correct, compare and restore there. One whose
  last change came from the assistant waits for a person: out of its
  skills until an operator or an administrator chooses "Approve" on it,
  after the changes since the last approved version, or saves it
  (`skill.approve`, `fleet skill approve`, never an agent; `skill.list`
  lists those waiting).
- Checks: the cycles that check the hosts can be followed. A check shows
  "Checking" while a cycle holds it and its last cycle, which opens the
  page of that run; "Recent cycles" lists those that held it, and "Check
  now" links to the cycle it queued. The page of a cycle has what it found
  on each host for each check, for every role. Runs: a filter by type,
  check cycles among them (`run.list` takes `check_id`; `check.cycle`,
  `fleet check cycle`).
- Job templates: an inventory chosen at launch. A template may allow other
  inventories beside its default, or any, and whoever launches it chooses
  one (`inventory_ids` and `any_inventory` of the template, `inventory_id`
  of `run.start`, `fleet launch --inventory`): one template serves every
  lab instead of one each. The list shows a template under each inventory
  it allows and launches it there; an agent's tool of it takes the
  inventory as a parameter. A schedule keeps the inventory it launches on.
- Workflows: one that names an inventory runs every step there, each
  template allowing it; one that names none may be launched on an
  inventory every step allows (`inventory_id` of `workflow.launch`, and of
  its schedule).
- Inventories: a machine credential of their own, which a run takes when
  its template has none, a check when it has none, and an ad-hoc command
  when none is given.
- Documents: texts in Markdown and their attachments, in folders, kept on
  the controller apart from the store and never given to runs. Every
  change to a text is kept in a history of its own, with who made it,
  and a text can be compared with or restored to any earlier version.
  Viewers read and search them, operators also write, upload, move,
  copy and delete. Commands `doc.*` (`fleet doc put` and `fleet doc get`
  at a terminal); `[documents] max_mb` of `config.toml` bounds them
  (1024).
- Resources, Documents: folders of texts and attachments managed as the
  store is; a text shown as it will be printed, written beside a preview,
  its history with the changes and "Put it back"; printed or saved as
  PDF from the browser, downloaded as Markdown or as a web page that
  holds its images. Links lead to other documents and to runs and hosts
  (`fleet:run/ID`, `fleet:host/ID`); images come only from the
  documents, and raw HTML is shown as text. The assistant's writes have
  "Open the document" in the chat.
- Assistant: in every profile it reads, searches and writes the
  documents, a change asked about as launches are and a deletion every
  time; in `autonomous` it changes them unasked, at most
  `[chat] autonomous_writes` times in a conversation (20).
- Schedules: a schedule launches a workflow as well as a job template,
  when none of its steps, nor their templates, asks for an approval
  (`workflow_id` of `schedule.create`). Its steps run as they are, with
  no limit, answers or values of the schedule's, and queue with its
  priority; a step whose template comes to need an approval after the
  workflow was scheduled fails instead of waiting. A workflow that a
  schedule launches cannot be deleted.
- Nodes, Network: an administrator scans a subnet the controller is
  attached to, or one listed in `[network] scan_subnets` of
  `config.toml`, for the hosts that answer: each address is tried with
  an ICMP echo and a TCP connection to a few ports
  (`[network] scan_ports`), and each host found is shown with its names
  from the DNS and NetBIOS, its MAC address on the controller's own
  segment, its open ports and the hosts of inventories it may be. A scan
  is a run, of at most `[network] scan_max_addresses` addresses (1024).
  Commands `netscan.subnets`, `netscan.start` and `netscan.result`.
- Nodes, Network: the hosts a scan found, all or those chosen, make a new
  inventory written in Fleet, named by their NetBIOS or DNS names or by
  address, with `ansible_host` and `mac_address`
  (`netscan.inventory`); or their MAC addresses are written as
  `mac_address` into an inventory written in Fleet that holds them, in
  `host_vars/HOST/fleet_mac.yaml`, after a preview of what changes
  (`netscan.macs`). A file `host_vars/HOST.yaml` already there is moved
  into `host_vars/HOST/`, unchanged, since Ansible would no longer read
  it beside that directory.
- Inventories, "Probe the hosts": an operator probes the hosts of an
  inventory from the controller, to tell why one does not answer: its
  name does not resolve, nothing answers, it is on the segment but
  answers nothing, its connection's port is closed, the MAC address seen
  is not the one the CMDB knows (and whose it is), the CMDB saw it at
  another address, it gives another name (`netscan.probe`). The
  assistant uses it before saying that hosts are off.
- Collection: `lineadicomando.fleet.net_probe`, for Linux and Windows
  nodes, probes from a node a subnet the controller may scan or hosts of
  the inventory, and gives the MAC addresses of the node's segment, beyond
  a router the controller cannot see past. It is among the default
  modules of `[adhoc] modules` and `[mcp] adhoc_modules`, so the assistant
  in the profile diagnostic may run it; its ports are those of
  `[network] scan_ports`.
- Assistant: in the profiles diagnostic and administrator the assistant
  of an administrator may scan a subnet, asked every time (a class of
  tools of its own, `scan`).
- Assistant: above the message box, what the assistant launched in the
  conversation. In the profile `autonomous`, the runs launched of those
  it may and, when they are spent, "Allow N more" (`[chat]
  autonomous_runs` more each time) instead of starting again; in any
  profile, while its runs have not ended, "Stop everything" stops the
  answer and cancels them. Commands `chat.session`, `chat.grant_runs` and
  `chat.cancel_runs`, which agents cannot call; `fleet-mcp --session`.
- Assistant: in the profile `autonomous`, the start of a session (and
  the change to that profile) has a field for the runs it may launch in
  each conversation, lower or higher than `[chat] autonomous_runs`
  (command `chat.limit_runs`, which agents cannot call).

### Changed

- Documents: a document follows the theme of Cockpit, dark included; a
  page saved from it follows the theme of the system it is opened on.
  Printed, it stays light.
- Checks: a check cycle is kept a week after it ends instead of a day
  (`[checks] keep_days` of `config.toml`, 7): lower it for a large fleet
  checked often.
- Assistant: an autonomous agent is told to launch one run on a group
  rather than one per host, and to stop and say so when its runs are
  spent.
- `[chat] autonomous_runs` is 30 by default, not 10: a controller that
  leaves it commented out lets an autonomous agent launch 30 runs in a
  conversation.
- Nodes: its sections are in the order Assets, Checks, Hosts, Host keys,
  and the tab opens on Assets.
- Workflows: in the form, the job templates a step may take are grouped
  by inventory, and each step says the inventory it runs on; the steps of
  a workflow run show theirs too (`inventory_id` and `inventory` of each
  step in `workflow.progress`).
- A job template is chosen by typing part of its name, its inventory, its
  project or its playbook, in the steps of a workflow, in schedules and in
  the rules of notifications; so are a schedule's workflow and the target
  of a rule.

### Fixed

- Documents: "Download as a web page" did nothing in Firefox.
- `fleet store put` failed after sending the file, with Python 3.13 as
  Debian 13 has it ("flush of closed file"), though the file arrived.
- Assistant: a message written and not sent is still there after a
  visit to another tab of Fleet (not after reloading the page).
- A `fleetctl` stopped (Ctrl-Z) or hung while it collected the runs that
  ended no longer holds up every page and the start of queued runs: the
  pages go on after a few seconds, the end of a run is recorded by a later
  reading. A change of the documents waits at most 30 seconds for another
  ("try again in a while").
- Store: a folder of more than about a thousand files is downloaded whole;
  the browser saved an empty archive. A file deleted while the archive is
  being made is left out of it.
- Upgrade: the packages deleted the listing of an inventory that a run had
  read and that was not yet collected, so that run ended in error; they now
  delete only those left by versions before 0.3.0, which held its
  variables.
- Approvals: the dialog of an approval or a rejection stays open, with the
  comment typed, when someone else decides first; it says so. It vanished
  with the run from the list.
- Files of a project or an inventory: a file whose tab was open while the
  page was left for another tab of Fleet is read again on coming back; it
  spun for ever.
- Launch: an optional choice of a survey that has a default no longer
  offers "None", which gave the default anyway.
- Runs on more than about 1900 hosts: their totals per host, and what the
  playbook set with `set_stats` for the next steps of a workflow, are no
  longer lost.
- Store: a move to where a file had been deleted by hand failed after
  moving the file.
- The packages install `CHANGELOG.md` with the rest of the documentation,
  and the documents installed link to those of the repository they do not
  hold.

### Security

- The ticket of a secret answer to a survey no longer appears in the
  journal, nor in the audit trail of a launch that fails: a reader of the
  journal could launch a run with it, and somebody else's secret, before
  the run it was meant for.
- A playbook can no longer make a failed run pass for a successful one: it
  could rewrite the outcome of its run, which is kept in the run's own
  directory, from a process left running or through the memory of the
  process that writes it. Nothing of the job outlives it now, and that
  process is out of its reach.

## [0.4.0] - 2026-11-18

### Added

- Assistant: "Change the profile" in a session goes on with the same
  conversation in another profile (Diagnostic to Administrator, say). The
  assistant is started again in it, its risks told first when not
  accepted yet; what was allowed always in the conversation is asked
  again.
- Hosts and Problems: a button "Assistance" on a check whose result is not
  ok, when the assistant is set up, prepares in the chat a message asking it
  to investigate it: the check and its host, the output of the check cycles
  (for operators and administrators), and the node itself with commands
  that only read when the profile allows them (Diagnostic is suggested).
- Runs: a button "Assistance" on a run that has ended, when the assistant
  is set up, prepares in the chat a message asking it to examine the run,
  find anomalies, investigate their causes and propose solutions. The
  message is edited and sent by the user, in the conversation open if
  there is one; before a session starts, the profile Diagnostic is
  suggested when enabled.
- Hosts and Assets: a filter by any word (name, address, inventory,
  groups, system; in Problems also the check and what it found, in Assets
  also the hardware), and the hosts grouped by inventory when there is
  more than one. The filter, the grouping and, in Hosts, the view chosen
  are kept in the browser for each user.
- Overview: the first tab shows the state of the fleet at a glance, read
  again every 30 s. A row of figures (hosts and how many cannot be
  reached, open problems not acknowledged, the share of successful runs,
  the queue, security updates), each leading to its section; the checks
  by state with the worst open problems; the runs of each day by outcome,
  with their durations and the latest failures; problems and recoveries
  of each day with the mean time to recover; the job templates and hosts
  that fail most; the next schedules and maintenance windows; the systems
  of the hosts, their updates and compliance, and those not restarted for
  a month; at the bottom, your access, the version, the checks of the
  installation and whether the controller keeps up. Over 7, 14 or 30
  days, in the browser's time zone. Also `overview.get` for viewers, and
  `fleet overview get` at a terminal.
- Workflows: a workflow may name a project, an inventory or both, and then
  only their job templates can be its steps, so that a template alike on
  another inventory is not taken for one of them. Chosen in the form of
  the workflow (which now also names the inventory beside each template
  when none is chosen), or with `project_id` and `inventory_id` of
  `workflow.create` and `workflow.update`. A job template that is a step
  of such a workflow cannot be moved to another project or inventory:
  make another one. Workflows made before name neither, and are as they
  were.
- Duplicate: a project or an inventory written in the interface can be
  copied under a new name, from the menu of its row or with `fleet
  project duplicate` and `fleet inventory duplicate`. The copy begins with
  the files as they are now, in one commit, without the history; an
  inventory keeps its vault and sets of variables. Templates and checks
  of the original are not copied.
- Use as starter: a project or an inventory written in the interface can
  be kept as a starter ("Usa come punto di partenza" in Italian), from the
  menu of its row or with `fleet project starter` and `fleet inventory
  starter`. It stays in its list, marked, and is edited as before; it is
  offered when a project or an inventory is created, and no template,
  check, ad-hoc command or maintenance window can use it. The hosts of an
  inventory so kept leave the list of hosts. The flag is reversible, and
  is carried by the export of the configuration.
- Launching a template, its tags, tags to skip, hosts in parallel (forks)
  and output detail can be changed for that run only, under "Values of the
  template" in the launch form and in the form of a schedule, or with
  `--tags`, `--skip-tags`, `--forks` and `--verbosity` of `fleet launch`.
  What was changed is shown with the run; the template stays as it is.

### Changed

- Assistant: as installed, all five profiles are enabled (`[chat]
  profiles`) and agents are held to the administrator role (`[mcp]
  max_role = "admin"`), so that an administrator's assistant may run
  ad-hoc commands and make changes, asking before each. The profile
  Administrator is offered and allowed only to administrators of Fleet
  (and only with `max_role = "admin"`): an operator gets the others, and
  Assisted when the default profile is Administrator. Each user still
  accepts the risks of Diagnostic, Autonomous and Administrator once
  before using them, and Assisted stays the default. A controller that
  left these keys out gets the new values on upgrade; to keep the old
  ones, set `max_role = "operator"` in `[mcp]` and `profiles =
  ["observer", "assisted"]` in `[chat]`.
- A run's page asks for confirmation before canceling the run. Its links
  back (to the page it was opened from, and to all runs) are above the
  title, apart from the button that cancels it. The store asks too
  before canceling a download or a copy into it.
- The pages of a run, of a host and of the files of a project or an
  inventory have their ways out as links above the title (back, all
  runs, all hosts, all projects or inventories, back to main from a
  draft), apart from the page's actions. A host's page, like a run's,
  goes back to the page it was opened from (Assets, Overview,
  Notifications). In the review of a draft, "Edit the draft" is a link
  above the changes, no longer a button beside "Discard".
- Job templates are grouped by inventory by default, no longer by
  project; a browser that already keeps a view of the list keeps its
  grouping.
- Launching a template, a workflow, a schedule or an ad-hoc command, and
  syncing or uploading a project, no longer opens the run: the page stays
  where it is, and a notice links to the run. A run opened from another
  page has a "Back" button to that page.
- The last run of job templates, workflows and schedules shows a run
  just launched, and follows it until it is over, without reloading the
  page.
- Under Automation, Runs is the last tab, after Schedules: the tab
  Automation now opens Job templates.
- The run that updates a git project ends its output with what it
  fetched: the reference, the revision, and the date and subject of the
  commit. Its output was empty when the update succeeded.
- The Italian interface and the Italian notifications are reworded: one
  word for each thing throughout ("run", "template", "check",
  "pianificazione", "dry run"), and sentences that read as Italian. In
  the messages of a channel, a recovery is "Ripristinato", a reminder
  "Problema ancora aperto" and a dry run "(dry run)": a filter on the
  old words needs changing.

### Fixed

- Assistant: the output of a run reaches it in chunks of 16 KiB without
  the codes of colours, which its program takes whole: a chunk of the
  page's size was put in a file the assistant could not read. It can also
  search the output for a text (`match` of `run.log`), with the lines
  around each line found.
- Charts on the page of a host: the labels of their axes and legends are
  readable on the dark theme.
- The selected text in the file editor is visible again on the line
  of the cursor, which hid it.
- Firefox saved none of the files made in the page: the exports of assets
  and of the configuration, `known_hosts`, and the downloads from the
  files of a project or an inventory. The policy of Cockpit's own page
  blocked them, which the fix of 0.2.0 did not reach.
- A run whose result could not be collected (a check answered by a node
  with results of the wrong shape, a `result.json` that a playbook
  rewrote) no longer stops every run that follows: it ends in error, and
  the runs, the Overview and the scheduler carry on. Before, they all
  answered "internal error" until the database was repaired by hand.
- Deleting a credential that a project, an inventory, a job template or a
  check still uses deleted its secrets and then failed, leaving it in use
  and empty. It is now refused before anything is deleted, and the page
  says what uses it.
- A check that sets only one threshold of `days_left` (certificates)
  applied it the wrong way round: a certificate with 200 days left was
  critical. One threshold alone now keeps the direction of the defaults.
- Overview: a host that could not be reached made each of its checks a
  problem, and a recovery when it came back, though nothing was told and
  their states were not problems. Counted from now on (schema 32); what
  was recorded before stays as it was.
- An event of a run whose output had bytes that are not UTF-8 (a file in
  Latin-1 read by `command`) was lost whole, and the task of that host
  showed nothing. Those bytes are now written as `?`.
- A run launched while the files of a project or an inventory were being
  changed (a restore, a change of several files, the end of a sync) could
  take them half changed. It now waits for the change to end, a minute at
  most, and otherwise is refused.
- Store: two uploads of the same file by the same person (two tabs, a
  retry) wrote into each other and could leave a corrupt file with a
  verified digest; the second is now refused. Uploads that began together
  could take the store past `[store] max_gb`: the limit is checked again
  when a file has arrived, and one refused then can be resumed when there
  is room.
- A download that arrived whole but was refused by the store (no room, the
  path taken) could not be resumed: the server answered 416 every time.
- Purging the `.deb` failed when `/var/lib/cockpit-fleet` is a mount point,
  as `docs/install.md` suggests for the store: it is now emptied and left.
- A command that changes something and failed in a way no command expects,
  or was interrupted with ^C at a terminal, left no record in the audit
  trail: it now leaves one, as an error.
- A run canceled while still queued could be started at the same moment
  and recorded as canceled while it ran; it is now stopped as a running
  run is.
- A schedule whose run could not be prepared for a reason of the system (a
  full disk) stopped the whole tick, pruning included; that schedule now
  records the error and the others go on.
- A run lost without a result (a reboot, a unit killed) kept the copy of
  its project and the encrypted secrets until its directory was removed.
- A script saved in the file editor, or copied, lost its executable bit.
- A setting of a project's `ansible.cfg` spread over several lines is
  left out: written back, its lines could add settings the list of those
  allowed leaves out.
- Credentials, channels and sets of variables: when a secret could not be
  saved after the thing itself was created, trying again created it a
  second time; it now saves the one made. A set of variables whose secret
  values could not all be deleted with it says which stay.
- Store: an upload canceled just as it started was sent all the same.
- The output of a run is kept in the page up to twice what it shows, and
  its last lines are not missed when the run ends during a read.
- The file editor could lose a key typed while a large file was being
  drawn again.
- `fleet-mcp` stopped on a request it could not read (a name that is not
  text, a line that is not UTF-8, JSON nested too deep); it now answers it
  with an error and goes on.
- `fleet assistant` at a terminal: the agent could not be interrupted with
  ^C if it relied on the default handling of the signal, and closing the
  terminal left a temporary directory behind.
- The command shown to launch a job template whose name begins with `-` was
  read by `fleet launch` as an option; the name now goes after `--`.
- `man fleet` and the completion for bash had not caught up with `fleet
  launch` (`--tags`, `--skip-tags`, `--forks`, `--verbosity`, `--serial`),
  `fleet assistant --profile` and `fleet store sftp`.

### Security

- `fleet-mcp` started without `--profile` (as by an SSH key with a forced
  command) let the agent name the profile of a call itself, and so leave
  `autonomous` for a profile without its limits. The profile an agent
  names is now ignored, as the session and the origin already were.
- An agent in the `autonomous` profile may no longer turn schedules on:
  what they launch would have run outside its count of runs, its dry runs
  only and the maintenance windows.
- An ad-hoc command of an agent in the `diagnostic` profile ran without a
  question when its module only reads, whatever its arguments: a template
  in them (`{{ lookup('pipe', ...) }}`) ran a command on the controller.
  Arguments with a template now make it an ordinary ad-hoc command, which
  that profile does not allow.
- The chat page: a question of the assistant that took the place of one
  just answered could be allowed by the same click; its buttons now wait
  a moment. A call the page knows only by its title, which the model
  words, is asked about even when it would only read.

## [0.3.0] - 2026-11-15

### Added

- Each release is published on GitHub with its packages: the `.deb` for
  Debian 13, the `.rpm` for Fedora 44 and AlmaLinux 10, and their
  `SHA256SUMS`.
- The store from an SFTP client: WinSCP, `sftp`, `sshfs` and the like
  reach it over a person's own SSH login to the controller, naming
  `/usr/bin/fleet store sftp` as the SFTP server, with nothing to set up
  on the controller. Viewers read; operators and administrators also
  write, rename and delete, each change in the audit trail. A file written
  reaches the store, and runs, only when it is complete; WinSCP's
  interrupted transfers are resumed. The Store page says how, under "With
  an SFTP client", with the command to copy.
- Starters: a new project or inventory written in the interface may begin
  with ready-made files instead of none, and "New file" in a project with
  a playbook. The package has a Linux and a Windows project, inventory and
  playbook (the Windows ones with what Ansible needs over SSH, and the
  collection `ansible.windows`); an administrator adds their own in
  `/etc/cockpit-fleet/starters`, offered beside the package's. `fleet
  starter list` shows them, and `project create` and `inventory create`
  take `--starter`.
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
  A run stops when a secret variable it takes has no value, and the
  variables of a run shown in a template's form mark those that have none.
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

- An assistant that creates or updates an inventory written in the
  interface no longer writes its files straight into it: they go to the
  draft of its session, like any file it writes, for an administrator to
  review and merge. Its name and other settings still change at once, and
  an assistant can no longer change such an inventory's format.
- YAML files that cockpit-fleet names itself end in `.yaml`, no longer in
  `.yml`: the main file of a new inventory written in the interface
  (`hosts.yaml`), the files of the starters and of the bundled collection,
  and those of a run's runtime directory. An inventory made before keeps
  its `hosts.yml`, which is still its main file.
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
- A button that waits (Save, Create, Refresh and the like) no longer tells
  a screen reader "Loading..." in English, and the Italian interface calls
  runs and inventories the same way everywhere ("le run", "inventario").

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

[Unreleased]: https://github.com/lineadicomando/cockpit-fleet/compare/v0.4.0...main
[0.4.0]: https://github.com/lineadicomando/cockpit-fleet/releases/tag/v0.4.0
[0.3.0]: https://github.com/lineadicomando/cockpit-fleet/releases/tag/v0.3.0
[0.2.0]: https://github.com/lineadicomando/cockpit-fleet/releases/tag/v0.2.0
[0.1.0]: https://github.com/lineadicomando/cockpit-fleet/releases/tag/v0.1.0
