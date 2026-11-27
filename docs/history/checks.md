# Checks: history

Archive: what was tried, measured and found while building checks; not
needed to work on it.

**16 October, `checks.sh` on Debian 13** (four Linux containers and an
address with no host): every kind of the catalog and a playbook of the
user, an unreachable host told about once, a disk of 16 MB filled up told
about in 117 to 119 s with an interval of 60 s and two attempts, and its
recovery; on Windows 11 over SSH (`windows.sh`), the catalog with the same
metrics as on Linux, `C:` for a mount point.

**Connections kept between cycles, weighed and left.** Kept by a unit per
group, mounted into its cycles only, they would need a unit that stays up,
`fleet-unit` adding mounts per run, and sessions that outlive a credential
revoked, a host key no longer trusted, or options (forwards, a
`ProxyCommand`) that the first cycle's inventory gave them.

**The load test** (`load.sh`), on a Debian 13 machine with 2 vCPUs and
4 GB, every 10 minutes: aliases of the controller with the local
connection (the modules then measure the sandbox of a run) and a few
container nodes over SSH, ten checks each: nine of the catalog (seven
kinds, `disk` and `certificates` twice) in one task per host, and a
playbook of the user in cycles of its own.

- 21 October: a cycle of 50 hosts and 20 forks (`shard_size` and `forks`
  as configured; sizing kept both, half the interval being far off) took
  ~9 s for the catalog and ~6 s for the playbook, ~680 MB at its peak,
  0.27 s of processor per host; its results taken in within 0.3 s. With
  `max_concurrent = 1`, 500 hosts made 20 cycles, done in ~3 minutes, the
  processor at ~93% meanwhile and idle the rest; the last waited ~160 s in
  the queue. 1000 hosts made 40 and the last waited 266 s. `state.list`
  of every state answered 10 000 states in 0.44 s (median) and 2.9 s (p95,
  while cycles ran), against 0.27 s and 1.3 s at 500 hosts and 0.16 s and
  0.18 s at 200; the other calls ~0.1 s median. Three nodes over SSH
  beside the aliases took ~2.1 s per cycle, 0.33 s of processor per host:
  too few to weigh what opening connections costs at scale.
- 21–22 October, 10 hours with 500 hosts (stopped by the host's shutdown):
  1289 cycles, every one successful, none skipped, the oldest result 1.13
  intervals old, at least 2.8 GB of memory available and no swap. But
  `state.list` came out worse than in the first 1.6 h: 0.30 s median,
  2.5 s at p95 and 3.9 s at most, against 2 s and 5 s allowed (`host.list`
  1.6 s at p95, the others under 0.5 s).
- 22 October, timed in phases on the same database: with no cycle running
  `state.list` took 0.17 s inside fleetctl (5030 states, 3.6 MB of JSON),
  with one 1.1–1.7 s, and importing Python alone went from 0.07 s to
  0.9 s. systemd had left the cpu controller off in `system.slice`, so
  every process there competed on its own and the forks of a cycle took
  the processor from fleetctl. `CPUWeight=20` on a slice of the runs, set
  at runtime, brought `state.list` back to 0.17 s while a cycle ran. The
  page of hosts also stopped asking for every state but in its grid.
- 22–23 October, 24 hours with 500 hosts (the target of phase 8): 3124
  cycles, every one successful, none skipped, no group behind, the oldest
  result 1.05 intervals old, the last cycle of an interval waiting at most
  176 s, at least 2.5 GB of memory available and no swap; every call of
  the interface under 0.4 s at p95 and 0.75 s at most (`state.list` of the
  problems 0.17 s at p95, every state for the grid 0.38 s). The database
  grew by ~60 MB a day, the run directories by ~400 MB a day, which
  levelled off after the first day: check cycles were then kept a day, so
  the test ended just before it showed. With `keep_days` at 7 since phase
  22, such a fleet keeps about seven times that.
