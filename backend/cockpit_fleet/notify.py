"""Notifications: rules choose the runs and the states of checks to tell
about, channels carry the message, and an outbox in the database lies
between them.

When a run ends, or the hard state of a check changes, one row goes in the
outbox for every channel a rule sends it to, and notify.wake is written:
cockpit-fleet-notify.path starts cockpit-fleet-notify.service, which
delivers with the secrets of the channels decrypted for it by PID 1. A
channel that does not answer is tried again later, with a growing delay,
and holds up no other; the tick of the scheduler writes notify.wake again
when something is due.
"""

import fcntl
import functools
import json
import os
import socket
import time
from pathlib import Path

from . import channels, journal, paths, store
from .cli import Bool, Choice, Context, Id, Int, Obj, Str, Strs, Values, command
from .errors import BadRequest, Conflict, NotFound, Unavailable
from .validators import check_group

# In the order of preference, when a run is more than one: with changes before succeeded.
# The ends of a run, then a run that waits for someone to approve it.
EVENTS = ("failed", "changed", "succeeded", "canceled", "approval")
_ENDS = EVENTS[:4]
# What the states of checks give: a problem, its end, and a problem nobody
# acknowledged that is told again.
CHECK_EVENTS = ("problem", "recovery", "reminder")
# What the controller says of itself: check cycles that fall behind, and catch
# up; that cannot be prepared (a project the scheduler cannot see), and can again.
SYSTEM_EVENTS = ("late", "caught_up", "unprepared", "prepared")
SCOPES = ("all", "project", "template", "schedule", "inventory", "group", "check")
# Scopes that only runs have, and those that only checks have.
_RUN_SCOPES = ("project", "template", "schedule")
_CHECK_SCOPES = ("group", "check")
# The scopes in which the controller's own events can be.
_SYSTEM_SCOPES = ("all", "inventory")
LANGUAGES = ("en", "it")
STATES = ("pending", "sent", "failed")
# Runs that are about the nodes; the jobs that serve a project are not.
RUN_KINDS = ("playbook", "adhoc", "workflow")
_SCOPE_TABLES = {"project": "projects", "template": "templates", "schedule": "schedules",
                 "inventory": "inventories", "group": "inventories", "check": "checks"}

# Tried again after 1, 2, 4 ... minutes, at most an hour apart, for a day.
RETRY_FIRST = 60
RETRY_MAX = 3600
GIVE_UP_AFTER = 24 * 3600
# Notifications that have ended are kept this long.
KEEP = 90 * 24 * 3600
# Events named one by one in a message that groups several.
_LISTED = 20
_PASSES = 10


# --- messages -------------------------------------------------------------------

_WORDS = {
    "en": {
        "failed": "Run failed", "succeeded": "Run succeeded", "changed": "Run with changes",
        "canceled": "Run canceled", "approval": "Run waiting for approval",
        "test": "Test message",
        "test_text": "The channel {channel} of Fleet on {host} works.",
        "several": "{count} runs ended", "several_events": "{count} events",
        "template": "Template", "project": "Project",
        "schedule": "Schedule", "by": "Launched by", "check": "dry run",
        "hosts": "Hosts: {ok} ok, {changed} changed, {failed} failed, {unreachable} unreachable",
        "failed_hosts": "Failed", "error": "Error", "more": "and {count} more",
        "problem": "Problem", "recovery": "Recovered", "reminder": "Still a problem",
        "ok": "OK", "warning": "WARNING", "critical": "CRITICAL", "unknown": "UNKNOWN",
        "inventory": "Inventory", "since": "Since",
        "late": "Falling behind", "caught_up": "Back on time",
        "unprepared": "Checks cannot start", "prepared": "Checks start again",
    },
    "it": {
        "failed": "Run fallita", "succeeded": "Run riuscita", "changed": "Run con modifiche",
        "canceled": "Run annullata", "approval": "Run in attesa di approvazione",
        "test": "Messaggio di prova",
        "test_text": "Il canale {channel} di Fleet su {host} funziona.",
        "several": "{count} run terminate", "several_events": "{count} eventi",
        "template": "Template", "project": "Progetto",
        "schedule": "Pianificazione", "by": "Lanciata da", "check": "prova",
        "hosts": "Host: {ok} ok, {changed} modificati, {failed} falliti,"
                 " {unreachable} irraggiungibili",
        "failed_hosts": "Falliti", "error": "Errore", "more": "e altre {count}",
        "problem": "Problema", "recovery": "Risolto", "reminder": "Ancora un problema",
        "ok": "OK", "warning": "AVVISO", "critical": "CRITICO", "unknown": "SCONOSCIUTO",
        "inventory": "Inventario", "since": "Dal",
        "late": "In ritardo", "caught_up": "Di nuovo in orario",
        "unprepared": "Controlli bloccati", "prepared": "Controlli ripartiti",
    },
}


