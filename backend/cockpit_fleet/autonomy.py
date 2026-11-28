"""What holds an agent that acts without asking: the limits of the mode
autonomous (profiles.AUTONOMOUS), the profile assisted with the questions
turned off by its person for a conversation (chat.unattended), or named so by
a fleet-mcp started by hand.

Nobody confirms its calls, so the backend is the only control left, and an
agent that reads what the nodes wrote can be steered by it. Within one
session it launches at most so many runs ([chat] autonomous_runs, or as
many as the person it acts for chose when the conversation began:
session_limits; and as many more as they allow from the chat page:
session_grants; counted from the audit trail), none on an inventory in
maintenance, and with
[chat] autonomous_dry_run only dry runs; it never silences a problem. It
changes the documents at most [chat] autonomous_writes times. A run
of a template that needs approval still waits for a person: an agent has no
right to approve.

A session is a fleet-mcp: it adds its identifier to every call, so an agent
cannot reset its count. The chat page draws one for each conversation and
gives it to fleet-mcp (--session), to follow the count and allow more;
started without, fleet-mcp draws its own. Starting another fleet-mcp, or
naming another session, starts another count, which is what whoever can
start one could do by hand with the same role. Counts and grants are each
user's: naming another user's session gains nothing.
"""

import re

from . import profiles, store
from .errors import Forbidden

SESSION_RE = re.compile(r"[0-9a-f]{16}")
# What starts work on the nodes: counted, and held off an inventory in maintenance.
LAUNCHES = ("run.start", "run.adhoc", "workflow.launch", "workflow.resume", "schedule.run",
            "check.run")
# What keeps a problem from being told.
SILENCERS = ("state.ack", "state.unack", "maintenance.create", "maintenance.delete")
# What launches for ever after, out of reach of the count of a session.
UNBOUNDED = ("schedule.enable",)
# What changes the documents (the class doc): counted apart from launches.
DOCUMENT_CHANGES = ("doc.write", "doc.mkdir", "doc.move", "doc.copy", "doc.delete",
                    "doc.restore", "doc.put")


def enabled(config) -> dict[str, bool]:
    """The modes without questions config.toml allows (profiles.mode)."""
    return {profiles.AUTONOMOUS: config.chat_autonomous,
            profiles.UNATTENDED: config.chat_unattended}


def mode(ctx, session: str | None, profile: str) -> str:
    """The mode without questions the caller turned on for an agent of theirs
    in PROFILE in SESSION (chat.unattended), while config.toml still allows
    it; "" when the person is asked."""
    found = profiles.mode(enabled(ctx.config), profile)
    if not (found and session):
        return ""
    row = ctx.conn.execute("SELECT 1 FROM unattended_sessions WHERE session = ? AND user = ?"
                           " AND profile = ?", (session, ctx.caller.user, profile)).fetchone()
    return found if row else ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _inventories(conn, name: str, params: dict) -> set[str]:
    """The inventories a launch reaches; empty when its parameters name
    nothing that exists, which the command itself then refuses."""
    if name == "run.start":
        if _text(params.get("inventory_id")):
            return {_text(params.get("inventory_id"))}
        rows = conn.execute("SELECT inventory_id FROM templates WHERE id = ?",
                            (_text(params.get("template_id")),))
    elif name == "run.adhoc":
        return {_text(params.get("inventory_id"))}
    elif name in ("workflow.launch", "workflow.resume"):
        if name == "workflow.launch" and _text(params.get("inventory_id")):
            return {_text(params.get("inventory_id"))}
        workflow = _text(params.get("id"))
        if name == "workflow.resume":
            row = conn.execute("SELECT workflow_id FROM runs WHERE id = ?", (workflow,)).fetchone()
            workflow = row["workflow_id"] if row and row["workflow_id"] else ""
        rows = conn.execute("SELECT inventory_id FROM workflows WHERE id = ?1"
                            " AND inventory_id IS NOT NULL UNION SELECT t.inventory_id"
                            " FROM workflow_templates w JOIN templates t ON t.id = w.template_id"
                            " WHERE w.workflow_id = ?1 AND NOT EXISTS (SELECT 1 FROM workflows"
                            " WHERE id = ?1 AND inventory_id IS NOT NULL)", (workflow,))
    elif name == "schedule.run":
        schedule = _text(params.get("id"))
        rows = conn.execute("SELECT s.inventory_id FROM schedules s WHERE s.id = ?1"
                            " AND s.inventory_id IS NOT NULL"
                            " UNION SELECT f.inventory_id FROM schedules s JOIN workflows f"
                            " ON f.id = s.workflow_id WHERE s.id = ?1"
                            " AND f.inventory_id IS NOT NULL"
                            " UNION SELECT t.inventory_id FROM schedules s"
                            " JOIN templates t ON t.id = s.template_id WHERE s.id = ?1"
                            " AND s.inventory_id IS NULL"
                            " UNION SELECT t.inventory_id FROM schedules s"
                            " JOIN workflows f ON f.id = s.workflow_id"
                            " JOIN workflow_templates w ON w.workflow_id = s.workflow_id"
                            " JOIN templates t ON t.id = w.template_id WHERE s.id = ?1"
                            " AND s.inventory_id IS NULL AND f.inventory_id IS NULL",
                            (schedule,))
    else:
        rows = conn.execute("SELECT inventory_id FROM checks WHERE id = ?",
                            (_text(params.get("id")),))
    # A template with no default inventory reaches none until one is chosen.
    return {row[0] for row in rows} - {None}


