"""Approvals: a run of a template, or a step of a workflow, that needs one
waits until someone lets it start.

The run is prepared when it is launched, as any other: its copy of the
project, the ciphertext of its secrets, its spec. What is approved is then
exactly what will run, whatever changes in the template or the project
meanwhile. It waits as pending_approval, which dispatch() never starts;
approving it queues it, rejecting it or letting it expire ends it and
removes what was prepared.

A template or a step says how (MODES): "none"; "confirm", a checkpoint
that the user who launched the run may pass too, without the right to
approve; "other", another person. Who else may decide: an administrator, or
a member of cockpit-fleet-approver, whatever their role; in "other" not the
user who launched it, unless an administrator and [approvals] admins_exempt.
A run a schedule launches has nobody who launched it at hand: in "confirm"
it waits as in "other". An agent never decides, whatever role it acts with,
not even on a run it launched.
"""

from . import jobs, journal, notify, store
from .auth import role_at_least
from .cli import Choice, Context, Id, Int, Str, Values, command, is_agent
from .errors import BadRequest, Conflict, Forbidden

MODES = ("none", "confirm", "other")


def strongest(*modes: str) -> str:
    """The mode that asks the most of the ones given."""
    return max(modes, key=MODES.index)


def mode_of(item: dict, what: str) -> str:
    """The mode a template or a step is given: "approval", or the older
    "needs_approval", which meant another person. Both, they must agree."""
    mode = item.get("approval")
    legacy = item.get("needs_approval")
    if mode is not None and mode not in MODES:
        raise BadRequest(f"approval of {what} is one of: {', '.join(MODES)}")
    if legacy is not None and not isinstance(legacy, bool):
        raise BadRequest(f"needs_approval of {what} is true or false")
    if legacy is None:
        return mode or "none"
    if mode is not None and (mode != "none") != legacy:
        raise BadRequest(f"approval and needs_approval of {what} disagree: give approval only")
    return mode or ("other" if legacy else "none")


def request(ctx: Context, run_id: str, mode: str) -> None:
    """Record that a run just queued as pending_approval waits, in MODE, and
    tell about it."""
    now = store.now()
    run = store.fetch(ctx.conn, "runs", run_id, "run")
    if run["schedule_id"]:
        mode = "other"
    ctx.conn.execute(
        "INSERT INTO approvals (run_id, requested_by, requested_at, expires_at, mode)"
        " VALUES (?, ?, ?, ?, ?)",
        (run_id, run["created_by"], now, now + ctx.config.approvals_expire_hours * 3600, mode))
    journal.send(f"run {run_id}: waits for approval", 5, FLEET_RUN=run_id)
    notify.run_waits(ctx, run_id)


def _row(row) -> dict:
    return {key: row[key] for key in ("run_id", "requested_by", "requested_at", "expires_at",
                                      "decision", "decided_by", "decided_at", "comment",
                                      "mode")}


def of_run(ctx: Context, run_id: str) -> dict | None:
    row = ctx.conn.execute("SELECT * FROM approvals WHERE run_id = ?", (run_id,)).fetchone()
    return _row(row) if row else None


def may_decide(ctx: Context, approval: dict) -> bool:
    """Whether the caller may approve or reject a run that waits so."""
    caller = ctx.caller
    if is_agent(ctx.via) or not approval:
        return False
    own = caller.user == approval["requested_by"]
    if own and approval["mode"] == "confirm":
        return True
    if role_at_least(caller.role, "admin"):
        return not own or ctx.config.approvals_admins_exempt
    return "approve" in caller.rights and not own


def expire(ctx: Context) -> int:
    """End the runs whose time to be approved is over; called by the tick."""
    now = store.now()
    # One canceled while it waited keeps its decision empty: it is not expired.
    due = ctx.conn.execute("SELECT a.run_id FROM approvals a JOIN runs r ON r.id = a.run_id"
                           " WHERE a.decision = '' AND a.expires_at <= ? AND r.status = ?",
                           (now, jobs.WAITING)).fetchall()
    return sum(_decide(ctx, row["run_id"], "expired", "system", "") for row in due)


