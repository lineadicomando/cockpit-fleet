#!/usr/bin/env python3
"""The samples of load.sh, and what they add up to.

    load-sample.py sample DB HEALTH TIMINGS PREVIOUS   one line of JSON on stdout
    load-sample.py summary SAMPLES INTERVAL WARMUP     a report; exit 1 if it failed

A sample reads the database (read only), the answer of system.health that
load.sh just timed, the timings of the calls the interface makes, and the
processor and memory of this machine.
"""

import json
import os
import shutil
import sqlite3
import statistics
import sys
import time

# A state is late when it was last checked longer ago than its interval and this.
GRACE = 120
# The calls of the interface: their time at the 95th percentile, and at most.
UI_P95_MS = 2000
UI_MAX_MS = 5000


def _cpu() -> tuple[int, int]:
    with open("/proc/stat") as stat:
        fields = [int(x) for x in stat.readline().split()[1:]]
    idle = fields[3] + fields[4]
    return sum(fields), idle


def _meminfo() -> dict:
    out = {}
    with open("/proc/meminfo") as info:
        for line in info:
            key, value = line.split(":", 1)
            out[key] = int(value.split()[0]) // 1024
    return out


def _du(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                pass
    return total


def sample(db: str, health_file: str, timings_file: str, previous_file: str) -> dict:
    now = time.time()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    states = conn.execute(
        "SELECT count(*) AS total, sum(s.state IS NULL) AS unchecked,"
        " sum(s.state = 0) AS ok, sum(s.state = 3) AS unknown, sum(s.state IN (1, 2)) AS problems,"
        " sum(s.checked_at < ? - c.interval - ?) AS late,"
        " max((? - s.checked_at) / c.interval) AS oldest"
        " FROM check_states s JOIN checks c ON c.id = s.check_id WHERE c.enabled = 1",
        (now, GRACE, now)).fetchone()
    # Cycles that ended since the last sample: how long they ran, and how long
    # their results took to be taken in once Ansible was done.
    try:
        with open(previous_file) as prev:
            since = json.load(prev)["now"]
    except (OSError, ValueError, KeyError):
        since = now - 60
    cycles = conn.execute(
        "SELECT y.grp, i.name AS inventory, y.status, y.hosts, y.forks, y.skipped,"
        " y.finished_at - y.started_at AS took, r.finished_at - y.finished_at AS ingest,"
        " y.memory_peak, y.cpu_usec"
        " FROM cycles y JOIN runs r ON r.id = y.run_id JOIN inventories i ON i.id = y.inventory_id"
        " WHERE r.finished_at > ? AND r.finished_at <= ?", (since, now)).fetchall()
    with open(health_file) as answer:
        health = json.load(answer).get("data", {})
    with open(timings_file) as lines:
        timings = dict(line.split() for line in lines if line.strip())
    total, idle = _cpu()
    try:
        with open(previous_file) as prev:
            last = json.load(prev)["cpu"]
        busy = 100 * (1 - (idle - last[1]) / max(total - last[0], 1))
    except (OSError, ValueError, KeyError):
        busy = None
    mem = _meminfo()
    return {
        "now": now,
        "cpu": [total, idle],
        "cpu_busy_pct": None if busy is None else round(busy, 1),
        "load1": float(open("/proc/loadavg").read().split()[0]),
        "mem_available_mb": mem.get("MemAvailable"),
        "swap_used_mb": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0),
        "states": {key: states[key] or 0 for key in
                   ("total", "unchecked", "ok", "unknown", "problems", "late")},
        "oldest_age_intervals": round(states["oldest"] or 0, 2),
        "cycles": [dict(row) for row in cycles],
        "groups": [{key: group.get(key) for key in
                    ("inventory", "name", "interval", "cycles", "unsuccessful", "duration_median",
                     "duration_max", "memory_peak", "skipped", "active", "behind", "sizing")}
                   for group in health.get("groups", [])],
        "queue": health.get("queue", []),
        "database": health.get("database", {}),
        "runs_bytes": _du("/var/lib/cockpit-fleet/runs"),
        "disk_free_bytes": shutil.disk_usage("/var/lib/cockpit-fleet").free,
        "ui_ms": {name: int(ms) for name, ms in timings.items()},
    }


