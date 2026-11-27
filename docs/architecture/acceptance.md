# Acceptance of the phases

What each closed phase had to show, and how it was shown.

- **3, complete runner** (13 October): `ansible-win_edulab` ran unmodified
  from the interface on a Windows 11 node: uploaded as an archive (its vault
  file is not in git), the two collections of its requirements installed
  from GitHub, the inventory read with the vault credential, host keys
  trusted, `playbooks/win_wm.yaml` launched by an operator with a survey (14
  tasks, 14 s), an ad-hoc `win_command`, and `shutdown.yaml`, which delegates
  a task to the controller.
- **3b, agents** (13 October): Claude Code over ssh, with a key forced on
  `fleet-mcp`, as an operator, launched `win_wm.yaml` through its tool, waited
  with `run_result` and reported the outcome; asked for an ad-hoc command it
  found no tool, and the same call sent with `via`, as an operator and as an
  administrator under the ceiling, was refused and audited. The chat was
  tried from the page with `fake-agent.py` and with Claude, as an operator.
- **4, schedules** (14 October): ten schedules on one minute with
  `max_concurrent = 2` never ran more than two, highest priority first
  (`schedules.sh`); after a shutdown with one run running and two queued,
  the first tick recorded the cut run as failed, started the queue, caught
  up one schedule once and recorded the other as missed.
- **5, notifications** (14 and 15 October): a failed run reached stand-ins
  of the four channels in one delivery, a refused fifth was retried by the
  tick (`notify.sh`); then real Telegram, mail over TLS and Matrix, the page
  in the browser in Italian for the three roles, no token in the database
  or the journal.
- **6, checks** (16 October; [history](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/checks.md)): the same disk metrics from
  Debian and Windows, a full disk told about within two intervals.
- **7, inventory of hosts** (16 October; [history](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/inventory-of-hosts.md)):
  `cmdb.sh` on the machines of the three distributions, drift from a file
  changed by hand at the next cycle, the CMDB of Windows by `windows.sh`.
- **8, time series and scale** (23 October): 500 hosts with ten checks
  each every 10 minutes ran 24 hours on a Debian machine of 2 vCPUs and
  4 GB with no backlog and every call of the interface under 0.4 s at
  p95 (`load.sh`; [history](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/history/checks.md)), after runs were given a
  slice of their own; then every script on the machines of the three
  distributions, and the health section and the charts in a browser on
  Debian with the 500 hosts.
- **9, workflows and approvals** (24 October): a workflow of three steps
  with an approval at the second stopped there, was approved by an account
  that has only the approver's right, and completed, what the first step
  passed on with `set_stats` reaching the next ones unrendered; a failed
  step handled by a step for it, a resume that ran only the steps not yet
  done, a cancel (`workflows.sh`), on containers and on the machines of the
  three distributions with `sandbox`, `roles` and `runs`, SELinux enforcing
  on Fedora 44 and AlmaLinux 10 with no denial; in a browser on Debian,
  approving as an administrator and as an approver with no role, the steps
  of a workflow run, the editor.
- **10, release 0.1.0** (25 October to 2 November): a security review of
  the root helpers, the sandbox, sudoers, archives, git and bundles, and
  negative tests over the whole registry (`test_negative.py`,
  `negative.sh`), what they found fixed and what was left recorded in
  [limits.md](../limits.md); Lighthouse 100 for accessibility on every tab,
  light and dark; upgrade, removal and purge of the `.deb` and the `.rpm`
  from phase 8's commit (`make ct-lifecycle`) on the three; the `.rpm`
  built and linted in CI; the GitHub workflow green on its first run.
- **11, files and inventories as trees** (2 November): a managed project
  changed many files at once, directories moved, copied and deleted, a
  path restored, a directory downloaded, an archive imported (`files.sh`);
  a managed inventory written as a tree like a school's (groups of groups,
  `host_vars`), read, launched on from another project, read again after a
  change, and one of before trees made its first file; a file inventory of
  another project copied for a run (`inventories.sh`); an inventory cloned
  and pushed at a terminal (`cli.sh`); on containers of the three, and on
  the machines of Fedora 44 and AlmaLinux 10 with `sandbox`, SELinux
  enforcing and no denial; in a browser on Debian, the tree dragged, used
  with the keyboard, several files open in tabs, Lighthouse 100 for
  accessibility. The tree's open folders are named by their name, yet axe
  still remarks that their text (their children) is not in it: the tree
  pattern nests them, and the score is not lowered.
