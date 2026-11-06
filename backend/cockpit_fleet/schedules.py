"""Schedules: a job template launched at the times of an OnCalendar expression.

There is no timer per schedule: root would have to write units from what a
user typed. One static timer runs `fleetctl scheduler.tick` every minute, as
the service account; the tick launches what is due through the same code as
run.start, so the queue and its limit apply to scheduled runs as to the others.
The times come from systemd-analyze, so an expression means here what it
means in a unit.
"""

import fcntl
import json
import os
import re
import secrets
import subprocess
import unicodedata
import zoneinfo
from datetime import UTC, datetime

from . import approvals, checks, jobs, journal, maintenance, notify, paths, runs, store, survey
from .cli import Bool, Choice, Context, Id, Int, Obj, Str, Values, command
from .errors import BadRequest, FleetError, Forbidden, Unavailable
from .validators import check_limit

OVERLAP = ("skip", "queue", "cancel")
MAX_RANDOM_DELAY = 6 * 3600
# A time this late when the tick gets to it was missed: the controller was off.
MISSED_AFTER = 300
_TIMEZONE_RE = re.compile(r"[A-Za-z0-9_+-]{1,32}(/[A-Za-z0-9_+-]{1,32}){0,3}")
_ANALYZE_TIMEOUT = 10


# --- times -------------------------------------------------------------------------

def _expression(calendar: str, timezone: str) -> str:
    return f"{calendar} {timezone}" if timezone else calendar


def analyze(calendar: str, timezone: str = "", count: int = 1) -> tuple[str, list[float]]:
    """The normalised form of an expression and its next times, as epoch
    seconds; no times when it never elapses again."""
    expression = _expression(calendar, timezone)
    try:
        result = subprocess.run(
            ["/usr/bin/systemd-analyze", "calendar", f"--iterations={count}", "--", expression],
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
            env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}, timeout=_ANALYZE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(f"cannot run systemd-analyze: {error}") from error
    if result.returncode != 0:
        raise BadRequest(f"not a valid OnCalendar expression: {calendar}")
    normalized, times = "", []
    # Each time comes in the controller's zone, followed by "(in UTC)" unless
    # that zone is UTC or another with no offset and no daylight saving time
    # ("GMT"): then the local time is already the one in UTC.
    local = None
    for line in result.stdout.splitlines():
        label, _, value = line.strip().partition(": ")
        if label == "Normalized form":
            normalized = value
        elif label == "Next elapse" or label.startswith("Iteration #"):
            if local:
                times.append(_epoch(local))
            local = None if value == "never" else value
        elif label == "(in UTC)":
            times.append(_epoch(value))
            local = None
    if local:
        times.append(_epoch(local))
    return normalized, times


def _epoch(value: str) -> float:
    """Epoch seconds of "Wed 2026-10-14 22:00:00 UTC", a time in UTC; the
    day's name and the zone's abbreviation are left out."""
    stamp = datetime.strptime(" ".join(value.split(" ")[1:3]), "%Y-%m-%d %H:%M:%S")
    return stamp.replace(tzinfo=UTC).timestamp()


def check_calendar(value: str) -> str:
    calendar = value.strip()
    if not 1 <= len(calendar) <= 256 or any(unicodedata.category(ch)[0] == "C"
                                            for ch in calendar):
        raise BadRequest("calendar must be 1 to 256 printable characters")
    return calendar


def check_timezone(value: str) -> str:
    """A time zone of the tz database, or empty for the controller's own."""
    if value == "":
        return value
    if not _TIMEZONE_RE.fullmatch(value) or value not in zoneinfo.available_timezones():
        raise BadRequest(f"unknown time zone: {value}")
    return value


def next_time(calendar: str, timezone: str, random_delay: int) -> float | None:
    """When a schedule is due next, its random delay added; None for never."""
    _normalized, times = analyze(calendar, timezone)
    if not times:
        return None
    return times[0] + (secrets.randbelow(random_delay + 1) if random_delay else 0)