@functools.cache
def _fqdn() -> str:
    # Asked once per process: where the name is not in DNS, as on the Enterprise
    # Linux family, whose nsswitch asks DNS first, each lookup can take seconds.
    return socket.getfqdn()


def base_url(ctx: Context) -> str:
    """Where the web console is reached: config.toml, or Cockpit's own port here."""
    return ctx.config.notify_url or f"https://{_fqdn()}:9090"


def _link(base: str, event: dict | None) -> str:
    if event and event.get("page") == "system":
        return f"{base}/fleet#/system"
    if event and event.get("host_id"):
        return f"{base}/fleet#/hosts/{event['host_id']}"
    run_id = event.get("run_id") if event else None
    return f"{base}/fleet#/runs/{run_id}" if run_id else f"{base}/fleet#/runs"


def _hosts(stats: dict) -> dict:
    """How many hosts ended in each way, and which failed."""
    counts = {"ok": 0, "changed": 0, "failed": 0, "unreachable": 0}
    failed = []
    for host, numbers in sorted(stats.items()):
        if not isinstance(numbers, dict):
            continue
        if numbers.get("unreachable"):
            counts["unreachable"] += 1
            failed.append(host)
        elif numbers.get("failures"):
            counts["failed"] += 1
            failed.append(host)
        elif numbers.get("changed"):
            counts["changed"] += 1
        else:
            counts["ok"] += 1
    return {"counts": counts, "failed": failed[:10]}


def _headline(words: dict, event: dict) -> str:
    if event["event"] in SYSTEM_EVENTS:
        return f"{words[event['event']]}: {event['title']}"
    if event["event"] in CHECK_EVENTS:
        state = words.get(event.get("state", ""), event.get("state", ""))
        return f"{words[event['event']]}: {event['title']} {state}"
    title = f"{words[event['event']]}: {event['title']}"
    return title + (f" ({words['check']})" if event.get("check") else "")


def _details(words: dict, event: dict) -> list[str]:
    if event["event"] in (*CHECK_EVENTS, *SYSTEM_EVENTS):
        lines = [event["summary"]] if event.get("summary") else []
        if event.get("inventory"):
            lines.append(f"{words['inventory']}: {event['inventory']}")
        if event.get("since"):
            since = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(event["since"]))
            lines.append(f"{words['since']}: {since}")
        return lines
    lines = []
    for key in ("template", "project", "schedule"):
        if event.get(key):
            lines.append(f"{words[key]}: {event[key]}")
    if event.get("created_by"):
        lines.append(f"{words['by']}: {event['created_by']}")
    hosts = event.get("hosts")
    if hosts and any(hosts["counts"].values()):
        lines.append(words["hosts"].format(**hosts["counts"]))
        if hosts["failed"]:
            lines.append(f"{words['failed_hosts']}: {', '.join(hosts['failed'])}")
    if event.get("error"):
        lines.append(f"{words['error']}: {event['error'][:300]}")
    return lines


def compose(language: str, events: list[dict], base: str, channel: str = "") -> channels.Message:
    """One message for what happened, in the language of the channel."""
    words = _WORDS.get(language, _WORDS["en"])
    links = [{**event, "link": _link(base, event)} for event in events]
    if len(events) == 1 and events[0]["event"] == "test":
        host = base.split("//", 1)[-1]
        return channels.Message(f"Fleet: {words['test']}",
                                words["test_text"].format(channel=channel, host=host),
                                _link(base, None), links)
    if len(events) == 1:
        event = links[0]
        return channels.Message(f"Fleet: {_headline(words, event)}",
                                "\n".join([*_details(words, event), event["link"]]),
                                event["link"], links)
    lines = [f"- {_headline(words, event)}\n  {event['link']}" for event in links[:_LISTED]]
    if len(events) > _LISTED:
        lines.append(words["more"].format(count=len(events) - _LISTED))
    several = "several" if all(event["event"] in _ENDS for event in events) else "several_events"
    return channels.Message(f"Fleet: {words[several].format(count=len(events))}",
                            "\n".join(lines), _link(base, None), links)


