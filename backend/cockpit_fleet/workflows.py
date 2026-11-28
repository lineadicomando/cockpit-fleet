"""Workflows: job templates chained into a graph, each step started by how
the steps before it ended.

A workflow is a set of nodes, each a template with a limit that narrows it
and answers to its survey, and edges between them that say when the child
runs: when the parent succeeds, fails, or always. Launching one records a
run of kind workflow, which has no unit of its own: it holds a copy of the
graph, so that changing the workflow does not change what is running, and
its steps are runs of their templates that refer to it.

There is no daemon here either. advance() looks at the steps of a workflow
run and starts those whose time has come; it is called when a step ends
(jobs._finish), when the workflow is launched, and from reconcile(), so a
workflow left behind by a crash is taken up again by the tick.

What a step passes on: the variables a playbook set with set_stats
(aggregated, not per host), which the callback writes with the stats of the
run and _collect keeps as the run's artifacts. A step gets those of the
steps before it in the graph, the latest last, as extra variables tagged
unsafe: above the template's variables and the answers of its node.

A workflow may name a project, an inventory or both: its steps are then
templates of that project, and every step runs on that inventory, which
each template must allow. A run holds a copy of that too, and checks each
step again as it starts it. One that names no inventory may be given one
at launch, among those every step allows; without, each step runs on its
template's default.
"""

import fcntl
import json
import os

from . import approvals, jobs, journal, runs, store, survey
from .cli import Context, Id, Objs, Str, Values, command
from .errors import BadRequest, Conflict, FleetError
from .names import check_id
from .resources import allows
from .validators import check_limit

MAX_NODES = 50
MAX_EDGES = 200
CONDITIONS = ("success", "failure", "always")
# How a step ended, for the edges that leave it.
_ENDED_WELL = ("successful",)
_ENDED_BADLY = ("failed", "error", "rejected", "expired", "canceled")
_ACTIVE = (jobs.WAITING, *jobs.ACTIVE)
# Statuses a workflow run may be resumed from.
RESUMABLE = ("failed", "error", "canceled")
# What a workflow may take all its steps from: column, table, what it is.
_SCOPE = (("project_id", "projects", "project"), ("inventory_id", "inventories", "inventory"))

# Workflow runs being advanced by this process: a step that ends while one is
# (its unit could not start) asks for another pass instead of waiting for a
# lock this process already holds.
_HELD: set[str] = set()
_AGAIN: set[str] = set()


# --- the graph -------------------------------------------------------------------

def scope_of(row) -> dict:
    """The project and the inventory a workflow takes its steps from, those it has."""
    return {column: row[column] for column, _, _ in _SCOPE if row[column]}


def check_scope(conn, template, scope: dict) -> None:
    """Refuse a template that is not of the project of a workflow, or that
    does not allow its inventory."""
    if scope.get("project_id") and template["project_id"] != scope["project_id"]:
        raise BadRequest(f"the job template {template['name']} is not of the project"
                         " this workflow takes its steps from")
    if scope.get("inventory_id") and not allows(conn, template, scope["inventory_id"]):
        raise BadRequest(f"the job template {template['name']} is not launched on the"
                         " inventory this workflow runs on")


def run_inventory(ctx: Context, workflow, nodes: list[dict],
                  inventory_id: str | None) -> str | None:
    """The inventory every step of a run of WORKFLOW runs on: its own, or
    the one chosen at launch, which every step must allow; None: each
    step's default."""
    if workflow["inventory_id"]:
        if inventory_id and inventory_id != workflow["inventory_id"]:
            raise Conflict(f"the workflow {workflow['name']} runs on its own inventory")
        return workflow["inventory_id"]
    if not inventory_id:
        lacking = _lacking(ctx.conn, nodes)
        if lacking is not None:
            raise BadRequest(f"the step {lacking['name'] or lacking['id']} of the workflow"
                             f" {workflow['name']} has no default inventory: choose one")
        return None
    inventory = store.usable(store.fetch(ctx.conn, "inventories", inventory_id, "inventory"),
                             "inventory")
    for node in nodes:
        template = store.fetch(ctx.conn, "templates", node["template_id"], "template")
        if not allows(ctx.conn, template, inventory_id):
            raise Conflict(f"the step {node['name'] or node['id']} of the workflow"
                           f" {workflow['name']} is not launched on the inventory"
                           f" {inventory['name']}")
    return inventory_id


