# The Overview: history

Archive: what was tried, measured and found while building the Overview;
not needed to work on it.

**16 November**, on the development host: 500 hosts with 10 checks each,
200 000 changes of state over 60 days (every check changing about every
day and a half, far more than a fleet does), 8 640 runs in 30 days (a run
every 15 minutes), a third of them with the outcome of 500 hosts, a tenth
failed. The whole answer in about 135 ms for 14 days and 245 ms for 30.
Runs take 33 and 70 ms, the changes of state 100 and 170 ms (read as
tuples, not `sqlite3.Row`: a third of the time). Everything else under
2 ms. Reading `runs.stats` for every run of 30 days took 0.7 s in Python
and 1.2 s with SQLite's `json_each`, hence only failed runs are read.
