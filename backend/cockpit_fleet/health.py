"""The health of the controller itself: whether the check cycles keep to
their intervals and to the memory they may take, what waits in the queue of
the dispatch, and how large the database is. Read only, from what is
already kept: the cycles of the last day, the queue, SQLite's own counts.
"""

import os
import statistics

from . import checks, jobs, store
from .catalog import OWN_KINDS
from .cli import Context, Values, command

# What the cycles took is summed over this much time.
WINDOW = 86400
_FIRST = ("inventory", "name", "interval", "duration_median", "duration_max", "skipped")


def _groups(ctx: Context) -> dict[tuple[str, str], dict]:
    """The groups of cycles that the enabled checks make, by inventory: what
    they run and the shortest interval among them."""
    groups: dict[tuple[str, str], dict] = {}
    for row in ctx.conn.execute(
            "SELECT c.id, c.name, c.kind, c.interval, c.inventory_id, i.name AS inventory"
            " FROM checks c JOIN inventories i ON i.id = c.inventory_id WHERE c.enabled = 1"
            " ORDER BY i.name COLLATE NOCASE, c.name COLLATE NOCASE"):
        group = checks.group_of(row["kind"], row["id"])
        entry = groups.setdefault((row["inventory_id"], group), {
            "inventory_id": row["inventory_id"], "inventory": row["inventory"], "group": group,
            "runs": "own" if row["kind"] in OWN_KINDS else "slow" if group else "checks",
            "name": row["name"] if row["kind"] in OWN_KINDS else group,
            "interval": row["interval"], "checks": 0})
        entry["interval"] = min(entry["interval"], row["interval"])
        entry["checks"] += 1
    return groups


def _cycles(ctx: Context, inventory_id: str, group: str, since: float) -> dict:
    rows = ctx.conn.execute(
        "SELECT status, started_at, finished_at, hosts, forks, memory_peak, cpu_usec, skipped"
        " FROM cycles WHERE inventory_id = ? AND grp = ? AND finished_at >= ?"
        " ORDER BY finished_at", (inventory_id, group, since)).fetchall()
    durations = [max(row["finished_at"] - row["started_at"], 0) for row in rows]
    peaks = [row["memory_peak"] for row in rows if row["memory_peak"]]
    last = dict(rows[-1]) if rows else None
    if last:
        last["duration"] = round(durations[-1], 1)
    return {
        "cycles": len(rows),
        "unsuccessful": sum(1 for row in rows if row["status"] != "successful"),
        "duration_median": round(statistics.median(durations), 1) if durations else None,
        "duration_max": round(max(durations), 1) if durations else None,
        "memory_peak": max(peaks) if peaks else None,
        "skipped": sum(row["skipped"] for row in rows),
        "last": last,
    }


def _active(ctx: Context, inventory_id: str, group: str) -> tuple[int, int]:
    """Cycles of the group queued or running, and the due times they skipped so far."""
    row = ctx.conn.execute(
        "SELECT count(*) AS n, coalesce(sum(json_extract(options, '$.skipped')), 0) AS skipped"
        " FROM runs WHERE kind = 'check' AND status IN ('queued', 'running')"
        " AND inventory_id = ? AND coalesce(json_extract(options, '$.group'), '') = ?",
        (inventory_id, group)).fetchone()
    return row["n"], int(row["skipped"])


def _queue(ctx: Context, now: float) -> list[dict]:
    out = []
    for name, lane, limit in jobs.lanes(ctx.config):
        row = ctx.conn.execute(
            "SELECT sum(status = 'running') AS running, sum(status = 'queued') AS queued,"  # noqa: S608
            " min(CASE WHEN status = 'queued' THEN created_at END) AS oldest FROM runs"
            f" WHERE status IN ('queued', 'running') AND {lane}").fetchone()
        out.append({"lane": name, "limit": limit, "running": row["running"] or 0,
                    "queued": row["queued"] or 0,
                    "oldest_wait": round(now - row["oldest"], 1) if row["oldest"] else None})
    return out


def _database(ctx: Context) -> dict:
    """Sizes as SQLite counts them: no count(*) over tables of millions of rows."""
    conn = ctx.conn
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    size = conn.execute("PRAGMA page_size").fetchone()[0]
    path = next((row["file"] for row in conn.execute("PRAGMA database_list")
                 if row["name"] == "main"), "")
    try:
        wal = os.stat(f"{path}-wal").st_size if path else 0
    except OSError:
        wal = 0
    return {"bytes": pages * size, "free_bytes": free * size, "wal_bytes": wal,
            "incremental": conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2}


@command("system.health", role="viewer")
def system_health(ctx: Context, params: Values) -> dict:
    """How the controller keeps up. For each group of check cycles (by
    inventory: the checks that go together, a slow kind, a check of the
    user): its shortest interval, how long its cycles of the last day took,
    the peak of their memory against the budget of one cycle, the due times
    they skipped because a cycle had not ended, whether it is falling
    behind, why its cycles cannot be prepared if they cannot, and the forks
    and hosts per cycle the planner now chooses. Then the queue of each lane
    of the dispatch, and the size of the database."""
    now = store.now()
    budget = checks.memory_budget(ctx)
    ceiling = min(ctx.config.checks_forks, ctx.config.max_forks)
    lag = {(row["inventory_id"], row["grp"]): row
           for row in ctx.conn.execute("SELECT * FROM cycle_lag")}
    faults = {(row["inventory_id"], row["grp"]): row
              for row in ctx.conn.execute("SELECT * FROM cycle_faults")}
    groups = []
    for (inventory_id, group), entry in _groups(ctx).items():
        entry.update(_cycles(ctx, inventory_id, group, now - WINDOW))
        active, skipping = _active(ctx, inventory_id, group)
        entry["active"] = active
        entry["skipped"] += skipping
        behind = lag.get((inventory_id, group))
        entry["behind"] = None if behind is None else {
            "streak": behind["streak"], "since": behind["since"], "told": bool(behind["told"])}
        fault = faults.get((inventory_id, group))
        entry["unprepared"] = None if fault is None else {
            "reason": fault["reason"], "since": fault["since"]}
        entry["sizing"] = checks.size(checks.recent_cycles(ctx, inventory_id, group), ceiling,
                                      ctx.config.checks_shard_size, budget,
                                      entry["interval"] / 2)
        # What a table at a terminal shows first.
        groups.append({key: entry[key] for key in _FIRST} | entry)
    return {
        "now": now,
        "window": WINDOW,
        "late_after": ctx.config.checks_late_after,
        "memory": {"budget": budget, "configured_mb": ctx.config.checks_memory_mb,
                   "cycles_at_once": 2 * ctx.config.checks_max_concurrent},
        "groups": groups,
        "queue": _queue(ctx, now),
        "database": _database(ctx),
    }
