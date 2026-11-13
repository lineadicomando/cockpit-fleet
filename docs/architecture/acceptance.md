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
- **6, checks** (16 October): see [Checks](checks.md): the same disk metrics from
  Debian and Windows, a full disk told about within two intervals.
- **7, inventory of hosts** (16 October): see [The inventory of hosts](inventory-of-hosts.md):
  `cmdb.sh` on the machines of the three distributions, drift from a file
  changed by hand at the next cycle, the CMDB of Windows by `windows.sh`.
- **8, time series and scale** (23 October): 500 hosts with ten checks
  each every 10 minutes ran 24 hours on a Debian machine of 2 vCPUs and
  4 GB with no backlog and every call of the interface under 0.4 s at
  p95 (`load.sh`; see [Checks](checks.md), "At scale"), after runs were given a
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
