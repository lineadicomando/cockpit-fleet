"""The overview of the fleet: what the first page of the interface shows,
summed in one call from what is already kept. The hosts and their states,
the runs of a window of days by day and outcome, what waits or is due, the
updates and compliance of the hosts, problems and recoveries by day and
the time to recover. Read only, apart from the reconciliation of runs that
run.list makes too; never a count over the tables of metrics.
"""

import statistics
from collections import Counter, defaultdict

from . import jobs, store
from .catalog import STATE_NAMES
from .checks import in_maintenance, state_counts
from .cli import Context, Int, Values, command

DAY = 86400
# How many rows each list of the overview holds.
TOP = 5
# A host not started again for this long is counted apart.
UPTIME_DAYS = 30
FAILED = ("failed", "error")
STOPPED = ("canceled", "rejected", "expired")
WAITING = ("pending_approval", "queued", "running")
# Problems first by how bad: critical, then unknown, then warning.
_RANK = {2: 0, 3: 1, 1: 2}


class _Days:
    """The days of the window in the browser's time zone, each named by the
    time its midnight falls at."""

    def __init__(self, now: float, days: int, offset_minutes: int):
        self.offset = offset_minutes * 60
        today = int((now + self.offset) // DAY)
        self.first = today - days + 1
        self.starts = [(self.first + n) * DAY - self.offset for n in range(days)]

    @property
    def since(self) -> float:
        return self.starts[0]

    def index(self, ts: float) -> int | None:
        n = int((ts + self.offset) // DAY) - self.first
        return n if 0 <= n < len(self.starts) else None


def _hosts(ctx: Context, now: float) -> dict:
    conn = ctx.conn
    row = conn.execute(
        "SELECT count(*) AS total, coalesce(sum(reachable = 1), 0) AS reachable,"
        " coalesce(sum(reachable = 0), 0) AS unreachable FROM hosts").fetchone()
    maintained = sum(
        1 for host in conn.execute(
            "SELECT h.id, h.inventory_id, h.groups FROM hosts h WHERE h.inventory_id IN"
            " (SELECT inventory_id FROM maintenances WHERE starts_at <= ? AND ends_at > ?)",
            (now, now))
        if in_maintenance(ctx, host, now))
    systems = Counter()
    for found in conn.execute(
            "SELECT os, platform, count(*) AS n FROM hosts GROUP BY os, platform"):
        systems[found["os"] or found["platform"] or ""] += found["n"]
    boots = conn.execute(
        "SELECT count(*) AS known, coalesce(sum(boot_at < ?), 0) AS long FROM cmdb_hosts"
        " WHERE boot_at IS NOT NULL", (now - UPTIME_DAYS * DAY,)).fetchone()
    return {
        "total": row["total"], "reachable": row["reachable"], "unreachable": row["unreachable"],
        "unknown": row["total"] - row["reachable"] - row["unreachable"],
        "in_maintenance": maintained,
        "systems": [{"name": name, "hosts": n} for name, n in
                    sorted(systems.items(), key=lambda item: (-item[1], item[0]))],
        "uptime": {"days": UPTIME_DAYS, "known": boots["known"], "longer": boots["long"]},
    }


def _problems(ctx: Context, now: float) -> dict:
    """The problems as hard states, the worst and oldest first, those nobody
    took in hand before the others."""
    rows = ctx.conn.execute(
        "SELECT s.host_id, s.check_id, s.hard_state, s.summary, s.changed_at, s.ack_by,"
        " s.flapping, h.name AS host, h.inventory_id, h.groups, h.reachable,"
        " c.name AS check_name, c.kind FROM check_states s JOIN checks c ON c.id = s.check_id"
        " JOIN hosts h ON h.id = s.host_id"
        " WHERE c.enabled = 1 AND s.state_type = 'hard' AND s.hard_state != 0").fetchall()
    rows.sort(key=lambda row: (row["ack_by"] != "", _RANK.get(row["hard_state"], 3),
                               row["changed_at"] or now))
    top = []
    for row in rows[:TOP]:
        top.append({
            "host_id": row["host_id"], "host": row["host"], "check_id": row["check_id"],
            "check": row["check_name"], "state": STATE_NAMES[row["hard_state"]],
            "summary": row["summary"], "since": row["changed_at"],
            "acknowledged": bool(row["ack_by"]), "flapping": bool(row["flapping"]),
            "in_maintenance": in_maintenance(ctx, {"id": row["host_id"],
                                                   "inventory_id": row["inventory_id"],
                                                   "groups": row["groups"]}, now),
            "suppressed": row["kind"] != "reachability" and row["reachable"] == 0,
        })
    return {"open": len(rows), "unacknowledged": sum(1 for row in rows if not row["ack_by"]),
            "top": top}


def _runs(ctx: Context, days: _Days) -> dict:
    """The runs created in the window, as on the page of runs: every kind but
    check cycles. The hosts that failed are read from the runs that failed
    alone: the outcome per host of every run would cost seconds at hundreds
    of hosts."""
    conn = ctx.conn
    by_day = [{"start": start, "successful": 0, "failed": 0, "stopped": 0}
              for start in days.starts]
    totals = Counter()
    durations = []
    templates: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    hosts: Counter = Counter()
    cursor = conn.cursor()
    cursor.row_factory = None
    for status, template_id, created_at, started_at, finished_at in cursor.execute(
            "SELECT status, template_id, created_at, started_at, finished_at FROM runs"
            " WHERE created_at >= ? AND kind != 'check'", (days.since,)):
        outcome = ("successful" if status == "successful" else "failed" if status in FAILED
                   else "stopped" if status in STOPPED else "")
        if not outcome:
            continue
        totals[outcome] += 1
        index = days.index(created_at)
        if index is not None:
            by_day[index][outcome] += 1
        if outcome == "stopped":
            continue
        if started_at and finished_at:
            durations.append(max(finished_at - started_at, 0))
        if template_id:
            templates[template_id][0] += 1
            templates[template_id][1] += outcome == "failed"
    for (stats,) in cursor.execute(
            "SELECT stats FROM runs WHERE created_at >= ? AND kind != 'check'"
            " AND status IN ('failed', 'error')", (days.since,)):
        stats = store.loads(stats, {})
        for host, counts in stats.items() if isinstance(stats, dict) else ():
            if isinstance(counts, dict) and (counts.get("failures") or counts.get("unreachable")):
                hosts[host] += 1
    decided = totals["successful"] + totals["failed"]
    names = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM templates")}
    unstable = sorted(((tid, n, failed) for tid, (n, failed) in templates.items()
                       if failed and tid in names),
                      key=lambda item: (-item[2], -item[1], names[item[0]]))[:TOP]
    failing_hosts = sorted(hosts.items(), key=lambda item: (-item[1], item[0]))[:TOP]
    recent = conn.execute(
        "SELECT id, title, status, created_by, finished_at FROM runs WHERE kind != 'check'"
        " AND status IN ('failed', 'error') AND created_at >= ?"
        " ORDER BY finished_at DESC, rowid DESC LIMIT ?", (days.since, TOP)).fetchall()
    waiting = dict.fromkeys(WAITING, 0)
    for row in conn.execute("SELECT status, count(*) AS n FROM runs WHERE kind != 'check'"
                            " AND status IN ('pending_approval', 'queued', 'running')"
                            " GROUP BY status"):
        waiting[row["status"]] = row["n"]
    return {
        "days": by_day,
        "successful": totals["successful"], "failed": totals["failed"],
        "stopped": totals["stopped"],
        "success_rate": round(totals["successful"] / decided, 4) if decided else None,
        "duration_median": round(statistics.median(durations), 1) if durations else None,
        "duration_max": round(max(durations), 1) if durations else None,
        "recent_failures": [dict(row) for row in recent],
        "templates": [{"id": tid, "name": names[tid], "runs": n, "failed": failed}
                      for tid, n, failed in unstable],
        "hosts": [{"name": host, "failed": failed} for host, failed in failing_hosts],
        "queue": waiting,
    }


# The hard state after a change; rows of before schema 32 have only the state.
_HARD = "coalesce(hard_state, state)"


def _problem_began(ctx: Context, host_id: str, check_id: str, before: float) -> float | None:
    """When the hard problem under way at a time began: its first hard change
    after the last hard ok."""
    row = ctx.conn.execute(
        "SELECT min(ts) AS ts FROM state_changes WHERE host_id = ? AND check_id = ?"  # noqa: S608
        f" AND state_type = 'hard' AND {_HARD} != 0 AND ts < ? AND ts > coalesce("
        "(SELECT max(ts) FROM state_changes WHERE host_id = ? AND check_id = ?"
        f" AND state_type = 'hard' AND {_HARD} = 0 AND ts < ?), 0)",
        (host_id, check_id, before, host_id, check_id, before)).fetchone()
    return row["ts"]


def _alerts(ctx: Context, days: _Days) -> dict:
    """Hard problems that began and recoveries, by day, from the changes of
    state; the time to recover of the problems that ended in the window."""
    conn = ctx.conn
    by_day = [{"start": start, "problems": 0, "recoveries": 0} for start in days.starts]
    changes: dict[tuple[str, str], list] = defaultdict(list)
    cursor = conn.cursor()
    cursor.row_factory = None
    for host_id, check_id, ts, state in cursor.execute(
            f"SELECT host_id, check_id, ts, {_HARD} FROM state_changes"  # noqa: S608
            " WHERE ts >= ? AND state_type = 'hard' ORDER BY ts, id", (days.since,)):
        changes[(host_id, check_id)].append((ts, state))
    took = []
    for (host_id, check_id), rows in changes.items():
        before = cursor.execute(
            f"SELECT {_HARD} FROM state_changes WHERE host_id = ? AND check_id = ?"  # noqa: S608
            " AND state_type = 'hard' AND ts < ? ORDER BY ts DESC, id DESC LIMIT 1",
            (host_id, check_id, days.since)).fetchone()
        problem = before is not None and before[0] != 0
        began = None
        for ts, state in rows:
            if state != 0 and not problem:
                problem, began = True, ts
                key = "problems"
            elif state == 0 and problem:
                if began is None:
                    began = _problem_began(ctx, host_id, check_id, ts)
                if began is not None:
                    took.append(ts - began)
                problem, began = False, None
                key = "recoveries"
            else:
                continue
            index = days.index(ts)
            if index is not None:
                by_day[index][key] += 1
    return {"days": by_day, "recovered": len(took),
            "time_to_recover": round(statistics.mean(took), 1) if took else None}


def _updates(ctx: Context) -> dict:
    row = ctx.conn.execute(
        "SELECT count(*) AS updates, coalesce(sum(security), 0) AS security,"
        " count(DISTINCT host_id) AS hosts,"
        " count(DISTINCT CASE WHEN security = 1 THEN host_id END) AS security_hosts"
        " FROM cmdb_updates").fetchone()
    known = ctx.conn.execute(
        "SELECT count(*) FROM cmdb_hosts WHERE updates_at IS NOT NULL").fetchone()[0]
    return {**dict(row), "known": known}


def _compliance(ctx: Context) -> dict:
    """Hosts by the worst hard state of their compliance checks, as cmdb.list says."""
    counts = {"compliant": 0, "drifted": 0}
    for row in ctx.conn.execute(
            "SELECT max(s.hard_state) AS worst FROM check_states s"
            " JOIN checks c ON c.id = s.check_id WHERE c.kind = 'compliance' AND c.enabled = 1"
            " AND s.state IS NOT NULL GROUP BY s.host_id"):
        counts["compliant" if row["worst"] == 0 else "drifted"] += 1
    return counts


def _upcoming(ctx: Context, now: float) -> dict:
    conn = ctx.conn
    schedules = conn.execute(
        "SELECT s.id, s.name, s.next_at, s.template_id, s.workflow_id,"
        " coalesce(t.name, '') AS template, coalesce(w.name, '') AS workflow FROM schedules s"
        " LEFT JOIN templates t ON t.id = s.template_id"
        " LEFT JOIN workflows w ON w.id = s.workflow_id"
        " WHERE s.enabled = 1 AND s.next_at IS NOT NULL"
        " ORDER BY s.next_at LIMIT ?", (TOP,)).fetchall()
    windows = conn.execute(
        "SELECT m.id, m.starts_at, m.ends_at, m.host_group, m.comment, i.name AS inventory,"
        " h.name AS host FROM maintenances m JOIN inventories i ON i.id = m.inventory_id"
        " LEFT JOIN hosts h ON h.id = m.host_id WHERE m.ends_at > ? ORDER BY m.starts_at"
        " LIMIT ?", (now, TOP)).fetchall()
    return {
        "schedules": [dict(row) for row in schedules],
        "maintenances": [{**dict(row), "host": row["host"] or "",
                          "active": row["starts_at"] <= now} for row in windows],
    }


def _controller(ctx: Context, since: float) -> dict:
    conn = ctx.conn
    failed = conn.execute("SELECT count(*) FROM notifications WHERE state = 'failed'"
                          " AND created_at >= ?", (since,)).fetchone()[0]
    pending = conn.execute("SELECT count(*) FROM notifications WHERE state = 'pending'"
                           ).fetchone()[0]
    return {
        "behind": conn.execute("SELECT count(*) FROM cycle_lag").fetchone()[0],
        "unprepared": conn.execute("SELECT count(*) FROM cycle_faults").fetchone()[0],
        "notifications": {"failed": failed, "pending": pending},
    }


@command("overview.get", role="viewer",
         params={
             "days": Int(1, 90, "Days of the window, today included", default=14),
             "tz_offset": Int(-840, 840, "Minutes east of UTC the days begin at", default=0),
         })
def overview_get(ctx: Context, params: Values) -> dict:
    """The state of the fleet at a glance. The hosts: how many, reachable,
    in maintenance, by system, not started again for a month. The checks by
    state, and the open problems, the worst and oldest first. The runs of
    the last days (check cycles apart) by day and outcome, their success
    rate and durations, the latest that failed, the job templates and hosts
    that fail most, and what waits in the queue. Problems and recoveries by
    day with the mean time to recover. Updates the hosts lack, compliance,
    the next schedules and maintenance windows, and whether the controller
    keeps up: check cycles behind, notifications not delivered."""
    jobs.reconcile(ctx)
    now = store.now()
    days = _Days(now, params["days"], params["tz_offset"])
    return {
        "now": now,
        "since": days.since,
        "hosts": _hosts(ctx, now),
        "states": state_counts(ctx, "1 = 1", ()),
        "problems": _problems(ctx, now),
        "runs": _runs(ctx, days),
        "alerts": _alerts(ctx, days),
        "updates": _updates(ctx),
        "compliance": _compliance(ctx),
        "upcoming": _upcoming(ctx, now),
        "controller": _controller(ctx, days.since),
    }