# --- the outbox -----------------------------------------------------------------

def wake() -> None:
    """Have cockpit-fleet-notify.path start the delivery."""
    try:
        paths.NOTIFY_WAKE.write_text(f"{store.now()}\n")
    except OSError as error:
        journal.send(f"cannot wake the delivery of notifications: {error}", 4)


def _run_events(run) -> set[str]:
    if run["status"] == "successful":
        found = {"succeeded"}
        stats = store.loads(run["stats"], {})
        if any(isinstance(numbers, dict) and numbers.get("changed")
               for numbers in stats.values()):
            found.add("changed")
        return found
    if run["status"] in ("failed", "error"):
        return {"failed"}
    # Rejected or expired before it started: it did not run, as if canceled.
    return {"canceled"} if run["status"] in ("canceled", "rejected", "expired") else set()


def _in_scope(rule, run) -> bool:
    if rule["scope"] in _CHECK_SCOPES:
        return False
    column = {"project": "project_id", "template": "template_id",
              "schedule": "schedule_id", "inventory": "inventory_id"}.get(rule["scope"])
    return column is None or run[column] == rule["scope_id"]


def _payload(ctx: Context, run, event: str) -> dict:
    def name(table: str, row_id: str | None) -> str:
        row = ctx.conn.execute(f"SELECT name FROM {table} WHERE id = ?",  # noqa: S608
                               (row_id,)).fetchone()
        return row["name"] if row else ""

    options = store.loads(run["options"], {})
    return {
        "event": event, "run_id": run["id"], "kind": run["kind"], "status": run["status"],
        "title": run["title"], "template": name("templates", run["template_id"]),
        "project": name("projects", run["project_id"]),
        "schedule": name("schedules", run["schedule_id"]),
        "created_by": run["created_by"], "check": bool(options.get("check")),
        "hosts": _hosts(store.loads(run["stats"], {})), "error": run["error"][:500],
        "finished_at": run["finished_at"],
    }


def run_ended(ctx: Context, run_id: str) -> int:
    """Put in the outbox what the rules say about a run that ended; one
    notification per channel, however many of its rules match."""
    run = ctx.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["kind"] not in RUN_KINDS:
        return 0
    return _tell_run(ctx, run, _run_events(run))


def run_waits(ctx: Context, run_id: str) -> int:
    """Put in the outbox what the rules say about a run that waits for an approval."""
    run = ctx.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["kind"] not in RUN_KINDS:
        return 0
    return _tell_run(ctx, run, {"approval"})


def _tell_run(ctx: Context, run, happened: set[str]) -> int:
    if not happened:
        return 0
    chosen: dict[str, str] = {}
    rules = ctx.conn.execute(
        "SELECT r.*, c.group_window FROM notify_rules r JOIN channels c ON c.id = r.channel_id"
        " WHERE r.enabled = 1 AND c.enabled = 1 ORDER BY r.created_at").fetchall()
    for rule in rules:
        matched = happened.intersection(store.loads(rule["events"], []))
        if matched and _in_scope(rule, run) and rule["channel_id"] not in chosen:
            # A run with changes is told as such where that was asked for.
            chosen[rule["channel_id"]] = next(event for event in EVENTS if event in matched)
    now = store.now()
    windows = {rule["channel_id"]: rule["group_window"] for rule in rules}
    for channel_id, event in chosen.items():
        store.insert(ctx.conn, "notifications", {
            "channel_id": channel_id, "event": event, "run_id": run["id"],
            "payload": json.dumps(_payload(ctx, run, event)), "state": "pending",
            "next_at": now + windows[channel_id], "created_at": now}, "notification")
    if chosen:
        wake()
    return len(chosen)