def _pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def summary(path: str, interval: int, warmup: int) -> int:
    with open(path) as lines:
        samples = [json.loads(line) for line in lines if line.strip()]
    if not samples:
        print("FAIL  no samples")
        return 1
    start = samples[0]["now"]
    steady = [s for s in samples if s["now"] - start >= warmup] or samples[-1:]
    failed = 0

    def verdict(ok: bool, text: str) -> None:
        nonlocal failed
        print(f"{'PASS' if ok else 'FAIL'}  {text}")
        failed |= not ok

    hours = (samples[-1]["now"] - start) / 3600
    print(f"{len(samples)} samples over {hours:.1f} h, the first {warmup // 60} min left out"
          f" ({len(steady)} kept); states: {steady[-1]['states']['total']}")

    # Cycles: how long, how they ended, and how long ingest took.
    cycles = [c for s in steady for c in s["cycles"]]
    by_group: dict[tuple, list] = {}
    for c in cycles:
        by_group.setdefault((c["inventory"], c["grp"] or "catalog"), []).append(c)
    print("\ncycles               count  hosts  forks  took median/p95/max s   ingest p95/max s"
          "   memory max MB  cpu s/host")
    for (inventory, group), rows in sorted(by_group.items()):
        took = [c["took"] for c in rows]
        ingest = [c["ingest"] for c in rows if c["ingest"] is not None]
        peaks = [c["memory_peak"] for c in rows if c["memory_peak"]]
        cpu = [c["cpu_usec"] / 1e6 / c["hosts"] for c in rows if c["cpu_usec"] and c["hosts"]]
        label = f"{inventory}/{group if len(group) < 12 else 'playbook'}"
        print(f"{label:<20} {len(rows):>5}  {statistics.median(c['hosts'] for c in rows):>5.0f}"
              f"  {statistics.median(c['forks'] for c in rows):>5.0f}"
              f"  {statistics.median(took):>8.1f} / {_pct(took, 95):>6.1f} / {max(took):>6.1f}"
              f"   {_pct(ingest, 95) if ingest else 0:>6.1f} / {max(ingest) if ingest else 0:>5.1f}"
              f"   {max(peaks) / 2**20 if peaks else 0:>13.0f}"
              f"  {statistics.median(cpu) if cpu else 0:>10.2f}")
    bad = [c for c in cycles if c["status"] != "successful"]
    verdict(not bad, f"every cycle ended successful ({len(bad)} did not)")
    skipped = sum(c["skipped"] or 0 for c in cycles)
    verdict(skipped == 0, f"no due time skipped ({skipped})")
    behind = {g["inventory"] + "/" + str(g["name"]) for s in steady for g in s["groups"]
              if g["behind"]}
    verdict(not behind, f"no group fell behind ({', '.join(sorted(behind)) or 'none'})")

    late = max(s["states"]["late"] for s in steady)
    verdict(late == 0, f"no state older than its interval and {GRACE} s (at most {late})")
    oldest = max(s["oldest_age_intervals"] for s in steady)
    print(f"      the oldest result was {oldest:.2f} intervals old")
    waits = [q["oldest_wait"] or 0 for s in steady for q in s["queue"] if q["lane"] == "checks"]
    worst = max(waits) if waits else 0
    verdict(worst < interval / 2, f"cycles waited in the queue at most {worst:.0f} s"
                                  f" (under half the interval, {interval // 2} s)")
    unknown = steady[-1]["states"]["unknown"]
    print(f"      at the end: {steady[-1]['states']['ok']} ok, {unknown} unknown,"
          f" {steady[-1]['states']['problems']} warning or critical")

    # The interface.
    print("\ncall of the interface   median   p95    max ms")
    names = sorted({name for s in steady for name in s["ui_ms"]})
    for name in names:
        values = [s["ui_ms"][name] for s in steady if name in s["ui_ms"]]
        p95 = _pct(values, 95)
        print(f"{name:<22} {statistics.median(values):>7.0f} {p95:>6.0f} {max(values):>6.0f}")
        verdict(p95 <= UI_P95_MS and max(values) <= UI_MAX_MS,
                f"{name} answers in time (p95 under {UI_P95_MS} ms, all under {UI_MAX_MS} ms)")

    # The machine.
    busy = [s["cpu_busy_pct"] for s in steady if s["cpu_busy_pct"] is not None]
    print(f"\nprocessor busy: median {statistics.median(busy) if busy else 0:.0f}%,"
          f" p95 {_pct(busy, 95) if busy else 0:.0f}%;"
          f" load1 at most {max(s['load1'] for s in steady):.1f}")
    print(f"memory available: at least {min(s['mem_available_mb'] for s in steady)} MB;"
          f" swap used at most {max(s['swap_used_mb'] for s in steady)} MB")
    first, last = samples[0], samples[-1]
    db_growth = (last["database"].get("bytes", 0) - first["database"].get("bytes", 0)) / 2**20
    print(f"database: {last['database'].get('bytes', 0) / 2**20:.0f} MB at the end"
          f" (+{db_growth:.0f} MB, {db_growth / max(hours, 0.01) * 24:.0f} MB a day at this rate),"
          f" WAL at most {max(s['database'].get('wal_bytes', 0) for s in samples) / 2**20:.0f} MB")
    print(f"run directories: {last['runs_bytes'] / 2**20:.0f} MB at the end;"
          f" disk free {last['disk_free_bytes'] / 2**30:.1f} GB")
    print()
    print("All checks passed." if not failed else "Some checks FAILED.")
    return failed


if __name__ == "__main__":
    if sys.argv[1] == "sample":
        print(json.dumps(sample(*sys.argv[2:6])))
    elif sys.argv[1] == "summary":
        sys.exit(summary(sys.argv[2], int(sys.argv[3]), int(sys.argv[4])))
    else:
        sys.exit("usage: load-sample.py sample DB HEALTH TIMINGS PREVIOUS"
                 " | summary SAMPLES INTERVAL WARMUP")
