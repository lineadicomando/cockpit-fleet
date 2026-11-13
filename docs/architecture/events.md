# Events

The callback `lineadicomando.fleet.events` appends one JSON object per line:
`playbook_start`, `play_start`, `task_start`, `host_ok`, `host_failed`,
`host_skipped`, `host_unreachable`, `item_ok`, `item_failed`, `item_skipped`
(one per element of a loop), `stats`. Results are trimmed (long strings cut,
`invocation`, `diff` and, by default, `ansible_facts` dropped).

What a task changed, or would change in a dry run, is the field `diff` of the
host's event, or of the element's for a loop: a unified diff as text, the same
`ansible-playbook --diff` prints. The callback renders it, not the browser,
because it takes the whole of `before` and `after`, which in the event would be
cut like any other string. One result's diff is cut at 16 kB (`diff_cut`); the
run's output has all of it. Ansible gives no diff for a task with `no_log` or
`diff: false`, so neither does the event. The "Differences" tab of a run groups
them by host and draws a host's only when it is opened. Like the output, they
are for operators and administrators: a diff shows the content of files.

Checked with Debian's ansible-core 2.19.11: six tasks in a dry run on two hosts,
one of them rewriting a file of 4000 lines, give 41 kB of events.

Measured on the test controller (2 vCPU, 2 GB), 200 hosts simulated with the
local connection, 10 forks, fact gathering plus three tasks:

| | Time | Output |
|---|---|---|
| without the callback | 113 s | |
| with the callback | 117 s | 389 kB, 1.9 kB per host |
| with the callback, facts kept | 119 s | 2.6 MB, 12.8 kB per host |

The callback costs about 3%. The number that matters for sizing is the first
one: about 0.6 s of wall time per host on two cores, before any network delay.
A collection cycle over 200 hosts takes around two minutes on this hardware, so
intervals of 5 to 15 minutes are realistic and controller CPU, not storage, is
the first limit.
