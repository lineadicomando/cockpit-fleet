# System health

`system.health` says how the controller keeps up, from what is already
kept, for viewers like `selfcheck`: watching the checks includes knowing
whether their results are fresh. It holds no configuration beyond the
memory a cycle may take. The System tab shows it above the installation
checks, read again every 30 s.

- For each group of cycles that the enabled checks make (by inventory: the
  checks that go together, each slow kind, each check of the user): its
  shortest interval; from `cycles`, over the last day, how many cycles
  ended and how many not successfully, their usual (median) and longest
  duration, the highest peak of memory against the budget of one cycle,
  the due times they skipped (with those of cycles still under way); the
  delay of `cycle_lag`; and the forks and hosts per cycle the planner would
  choose now, from `checks.size()`, measured or as configured.
- The queue of each lane of the dispatch (`jobs.lanes()`, the ones
  `dispatch()` uses): running against its limit, waiting, the longest wait.
- The database: pages, free pages and the page size as SQLite counts them
  (`PRAGMA page_count`, `freelist_count`), as `maintenance.vacuum()` reads
  them, the size of the write-ahead log, and whether free pages are given
  back. Never `count(*)`: the tables of metrics reach millions of rows.

The page puts the worst first for each group (`src/lib/health.ts`): falling
behind; skipped, or a cycle longer than the interval; slow, a usual cycle
over half of it, which is what sizing aims at; over its memory; cycles not
successful.
