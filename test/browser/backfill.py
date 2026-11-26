#!/usr/bin/python3 -I
"""A past for a controller of the tests in a browser, written into its database.

    backfill.py DB [--days N] [--user NAME]

The checks and their metrics, the runs and the problems of a controller
that was just made start today; the overview, the charts and the time to
recover need days behind them. This gives each series of metrics a value
every ten minutes back to N days ago (the hourly and daily aggregates as
metrics.record keeps them), a few problems that began and ended, and the
runs of each template, one a working morning, their output already deleted
as [runs] keep_days does. Only for the controller of 'browser.sh up', and
after its first cycles: it reads the series and hosts they made, and
writes nothing older than what exists. Fails rather than guess when the
tables it writes are not as it knows them.
"""

import argparse
import json
import math
import random
import secrets
import sqlite3
import sys
import time

HOUR = 3600
DAY = 86400
STEP = 600
# The columns written, as of schema 38; a later migration that changes them
# should make this fail, not write something half right.
NEEDS = {
    "metric_series": {"id", "host_id", "check_id", "name", "instance", "unit"},
    "metric_points": {"series_id", "ts", "value"},
    "metric_hourly": {"series_id", "ts", "n", "low", "high", "total"},
    "metric_daily": {"series_id", "ts", "n", "low", "high", "total"},
    "state_changes": {"host_id", "check_id", "ts", "state", "state_type", "summary"},
    "runs": {"id", "kind", "status", "title", "template_id", "project_id", "inventory_id",
             "created_by", "created_at", "started_at", "finished_at", "rc", "stats", "error",
             "pruned_at"},
}
_AGGREGATE = (
    "INSERT INTO {table} (series_id, ts, n, low, high, total) VALUES (?, ?, 1, ?, ?, ?)"
    " ON CONFLICT (series_id, ts) DO UPDATE SET n = n + 1, low = min(low, excluded.low),"
    " high = max(high, excluded.high), total = total + excluded.total")


def check_schema(conn: sqlite3.Connection) -> None:
    for table, columns in NEEDS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = columns - have
        if missing:
            sys.exit(f"backfill: {table} lacks {', '.join(sorted(missing))}: update backfill.py")


def working(ts: float) -> float:
    """How busy a classroom is at a time: 0 at night and at weekends, up to 1
    in the late morning of a working day (local time of the controller)."""
    t = time.localtime(ts)
    if t.tm_wday >= 5:
        return 0.05
    hour = t.tm_hour + t.tm_min / 60
    return max(0.0, math.sin(math.pi * (hour - 7.5) / 10)) if 7.5 <= hour <= 17.5 else 0.0


def value(name: str, unit: str, last: float, ts: float, now: float, rng: random.Random) -> float:
    ago = now - ts
    if "uptime" in name or name.endswith("_seconds") or (unit == "s" and last > 3600):
        # Before the last boot: started again a week before, and so on.
        v = last - ago
        while v < 60:
            v += 7 * DAY
        return round(v)
    if name == "offset":
        return round(rng.gauss(0, 0.4), 2)
    if name.startswith("load"):
        return round(max(0.0, 0.1 + 1.6 * working(ts) + rng.gauss(0, 0.15)), 2)
    if name == "cpu_pct":
        return round(min(100.0, max(0.5, 3 + 55 * working(ts) + rng.gauss(0, 6))), 1)
    if name == "memory_used_pct":
        return round(min(99.0, max(5.0, last - 8 + 18 * working(ts) + rng.gauss(0, 2))), 1)
    if name == "used_pct":
        # A disk that filled slowly toward where it is now.
        return round(max(1.0, last - 12 * ago / (14 * DAY) + rng.gauss(0, 0.3)), 1)
    if unit == "%" or name.endswith("_pct"):
        return round(min(100.0, max(0.0, last + rng.gauss(0, 1))), 1)
    wave = 1 + 0.05 * math.sin(ts / DAY * 2 * math.pi)
    return round(last * wave + rng.gauss(0, abs(last) * 0.02), 2)


def metrics(conn: sqlite3.Connection, since: float, now: float) -> int:
    written = 0
    series = conn.execute(
        "SELECT s.id, s.name, s.unit,"
        " (SELECT min(ts) FROM metric_points p WHERE p.series_id = s.id),"
        " (SELECT value FROM metric_points p WHERE p.series_id = s.id ORDER BY ts DESC LIMIT 1)"
        " FROM metric_series s").fetchall()
    for series_id, name, unit, first, last in series:
        if first is None or last is None:
            continue
        # The same past each time it is made: not for secrets.
        rng = random.Random(f"{series_id}-{name}")  # noqa: S311
        ts = int(since) - int(since) % STEP
        while ts < first - STEP:
            v = value(name, unit, last, ts, now, rng)
            if conn.execute("INSERT INTO metric_points (series_id, ts, value) VALUES (?, ?, ?)"
                            " ON CONFLICT DO NOTHING", (series_id, ts, v)).rowcount == 1:
                for table, size in (("metric_hourly", HOUR), ("metric_daily", DAY)):
                    conn.execute(_AGGREGATE.format(table=table),
                                 (series_id, ts - ts % size, v, v, v))
                written += 1
            ts += STEP
    return written