def _decide(ctx: Context, run_id: str, decision: str, user: str, comment: str) -> bool:
    """Claim a waiting run for a decision; False when another got there first."""
    conn = ctx.conn
    status = {"approved": "queued", "rejected": "rejected", "expired": "expired"}[decision]
    conn.execute("BEGIN IMMEDIATE")
    try:
        claimed = conn.execute(
            "UPDATE approvals SET decision = ?, decided_by = ?, decided_at = ?, comment = ?"
            " WHERE run_id = ? AND decision = ''",
            (decision, user, store.now(), comment, run_id)).rowcount
        if claimed and decision == "approved":
            conn.execute("UPDATE runs SET status = 'queued' WHERE id = ? AND status = ?",
                         (run_id, jobs.WAITING))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if not claimed:
        return False
    run = store.fetch(conn, "runs", run_id, "run")
    journal.send(f"run {run_id}: {decision}" + (f" by {user}" if decision != "expired" else ""),
                 6 if decision == "approved" else 5, FLEET_RUN=run_id,
                 FLEET_DECISION=decision)
    if decision == "approved":
        jobs.dispatch(ctx)
    elif run["status"] == jobs.WAITING:
        error = (f"rejected by {user}" + (f": {comment}" if comment else "")
                 if decision == "rejected" else "not approved in time")
        jobs.end_unstarted(ctx, run_id, status, error)
    return True


def _waiting(ctx: Context, run_id: str):
    run = store.fetch(ctx.conn, "runs", run_id, "run")
    approval = of_run(ctx, run_id)
    if approval is None or run["status"] != jobs.WAITING or approval["decision"]:
        raise Conflict("this run is not waiting for an approval")
    if approval["expires_at"] <= store.now():
        _decide(ctx, run_id, "expired", "system", "")
        raise Conflict("the time to approve this run is over: it expired")
    if not may_decide(ctx, approval):
        if is_agent(ctx.via):
            raise Forbidden("an agent cannot approve or reject a run: a person must")
        if ctx.caller.user == approval["requested_by"]:
            raise Forbidden("you launched this run: another user must approve it")
        raise Forbidden("approving a run takes an administrator or a member of"
                        " cockpit-fleet-approver")
    return run


_DECIDE_PARAMS = {
    "id": Id("The run that waits"),
    "comment": Str("Why, kept with the decision", default="", max_length=500),
}


@command("approval.approve", role="operator", right="approve", mutating=True, tool=False,
         params=_DECIDE_PARAMS)
def approval_approve(ctx: Context, params: Values) -> dict:
    """Let a run that waits for approval start. Who launched it may when it
    waits for a confirmation; otherwise not, unless an administrator."""
    _waiting(ctx, params["id"])
    if not _decide(ctx, params["id"], "approved", ctx.caller.user, params["comment"].strip()):
        raise Conflict("this run was decided meanwhile")
    return _entry(ctx, store.fetch(ctx.conn, "runs", params["id"], "run"))


@command("approval.reject", role="operator", right="approve", mutating=True, tool=False,
         params=_DECIDE_PARAMS)
def approval_reject(ctx: Context, params: Values) -> dict:
    """Refuse a run that waits for approval: it ends without starting."""
    _waiting(ctx, params["id"])
    if not _decide(ctx, params["id"], "rejected", ctx.caller.user, params["comment"].strip()):
        raise Conflict("this run was decided meanwhile")
    return _entry(ctx, store.fetch(ctx.conn, "runs", params["id"], "run"))


def _entry(ctx: Context, run) -> dict:
    """A run with its approval and what is needed to decide on it."""
    from .runs import run_row  # it builds on this module

    approval = of_run(ctx, run["id"]) or {}

    def name(table: str, row_id: str | None) -> str:
        row = ctx.conn.execute(f"SELECT name FROM {table} WHERE id = ?",  # noqa: S608
                               (row_id,)).fetchone()
        return row["name"] if row else ""

    return {**run_row(run), "approval": approval,
            "template": name("templates", run["template_id"]),
            "project": name("projects", run["project_id"]),
            "inventory": name("inventories", run["inventory_id"]),
            "can_decide": (run["status"] == jobs.WAITING and not approval.get("decision")
                           and may_decide(ctx, approval)),
            }


@command("approval.list", role="viewer", right="approve",
         params={
             "state": Choice(("pending", "decided"), "The runs that wait, or the last ones"
                             " decided", default="pending"),
             "limit": Int(1, 200, default=50),
         })
def approval_list(ctx: Context, params: Values) -> dict:
    """The runs that wait for an approval, oldest first, with what they would
    run (limit, dry run, answers of the survey) and whether the caller may
    decide on them; or the last decisions, newest first."""
    expire(ctx)
    if params["state"] == "pending":
        rows = ctx.conn.execute(
            "SELECT r.* FROM approvals a JOIN runs r ON r.id = a.run_id"
            " WHERE a.decision = '' AND r.status = ? ORDER BY a.requested_at LIMIT ?",
            (jobs.WAITING, params["limit"])).fetchall()
    else:
        rows = ctx.conn.execute(
            "SELECT r.* FROM approvals a JOIN runs r ON r.id = a.run_id"
            " WHERE a.decision != '' OR r.status != ?"
            " ORDER BY coalesce(a.decided_at, r.finished_at) DESC LIMIT ?",
            (jobs.WAITING, params["limit"])).fetchall()
    return {"approvals": [_entry(ctx, row) for row in rows]}
