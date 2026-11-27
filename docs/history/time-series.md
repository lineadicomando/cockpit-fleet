# Time series: history

Archive: what was tried, measured and found while building time series;
not needed to work on it.

The charts cost about 380 kB of minified bundle, a quarter of the rest at
the time; a page that shows no chart loads as before.

Measured on the development host (20 October), 500 hosts with 10 metrics
each: a cycle's 5000 values are written in 0.12 s outside a transaction
(0.06 s in one), the tick's deletion takes 12 ms when nothing is due and
0.1 s for a day of 125 000 values. At the target of phase 8 (500 hosts, 10
checks every 10 minutes, about 20 values per host) 14 days of values are
some 20 million rows, about 500 MB. The load test found the database
growing faster than that: ~47 MB a day at 200 hosts over 1.5 hours,
which would be ~120 MB a day at 500 and well above 500 MB in 14 days; the
24 hours at 500 hosts measured ~60 MB a day, while retention had yet
to delete anything.