def choices(conn, nodes: list[dict]) -> list[str] | None:
    """The inventories every step allows, by id; None: any."""
    allowed: set[str] | None = None
    for node in nodes:
        template = conn.execute("SELECT * FROM templates WHERE id = ?",
                                (node["template_id"],)).fetchone()
        if template is None:
            return []
        if template["any_inventory"]:
            continue
        mine = {template["inventory_id"], *(row[0] for row in conn.execute(
            "SELECT inventory_id FROM template_inventories WHERE template_id = ?",
            (template["id"],)))} - {None}
        allowed = mine if allowed is None else allowed & mine
    return None if allowed is None else sorted(allowed)


def _check_nodes(ctx: Context, nodes: list[dict], scope: dict) -> list[dict]:
    if not nodes:
        raise BadRequest("a workflow needs at least one step")
    if len(nodes) > MAX_NODES:
        raise BadRequest(f"a workflow has at most {MAX_NODES} steps")
    seen: set[str] = set()
    checked = []
    for node in nodes:
        unknown = set(node) - {"id", "name", "template_id", "limit", "answers", "approval",
                               "needs_approval"}
        if unknown:
            raise BadRequest(f"unknown key in a step: {', '.join(sorted(unknown))}")
        try:
            node_id = check_id(node.get("id"), "step id")
            template_id = check_id(node.get("template_id"), "template id")
        except ValueError as error:
            raise BadRequest(str(error)) from error
        if node_id in seen:
            raise BadRequest(f"two steps have the id {node_id}")
        seen.add(node_id)
        name = node.get("name", "")
        if not isinstance(name, str) or len(name) > 200 or "\x00" in name:
            raise BadRequest("the name of a step is a string of at most 200 characters")
        limit = node.get("limit", "")
        if not isinstance(limit, str):
            raise BadRequest("the limit of a step is a host pattern")
        approval = approvals.mode_of(node, "a step")
        answers = node.get("answers", {})
        if not isinstance(answers, dict):
            raise BadRequest("the answers of a step are an object")
        template = store.fetch(ctx.conn, "templates", template_id, "template")
        check_scope(ctx.conn, template, scope)
        checked.append({
            "id": node_id, "name": name.strip(), "template_id": template_id,
            "limit": check_limit(limit) if limit.strip() else "",
            "answers": survey.stored_answers(template, answers, "a step of a workflow",
                                              "a workflow"),
            "approval": approval,
        })
    return checked


def step_mode(node: dict) -> str:
    """The mode of approval of a stored step; one stored before modes says
    needs_approval."""
    return node.get("approval") or ("other" if node.get("needs_approval") else "none")


def node_out(node: dict) -> dict:
    """A stored step as commands give it: with needs_approval too, as it was
    given before approval had modes."""
    mode = step_mode(node)
    return {**node, "approval": mode, "needs_approval": mode != "none"}


def _check_edges(nodes: list[dict], edges: list[dict]) -> list[dict]:
    if len(edges) > MAX_EDGES:
        raise BadRequest(f"a workflow has at most {MAX_EDGES} links between steps")
    ids = {node["id"] for node in nodes}
    seen: set[tuple[str, str]] = set()
    checked = []
    for edge in edges:
        unknown = set(edge) - {"from", "to", "condition"}
        if unknown:
            raise BadRequest(f"unknown key in a link: {', '.join(sorted(unknown))}")
        parent, child, condition = edge.get("from"), edge.get("to"), edge.get("condition")
        if not isinstance(parent, str) or not isinstance(child, str) or \
                parent not in ids or child not in ids:
            raise BadRequest("a link joins two steps of the workflow")
        if parent == child:
            raise BadRequest("a step cannot follow itself")
        if condition not in CONDITIONS:
            raise BadRequest(f"the condition of a link is one of: {', '.join(CONDITIONS)}")
        if (parent, child) in seen:
            raise BadRequest(f"{parent} and {child} are linked twice")
        seen.add((parent, child))
        checked.append({"from": parent, "to": child, "condition": condition})
    # No cycle: each step runs at most once.
    incoming = {node_id: 0 for node_id in ids}
    for edge in checked:
        incoming[edge["to"]] += 1
    ready = [node_id for node_id, count in incoming.items() if count == 0]
    reached = 0
    while ready:
        current = ready.pop()
        reached += 1
        for edge in checked:
            if edge["from"] == current:
                incoming[edge["to"]] -= 1
                if incoming[edge["to"]] == 0:
                    ready.append(edge["to"])
    if reached != len(ids):
        raise BadRequest("the links make a loop: a step would come after itself")
    return checked