- **12, profiles of the assistant** (13 November): with Claude on Debian
  13, as an administrator under `[mcp] max_role = "admin"`, each of the
  five profiles on the page did what it allows and asked what it should:
  reads unasked, launches asked with "always" for the rest of the
  conversation, the risks of the three beyond `assisted` accepted once,
  ad-hoc commands that change nothing unasked in `diagnostic`, none at all
  in `autonomous`, which launched unasked; in `administrator` an ad-hoc
  `command` asked about once and then waiting for the user's approval, and
  a playbook the assistant wrote kept in a draft until the user reviewed
  and merged it. `fleet assistant` at the terminal asked the same in
  `assisted` and nothing in `autonomous`. Every start, acceptance and call
  in the audit as the user's, with the profile; see [The chat](chat.md).
- **13, the store** (14 November): `ansible-win_edulab` uploaded as an
  archive on Debian 13, its collections from its requirements, its
  inventory `school` read with the vault credential, run on its own
  Windows 11 VM with `win_workman_storage_path` set to `{{ fleet_store }}`
  by the templates. The role `p7zip` downloaded its installer with
  `get_url` delegated to the controller and `win_copy`ed it from there,
  and the run's intake added it to the store; a later run that took 7-Zip
  off and installed it again found it there with its checksum and did not
  download it (the same inode; `get_url` reported a change of mode only,
  which reached the store's file through its link: in
  [limits.md](../limits.md)). `file_copy` `pull` fetched a file of the
  node into `file_copy_pull/`, taken in likewise. An ISO of 7 GB uploaded
  from the Store page into `win_iso/` arrived whole, its digest right,
  once the upload held back on what `fleetctl` had written rather than on
  Cockpit's acknowledgements (see [The store](store.md), "Uploads").
  Veyon's keys were not tried: the role keeps them on the controller,
  where the store, read by every run, is no place for them.
  By hand in Chrome and Firefox: a folder dropped from the desktop, a
  drag with Ctrl that copies (Chrome on Linux tells the page of Ctrl only
  on the keys and at the start of a drag), the lists of uploads and
  downloads closed by the user rather than going by themselves.
