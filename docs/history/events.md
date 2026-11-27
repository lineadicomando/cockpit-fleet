# Events: history

Archive: what was tried, measured and found while building events; not
needed to work on it.

Diffs checked with Debian's ansible-core 2.19.11: six tasks in a dry run on
two hosts, one of them rewriting a file of 4000 lines, give 41 kB of
events.

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
