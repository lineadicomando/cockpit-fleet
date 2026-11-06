"""What holds an agent that acts without asking: the limits of a profile of
the assistant that is autonomous (profiles.AUTONOMOUS).

Nobody confirms its calls, so the backend is the only control left, and an
agent that reads what the nodes wrote can be steered by it. Within one
session it launches at most so many runs ([chat] autonomous_runs, counted
from the audit trail), none on an inventory in maintenance, and with
[chat] autonomous_dry_run only dry runs; it never silences a problem. A run
of a template that needs approval still waits for a person: an agent has no
right to approve.

A session is a fleet-mcp: it draws its identifier when it starts and adds it
to every call, so an agent cannot reset its count. Starting another
fleet-mcp starts another session, which is what whoever can start one could
do by hand with the same role.
"""

import re

from . import store
from .errors import Forbidden

SESSION_RE = re.compile(r"[0-9a-f]{16}")
# What starts work on the nodes: counted, and held off an inventory in maintenance.
LAUNCHES = ("run.start", "run.adhoc", "workflow.launch", "workflow.resume", "schedule.run",
            "check.run")
# What keeps a problem from being told.
SILENCERS = ("state.ack", "state.unack", "maintenance.create", "maintenance.delete")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _inventories(conn, name: str, params: dict) -> set[str]:
    """The inventories a launch reaches; empty when its parameters name
    nothing that exists, which the command itself then refuses."""
    if name == "run.start":
        rows = conn.execute("SELECT inventory_id FROM templates WHERE id = ?",
                            (_text(params.get("template_id")),))
    elif name == "run.adhoc":
        return {_text(params.get("inventory_id"))}
    elif name in ("workflow.launch", "workflow.resume"):
        workflow = _text(params.get("id"))
        if name == "workflow.resume":
            row = conn.execute("SELECT workflow_id FROM runs WHERE id = ?", (workflow,)).fetchone()
            workflow = row["workflow_id"] if row and row["workflow_id"] else ""
        rows = conn.execute("SELECT t.inventory_id FROM workflow_templates w"
                            " JOIN templates t ON t.id = w.template_id WHERE w.workflow_id = ?",
                            (workflow,))
    elif name == "schedule.run":
        rows = conn.execute("SELECT t.inventory_id FROM schedules s"
                            " JOIN templates t ON t.id = s.template_id WHERE s.id = ?",
                            (_text(params.get("id")),))
    else:
        rows = conn.execute("SELECT inventory_id FROM checks WHERE id = ?",
                            (_text(params.get("id")),))
    return {row[0] for row in rows}


def launched(conn, session: str) -> int:
    """How many launches of a session went through."""
    marks = ", ".join("?" * len(LAUNCHES))
    return conn.execute(
        f"SELECT count(*) FROM audit WHERE session = ? AND outcome = 'ok'"  # noqa: S608
        f" AND command IN ({marks})", (session, *LAUNCHES)).fetchone()[0]


def hold(ctx, name: str, params: dict) -> None:
    """Refuse a call of an autonomous agent that goes beyond its limits;
    with dry runs only, make a launch of a template one."""
    if name in SILENCERS:
        raise Forbidden("an agent acting without asking does not silence problems:"
                        " ask a person, or use a profile that asks")
    if name not in LAUNCHES:
        return
    config = ctx.config
    if not ctx.session:
        raise Forbidden("an agent acting without asking launches only within a session"
                        " of fleet-mcp")
    limit = config.chat_autonomous_runs
    if launched(ctx.conn, ctx.session) >= limit:
        raise Forbidden("this session has launched as many runs as an agent acting without"
                        f" asking may: {limit} ([chat] autonomous_runs in config.toml)")
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