def launched(conn, session: str, user: str, commands: tuple[str, ...] = LAUNCHES) -> int:
    """How many launches of a user's session went through; or calls of
    other COMMANDS."""
    marks = ", ".join("?" * len(commands))
    return conn.execute(
        f"SELECT count(*) FROM audit WHERE session = ? AND user = ? AND outcome = 'ok'"  # noqa: S608
        f" AND command IN ({marks})", (session, user, *commands)).fetchone()[0]


def granted(conn, session: str, user: str) -> int:
    """How many launches beyond [chat] autonomous_runs a user allowed a session."""
    return conn.execute("SELECT coalesce(sum(runs), 0) FROM session_grants"
                        " WHERE session = ? AND user = ?", (session, user)).fetchone()[0]


def allowed(conn, config, session: str, user: str) -> int:
    """How many launches a user's session may make: the base the user chose
    for it, or [chat] autonomous_runs, and what they allowed since."""
    row = conn.execute("SELECT runs FROM session_limits WHERE session = ? AND user = ?",
                       (session, user)).fetchone()
    base = row[0] if row else config.chat_autonomous_runs
    return base + granted(conn, session, user)


def _hold_writes(ctx) -> None:
    """Refuse a change to the documents beyond [chat] autonomous_writes."""
    if not ctx.session:
        raise Forbidden("an agent acting without asking changes the documents only within a"
                        " session of fleet-mcp")
    limit = ctx.config.chat_autonomous_writes
    if launched(ctx.conn, ctx.session, ctx.caller.user, DOCUMENT_CHANGES) >= limit:
        raise Forbidden(f"this session has changed the documents as many times as it may:"
                        f" {limit} ([chat] autonomous_writes in config.toml); tell the person"
                        " you act for, and leave the rest to them")


def hold(ctx, name: str, params: dict) -> None:
    """Refuse a call of an autonomous agent that goes beyond its limits;
    with dry runs only, make a launch of a template one."""
    if name in SILENCERS:
        raise Forbidden("an agent acting without asking does not silence problems:"
                        " ask a person, or use a profile that asks")
    if name in UNBOUNDED:
        raise Forbidden("an agent acting without asking does not turn schedules on: what they"
                        " launch would not count against its limits; ask a person, or use"
                        " a profile that asks")
    if name in DOCUMENT_CHANGES:
        _hold_writes(ctx)
        return
    if name not in LAUNCHES:
        return
    config = ctx.config
    if not ctx.session:
        raise Forbidden("an agent acting without asking launches only within a session"
                        " of fleet-mcp")
    user = ctx.caller.user
    limit = allowed(ctx.conn, config, ctx.session, user)
    if launched(ctx.conn, ctx.session, user) >= limit:
        raise Forbidden(f"this session has launched as many runs as it may: {limit}"
                        " (as the person chose, or [chat] autonomous_runs in config.toml,"
                        " and those allowed since);"
                        " tell the person you act for, who can allow more from the chat page,"
                        " and wait for them")
    now = store.now()
    for inventory in _inventories(ctx.conn, name, params):
        if ctx.conn.execute("SELECT 1 FROM maintenances WHERE inventory_id = ?"
                            " AND starts_at <= ? AND ends_at > ?",
                            (inventory, now, now)).fetchone():
            raise Forbidden("the inventory is in maintenance: an agent acting without asking"
                            " launches nothing on it")
    if config.chat_autonomous_dry_run:
        if name == "run.start":
            params["check"] = True
        elif name != "check.run":
            raise Forbidden(f"an agent acting without asking makes dry runs only"
                            f" ([chat] autonomous_dry_run in config.toml): {name} has none")
