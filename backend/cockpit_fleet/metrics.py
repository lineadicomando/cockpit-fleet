"""Time series: the values the checks measure, kept over time.

Each metric of a check on a host (with its instance, such as a mount point)
is a series. Its values are kept as they came for [metrics] raw_days, and
as hourly and daily aggregates (count, lowest, highest, sum) for longer.
The aggregates are brought up to date as each value arrives, so that there
is no pass that reads the values back to sum them; retention only deletes.
Days are those of UTC.
"""

from . import store
from .catalog import finite
from .cli import Choice, Context, Id, Int, Str, Values, command
from .errors import BadRequest

HOUR = 3600
DAY = 86400
# A time a node gives for a value is taken only this far before the cycle
# began or after it ended; otherwise the value is dated when it arrived.
_SKEW = 300
# Values returned for one series at most; the answer says when it was cut.
MAX_POINTS = 5000
# What the automatic resolution reads: values as they came for a span of up
# to this, hourly aggregates up to the second.
_RAW_SPAN = 2 * DAY
_HOURLY_SPAN = 62 * DAY

_AGGREGATE = (
    "INSERT INTO {table} (series_id, ts, n, low, high, total) VALUES (?, ?, 1, ?, ?, ?)"
    " ON CONFLICT (series_id, ts) DO UPDATE SET n = n + 1, low = min(low, excluded.low),"
    " high = max(high, excluded.high), total = total + excluded.total")


def when(ts: object, since: float, now: float) -> int:
    """The time of a value: the one of the event that brought it, if believable."""
    found = finite(ts)
    if found is None or not since - _SKEW <= found <= now + _SKEW:
        found = now
    return int(found)


def record(ctx: Context, host_id: str, check_id: str, metrics: list[dict], ts: int) -> int:
    """Keep the metrics of one result, already cleaned. A second value of a
    series at the same second is left out. Returns the values kept."""
    conn = ctx.conn
    kept = 0
    for metric in metrics:
        value = finite(metric.get("value"))
        if value is None:
            continue
        series_id = conn.execute(
            "INSERT INTO metric_series (host_id, check_id, name, instance, unit, last_at)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (host_id, check_id, name, instance)"
            " DO UPDATE SET unit = excluded.unit, last_at = excluded.last_at RETURNING id",
            (host_id, check_id, metric["name"], metric.get("instance", ""),
             metric.get("unit", ""), ts)).fetchone()[0]
        if conn.execute("INSERT INTO metric_points (series_id, ts, value) VALUES (?, ?, ?)"
                        " ON CONFLICT DO NOTHING", (series_id, ts, value)).rowcount != 1:
            continue
        for table, size in (("metric_hourly", HOUR), ("metric_daily", DAY)):
            conn.execute(_AGGREGATE.format(table=table),
                         (series_id, ts - ts % size, value, value, value))
        kept += 1
    return kept


def prune(ctx: Context, now: float) -> dict:
    """Delete what is past its retention. Each table is reached through its
    key, one series after another, not read whole."""
    conn = ctx.conn
    config = ctx.config
    out = {}
    for table, days in (("metric_points", config.metrics_raw_days),
                        ("metric_hourly", config.metrics_hourly_days),
                        ("metric_daily", config.metrics_daily_days)):
        out[table] = conn.execute(
            f"DELETE FROM {table} WHERE series_id IN (SELECT id FROM metric_series)"  # noqa: S608
            " AND ts < ?", (int(now - days * DAY),)).rowcount
    # A series that stopped (a disk removed, a check changed) goes with its last day.
    out["metric_series"] = conn.execute(
        "DELETE FROM metric_series WHERE last_at < ?",
        (now - config.metrics_daily_days * DAY - DAY,)).rowcount
    return out


def _resolution(asked: str, since: int, until: int, now: float, config) -> str:
    if asked != "auto":
        return asked
    span = until - since
    if span <= _RAW_SPAN and since >= now - config.metrics_raw_days * DAY:
        return "raw"
    if span <= _HOURLY_SPAN and since >= now - config.metrics_hourly_days * DAY:
        return "hour"
    return "day"


@command("metric.query", role="viewer",
         params={
             "host_id": Id("The host"),
             "check_id": Id("Only this check", required=False),
             "name": Str("Only this metric, by name", max_length=64),
             "instance": Str("Only this instance of it, such as a mount point",
                             max_length=256),
             "since": Int(0, 2**40, "From, in seconds since the epoch; a day ago by default"),
             "until": Int(0, 2**40, "Up to, in seconds since the epoch; now by default"),
             "resolution": Choice(("auto", "raw", "hour", "day"),
                                  "Values as they came, or their hourly or daily aggregates;"
                                  " auto chooses by the span", default="auto"),
         })
def metric_query(ctx: Context, params: Values) -> dict:
    """The values a host's checks measured over a span of time, one series
    per metric and instance. As they came, a point is [time, value]; as
    aggregates, [start of the hour or day, average, lowest, highest]."""
    conn = ctx.conn
    store.fetch(conn, "hosts", params["host_id"], "host")
    now = store.now()
    until = params["until"] if params["until"] is not None else int(now)
    since = params["since"] if params["since"] is not None else until - DAY
    if since >= until:
        raise BadRequest("since must be earlier than until")
    resolution = _resolution(params["resolution"], since, until, now, ctx.config)
    where, args = ["s.host_id = ?"], [params["host_id"]]
    for column in ("check_id", "name", "instance"):
        if params[column]:
            where.append(f"s.{column} = ?")
            args.append(params[column])
    rows = conn.execute(
        f"SELECT s.*, c.name AS check_name FROM metric_series s JOIN checks c"  # noqa: S608
        f" ON c.id = s.check_id WHERE {' AND '.join(where)}"
        " ORDER BY c.name COLLATE NOCASE, s.name, s.instance", args).fetchall()
    series = []
    for row in rows:
        if resolution == "raw":
            found = conn.execute(
                "SELECT ts, value FROM metric_points WHERE series_id = ? AND ts >= ? AND ts <= ?"
                " ORDER BY ts LIMIT ?", (row["id"], since, until, MAX_POINTS + 1)).fetchall()
            points = [[point["ts"], point["value"]] for point in found]
        else:
            table, size = ("metric_hourly", HOUR) if resolution == "hour" else ("metric_daily",
                                                                               DAY)
            found = conn.execute(
                f"SELECT ts, n, low, high, total FROM {table}"  # noqa: S608
                " WHERE series_id = ? AND ts >= ? AND ts <= ? ORDER BY ts LIMIT ?",
                (row["id"], since - since % size, until, MAX_POINTS + 1)).fetchall()
            points = [[point["ts"], point["total"] / point["n"], point["low"], point["high"]]
                      for point in found]
        if not points:
            continue
        series.append({"check_id": row["check_id"], "check": row["check_name"],
                       "name": row["name"], "instance": row["instance"], "unit": row["unit"],
                       "points": points[:MAX_POINTS], "truncated": len(points) > MAX_POINTS})
    return {"resolution": resolution, "since": since, "until": until, "series": series}