def _check_in_scope(rule, check_id: str, inventory_id: str, groups: list[str]) -> bool:
    scope = rule["scope"]
    if scope == "all":
        return True
    if scope == "inventory":
        return rule["scope_id"] == inventory_id
    if scope == "group":
        return rule["scope_id"] == inventory_id and (
            rule["scope_group"] == "all" or rule["scope_group"] in groups)
    return scope == "check" and rule["scope_id"] == check_id


def check_event(ctx: Context, event: str, payload: dict, host_id: str, check_id: str,
                inventory_id: str, groups: list[str]) -> int:
    """Put in the outbox what the rules say about the state of a check on a
    host; one notification per channel, however many of its rules match."""
    rules = ctx.conn.execute(
        "SELECT r.*, c.group_window FROM notify_rules r JOIN channels c ON c.id = r.channel_id"
        " WHERE r.enabled = 1 AND c.enabled = 1 ORDER BY r.created_at").fetchall()
    now = store.now()
    chosen: dict[str, int] = {}
    for rule in rules:
        if (event in store.loads(rule["events"], [])
                and _check_in_scope(rule, check_id, inventory_id, groups)
                and rule["channel_id"] not in chosen):
            chosen[rule["channel_id"]] = rule["group_window"]
    for channel_id, window in chosen.items():
        store.insert(ctx.conn, "notifications", {
            "channel_id": channel_id, "event": event, "host_id": host_id, "check_id": check_id,
            "payload": json.dumps(payload), "state": "pending", "next_at": now + window,
            "created_at": now}, "notification")
    if chosen:
        wake()
    return len(chosen)


def system_event(ctx: Context, event: str, payload: dict, inventory_id: str) -> int:
    """Put in the outbox what the rules say about the controller itself,
    for an inventory; one notification per channel."""
    rules = ctx.conn.execute(
        "SELECT r.*, c.group_window FROM notify_rules r JOIN channels c ON c.id = r.channel_id"
        " WHERE r.enabled = 1 AND c.enabled = 1 ORDER BY r.created_at").fetchall()
    now = store.now()
    chosen: dict[str, int] = {}
    for rule in rules:
        if (event in store.loads(rule["events"], [])
                and (rule["scope"] == "all"
                     or (rule["scope"] == "inventory" and rule["scope_id"] == inventory_id))
                and rule["channel_id"] not in chosen):
            chosen[rule["channel_id"]] = rule["group_window"]
    for channel_id, window in chosen.items():
        store.insert(ctx.conn, "notifications", {
            "channel_id": channel_id, "event": event, "payload": json.dumps(payload),
            "state": "pending", "next_at": now + window, "created_at": now}, "notification")
    if chosen:
        wake()
    return len(chosen)


def due(ctx: Context, now: float) -> bool:
    return ctx.conn.execute(
        "SELECT 1 FROM notifications n JOIN channels c ON c.id = n.channel_id"
        " WHERE n.state = 'pending' AND n.next_at <= ? AND c.enabled = 1 LIMIT 1",
        (now,)).fetchone() is not None


def wake_if_due(ctx: Context) -> None:
    """Called by the tick: a notification whose time to be tried again came."""
    if due(ctx, store.now()):
        wake()


def secret_name(channel_id: str) -> str:
    return f"{paths.NOTIFY_SECRET_PREFIX}{channel_id}-token"


def secret_file(channel_id: str) -> Path:
    return paths.NOTIFY_SECRETS_DIR / f"{secret_name(channel_id)}.cred"


def _read_secret(directory: Path | None, channel_id: str) -> str:
    """The secret of a channel as PID 1 decrypted it for this unit; empty when
    the channel has none."""
    if directory is None:
        return ""
    try:
        return (directory / f"notify_{secret_name(channel_id)}.cred").read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _delay(attempts: int) -> float:
    return min(RETRY_FIRST * 2 ** max(attempts - 1, 0), RETRY_MAX)


