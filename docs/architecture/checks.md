# Checks

A check is a kind of the catalog (reachability, disk, processor, memory,
services, uptime and reboot, certificates, clock, and the slow kinds `cmdb`
and `updates`, see [The inventory of hosts](inventory-of-hosts.md)), a playbook of the user, or a
playbook run for compliance, applied to a group of an inventory, with an interval, the attempts
before a problem is confirmed and the interval meanwhile, parameters,
thresholds and a machine credential. Each host of the group has a state for
it. Nothing is installed on the hosts: Ansible reaches them as for a run.

```
scheduler.tick ── due states, by inventory, credential and become ──▶ shards of hosts
   │ jobs.queue: a run of the kind "check", its plan in spec.json
   ▼
cockpit-fleet-run@ID   the same sandbox, a limit of its own ([checks] max_concurrent)
   │ one task per host: lineadicomando.fleet.checks → checks.py or win_checks.ps1
   │ events.jsonl
   ▼
reconcile → checks.ingest ── thresholds, soft and hard ──▶ check_states, state_changes
   │ a hard state that changed
   ▼
notify.check_event → the outbox, as for runs
```

**The hosts** come from the `inventory` job: when it succeeds, `hosts` gets
a row per host of that inventory (the identity is the inventory and the
name), with its address and groups, and loses those that are gone, with
their states. A check on an inventory never read queues the job, and the
tick reads again, every hour, the inventories that checks use. What a host
is (platform, system, kernel) is what the first cycle reports; no fact
gathering is spent on it.

**One task per host.** The catalog is one module in two files: `checks.py`
for Linux and `win_checks.ps1` for Windows, with the same parameters and the
same output. Ansible would not choose between them by itself: for a host
without a PowerShell shell it looks a module up with no suffix, and took the
`.ps1` (found on ansible-core 2.20). So the task names the action
`lineadicomando.fleet.checks`, which runs on the controller and calls one
module or the other according to the shell plugin of the connection. Every
check of a host runs in that one task, which matters on Windows, where a
task costs 4 to 5 s: eight checks take 5.4 s there (`windows.sh`). The
Windows module needs nothing but ansible-core (`Ansible.Basic` is part of
it), so AlmaLinux, which has no `ansible.windows`, checks Windows too, and
it sets the invariant culture, so that a summary says `32.2%` whatever the
language of the node. The Linux module runs on Python 3.6 and later, what
ansible-core 2.16 supports on a node, with the standard library only;
certificates are read with a reader of DER of its own.

**The plan** is data. `fleet-run` writes it into the runtime directory as a
variables file whose strings are all tagged `!unsafe`, the hosts into a file
given to `--limit @…`, and the playbook (one play, `strategy: free`, the
task with a `timeout`) beside them; nothing of it is in the command line. A
service named `{{ lookup('pipe', 'id') }}` reaches the node as those
characters.

**Results are untrusted.** They are written by the nodes. A result is taken
only for a check planned for that host in that cycle, and the host is the
one Ansible names in the event, never one a result names. States, metrics
(name, value, unit, instance, bounds), summaries and details are checked and
cut to size; a node cannot report for another host, nor for a check it was
not asked for.

**Thresholds** are applied on the controller, at ingest, so that changing
one needs no new collection and a node does not decide whether it is in
trouble. Each metric may have a warning and a critical; when the critical is
below the warning, lower is worse (the days before a certificate expires).
A check sets only what differs from the catalog's defaults. The modules of
the catalog report metrics and leave the state to the thresholds (they say
`unknown` when they cannot measure); the clock's offset is computed here,
from the time the node says and the time of the event.

**States.** Ok, warning, critical, unknown, with unknown between warning and
critical when they are compared. A problem is soft until it has been seen
`max_attempts` times in a row, checked again at `retry_interval` meanwhile;
then it is hard. A change between two problems is hard at once, and so is a
recovery. The next time is counted from when the cycle was planned, on the
tick, with 10 s of slack: counted from when the result arrived, it slipped
to the following tick every time (177 s instead of two intervals of 60).
Every change of the last state or of the hard one is kept in
`state_changes`. A check that changed in at least half of its last 20
results, with 10 at least, flaps, and stops flapping under a quarter.

