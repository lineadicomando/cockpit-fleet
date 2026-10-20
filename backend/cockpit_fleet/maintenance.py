"""Maintenance, at each tick of the scheduler: what is past its retention is
deleted, and the space the database no longer uses is given back.

Every retention is here, apart from the outbox of notifications, which the
service that delivers it prunes. Each step is cheap when there is nothing to
do: tables are reached through an index, and the directory of the runs is
looked through at most once an hour.
"""

import os
import shutil
import traceback

from . import checks, cmdb, jobs, journal, metrics, paths, store
from .cli import Context

DAY = 86400
# Runs whose output is deleted in one tick at most; the rest go at the next.
_BATCH = 500
# A directory of the runs with no run is left this long: a run's directory
# is made before its row is written.
_ORPHAN_AGE = DAY
_SWEEP_EVERY = 3600
# A database that does not give back its free pages a few at a time yet is
# rewritten so that it does, when it is this small: it takes a moment then.
_CONVERT_LIMIT = 64 * 1024 * 1024
# Free pages are given back when they are this share of the file, at most
# this many in one tick (32 MB of 4 KB pages); below it new rows reuse them.
_FREE_SHARE = 4
_FREE_MIN = 256
_FREE_STEP = 8192


def prune_runs(ctx: Context, now: float) -> int:
    """Delete the directory of the runs that ended more than [runs]
    keep_days ago: their output and events. The row, with its outcome,
    stays and says when."""
    days = ctx.config.runs_keep_days
    if not days:
        return 0
    rows = ctx.conn.execute(
        "SELECT id FROM runs WHERE pruned_at IS NULL AND finished_at < ? AND kind != 'check'"
        " LIMIT ?", (now - days * DAY, _BATCH)).fetchall()
    for row in rows:
        shutil.rmtree(jobs.run_dir(row["id"]), ignore_errors=True)
        ctx.conn.execute("UPDATE runs SET pruned_at = ? WHERE id = ?", (now, row["id"]))
    return len(rows)


def sweep(ctx: Context, now: float) -> int:
    """Delete the directories of the runs that have no run, once they are a
    day old: left by a run deleted while its directory could not be."""
    try:
        if now - paths.SWEEP_STAMP.stat().st_mtime < _SWEEP_EVERY:
            return 0
    except FileNotFoundError:
        pass
    paths.SWEEP_STAMP.touch()
    os.utime(paths.SWEEP_STAMP, (now, now))
    known = {row["id"] for row in ctx.conn.execute("SELECT id FROM runs")}
    removed = 0
    with os.scandir(paths.RUNS_DIR) as entries:
        for entry in entries:
            if entry.name in known or not entry.is_dir(follow_symlinks=False):
                continue
            if now - entry.stat(follow_symlinks=False).st_mtime < _ORPHAN_AGE:
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
    return removed


def vacuum(ctx: Context, now: float) -> dict:
    """Give back to the file system the pages that deletions left free, when
    they are a good share of the file and a few at a time."""
    conn = ctx.conn
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    if conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
        size = pages * conn.execute("PRAGMA page_size").fetchone()[0]
        if size > _CONVERT_LIMIT:
            return {"incremental": False}
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        conn.execute("VACUUM")
        return {"incremental": True, "converted": True}
    if free < _FREE_MIN or free * _FREE_SHARE < pages:
        return {"incremental": True, "freed": 0}
    step = min(free, _FREE_STEP)
    conn.execute(f"PRAGMA incremental_vacuum({step})").fetchall()
    return {"incremental": True, "freed": step}


def tidy(ctx: Context) -> dict:
    """What the tick does for maintenance, after the checks."""
    now = store.now()
    out: dict = {}
    for name, step in (("cycles", checks.prune_cycles), ("runs", prune_runs),
                       ("orphans", sweep), ("cmdb", cmdb.prune), ("metrics", metrics.prune),
                       ("vacuum", vacuum)):
        try:
            out[name] = step(ctx, now)
        except Exception:  # noqa: BLE001  (one step must not keep the others from running)
            journal.send(f"maintenance: {name} failed", 3,
                         FLEET_TRACEBACK=traceback.format_exc())
    return out