def _deliver_channel(ctx: Context, channel, secrets: Path | None, now: float) -> dict:
    conn = ctx.conn
    rows = conn.execute("SELECT * FROM notifications WHERE channel_id = ? AND state = 'pending'"
                        " ORDER BY id LIMIT 500", (channel["id"],)).fetchall()
    # A test goes alone and at once; the rest goes together when the first is due.
    tests = [row for row in rows if row["event"] == "test" and row["next_at"] <= now]
    batch = tests[:1] or [row for row in rows if row["event"] != "test"]
    if not batch or min(row["next_at"] for row in batch) > now:
        return {"channel": channel["name"], "outcome": "waiting"}

    if batch[0]["event"] != "test":
        sent = conn.execute(
            "SELECT message, max(sent_at) AS at FROM notifications WHERE channel_id = ?"
            " AND state = 'sent' AND event != 'test' AND sent_at > ? GROUP BY message"
            " ORDER BY at", (channel["id"], now - 3600)).fetchall()
        if len(sent) >= channel["max_per_hour"]:
            # Over the limit: they wait together, and leave as one message.
            later = sent[len(sent) - channel["max_per_hour"]]["at"] + 3600
            conn.execute("UPDATE notifications SET next_at = max(next_at, ?) WHERE channel_id = ?"
                         " AND state = 'pending' AND event != 'test'", (later, channel["id"]))
            return {"channel": channel["name"], "outcome": "limited", "next_at": later}

    events = [store.loads(row["payload"], {}) for row in batch]
    message = compose(channel["language"], events, base_url(ctx), channel["name"])
    ids = [row["id"] for row in batch]
    reference = f"fleet-{ids[0]}-{len(ids)}"
    marks = ", ".join("?" for _ in ids)
    try:
        channels.send(channel["kind"], store.loads(channel["settings"], {}),
                      _read_secret(secrets, channel["id"]), message, ctx.config.proxy, reference)
    except channels.SendError as error:
        attempts = max(row["attempts"] for row in batch) + 1
        conn.execute(f"UPDATE notifications SET attempts = attempts + 1, error = ?,"  # noqa: S608
                     f" next_at = ? WHERE id IN ({marks})",
                     (str(error), now + _delay(attempts), *ids))
        conn.execute(f"UPDATE notifications SET state = 'failed' WHERE id IN ({marks})"  # noqa: S608
                     " AND created_at < ?", (*ids, now - GIVE_UP_AFTER))
        journal.send(f"notification channel {channel['id']} ({channel['name']}): {error}", 4,
                     FLEET_CHANNEL=channel["id"], FLEET_OUTCOME="error")
        return {"channel": channel["name"], "outcome": "error", "error": str(error),
                "count": len(ids)}
    conn.execute(f"UPDATE notifications SET state = 'sent', sent_at = ?, message = ?,"  # noqa: S608
                 f" error = '', attempts = attempts + 1 WHERE id IN ({marks})",
                 (now, ids[0], *ids))
    journal.send(f"notification channel {channel['id']} ({channel['name']}): sent {len(ids)}", 6,
                 FLEET_CHANNEL=channel["id"], FLEET_OUTCOME="sent")
    return {"channel": channel["name"], "outcome": "sent", "count": len(ids)}


def deliver(ctx: Context, secrets: Path | None) -> dict:
    """Send what is due, channel by channel, until nothing is."""
    descriptor = os.open(paths.NOTIFY_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"busy": True, "channels": []}
        ctx.conn.execute("DELETE FROM notifications WHERE state != 'pending' AND created_at < ?",
                         (store.now() - KEEP,))
        results: list[dict] = []
        tried: set[str] = set()
        for _ in range(_PASSES):
            now = store.now()
            ready = ctx.conn.execute(
                "SELECT DISTINCT c.* FROM channels c JOIN notifications n ON n.channel_id = c.id"
                " WHERE c.enabled = 1 AND n.state = 'pending' AND n.next_at <= ?"
                " ORDER BY c.name", (now,)).fetchall()
            # A channel that just failed waits for its next time, not for the next pass.
            ready = [channel for channel in ready if channel["id"] not in tried]
            if not ready:
                break
            for channel in ready:
                result = _deliver_channel(ctx, channel, secrets, now)
                if result["outcome"] != "sent":
                    tried.add(channel["id"])
                results.append(result)
        return {"busy": False, "channels": results}
    finally:
        os.close(descriptor)


# --- commands: channels ------------------------------------------------------------