**Unreachable hosts.** A host Ansible cannot reach has its `reachability`
check critical and its other checks unknown and suppressed: they keep their
hard state and their count, and nothing is told about them. A host is told
about once, not once per check: `reachability` should be applied to every
host.

**Cycles.** A cycle is a run of the kind `check`, in the same unit as every
job, with a limit of its own, `[checks] max_concurrent` (1), apart from
`[runs] max_concurrent`: checks do not wait behind a long playbook, nor a
playbook behind them. What is due is grouped by inventory, credential and
`become`, then cut in shards of at most `[checks] shard_size` hosts (50),
as even as they can be, each a cycle with at most `[checks] forks` (20);
fewer of either when what the last cycles took says so (below). A state held
by a cycle is not planned again until it ends: when it is due again
meanwhile, that due time is skipped (below). ansible-playbook says 2 when a host failed and 4 when
one could not be reached: for a cycle these are outcomes of hosts, which
their states record, so the cycle is successful and its unit is not left
failed; with a host down, a failed unit a minute would pile up. The output
of a cycle is the default callback without the hosts that went well, so the
journal gets a line per failure, not the results. The slow kinds (`cmdb`,
`updates`, `compliance`) go in cycles of their own kind only, with
`[checks] slow_timeout` (900 s) as the time limit and a lane of the dispatch
of their own, as many as `[checks] max_concurrent`: a Windows Update search
of some minutes does not hold back a disk check. The lane is the `slow`
mark in the options of the run. Cycles are not in the
list of runs unless asked for (`run.list` with `kind: check`), and are
deleted with their directory after a day, by the tick: their results are in
the states.

**Connections.** Within a run, SSH keeps one connection per host
(`ControlMaster=auto`, `ControlPersist=60s`), its socket in the run's
runtime directory, gone with it. The catalog is pipelined
(`ANSIBLE_PIPELINING`, set by `runner.check_env`): its one task per host
goes as one command over that connection, instead of a directory made on
the node, the module copied, run and removed. A host whose inventory sets
`ansible_pipelining` keeps that, a playbook of the user keeps what its
project says, and SSH to a PowerShell shell is always pipelined by Ansible.
Connections are not kept from one cycle to the next. Kept by the account,
any run of any project could use the open connections of another inventory,
`become` included, without its key (rule 8 of `CLAUDE.md`). Kept by a unit
per group, mounted into its cycles only, they would need a unit that stays
up, `fleet-unit` adding mounts per run, and sessions that outlive a
credential revoked, a host key no longer trusted, or options (forwards, a
`ProxyCommand`) that the first cycle's inventory gave them; that is weighed
again only if the load test shows that opening connections is what keeps a
controller from its target. Nor are facts cached: the checks gather none
(`gather_facts: false`), what is known of a host comes from the checks.

**Sizing.** Every cycle leaves a row in `cycles`: its group (the
inventory, and the kind or the check of the user when it goes in cycles of
its own), its hosts, checks and forks, when it started and ended, the peak of
its memory and its processor time. `fleet-run` reads the last two from its
own cgroup (`memory.peak`, `cpu.stat`) once Ansible has ended, and writes
them in `result.json`: it is the main process of the unit, and systemd
forgets both once the unit has stopped (`MemoryPeak` of a stopped unit is
not set). They are written in the run's directory, so believed only as far
as their shape: a playbook of the user could skew the sizing of its own
cycles, nothing else. The rows stay 30 days, after the cycles themselves are
gone. The planner sizes the next cycle of a group on its last 20 successful
ones:

- forks: as many as fit in the memory one cycle may take, `[checks]
  memory_mb` (by default half the controller's) shared by every cycle that
  may run at once (`max_concurrent` in each of the two lanes). The memory of
  a cycle is fitted as a base plus so much per fork when cycles ran with
  different forks, else all of it is counted per fork, which errs low and
  grows back as cycles with more forks are measured;
- hosts: as many as those forks get through in half the shortest interval of
  what the group checks, at the time a host took a fork (the time of a cycle
  times its forks over its hosts), and never fewer than the forks.

With nothing measured, the configured values hold. They stay the most there
may be: sizing only takes away, since more forks than configured is more
memory than the administrator agreed to, and a larger shard only saves the
start of a playbook. What a cycle is too slow for, sizing cannot fix: it
ends late, and the next is not planned until it does.

**At scale.** `test/integration/load.sh` runs real cycles, started by the
timer, on aliases of the controller with the local connection (the modules
then measure the sandbox of a run) and a few container nodes over SSH, ten
checks each: nine of the catalog (seven kinds, `disk` and `certificates`
twice) in one task per host, and a playbook of the user in cycles of its
own. Measured on a Debian 13 machine with 2 vCPUs and 4 GB, every 10
minutes (21 October): a cycle of 50 hosts and 20 forks (`shard_size` and
`forks` as configured; sizing kept both, half the interval being far off)
takes ~9 s for the catalog and ~6 s for the playbook, ~680 MB at its peak,
0.27 s of processor per host; its results are taken in within 0.3 s. With
`[checks] max_concurrent = 1` the cycles of an interval run one after the
other: 500 hosts make 20 of them, done in ~3 minutes, the processor at
~93% meanwhile and idle the rest; the last waits ~160 s in the queue.
1000 hosts make 40 and the last waits 266 s, near half the interval, which
is where a second cycle at once (`max_concurrent = 2`) would be needed.
What gives first is the interface: `state.list` returns every state, and
with 10 000 of them answers in 0.44 s (median) and 2.9 s (p95, while
cycles run), against 0.27 s and 1.3 s at 500 hosts and 0.16 s and 0.18 s
at 200; the other calls stay at ~0.1 s median. Three nodes over SSH beside the aliases took ~2.1 s per cycle,
0.33 s of processor per host: too few to weigh what opening connections
costs at scale. Over 10 hours with 500 hosts (21–22 October, stopped by
the host's shutdown, not by the test) the cycles held the same: 1289,
every one successful, none skipped, the oldest result 1.13 intervals
old, at least 2.8 GB of memory available and no swap. But `state.list`
came out worse than in the first 1.6 h: 0.30 s median, 2.5 s at p95 and
3.9 s at most, against 2 s and 5 s allowed (`host.list` 1.6 s at p95,
the others under 0.5 s). Timed in phases on the same database (22
October), the cause was not the query: with no cycle running `state.list`
took 0.17 s inside fleetctl (5030 states, 3.6 MB of JSON), with one 1.1–1.7
s, and importing Python alone went from 0.07 s to 0.9 s. systemd had left
the cpu controller off in `system.slice`, so every process there competed
on its own and the forks of a cycle took the processor from fleetctl. Runs
now go in `system-cockpitfleet.slice`, with `CPUWeight=20`: under
contention the interface and the units that record results come first,
and with nothing else to do the runs still have all of it. Set at runtime
on that machine, it brought `state.list` back to 0.17 s while a cycle ran.
The page of hosts also stopped asking for every state but in its grid: the
default view asks for the problems, with the counters of all. With both,
24 hours with 500 hosts (22–23 October, the target of phase 8) passed
every check of `load.sh`: 3124 cycles, every one successful, none
skipped, no group behind, the oldest result 1.05 intervals old, the last
cycle of an interval waiting at most 176 s, at least 2.5 GB of memory
available and no swap; every call of the interface under 0.4 s at p95
and 0.75 s at most (`state.list` of the problems 0.17 s at p95, every
state for the grid 0.38 s). The database grew by ~60 MB a day (raw
metrics are kept 14 days, so it levels off later), the run directories
by ~400 MB a day, which levels off after the first day: those of check
cycles go after a day (Retention), so a test of 24 hours ends just
before it shows.

**Skipped, and falling behind.** A cycle answers the due time it was queued
for; when one interval later it has not ended (still queued counts too),
the next due time of each state it holds is skipped, never doubled, and
counted once: `skipped` in the state, how many of its due times have passed
since the cycle was queued, the same reckoning as the next time of a result.
The tick adds what is new to the options of the cycle, which end in its row
of `cycles`, and writes a warning to the journal for each group with the
due times it skipped. A hundred hosts behind by one cycle are one due time
of the group, not a hundred. `cycle_lag` keeps, per inventory and group,
the due times skipped in a row and since when; at `[checks] late_after` (3)
the group is `late`, told once to the rules that ask for it. It is
`caught_up` when one of its cycles ends with nothing skipped and no other
cycle of the group still holds a skipped state, so that a small shard on
time does not end the delay of a large one; the row is then deleted, and a
delay told about is told to be over. A row nothing has added to in 30 days
goes with the measures: its group is gone. These events are about the
controller, not a host: their rules have the scope of everything or of an
inventory, and their message links to the System tab.

**Cycles that cannot be prepared.** A cycle the tick cannot prepare (its
project gone, or one the scheduler cannot see) leaves each check it would
have held unknown, with the reason as summary. Unknown is not a problem of
a node, so rules about problems would stay silent while the checks never
run. `cycle_faults` keeps, per inventory and group, the reason and since
when: the first failure is `unprepared`, told once with an error in the
journal, and the first cycle of the group queued afterwards is `prepared`,
which deletes the row. Among the cycles of a group in one tick (one per
credential), one failure is enough to hold the fault. The System tab shows
the group as "Cannot start", with the reason. Same scopes and link as
`late`.

**Checks of the user.** A check of the kind `playbook` names a playbook of a
project; it runs alone, on the hosts of its group, sharded the same way.
Its last task is `lineadicomando.fleet.fleet_result` with a state, a summary,
metrics and details: an action, which runs on the controller and sends
nothing to the node, so it is the same on Linux and Windows. It checks what
it is given and hands it back; ingest takes the last one of each host and
applies the thresholds of the check, keeping the worse of that state and
the one the playbook gave. A task that failed (and was not ignored) makes
the state unknown with its message. Every task has `[checks] timeout`
(`ANSIBLE_TASK_TIMEOUT`).

**Notifications.** After each ingest and at each tick, a hard state that
differs from the last one told about is told: `problem`, or `recovery` from
a problem told about. A problem nobody acknowledged is told again after
`[checks] reminder_hours` (24; 0 never) as `reminder`. Nothing is told for a
check that flaps or whose host is unreachable, nor for a host in a
maintenance window: what is still a problem when the window ends is told at
the next tick. Rules have three more scopes: an inventory (its runs and its
checks), a group of an inventory, one check; the scopes of a project,
template or schedule have runs only, those of a group or a check have checks
only, and a rule that mixes them is refused. The message links to the host,
`/fleet#/hosts/ID`.

**Roles.** A viewer sees hosts, states and their history; an operator also
acknowledges a problem (until it is ok again), runs a check now, and opens
and closes maintenance windows (a host, or a group of an inventory, at most
90 days); an administrator configures checks. Checks are in the exported
document, maintenance windows are not.

**The interface.** "Hosts" has the counters, the problems with what can be
done about each, the hosts, and a grid of hosts and checks; the page of a
host (`#/hosts/ID`) has what it is, its checks with their metrics, and its
last changes. "Checks" has the checks and the maintenance windows.

The list of checks is filtered, grouped and sorted in the browser, from
what `check.list` already returns (see "Long lists" in [the interface](interface.md)):
a text searched in the name, the kind, the inventory, the group and the
playbook; the state (with a problem, critical, all OK, not checked yet);
the inventory; the kind, in four families (resources: disk, cpu, memory;
services and system: reachability, services, uptime, certificates, clock;
inventory and compliance: cmdb, updates, compliance; playbooks of the
user); enabled or not. It is grouped by inventory (the default), family,
worst state or not at all; a group with a problem starts open, one all OK
closed, and every group is open while a filter is on. Ended maintenance
windows are hidden unless asked for. The logic is in `src/lib/checks.ts`.

Checked on Debian 13 (`checks.sh`, four Linux containers and an address
with no host): every kind of the catalog and a playbook of the user, an
unreachable host told about once, a disk of 16 MB filled up told about in
117 to 119 s with an interval of 60 s and two attempts, and its recovery;
and on Windows 11 over SSH (`windows.sh`), the catalog with the same metrics
as on Linux, `C:` for a mount point.