- **14, starters** (14 November): `files.sh`, `inventories.sh` and
  `negative.sh` in containers of the three distributions, AlmaLinux with
  ansible-core 2.16: the package's starters listed and usable, a project
  started in one commit whose playbooks passed their syntax checks, an
  inventory read with the connection variables of Windows, an
  administrator's starter beside the package's of the same name, one with
  a link to the secrets listed as unusable and refused, leaving nothing.
  In a browser on Debian 13, in Italian, as an administrator: "Start
  from" in "New project", "New inventory" (format and text hidden once a
  starter is chosen) and "New file", with the translated titles and
  descriptions. A project from the Windows starter had its collection
  installed by its requirements file; an inventory from the Windows
  starter, given the Windows 11 node, ran `ping.yml`, `site.yml` through
  the role and the playbook made with "New file", all successful.
  Following the user, `ansible_remote_tmp` is left commented out in the
  Windows starters, and the YAML files the project makes end in `.yaml`
  (done on 15 November, [decisions.md](https://github.com/lineadicomando/cockpit-fleet/blob/main/docs/decisions.md), "YAML
  extension"): `make ct-test` on the three passed but the known failure
  in [tests.md](tests.md).
- **15, secret variables** (14 November): in a browser on Debian 13, as an
  administrator, a set made on the Variables page with a plain variable
  and two secret ones, then edited: a value replaced, a variable removed
  (its encrypted file went with it), one added with no value, which the
  list showed as not set. The set given to an inventory from its form; the
  template's merged view showed the secrets hidden, where each came from,
  and its own variable winning over the set's. Its launch stopped on the
  secret with no value; once that was set, the run got the replaced value
  as typed (`{{ 2 + 2 }}` not evaluated), masked in its output, and no
  value was found in clear in the database, the files of the controller
  or the journal. The check found, and fixed: the label of a row with no
  name yet ("Valore di la variabile segreta"), a remove button named
  otherwise than it reads, busy buttons announced in English, the merged
  view not telling a secret with no value apart, and "i run" and
  "inventory" in the Italian strings of runs in series.
- **16, the store over SFTP** (15 November): `store.sh` on containers of
  the three distributions, OpenSSH's `sftp` over an SSH login through
  `fleet store sftp`: an operator made a folder, put, renamed, got and
  deleted a file of 5 MB, its digest the same, owned by the service, each
  change in the audit trail through `sftp`; a viewer read and was refused
  a write. `negative.sh` on the three, and every script on AlmaLinux. On
  `deb13`, WinSCP 6.7.1 from `win11` as an operator with a key: 300 MB up
  and down, the `.filepart` renamed and its time kept, a transfer killed
  half way resumed, a file replaced; `sshfs` 3.17 on `deb13` copied,
  moved, replaced and deleted, and was refused an append in place. The
  section "With an SFTP client" of the Store page seen in Italian in a
  browser; see [The store](store.md), "Over SFTP".
- **17, duplicate and starters kept here** (15 November): `files.sh` on
  containers of the three distributions, `negative.sh` and `checks.sh`
  on Debian and Fedora, and `make ct-lifecycle` on AlmaLinux (an upgrade
  from 0.3.0 through schema 29). In a browser on `deb13`, in Italian, as
  an administrator: a project made from the package's Linux starter,
  duplicated ("Modello web (copia)", one commit, the same files), the
  copy kept as a starter and marked in the list, offered in its own
  group of "Punto di partenza" when a project was created, and a project
  made from it with its files; the copy was not among the projects of a
  new template. An inventory made from a starter, duplicated and kept as
  a starter the same way, its menu then offering "Usa di nuovo nei run".
  The check found, and fixed, "set di variabili" in an Italian string
  where the page says "insiemi di variabili".
- **18, the Overview as a dashboard** (18 November): `overview.get`
  tested in `test_overview.py` (sums, windows, the time zone, problems
  and recoveries from hard states) and its figures in `overview.test.ts`;
  measured at 500 hosts (see [The Overview](dashboard.md)). In a browser,
  in Italian: as a viewer in the dark theme, Lighthouse 100 for
  accessibility; as an operator in the light theme, on a controller in
  use with 135 hosts and 292 runs in 14 days, the figures, the charts
  (their text read on either theme), the links to each section and the
  card of access. The check found, and fixed, the screen-reader tables of
  the charts drawn over the title in Firefox.
- **19, network discovery** (26 November): `netscan.sh` on the lab's
  network (`IT_NETSCAN_SUBNET=192.168.140.0/27`, `win11` up) on `deb13`,
  `alma10` and `fed44`: ICMP allowed to the service account as each is
  installed, no denial of SELinux. `windows.sh` on `deb13` with `net_probe`
  from `win11`, which found the gateway and the controller with their MAC
  addresses once its bug was fixed (`network.md`). In a browser, the
  scenario `network-scan` on `deb13`: a part of the
  lab's subnet scanned from the page, `win11` found by TCP and the neighbour
  table with its MAC address, its DNS and NetBIOS names (`CFLEET-WIN11`,
  `WORKGROUP`) and known as the host of an inventory; its MAC address
  written into that inventory after the preview; the inventory's hosts
  probed, the PC that is off "Nothing answers", `win11` "Gives another
  name". The check found, and fixed, a test of `netscan.sh` that canceled
  a scan after a fixed 3 s, before the unit of a VM's first run had begun.
- **21, inventory chosen at launch** (27 November): tested in
  `test_phase2.py`, `test_workflows.py`, `test_agents.py` and the
  frontend's `templates.test.ts` and `launch-modal.test.tsx`; `ct-test` on
  the three. On `deb13` with `win11` as the PC of `aula-1`: the scenario
  `labs-launch` (26 November); the workflow `manutenzione` (wake, then
  Windows Update) launched from the page with no inventory chosen, its
  steps on their templates' default, to its end; the assistant, asked in
  Italian for "the updates" of one room then the other, launched that
  workflow on `aula-2` (the PC that is not there failed to wake, the
  update did not start) and on `aula-1` (both steps successful), audited
  as `mcp:claude-code`. A template with no default inventory, by the
  user in a browser: its form, saved only with others allowed; under each
  of them in the list; its launch, a schedule and a workflow of it
  choosing one, the launch and the save held until then; the backend
  refusing the template and the schedule from `fleet` (`bad_request`,
  audited). The check found, and fixed, the last run under each
  inventory, which was the last on any.
- **22, check cycles in view** (27 November): the scenario
  `checks-cycles` in a container; on `deb13`, the user in a browser with
  eight checks of the catalog on the two rooms (every 2 minutes, `win11`
  the PC of `aula-1`, the PC of `aula-2` not there): the cycles of the
  timer reaching the list by themselves, `aula-1` OK, `aula-2`
  unreachable and its other checks unknown; the page of a cycle with
  what each check found on each host and the output of the host that did
  not answer; "Recent cycles"; "Check now" and its cycle "Running"; the
  list asked again at once when its tab is shown again. A cycle of one
  host lasts 3–4 s: one the timer starts is seldom caught "Checking". The
  check found, and fixed, the list that missed the cycles of the timer
  (it polled only while a cycle held a check).
