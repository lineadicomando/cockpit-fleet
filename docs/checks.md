# Writing a check

A check runs on the hosts of a group of an inventory, every so often, and
gives each host a state: ok, warning, critical or unknown. Nothing is
installed on the nodes: Ansible reaches them as it does for a run. This page
is for the administrator who sets checks up, and for whoever writes one as a
playbook. How cycles are planned, sized and recorded is in
[architecture.md](architecture.md), "Checks".

## The parts of a check

In **Checks**, "New check":

- **Kind**: one of the catalog below, a playbook of a project, or a
  compliance playbook.
- **Inventory** and **group**: the hosts it applies to (`all` for every
  host of the inventory). The hosts come from the inventory as Fleet last
  read it; it reads again, every hour, the inventories that checks use.
- **Interval**: how often a host is checked. **Attempts** and **retry
  interval**: a problem is *soft* until it has been seen that many times in
  a row, checked again at the retry interval meanwhile; then it is *hard*,
  and only a hard change is told about. A recovery is hard at once.
- **Machine credential** and **become**: how Ansible logs in, as for a run.
- **Parameters** (some kinds of the catalog) and **thresholds**.

Apply `reachability` to every host: a host Ansible cannot reach has that
check critical and its other checks unknown and silent, so that it is told
about once and not once per check.

## The catalog

One module, the same on Linux and on Windows (over SSH), with the same
metrics; the summary says in a line what it found.

| Kind | Measures | Parameters | Thresholds by default (warning / critical) |
|---|---|---|---|
| `reachability` | whether Ansible reaches the host | | the state is critical when it does not |
| `disk` | space and inodes used per filesystem (fixed drive on Windows) | `mounts` (only these; `C:` on Windows), `exclude_types` | `used_pct` 85 / 95, `inodes_used_pct` 85 / 95 |
| `cpu` | processor time over one second (load on Windows), load averages on Linux | | `cpu_pct` 90 / 98 |
| `memory` | memory and swap in use | | `memory_used_pct` 90 / 97, `swap_used_pct` 80 / 95 |
| `services` | whether the services named are running (systemd units, Windows services) | `names`, required | `not_running` – / 1 |
| `uptime` | time since boot, whether a reboot is required | | `reboot_required` 1 / – |
| `certificates` | days before certificates expire: PEM or DER files, directories, a Windows store such as `Cert:\LocalMachine\My` | `paths`, required | `days_left` 30 / 7 |
| `clock` | how far the node's clock is from the controller's, in seconds | | `offset` 10 / 60 |
| `cmdb` | hardware, packages and services, kept in **Assets** with their changes | | warning for one cycle when the hardware changed |
| `updates` | updates available (apt, dnf, zypper, Windows Update searched without installing) | `refresh` (needs become) | `security_updates` 1 / –, `updates` – / – |

`cmdb`, `updates` and compliance checks are *slow*: they run in cycles of
their own (`cmdb` and `updates` every 6 hours unless told otherwise), with `[checks] slow_timeout` (900 s)
as their limit, so that a long search for updates does not hold back a disk
check.

## Thresholds

Thresholds are applied on the controller when a result arrives, not on the
node, so changing one needs nothing new on the nodes. Each metric may have a
warning and a critical value; a check sets only those that differ from the
defaults, and a threshold applies to every metric of its name (every
filesystem, for `used_pct`). When the critical value is below the warning
one, lower is worse, as with the days before a certificate expires. Leave a
value empty to have no threshold of that level.

## A check as a playbook

When the catalog does not measure what you need, write a playbook in a
project and make a check of the kind **playbook** that names it. It runs on
the hosts of the check's group, with the check's credential, in a cycle of
its own; its last task, on each host, is `lineadicomando.fleet.fleet_result`:

```yaml
- name: Mail queue
  hosts: all
  gather_facts: false
  tasks:
    - name: Count the mails waiting
      ansible.builtin.command: postqueue -j
      register: queue
      changed_when: false

    - name: Tell cockpit-fleet
      lineadicomando.fleet.fleet_result:
        summary: "{{ queue.stdout_lines | length }} mails waiting"
        metrics:
          - name: queued
            value: "{{ queue.stdout_lines | length }}"
```

Then, in the check, a threshold on `queued` (say warning 10, critical 100)
turns the count into a state. The collection is part of the package: a
project needs nothing to use it.

`fleet_result` takes:

| Option | What |
|---|---|
| `state` | `ok` (the default), `warning`, `critical` or `unknown`, or 0 to 3 |
| `summary` | one line for a person, at most 500 characters |
| `metrics` | a list of measures, each with `name` (lowercase letters, digits and `_`, at most 64), `value` (a number; a template's text is read as one), and optionally `unit`, `instance` (which disk, which service), `min` and `max` |
| `details` | a mapping, anything else worth keeping with the result (at most 16 KB as JSON) |

It runs on the controller and sends nothing to the node, so it is the same
for Linux and Windows hosts. What becomes of it:

- The state of the host is the worse of the `state` given and what the
  thresholds of the check make of the metrics. A playbook may decide the
  state itself, set thresholds in the check, or both.
- A task that fails, and is not ignored, makes the state unknown, with the
  task's message as the summary. A playbook that ends without
  `fleet_result` gives unknown, "the playbook gave no result".
- When `fleet_result` runs more than once on a host, the last one counts.
- At most 200 metrics are kept per result. The values are kept as a time
  series, shown as a chart on the page of the host.
- Every task has `[checks] timeout` (60 s) as its limit.

Write it to be cheap and to change nothing: it runs every interval on every
host of the group. `changed_when: false` on commands keeps the output quiet,
and `gather_facts: false` saves a round trip per host unless facts are
needed. A playbook of the user keeps the pipelining its project's
`ansible.cfg` asks for.

To try it, launch it first from a job template on one host, and look at the
output: the result `fleet_result` gives is in it. Then create the check and
use "Check now" on one host in **Hosts**.

## Compliance: a playbook as the state you want

A check of the kind **compliance** names a playbook that describes how a
host should be (a file's content, a package installed, a setting), and runs
it in `--check --diff`: nothing is changed. The tasks that *would* change
something are the drift. The metric `changed` counts them, with a warning
from one by default; their differences are kept per host and shown on its
page, under "Drift", to operators and administrators (they may hold the
content of files). A failed task makes the state unknown.

Any playbook written to be run normally can be a compliance check, if its
modules support check mode; the playbook needs no `fleet_result`.

## Problems, notifications, maintenance

- A hard problem is told to the channels whose rules ask for `problem`, and
  its end for `recovery`. A problem nobody acknowledged is told again after
  `[checks] reminder_hours` (24).
- An operator acknowledges a problem in **Hosts**: it is not told again
  until it is ok again.
- A check that changes state in half of its last 20 results flaps, and is
  not told about until it settles.
- A maintenance window (a host, or a group, up to 90 days) keeps its hosts
  from being told about; what is still a problem when it ends is told then.

## At a terminal

```sh
fleet check catalog                        # the kinds, their parameters and thresholds
fleet check list
fleet check create --kind disk --name disk --inventory-id lab --group all \
    --thresholds '{"used_pct": {"warning": 80, "critical": 90}}'
fleet state list --problems
```

`fleet help check create` lists every parameter; an id may be given by name.