# --- cron --------------------------------------------------------------------------

_CRON_MACROS = {
    "@yearly": "*-01-01 00:00:00",
    "@annually": "*-01-01 00:00:00",
    "@monthly": "*-*-01 00:00:00",
    # cron's week starts on Sunday, systemd's "weekly" on Monday.
    "@weekly": "Sun *-*-* 00:00:00",
    "@daily": "*-*-* 00:00:00",
    "@midnight": "*-*-* 00:00:00",
    "@hourly": "*-*-* *:00:00",
}
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
# systemd's week, from Monday.
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _cron_value(text: str, low: int, high: int, names: tuple[str, ...], first: int) -> int:
    if text.lower() in names:
        return names.index(text.lower()) + first
    if not text.isdigit() or not low <= int(text) <= high:
        raise BadRequest(f"{text} is not between {low} and {high}")
    return int(text)


def _cron_field(text: str, low: int, high: int, names: tuple[str, ...] = (),
                first: int = 0) -> list[int]:
    """The values a field of a crontab line matches."""
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) < 1:
                raise BadRequest(f"{part}: the step must be a positive number")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            start_text, _, end_text = base.partition("-")
            start = _cron_value(start_text, low, high, names, first)
            end = _cron_value(end_text, low, high, names, first)
            if start > end:
                raise BadRequest(f"{part}: the range goes backwards")
        else:
            start = _cron_value(base, low, high, names, first)
            # "5/10" is "from 5 on, every 10", as in most crons.
            end = high if step_text else start
        values.update(range(start, end + 1, step))
    return sorted(values)


def _runs_of(values: list[int]) -> list[list[int]]:
    """Consecutive values together: [1, 2, 3, 7] -> [[1, 2, 3], [7]]."""
    groups: list[list[int]] = []
    for value in values:
        if groups and groups[-1][-1] == value - 1:
            groups[-1].append(value)
        else:
            groups.append([value])
    return groups


def _render(values: list[int], low: int, high: int, show=str) -> str:
    """A set of values in the syntax of an OnCalendar component."""
    if values == list(range(low, high + 1)):
        return "*"
    if len(values) > 2:
        step = values[1] - values[0]
        if step > 1 and values == list(range(values[0], high + 1, step)):
            return f"{show(values[0])}/{step}"
    parts = []
    for group in _runs_of(values):
        if len(group) > 2:
            parts.append(f"{show(group[0])}..{show(group[-1])}")
        else:
            parts.extend(show(value) for value in group)
    return ",".join(parts)


def from_cron(line: str) -> str:
    """The OnCalendar expression of the five fields of a crontab line."""
    text = line.strip()
    if text.lower() in _CRON_MACROS:
        return _CRON_MACROS[text.lower()]
    fields = text.split()
    if len(fields) != 5:
        raise BadRequest("a cron line has five fields: minute, hour, day of month, month,"
                         " day of week")
    minute, hour, day, month, weekday = fields
    if not day.startswith("*") and not weekday.startswith("*"):
        # cron runs when either matches; an OnCalendar expression wants both.
        raise BadRequest("a cron line that restricts both the day of the month and the day of"
                         " the week cannot be written as one OnCalendar expression")
    minutes = _cron_field(minute, 0, 59)
    hours = _cron_field(hour, 0, 23)
    days = _cron_field(day, 1, 31)
    months = _cron_field(month, 1, 12, _MONTHS, 1)
    # cron counts from Sunday, as 0 or 7.
    weekdays = sorted({(value - 1) % 7 for value in _cron_field(weekday, 0, 7, _DAYS)})
    two = "{:02d}".format
    date = f"*-{_render(months, 1, 12, two)}-{_render(days, 1, 31, two)}"
    time = f"{_render(hours, 0, 23, two)}:{_render(minutes, 0, 59, two)}:00"
    names = _render(weekdays, 0, 6, lambda value: _DAY_NAMES[value])
    return f"{date} {time}" if names == "*" else f"{names} {date} {time}"


