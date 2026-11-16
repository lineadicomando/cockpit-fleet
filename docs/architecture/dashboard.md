# The Overview

The first tab of the page shows the state of the fleet at a glance, from
what is already kept: nothing is collected or stored for it. One command,
`overview.get` (viewer, in `overview.py`), answers in one call; the page
reads it again every 30 s and when the window changes.

**What it answers.** For a window of days (7, 14 or 30 in the page, 1 to
90 to the command; today included, each day beginning at midnight where
the browser is, from `tz_offset`):

- hosts: how many, reachable or not, in a maintenance window now, by
  system (`hosts.os`, else the platform), and how many of those with a
  boot time in the CMDB have not been started again for 30 days;
- the checks by state (`checks.state_counts`, as `state.list` counts
  them) and the open problems as hard states: how many, how many not
  acknowledged, and five of them, those not acknowledged first, then the
  worst (critical, unknown, warning), then the oldest;
- runs created in the window, every kind but check cycles, as the page of
  runs lists them: by day and outcome (successful; failed or error;
  stopped: canceled, rejected, expired), the success rate of those that
  ended successful or failed, the median and longest duration, the five
  latest failures, the job templates and the hosts that failed most, and
  what waits now (running, queued, waiting for approval);
- problems and recoveries by day, from `state_changes` (hard changes only):
  a problem begins when a hard state that was ok, or none, becomes a
  problem; it ends at the next hard ok. The mean time to recover is over
  the problems that ended in the window, timed from when they began, before
  the window if need be;
- updates the hosts lack (security ones apart) and how many hosts have
  been asked, compliance as `cmdb.list` judges it (the worst hard state of
  the compliance checks of a host), the next five enabled schedules and
  the maintenance windows open or to come;
- whether the controller keeps up: groups of cycles in `cycle_lag` and
  `cycle_faults`, notifications not delivered in the window, those
  waiting.

**Why the hosts that fail most come from failed runs alone.** A run keeps
its outcome per host as JSON in `runs.stats`. Reading it for every run of
30 days, at 500 hosts and a run every 15 minutes, took 0.7 s in Python and
1.2 s with SQLite's `json_each`; a run that succeeded has no host that
failed, so only the runs that failed or erred are read. The figure is
then how many runs a host failed in, not out of how many.

**Measured** on the development host (16 November), 500 hosts with 10
checks each, 200 000 changes of state over 60 days (every check changing
about every day and a half, far more than a fleet does), 8 640 runs in 30
days, a third of them with the outcome of 500 hosts, a tenth failed: the
whole answer in about 135 ms for 14 days and 245 ms for 30. Runs take 33
and 70 ms, the changes of state 100 and 170 ms (read as tuples, not
`sqlite3.Row`: a third of the time). Everything else stays under 2 ms.

**The page** (`OverviewPage.tsx`): a row of figures (hosts, open problems,
successful runs, the queue, security updates), each leading to its
section; then cards: the checks by state as a ring with the open problems
under it, the runs by day as stacked bars with the durations and latest
failures, problems and recoveries by day side by side with the time to
recover, what fails most, what is coming up, what the hosts are; at the
bottom one card with the user's access, the version, the checks of the
installation (`selfcheck`) and whether the controller keeps up. Colours
are those of the states and outcomes elsewhere (PatternFly's status
tokens); the bars of each chart have a table for screen readers beside
them. Their text (axes, legends, the total in the ring) is drawn in the
colours of the page's text (`chartText.tsx`, also used by the charts of
a host): the charts' own theme draws a grey the dark theme hides. The charts (`OverviewCharts.tsx`) are in the chunk of those of a host
(`charts.tsx`, see [time series](time-series.md)): the figures and lists
show before it loads. How the answer becomes figures is
in `src/lib/overview.ts`, tested apart from the page.

At a terminal, `fleet overview get` prints the same answer.
