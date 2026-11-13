# Time series

The values the checks measure are kept over time, in `metrics.py`, for
the charts and for sizing.

```
ingest ── a result's metrics ──▶ metric_series      one per host, check, metric, instance
                                 metric_points      (series, time) → value, as it came
                                 metric_hourly      (series, hour) → count, lowest, highest, sum
                                 metric_daily       (series, day)  → the same
```

**Narrow rows.** A series is a metric of a check on a host with its instance
(a mount point, a certificate); its values are rows of the series, a time in
whole seconds and the value, in tables `WITHOUT ROWID` whose key is those
two, so that a value costs about 25 bytes and is reached through the key.
The time is the one of the event that brought the result, believed within
five minutes of the cycle, else the time it was taken in. A second value of
a series at the same second is left out. A host that could not be reached,
a task that failed, a result suppressed record nothing: a gap is a gap.

**Aggregates as values arrive.** Each value is added to the bucket of its
hour and of its day (UTC) by an upsert, so no pass reads the values back to
sum them and the current hour is already in the charts; an average is the
sum over the count. Retention is then deletion only: `[metrics] raw_days`
(14), `hourly_days` (90), `daily_days` (730), checked not to decrease. The
tick deletes past each, one series after another through the key (`series_id
IN (…) AND ts < ?`); a series that stopped reporting goes a day after its
daily values. Deleting a host or a check takes its series with it.

**Reading.** `metric.query`, for viewers like the states it comes from,
returns the series of a host, narrowed by check, metric and instance, over a
span: as they came for up to two days within `raw_days`, hourly up to 62
days within `hourly_days`, daily beyond, or as asked; at most 5000 values a
series, saying when it was cut.

**Charts.** The page of a host draws them under a check once its row is
opened: a day, a week, a month or a year, one chart per metric and one line
per instance (a mount point, a certificate), loaded again when the check has
new values. Aggregates of a single line are drawn as their average with a
band from the lowest to the highest. A line breaks where the distance from the
previous value is more than two and a half times the usual one (the median,
or the hour or day of an aggregate), so a host that could not be reached shows
as a gap. A percentage is drawn from 0 to 100, anything else from 0 unless it
goes below; bytes get ticks at round binary sizes. What turns the answer
into lines is in `src/lib/metrics.ts`, tested apart from the page.

They use `@patternfly/react-charts` 8 (Victory 37), the major that goes with
PatternFly 6. It costs about 380 kB of minified bundle, a quarter of the rest;
the bundle is therefore split (`splitting` in `build.js`) and the charts are a
chunk of their own, loaded the first time a check is opened. A page that shows
no chart loads as before. `build.js` empties `dist/` before a build, since
chunks are named by their hash and the package copies the whole directory. A
page loaded before an upgrade asks for a chunk that is no longer there: it
then says to reload the page instead of failing.

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