# --- commands ----------------------------------------------------------------------

def _schedule(ctx: Context, row) -> dict:
    data = {key: row[key] for key in ("id", "name", "template_id", "calendar", "timezone",
                                      "overlap", "priority", "random_delay", "next_at",
                                      "last_at", "last_outcome", "last_error", "last_run_id",
                                      "created_by", "created_at", "updated_at")}
    data.update(enabled=bool(row["enabled"]), limit=row["limit_hosts"],
                check=bool(row["check_mode"]), diff=bool(row["diff"]),
                catch_up=bool(row["catch_up"]), answers=store.loads(row["answers"], {}))
    template = ctx.conn.execute("SELECT name FROM templates WHERE id = ?",
                                (row["template_id"],)).fetchone()
    data["template"] = template["name"] if template else ""
    last = ctx.conn.execute("SELECT status FROM runs WHERE id = ?",
                            (row["last_run_id"],)).fetchone()
    data["last_status"] = last["status"] if last else ""
    return data


def _answers(template, answers: dict) -> dict:
    """The answers a schedule stores, checked against the template's survey."""
    return survey.stored_answers(template, answers, "scheduled", "a schedule")


_SCHEDULE_PARAMS = {
    "name": Str(required=True, max_length=200),
    "template_id": Id("The job template to launch"),
    "calendar": Str("When, as an OnCalendar expression of systemd: 'daily',"
                    " 'Mon..Fri *-*-* 02:00', '*:0/15'", required=True, max_length=256),
    "timezone": Str("A time zone such as Europe/Rome; empty for the controller's own",
                    default="", max_length=100),
    "enabled": Bool(default=True),
    "limit": Str("Narrows the hosts of the template", default="", max_length=512),
    "check": Bool("A dry run"),
    "diff": Bool(),
    "answers": Obj("Answers to the template's survey, by variable; no secret"),
    "overlap": Choice(OVERLAP, "What to do when the previous run of this schedule is still"
                      " queued or running: skip this time, queue it, or cancel the previous",
                      default="skip"),
    "catch_up": Bool("Launch once at start-up when times were missed while the controller"
                     " was off"),
    "priority": Int(-10, 10, "Queued runs of a higher priority start first", default=0),
    "random_delay": Int(0, MAX_RANDOM_DELAY, "Up to this many seconds later, chosen at random,"
                        " to spread the load", default=0),
}


def _values(ctx: Context, params: Values) -> dict:
    template = store.fetch(ctx.conn, "templates", params["template_id"], "template")
    calendar = check_calendar(params["calendar"])
    timezone = check_timezone(params["timezone"])
    limit = params["limit"]
    if not analyze(calendar, timezone)[1]:
        raise BadRequest(f"{calendar} has no time left to run at")
    return {
        "name": store.check_name(params["name"]),
        "template_id": template["id"],
        "calendar": calendar,
        "timezone": timezone,
        "enabled": int(params["enabled"]),
        "limit_hosts": check_limit(limit) if limit else "",
        "check_mode": int(params["check"]),
        "diff": int(params["diff"] or params["check"]),
        "answers": json.dumps(_answers(template, params["answers"])),
        "overlap": params["overlap"],
        "catch_up": int(params["catch_up"]),
        "priority": params["priority"],
        "random_delay": params["random_delay"],
        "updated_at": store.now(),
    }


def _planned(values: dict) -> float | None:
    if not values["enabled"]:
        return None
    return next_time(values["calendar"], values["timezone"], values["random_delay"])


@command("schedule.list", role="viewer")
def schedule_list(ctx: Context, params: Values) -> dict:
    """The schedules: which template each launches, when next, and how the last time went."""
    rows = ctx.conn.execute("SELECT * FROM schedules ORDER BY name COLLATE NOCASE").fetchall()
    return {"schedules": [_schedule(ctx, row) for row in rows]}