def _channel(ctx: Context, row) -> dict:
    counts = {state: 0 for state in STATES}
    for found in ctx.conn.execute("SELECT state, count(*) AS n FROM notifications"
                                  " WHERE channel_id = ? GROUP BY state", (row["id"],)):
        counts[found["state"]] = found["n"]
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"],
        "settings": store.loads(row["settings"], {}), "language": row["language"],
        "enabled": bool(row["enabled"]), "window": row["group_window"],
        "max_per_hour": row["max_per_hour"], "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        # Whether the secret is set; its value is never available here.
        "secret": secret_file(row["id"]).is_file(), "secret_name": secret_name(row["id"]),
        "secret_required": channels.SECRET_REQUIRED[row["kind"]],
        "pending": counts["pending"], "failed": counts["failed"],
    }


_CHANNEL_PARAMS = {
    "name": Str(required=True, max_length=200),
    "settings": Obj("Telegram: chat_id, server. Webhook: url (which may hold $secret), method,"
                    " template (JSON with $title, $text, $link, $count, \"$events\"), header."
                    " SMTP: host, port, security (starttls, tls, none), username, sender,"
                    " recipients. Matrix: homeserver, room_id"),
    "language": Choice(LANGUAGES, default="en"),
    "enabled": Bool(default=True),
    "window": Int(0, 3600, "Seconds to wait for more events, to send them as one message",
                  default=0),
    "max_per_hour": Int(1, 600, "Messages an hour at most; beyond, events wait and are grouped",
                        default=20),
}


def _channel_values(kind: str, params: Values) -> dict:
    return {
        "name": store.check_name(params["name"]),
        "settings": json.dumps(channels.check_settings(kind, params["settings"])),
        "language": params["language"], "enabled": int(params["enabled"]),
        "group_window": params["window"], "max_per_hour": params["max_per_hour"],
        "updated_at": store.now(),
    }


@command("channel.list", role="viewer")
def channel_list(ctx: Context, params: Values) -> dict:
    """The notification channels, with whether their secret is set; never its value."""
    rows = ctx.conn.execute("SELECT * FROM channels ORDER BY name COLLATE NOCASE").fetchall()
    return {"channels": [_channel(ctx, row) for row in rows],
            "kinds": list(channels.KINDS), "events": list(EVENTS),
            "check_events": list(CHECK_EVENTS), "system_events": list(SYSTEM_EVENTS)}


@command("channel.create", role="admin", mutating=True,
         params={"kind": Choice(channels.KINDS), **_CHANNEL_PARAMS})
def channel_create(ctx: Context, params: Values) -> dict:
    """Create a notification channel. Its secret is entered afterwards, by a person."""
    kind = params["kind"]
    channel_id = store.new_id("n")
    store.insert(ctx.conn, "channels", {"id": channel_id, "kind": kind,
                                        "created_at": store.now(),
                                        **_channel_values(kind, params)}, "channel")
    return _channel(ctx, store.fetch(ctx.conn, "channels", channel_id, "channel"))


@command("channel.update", role="admin", mutating=True,
         params={"id": Id(), **_CHANNEL_PARAMS})
def channel_update(ctx: Context, params: Values) -> dict:
    """Change a channel; its kind stays. Turned off, what it had to send is dropped."""
    channel_id = params["id"]
    row = store.fetch(ctx.conn, "channels", channel_id, "channel")
    store.update(ctx.conn, "channels", channel_id, _channel_values(row["kind"], params), "channel")
    if not params["enabled"]:
        ctx.conn.execute("UPDATE notifications SET state = 'failed', error = ?"
                         " WHERE channel_id = ? AND state = 'pending'",
                         ("the channel was turned off", channel_id))
    return _channel(ctx, store.fetch(ctx.conn, "channels", channel_id, "channel"))