def at(now: float, days_ago: int, hour: int, minute: int) -> float:
    t = time.localtime(now - days_ago * DAY)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, -1))


# Problems of the past: host, check, days ago, local time, minutes, state, summary.
TIMED_OUT = "UNREACHABLE: ssh: connect to host: Connection timed out"
INCIDENTS = [
    ("srv-dati", "raggiungibile", 12, (8, 41), 94, 2, TIMED_OUT),
    ("aula-pc02", "raggiungibile", 9, (10, 12), 47, 2,
     "UNREACHABLE: ssh: connect to host: No route to host"),
    ("aula-pc01", "disco", 5, (11, 30), 185, 1, "/ 86% used"),
    ("aula-pc03", "orologio", 3, (9, 5), 22, 1, "13.8 s from the controller"),
    ("aula-pc02", "raggiungibile", 1, (15, 50), 12, 2, TIMED_OUT),
]


def incidents(conn: sqlite3.Connection, now: float) -> int:
    written = 0
    for host, check, days_ago, (hour, minute), minutes, state, summary in INCIDENTS:
        row = conn.execute("SELECT h.id, c.id FROM hosts h, checks c"
                           " WHERE h.name = ? AND c.name = ?", (host, check)).fetchone()
        if row is None:
            continue
        began = at(now, days_ago, hour, minute)
        rows = [(began - 120, state, "soft", summary), (began, state, "hard", summary),
                (began + minutes * 60, 0, "hard", "")]
        for ts, st, kind, text in rows:
            conn.execute("INSERT INTO state_changes"
                         " (host_id, check_id, ts, state, state_type, summary)"
                         " VALUES (?, ?, ?, ?, ?, ?)", (*row, ts, st, kind, text))
            written += 1
    return written


def runs(conn: sqlite3.Connection, since: float, now: float, user: str) -> int:
    """Runs of the past; the lists of runs go by rowid, so the runs that were
    there are moved after them."""
    written = 0
    existing = [run_id for (run_id,) in conn.execute("SELECT id FROM runs ORDER BY rowid")]
    hosts = [name for (name,) in conn.execute("SELECT name FROM hosts ORDER BY name")]
    templates = conn.execute("SELECT id, name, project_id, inventory_id FROM templates").fetchall()
    for template_id, name, project_id, inventory_id in templates:
        first = conn.execute("SELECT min(created_at) FROM runs WHERE template_id = ?",
                             (template_id,)).fetchone()[0] or now
        rng = random.Random(name)  # noqa: S311
        for days_ago in range(int((now - since) / DAY), 0, -1):
            created = at(now, days_ago, 7, 30) + rng.randint(0, 600)
            if created >= first or time.localtime(created).tm_wday >= 5:
                continue
            took = rng.uniform(25, 70)
            failed = rng.random() < 0.12
            counts = {"ok": 3, "changed": 0, "failures": 0, "unreachable": 0, "skipped": 0}
            stats = {h: dict(counts) for h in hosts}
            if failed:
                stats[rng.choice(hosts)] = dict(counts, ok=0, unreachable=1)
            conn.execute(
                "INSERT INTO runs (id, kind, status, title, template_id, project_id, inventory_id,"
                " created_by, created_at, started_at, finished_at, rc, stats, error, pruned_at)"
                " VALUES (?, 'playbook', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("r" + secrets.token_hex(6), "failed" if failed else "successful", name,
                 template_id, project_id, inventory_id, user, created, created + 2,
                 created + 2 + took,
                 4 if failed else 0, json.dumps(stats), "", now))
            written += 1
    for run_id in existing:
        conn.execute("UPDATE runs SET rowid = (SELECT max(rowid) FROM runs) + 1 WHERE id = ?",
                     (run_id,))
    return written


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--user", default="tester")
    args = parser.parse_args()
    now = time.time()
    since = now - args.days * DAY
    conn = sqlite3.connect(args.database, timeout=30)
    check_schema(conn)
    with conn:
        values = metrics(conn, since, now)
        changes = incidents(conn, now)
        made = runs(conn, since, now, args.user)
    print(f"backfill: {values} values, {changes} changes of state, {made} runs")


if __name__ == "__main__":
    main()