@command("schedule.create", role="admin", mutating=True, params=_SCHEDULE_PARAMS)
def schedule_create(ctx: Context, params: Values) -> dict:
    """Create a schedule that launches a job template at the times of a calendar."""
    values = _values(ctx, params)
    schedule_id = store.new_id("s")
    store.insert(ctx.conn, "schedules", {"id": schedule_id, "created_by": ctx.caller.user,
                                         "created_at": store.now(), "next_at": _planned(values),
                                         **values}, "schedule")
    return _schedule(ctx, store.fetch(ctx.conn, "schedules", schedule_id, "schedule"))


@command("schedule.update", role="admin", mutating=True,
         params={"id": Id(), **_SCHEDULE_PARAMS})
def schedule_update(ctx: Context, params: Values) -> dict:
    """Change a schedule; its next time is computed again from now."""
    schedule_id = params["id"]
    store.fetch(ctx.conn, "schedules", schedule_id, "schedule")
    values = _values(ctx, params)
    store.update(ctx.conn, "schedules", schedule_id, {**values, "next_at": _planned(values)},
                 "schedule")
    return _schedule(ctx, store.fetch(ctx.conn, "schedules", schedule_id, "schedule"))


@command("schedule.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def schedule_delete(ctx: Context, params: Values) -> dict:
    """Delete a schedule; its past runs stay."""
    store.delete(ctx.conn, "schedules", params["id"], "schedule")
    return {}


def _set_enabled(ctx: Context, schedule_id: str, enabled: bool) -> dict:
    row = store.fetch(ctx.conn, "schedules", schedule_id, "schedule")
    if enabled and ctx.caller.role != "admin":
        template = store.fetch(ctx.conn, "templates", row["template_id"], "template")
        if template["needs_approval"]:
            # Its runs are launched by the schedule: whoever turned it on
            # could then approve them, and one person would do both.
            raise Forbidden("a schedule of a template whose runs need an approval"
                            " is turned on by an administrator")
    values = {"enabled": int(enabled), "calendar": row["calendar"], "timezone": row["timezone"],
              "random_delay": row["random_delay"]}
    store.update(ctx.conn, "schedules", schedule_id,
                 {"enabled": int(enabled), "next_at": _planned(values),
                  "updated_at": store.now()}, "schedule")
    return _schedule(ctx, store.fetch(ctx.conn, "schedules", schedule_id, "schedule"))


@command("schedule.enable", role="operator", mutating=True,
         params={
             "id": Id(),
         })
def schedule_enable(ctx: Context, params: Values) -> dict:
    """Turn a schedule on: it launches its template from its next time on."""
    return _set_enabled(ctx, params["id"], True)


@command("schedule.disable", role="operator", mutating=True,
         params={
             "id": Id(),
         })
def schedule_disable(ctx: Context, params: Values) -> dict:
    """Turn a schedule off; what it already launched goes on."""
    return _set_enabled(ctx, params["id"], False)


def _launch(ctx: Context, row, created_by: str | None = None) -> dict:
    return runs.launch(ctx, row["template_id"], row["limit_hosts"], bool(row["check_mode"]),
                       bool(row["diff"]), store.loads(row["answers"], {}), schedule=row,
                       created_by=created_by)


@command("schedule.run", role="operator", mutating=True,
         params={
             "id": Id(),
         })
def schedule_run(ctx: Context, params: Values) -> dict:
    """Launch what a schedule launches, now, as the caller; its times do not change."""
    row = store.fetch(ctx.conn, "schedules", params["id"], "schedule")
    return _launch(ctx, row)


@command("schedule.preview", role="viewer",
         params={
             "calendar": Str("An OnCalendar expression", default="", max_length=256),
             "cron": Str("Or the five fields of a crontab line, to be converted",
                         default="", max_length=256),
             "timezone": Str(default="", max_length=100),
             "count": Int(1, 20, default=5),
         })
def schedule_preview(ctx: Context, params: Values) -> dict:
    """The next times of a calendar, before it is saved; a crontab line is
    converted to the OnCalendar expression that means the same."""
    if bool(params["calendar"].strip()) == bool(params["cron"].strip()):
        raise BadRequest("give either calendar or cron")
    calendar = (check_calendar(params["calendar"]) if params["calendar"].strip()
                else from_cron(params["cron"]))
    normalized, times = analyze(calendar, check_timezone(params["timezone"]), params["count"])
    return {"calendar": calendar, "normalized": normalized, "next": times}


# --- the tick ----------------------------------------------------------------------

def _active_runs(ctx: Context, schedule_id: str) -> list[str]:
    return [row["id"] for row in ctx.conn.execute(
        "SELECT id FROM runs WHERE schedule_id = ?"
        " AND status IN ('pending_approval', 'queued', 'running')"
        " ORDER BY created_at", (schedule_id,))]


def _fire(ctx: Context, row, now: float) -> dict:
    """Do what a schedule that is due asks for, and plan its next time."""
    outcome, error, run_id = "launched", "", row["last_run_id"]
    try:
        planned = next_time(row["calendar"], row["timezone"], row["random_delay"])
    except FleetError as failure:
        planned, outcome, error = None, "error", str(failure)
    # Claimed first: a launch that fails in a way nobody foresaw is not tried
    # again every minute. Changed meanwhile by an administrator: theirs.
    claimed = ctx.conn.execute(
        "UPDATE schedules SET next_at = ?, last_at = ? WHERE id = ? AND next_at = ?",
        (planned, now, row["id"], row["next_at"])).rowcount
    if not claimed:
        return {"id": row["id"], "outcome": "changed"}

    if outcome == "error":
        pass
    elif now - row["next_at"] > MISSED_AFTER and not row["catch_up"]:
        outcome = "missed"
    else:
        previous = _active_runs(ctx, row["id"])
        if previous and row["overlap"] == "skip":
            outcome = "skipped"
        else:
            try:
                if row["overlap"] == "cancel":
                    for active in previous:
                        try:
                            jobs.cancel(ctx, active)
                        except BadRequest:
                            pass  # collected by the cancel of the one before it
                run_id = _launch(ctx, row, f"schedule:{row['name']}")["id"]
            except FleetError as failure:
                outcome, error = "error", str(failure)

    ctx.conn.execute(
        "UPDATE schedules SET last_outcome = ?, last_error = ?, last_run_id = ? WHERE id = ?",
        (outcome, error, run_id, row["id"]))
    journal.send(f"schedule {row['id']} ({row['name']}): {outcome}"
                 + (f", run {run_id}" if outcome == "launched" else "")
                 + (f" ({error})" if error else ""),
                 3 if outcome == "error" else 6 if outcome == "launched" else 5,
                 FLEET_SCHEDULE=row["id"], FLEET_OUTCOME=outcome,
                 **({"FLEET_RUN": run_id} if outcome == "launched" else {}))
    return {"id": row["id"], "outcome": outcome, **({"run_id": run_id}
                                                     if outcome == "launched" else {})}


def tick(ctx: Context) -> dict:
    descriptor = os.open(paths.SCHEDULER_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"busy": True, "schedules": []}
        # What ended or was left queued, by a reboot too, is taken care of first.
        jobs.reconcile(ctx)
        # Runs nobody approved in time.
        approvals.expire(ctx)
        # Notifications whose time to be tried again has come.
        notify.wake_if_due(ctx)
        now = store.now()
        due = ctx.conn.execute(
            "SELECT * FROM schedules WHERE enabled = 1 AND next_at IS NOT NULL AND next_at <= ?"
            " ORDER BY priority DESC, next_at", (now,)).fetchall()
        fired = [_fire(ctx, row, now) for row in due]
        return {"busy": False, "schedules": fired, "checks": checks.cycle(ctx),
                "maintenance": maintenance.tidy(ctx)}
    finally:
        os.close(descriptor)


@command("scheduler.tick", role="admin", tool=False)
def scheduler_tick(ctx: Context, params: Values) -> dict:
    """Launch what is due. Run every minute by cockpit-fleet-scheduler.timer."""
    return tick(ctx)