@command("channel.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def channel_delete(ctx: Context, params: Values) -> dict:
    """Delete a channel whose secret has been deleted, with its rules and its notifications."""
    row = store.fetch(ctx.conn, "channels", params["id"], "channel")
    if secret_file(row["id"]).is_file():
        raise Conflict("delete the secret of this channel first")
    store.delete(ctx.conn, "channels", row["id"], "channel")
    return {}


@command("channel.test", role="admin", mutating=True,
         params={
             "id": Id(),
         })
def channel_test(ctx: Context, params: Values) -> dict:
    """Send a test message through a channel now; notification.list tells how it went."""
    row = store.fetch(ctx.conn, "channels", params["id"], "channel")
    if not row["enabled"]:
        raise Conflict("this channel is turned off")
    if channels.SECRET_REQUIRED[row["kind"]] and not secret_file(row["id"]).is_file():
        raise Conflict("the secret of this channel is not set")
    now = store.now()
    cursor = ctx.conn.execute(
        "INSERT INTO notifications (channel_id, event, payload, state, next_at, created_at)"
        " VALUES (?, 'test', ?, 'pending', ?, ?)",
        (row["id"], json.dumps({"event": "test", "title": row["name"]}), now, now))
    wake()
    return {"id": cursor.lastrowid}


# --- commands: rules -----------------------------------------------------------

def _rule(ctx: Context, row) -> dict:
    data = {key: row[key] for key in ("id", "name", "channel_id", "scope", "scope_id",
                                      "created_at")}
    data["group"] = row["scope_group"]
    data.update(events=store.loads(row["events"], []), enabled=bool(row["enabled"]))
    channel = ctx.conn.execute("SELECT name FROM channels WHERE id = ?",
                               (row["channel_id"],)).fetchone()
    data["channel"] = channel["name"] if channel else ""
    data["scope_name"] = ""
    if row["scope"] in _SCOPE_TABLES:
        found = ctx.conn.execute(f"SELECT name FROM {_SCOPE_TABLES[row['scope']]}"  # noqa: S608
                                 " WHERE id = ?", (row["scope_id"],)).fetchone()
        data["scope_name"] = found["name"] if found else ""
    return data


_RULE_PARAMS = {
    "name": Str(required=True, max_length=200),
    "channel_id": Id("The channel the notifications go through"),
    "events": Strs("What to tell about: the ends of a run, " + ", ".join(_ENDS)
                   + "; a run that waits for approval, approval"
                   + "; the states of checks, " + ", ".join(CHECK_EVENTS)
                   + "; check cycles that fall behind and catch up, late, caught_up"
                   + "; check cycles that cannot be prepared and can again,"
                   " unprepared, prepared",
                   max_items=16),
    "scope": Choice(SCOPES, "Everything, or the runs of one project, template or schedule,"
                    " the runs and checks of an inventory, the checks of a group of an"
                    " inventory, or one check", default="all"),
    "scope_id": Id("The project, template, schedule, inventory or check, for a scope other"
                   " than all; the inventory for a group", required=False),
    "group": Str("The group of the inventory, for the scope group", default="",
                 max_length=128),
    "enabled": Bool(default=True),
}


def _rule_values(ctx: Context, params: Values) -> dict:
    events = params["events"]
    known = (*EVENTS, *CHECK_EVENTS, *SYSTEM_EVENTS)
    if not events or any(event not in known for event in events):
        raise BadRequest(f"events must name at least one of: {', '.join(known)}")
    store.fetch(ctx.conn, "channels", params["channel_id"], "channel")
    scope, scope_id = params["scope"], params["scope_id"]
    if scope == "all" and scope_id is not None:
        raise BadRequest("scope_id means nothing for the scope all")
    if scope != "all":
        if scope_id is None:
            raise BadRequest(f"scope_id is required for the scope {scope}")
        store.fetch(ctx.conn, _SCOPE_TABLES[scope], scope_id,
                    "inventory" if scope == "group" else scope)
    if scope in _RUN_SCOPES and set(events) & set(CHECK_EVENTS):
        raise BadRequest(f"the scope {scope} has runs only: it cannot tell about checks")
    if scope in _CHECK_SCOPES and set(events) & set(EVENTS):
        raise BadRequest(f"the scope {scope} has checks only: it cannot tell about runs")
    if scope not in _SYSTEM_SCOPES and set(events) & set(SYSTEM_EVENTS):
        raise BadRequest(f"what happens to check cycles is told for everything or for an"
                         f" inventory, not for the scope {scope}")
    group = params["group"].strip()
    if (scope == "group") != bool(group):
        raise BadRequest("group is required for the scope group, and only for it")
    if group:
        check_group(group)
    return {"name": store.check_name(params["name"]), "channel_id": params["channel_id"],
            "events": json.dumps([event for event in known if event in events]),
            "scope": scope, "scope_id": scope_id if scope != "all" else None,
            "scope_group": group, "enabled": int(params["enabled"])}


@command("rule.list", role="viewer")
def rule_list(ctx: Context, params: Values) -> dict:
    """The rules that decide which runs are told about, and through which channel."""
    rows = ctx.conn.execute("SELECT * FROM notify_rules ORDER BY name COLLATE NOCASE").fetchall()
    return {"rules": [_rule(ctx, row) for row in rows]}


@command("rule.create", role="admin", mutating=True, params=_RULE_PARAMS)
def rule_create(ctx: Context, params: Values) -> dict:
    """Create a rule: runs that end in one of its events, within its scope, go to its channel."""
    rule_id = store.new_id("u")
    store.insert(ctx.conn, "notify_rules", {"id": rule_id, "created_at": store.now(),
                                            **_rule_values(ctx, params)}, "rule")
    return _rule(ctx, store.fetch(ctx.conn, "notify_rules", rule_id, "rule"))


@command("rule.update", role="admin", mutating=True, params={"id": Id(), **_RULE_PARAMS})
def rule_update(ctx: Context, params: Values) -> dict:
    """Change a rule."""
    store.fetch(ctx.conn, "notify_rules", params["id"], "rule")
    store.update(ctx.conn, "notify_rules", params["id"], _rule_values(ctx, params), "rule")
    return _rule(ctx, store.fetch(ctx.conn, "notify_rules", params["id"], "rule"))


@command("rule.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def rule_delete(ctx: Context, params: Values) -> dict:
    """Delete a rule; what it already put in the outbox is still sent."""
    store.delete(ctx.conn, "notify_rules", params["id"], "rule")
    return {}


# --- commands: the outbox ------------------------------------------------------------

def _notification(row) -> dict:
    payload = store.loads(row["payload"], {})
    return {key: row[key] for key in ("id", "channel_id", "event", "run_id", "host_id",
                                      "check_id", "state", "attempts", "next_at", "error",
                                      "created_at", "sent_at")} | {
        "channel": row["channel"], "title": payload.get("title", "")}


@command("notification.list", role="viewer",
         params={
             "channel_id": Id(required=False),
             "state": Choice(("", *STATES), default=""),
             "limit": Int(1, 500, default=100),
             "before": Int(1, 2**62),
         })
def notification_list(ctx: Context, params: Values) -> dict:
    """The notifications, newest first: sent, waiting to be tried again, or given up."""
    where, args = ["1 = 1"], []
    if params["channel_id"]:
        where.append("n.channel_id = ?")
        args.append(params["channel_id"])
    if params["state"]:
        where.append("n.state = ?")
        args.append(params["state"])
    if params["before"]:
        where.append("n.id < ?")
        args.append(params["before"])
    query = ("SELECT n.*, c.name AS channel FROM notifications n"  # noqa: S608
             " JOIN channels c ON c.id = n.channel_id"
             f" WHERE {' AND '.join(where)} ORDER BY n.id DESC LIMIT ?")
    rows = ctx.conn.execute(query, (*args, params["limit"])).fetchall()
    return {"notifications": [_notification(row) for row in rows],
            "more": len(rows) == params["limit"]}


@command("notification.retry", role="admin", mutating=True, agent="run",
         params={
             "id": Int(1, 2**62),
         })
def notification_retry(ctx: Context, params: Values) -> dict:
    """Try again, now, a notification that was given up."""
    row = ctx.conn.execute("SELECT * FROM notifications WHERE id = ?",
                           (params["id"],)).fetchone()
    if row is None:
        raise NotFound(f"no such notification: {params['id']}")
    if row["state"] != "failed":
        raise Conflict("only a notification that was given up can be tried again")
    channel = store.fetch(ctx.conn, "channels", row["channel_id"], "channel")
    if not channel["enabled"]:
        raise Conflict("its channel is turned off")
    now = store.now()
    # Counted from now, so that it is not given up again at the first failure.
    ctx.conn.execute("UPDATE notifications SET state = 'pending', attempts = 0, next_at = ?,"
                     " created_at = ?, error = '' WHERE id = ?", (now, now, row["id"]))
    wake()
    return {}


@command("notification.deliver", role="admin", tool=False)
def notification_deliver(ctx: Context, params: Values) -> dict:
    """Send what is due. Run by cockpit-fleet-notify.service, which has the
    secrets of the channels decrypted for it."""
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise Unavailable("notifications are delivered by cockpit-fleet-notify.service, which"
                          " alone has the secrets of the channels")
    return deliver(ctx, Path(directory))
