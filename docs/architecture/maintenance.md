# Maintenance

Every retention is applied in one place, `maintenance.tidy`, at each tick
after the checks; only the outbox of notifications is pruned by the service
that delivers it (90 days). One step that fails is written to the journal
and does not keep the others from running.

| What | Kept | How |
|---|---|---|
| Check cycles | a day | row and directory deleted |
| What check cycles took | 30 days | deleted |
| Output of other runs | `[runs] keep_days` (90) | directory deleted, 500 a tick at most; the row stays with its outcome and `pruned_at` |
| Directories of `runs/` with no run | a day | deleted, the directory looked through once an hour (a stamp file) |
| CMDB changes | 365 days | deleted |
| Metrics | `[metrics]` (14, 90, 730 days) | deleted, see [Time series](time-series.md) |
| Runs, state changes, audit | always | |

A run whose output was deleted says so in its page, in `fleet log` and in
`fleet result`: its status, rc and outcome per host are in its row.

**Giving space back.** Deleted rows leave free pages, which new rows reuse:
at a steady state, the values that expire make room for those that arrive,
and the file stops growing. Giving pages back to the file system matters
after a large deletion (a shorter retention, a check removed). A database is
created with `auto_vacuum = INCREMENTAL`; the tick then gives back up to
32 MB of free pages at a time, and only when they are a quarter of the file
or more, so that it does not shrink a file that will grow again at once. A
database made before is rewritten once by `VACUUM` to work that way, if it
is below 64 MB (every one made before phase 8); a larger one is left as it
is. The scheduler's unit has a private `/tmp`, where `VACUUM` writes its
copy.