# The last run of a workflow on each inventory: the one chosen at launch or its
# own, "" for each step's own.
_LAST_RUNS = ("SELECT r.id, r.status, r.created_at, m.inventory FROM runs r"
              " JOIN (SELECT MAX(rowid) AS last, coalesce(json_extract(options, '$.inventory'),"
              " json_extract(options, '$.scope.inventory_id'), '') AS inventory"
              " FROM runs WHERE workflow_id = ? AND kind = 'workflow' GROUP BY inventory) m"
              " ON r.rowid = m.last")


def _workflow(ctx: Context, row) -> dict:
    last = ctx.conn.execute(
        "SELECT id, status, created_at FROM runs WHERE workflow_id = ? AND kind = 'workflow'"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1", (row["id"],)).fetchone()
    last_runs = {one["inventory"]: {key: one[key] for key in ("id", "status", "created_at")}
                 for one in ctx.conn.execute(_LAST_RUNS, (row["id"],)).fetchall()}
    return {"id": row["id"], "name": row["name"], "description": row["description"],
            "project_id": row["project_id"], "inventory_id": row["inventory_id"],
            "inventory_ids": _offered(ctx.conn, row),
            "needs_inventory": not row["inventory_id"] and _lacking(ctx.conn, store.loads(
                row["nodes"], [])) is not None,
            "nodes": [node_out(node) for node in store.loads(row["nodes"], [])],
            "edges": store.loads(row["edges"], []), "created_at": row["created_at"],
            "updated_at": row["updated_at"], "last_run": dict(last) if last else None,
            "last_runs": last_runs}


def _offered(conn, row) -> list[str] | None:
    """The inventories a launch of a workflow may choose, by id; None: any."""
    if row["inventory_id"]:
        return [row["inventory_id"]]
    return choices(conn, store.loads(row["nodes"], []))


_WORKFLOW_PARAMS = {
    "name": Str(required=True, max_length=200),
    "description": Str(default="", max_length=2000),
    "project_id": Id("If given, every step is a job template of this project",
                     required=False),
    "inventory_id": Id("If given, every step runs on this inventory, which its job template"
                       " must allow; if not, one may be chosen at launch",
                       required=False),
    "nodes": Objs("The steps: id (a-z, 0-9, - and _), name, template_id, limit (a host"
                  " pattern that narrows the template's), answers (to its survey, no secret),"
                  " approval (none, confirm or other: the step waits for an approval in"
                  " this mode, or in its template's when that asks more; needs_approval,"
                  " deprecated, true is other)", max_items=MAX_NODES),
    "edges": Objs("The links: from and to (ids of steps), condition (success, failure or"
                  " always: when the step from ends so, the step to runs). A step with no"
                  " link to it runs first; one with several runs once all of them have"
                  " ended, if one of their conditions holds", max_items=MAX_EDGES),
}


def _values(ctx: Context, params: Values) -> dict:
    scope = {}
    for column, table, what in _SCOPE:
        scope[column] = params[column] or None
        if scope[column]:
            store.usable(store.fetch(ctx.conn, table, scope[column], what), what)
    nodes = _check_nodes(ctx, params["nodes"], scope)
    edges = _check_edges(nodes, params["edges"])
    return {"name": store.check_name(params["name"]),
            "description": params["description"].strip(), **scope,
            "nodes": json.dumps(nodes), "edges": json.dumps(edges), "updated_at": store.now()}


@command("workflow.list", role="viewer")
def workflow_list(ctx: Context, params: Values) -> dict:
    """The workflows: their steps, the links between them, their last run, and in
    last_runs their last on each inventory, by inventory id ("" for a run on each
    step's own)."""
    rows = ctx.conn.execute("SELECT * FROM workflows ORDER BY name COLLATE NOCASE").fetchall()
    return {"workflows": [_workflow(ctx, row) for row in rows]}


@command("workflow.create", role="admin", mutating=True, params=_WORKFLOW_PARAMS)
def workflow_create(ctx: Context, params: Values) -> dict:
    """Create a workflow: job templates run one after the other, by how each
    ends. With a project, only its templates are steps; with an inventory,
    every step runs there, and only templates that allow it are steps."""
    workflow_id = store.new_id("w")
    store.insert(ctx.conn, "workflows", {"id": workflow_id, "created_at": store.now(),
                                         **_values(ctx, params)}, "workflow")
    _sync_uses(ctx, workflow_id)
    return _workflow(ctx, store.fetch(ctx.conn, "workflows", workflow_id, "workflow"))


@command("workflow.update", role="admin", mutating=True,
         params={"id": Id(), **_WORKFLOW_PARAMS})
def workflow_update(ctx: Context, params: Values) -> dict:
    """Change a workflow; its runs under way go on with the steps they started with."""
    store.fetch(ctx.conn, "workflows", params["id"], "workflow")
    values = _values(ctx, params)
    if values["inventory_id"] is None:
        _keep_schedules(ctx, params["id"], store.loads(values["nodes"], []))
    store.update(ctx.conn, "workflows", params["id"], values, "workflow")
    _sync_uses(ctx, params["id"])
    return _workflow(ctx, store.fetch(ctx.conn, "workflows", params["id"], "workflow"))


@command("workflow.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def workflow_delete(ctx: Context, params: Values) -> dict:
    """Delete a workflow; its past runs stay."""
    store.delete(ctx.conn, "workflows", params["id"], "workflow")
    return {}


def _lacking(conn, nodes: list[dict]) -> dict | None:
    """The first step whose template has no default inventory, if any."""
    return next((node for node in nodes if conn.execute(
        "SELECT 1 FROM templates WHERE id = ? AND inventory_id IS NULL",
        (node["template_id"],)).fetchone()), None)


def _keep_schedules(ctx: Context, workflow_id: str, nodes: list[dict]) -> None:
    """Refuse a step with no default inventory while a schedule launches the
    workflow, naming no inventory, on each step's default."""
    lacking = _lacking(ctx.conn, nodes)
    if lacking is None:
        return
    names = [row[0] for row in ctx.conn.execute(
        "SELECT name FROM schedules WHERE workflow_id = ? AND inventory_id IS NULL"
        " ORDER BY name COLLATE NOCASE", (workflow_id,))]
    if names:
        raise Conflict(f"the step {lacking['name'] or lacking['id']} has no default"
                       " inventory, and schedules launch this workflow on each step's: "
                       + ", ".join(names[:5]) + (" and others" if len(names) > 5 else "")
                       + "; give them an inventory first")


def _sync_uses(ctx: Context, workflow_id: str) -> None:
    """Which templates a workflow uses: a template in use cannot be deleted."""
    row = store.fetch(ctx.conn, "workflows", workflow_id, "workflow")
    ctx.conn.execute("DELETE FROM workflow_templates WHERE workflow_id = ?", (workflow_id,))
    for template_id in sorted({node["template_id"] for node in store.loads(row["nodes"], [])}):
        ctx.conn.execute("INSERT INTO workflow_templates (workflow_id, template_id)"
                         " VALUES (?, ?)", (workflow_id, template_id))


# --- runs of a workflow ------------------------------------------------------------

def unattended(ctx: Context, nodes: list[dict]) -> None:
    """Refuse steps that would wait for a person: a schedule launches only a
    workflow that runs from start to end with nobody to approve."""
    for node in nodes:
        template = ctx.conn.execute("SELECT name, approval FROM templates WHERE id = ?",
                                    (node["template_id"],)).fetchone()
        mode = approvals.strongest(step_mode(node),
                                   template["approval"] if template else "none")
        if mode != "none":
            label = node.get("name") or (template["name"] if template else node["id"])
            raise BadRequest(f"the step {label} waits for an approval: a schedule launches"
                             " only a workflow whose steps need none")


def _start(ctx: Context, workflow, options: dict, schedule=None,
           created_by: str | None = None) -> dict:
    run_id = store.new_id("r")
    jobs.create_dir(run_id)
    run = {"id": run_id, "kind": "workflow", "status": "running", "title": workflow["name"],
           "workflow_id": workflow["id"], "created_by": created_by or ctx.caller.user,
           "created_at": store.now(), "started_at": store.now(),
           "options": json.dumps(options), "session": ctx.session}
    if schedule is not None:
        run.update(schedule_id=schedule["id"], priority=schedule["priority"])
    store.insert(ctx.conn, "runs", run, "run")
    journal.send(f"workflow run {run_id} ({workflow['name']}): started", 6, FLEET_RUN=run_id)
    advance(ctx, run_id)
    return runs.run_row(store.fetch(ctx.conn, "runs", run_id, "run"))


@command("workflow.launch", role="operator", mutating=True,
         params={
             "id": Id(),
             "inventory_id": Id("For a workflow that names no inventory: the one every step"
                                " runs on, among those all of them allow; left out, each"
                                " step runs on its template's default", required=False),
         })
def workflow_launch(ctx: Context, params: Values) -> dict:
    """Launch a workflow: its first steps start now, the others as the steps
    before them end. A step whose template needs approval waits for it."""
    workflow = store.fetch(ctx.conn, "workflows", params["id"], "workflow")
    return launch(ctx, workflow, inventory_id=params["inventory_id"])


def launch(ctx: Context, workflow, schedule=None, created_by: str | None = None,
           inventory_id: str | None = None) -> dict:
    """Start a run of a workflow: what workflow.launch does, and what a
    schedule does at its time, after checking that no step needs approval."""
    nodes = store.loads(workflow["nodes"], [])
    if schedule is not None:
        unattended(ctx, nodes)
    options = {"nodes": nodes, "edges": store.loads(workflow["edges"], []),
               "scope": scope_of(workflow)}
    chosen = run_inventory(ctx, workflow, nodes, inventory_id)
    if chosen and not workflow["inventory_id"]:
        options["inventory"] = chosen
    return _start(ctx, workflow, options, schedule, created_by)


@command("workflow.resume", role="operator", mutating=True,
         params={
             "id": Id("A workflow run that failed or was canceled"),
         })
def workflow_resume(ctx: Context, params: Values) -> dict:
    """Run a workflow again from where a run of it stopped: a new run, with
    the same steps as that one, in which the steps that succeeded there are
    taken as done, with what they passed on, and the others run."""
    old = store.fetch(ctx.conn, "runs", params["id"], "run")
    if old["kind"] != "workflow":
        raise BadRequest("this run is not a workflow run")
    if old["status"] not in RESUMABLE:
        raise Conflict("only a workflow run that failed or was canceled can be resumed")
    if old["workflow_id"] is None:
        raise Conflict("this workflow was deleted")
    workflow = store.fetch(ctx.conn, "workflows", old["workflow_id"], "workflow")
    options = store.loads(old["options"], {})
    done = {node: row["id"] for node, row in _steps(ctx, old["id"], options).items()
            if row["status"] in _ENDED_WELL}
    return _start(ctx, workflow, {"nodes": options.get("nodes", []),
                                  "edges": options.get("edges", []),
                                  "scope": options.get("scope", {}),
                                  **({"inventory": options["inventory"]}
                                     if options.get("inventory") else {}),
                                  "resumed_from": old["id"], "carried": done})


def _steps(ctx: Context, run_id: str, options: dict) -> dict:
    """The latest run of each step of a workflow run, those carried from the
    run it resumes included."""
    found = {}
    for node, step_id in options.get("carried", {}).items():
        row = ctx.conn.execute("SELECT * FROM runs WHERE id = ?", (step_id,)).fetchone()
        if row is not None:
            found[node] = row
    for row in ctx.conn.execute("SELECT * FROM runs WHERE workflow_run_id = ?"
                                " ORDER BY created_at", (run_id,)).fetchall():
        found[row["workflow_node"]] = row
    return found


def _state(row) -> str:
    if row["status"] in _ACTIVE:
        return "active"
    return "success" if row["status"] in _ENDED_WELL else "failure"


def _fires(condition: str, state: str) -> bool:
    return condition == "always" or (condition == "success") == (state == "success")


def plan(options: dict, steps: dict, failures: dict) -> dict[str, str]:
    """The state of every step: active, success, failure (ended, or could not
    start), ready (to start now), skipped (none of its links held), or
    waiting (for steps before it)."""
    nodes = [node["id"] for node in options.get("nodes", [])]
    parents: dict[str, list[tuple[str, str]]] = {node: [] for node in nodes}
    for edge in options.get("edges", []):
        parents[edge["to"]].append((edge["from"], edge["condition"]))
    states = {node: _state(steps[node]) for node in nodes if node in steps}
    states.update({node: "failure" for node in failures if node not in states})
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node in states:
                continue
            before = parents[node]
            if not before:
                states[node] = "ready"
            elif all(states.get(parent) in ("success", "failure", "skipped")
                     for parent, _ in before):
                fired = any(_fires(condition, states[parent]) for parent, condition in before
                            if states[parent] != "skipped")
                states[node] = "ready" if fired else "skipped"
            else:
                continue
            changed = True
    return {node: states.get(node, "waiting") for node in nodes}


def _artifacts(options: dict, steps: dict, node: str) -> dict:
    """What the steps before a step passed on, by the order they ended in."""
    edges = options.get("edges", [])
    before: set[str] = set()
    frontier = [node]
    while frontier:
        current = frontier.pop()
        for edge in edges:
            if edge["to"] == current and edge["from"] not in before:
                before.add(edge["from"])
                frontier.append(edge["from"])
    merged: dict = {}
    for row in sorted((steps[name] for name in before if name in steps),
                      key=lambda row: row["finished_at"] or 0):
        if row["status"] in _ENDED_WELL:
            merged.update(store.loads(row["artifacts"], {}))
    return merged


def advance(ctx: Context, run_id: str) -> None:
    """Start the steps of a workflow run whose time has come, and record its
    end once no step is left to run."""
    if run_id in _HELD:
        _AGAIN.add(run_id)
        return
    descriptor = os.open(jobs.run_dir(run_id) / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _HELD.add(run_id)
        while True:
            _AGAIN.discard(run_id)
            _advance(ctx, run_id)
            if run_id not in _AGAIN:
                break
    finally:
        _HELD.discard(run_id)
        os.close(descriptor)


def _advance(ctx: Context, run_id: str) -> None:
    run = store.fetch(ctx.conn, "runs", run_id, "run")
    if run["kind"] != "workflow" or run["status"] != "running":
        return
    options = store.loads(run["options"], {})
    failures: dict = options.get("failures", {})
    nodes = {node["id"]: node for node in options.get("nodes", [])}
    while True:
        steps = _steps(ctx, run_id, options)
        states = plan(options, steps, failures)
        ready = [node for node, state in states.items() if state == "ready"]
        if run["cancel_requested"] or not ready:
            break
        for node in ready:
            try:
                _launch_step(ctx, run, nodes[node], _artifacts(options, steps, node),
                             options.get("scope", {}), options.get("inventory"))
            except FleetError as error:
                # It could not start: as if it had failed, for the links after it.
                failures[node] = str(error)
                store.update(ctx.conn, "runs", run_id,
                             {"options": json.dumps({**options, "failures": failures})}, "run")
                journal.send(f"workflow run {run_id}: step {node} cannot start: {error}", 4,
                             FLEET_RUN=run_id)
    # Once canceled, what would be ready never starts: only what runs is waited for.
    if any(state == "active" or (state == "ready" and not run["cancel_requested"])
           for state in states.values()):
        return
    if run["cancel_requested"]:
        status, error = "canceled", ""
    else:
        # A failure is handled by a step that runs for it; one that runs
        # always, such as telling about the outcome, does not undo it.
        handled = {edge["from"] for edge in options.get("edges", [])
                   if edge["condition"] == "failure"}
        unhandled = sorted(node for node, state in states.items()
                           if state == "failure" and node not in handled)
        status = "failed" if unhandled else "successful"
        error = ("; ".join(f"{_label(nodes[node], steps.get(node))}: "
                           + (failures.get(node) or steps[node]["status"]) for node in unhandled)
                 if unhandled else "")
    jobs.finish(ctx, run_id, status, error)


def _label(node: dict, step) -> str:
    return node["name"] or (step["title"] if step is not None else node["id"])


def _launch_step(ctx: Context, run, node: dict, artifacts: dict, scope: dict,
                 chosen: str | None) -> None:
    if scope:
        # Its template may have changed since: the run checks for itself.
        check_scope(ctx.conn, store.fetch(ctx.conn, "templates", node["template_id"],
                                          "template"), scope)
    if run["schedule_id"]:
        # Nobody is there to approve: a template that has come to need an
        # approval since the launch fails the step instead of holding it.
        unattended(ctx, [node])
    runs.launch(ctx, node["template_id"], node["limit"], False, False, node["answers"],
                created_by=run["created_by"],
                inventory_id=chosen or scope.get("inventory_id"),
                workflow={"run_id": run["id"], "node": node["id"], "title": node["name"],
                          "vars": artifacts, "approval": step_mode(node),
                          "priority": run["priority"]})


def cancel(ctx: Context, run_id: str) -> str:
    """Cancel a workflow run: no step starts any more, and those under way
    are canceled. The lock is not held: each step that ends advances it."""
    store.update(ctx.conn, "runs", run_id, {"cancel_requested": 1}, "run")
    for row in ctx.conn.execute(
            "SELECT id FROM runs WHERE workflow_run_id = ? AND status IN (?, ?, ?)",
            (run_id, *_ACTIVE)).fetchall():
        try:
            jobs.cancel(ctx, row["id"])
        except FleetError:
            pass  # it ended meanwhile
    advance(ctx, run_id)
    return store.fetch(ctx.conn, "runs", run_id, "run")["status"]


@command("workflow.progress", role="viewer",
         params={
             "id": Id("A workflow run"),
         })
def workflow_progress(ctx: Context, params: Values) -> dict:
    """The steps of a workflow run: each with its state, its run if it has
    one, the inventory it ran on (or will run on: the workflow's, the one
    chosen at launch, or its template's default now), and the links
    between them."""
    run = store.fetch(ctx.conn, "runs", params["id"], "run")
    if run["kind"] != "workflow":
        raise BadRequest("this run is not a workflow run")
    options = store.loads(run["options"], {})
    steps = _steps(ctx, run["id"], options)
    failures = options.get("failures", {})
    states = plan(options, steps, failures)
    carried = options.get("carried", {})
    inventories = {row["id"]: row["name"] for row in
                   ctx.conn.execute("SELECT id, name FROM inventories").fetchall()}
    out = []
    for node in options.get("nodes", []):
        step = steps.get(node["id"])
        state = states[node["id"]]
        if step is not None:
            inventory_id = step["inventory_id"]
        elif options.get("inventory") or options.get("scope", {}).get("inventory_id"):
            inventory_id = options.get("inventory") or options["scope"]["inventory_id"]
        else:
            row = ctx.conn.execute("SELECT inventory_id FROM templates WHERE id = ?",
                                   (node["template_id"],)).fetchone()
            inventory_id = row["inventory_id"] if row else None
        if state == "ready" and run["status"] != "running":
            state = "skipped"  # never started: the run ended first
        out.append({**node_out(node), "state": state,
                    "run": runs.run_row(step) if step else None,
                    "carried": node["id"] in carried and step is not None
                    and step["id"] == carried[node["id"]],
                    "error": failures.get(node["id"], ""),
                    "inventory_id": inventory_id,
                    "inventory": inventories.get(inventory_id, "")})
    return {"run": runs.run_row(run), "nodes": out, "edges": options.get("edges", []),
            "resumed_from": options.get("resumed_from")}
