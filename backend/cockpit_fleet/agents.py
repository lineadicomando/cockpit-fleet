"""What an agent gets through fleet-mcp: the commands as tools, the job
templates as tools of their own, and the outcome of a run in a form made to
be read by a program.

Nothing here gives an agent a way in of its own. A tool is a call to fleetctl
by the user who started fleet-mcp, so roles and the audit trail are the ones
of the interface, under the ceiling config.toml sets for agents.
"""

import json
import re
import time

from . import jobs, store
from .auth import role_at_least
from .cli import COMMANDS, Context, Id, Int, Values, command
from .runs import run_row

_MAX_TASKS = 300
_MAX_MESSAGE = 500
_MAX_PROBLEMS = 50


def _tool_name(name: str) -> str:
    return name.replace(".", "_")


def _field_schema(field: dict) -> dict:
    """One question of a survey as JSON Schema."""
    kind = field["type"]
    if kind == "number":
        out: dict = {"type": "number"}
        for key, word in (("min", "minimum"), ("max", "maximum")):
            if key in field:
                out[word] = field[key]
    elif kind == "boolean":
        out = {"type": "boolean"}
    elif kind == "choice":
        out = {"type": "string", "enum": list(field.get("choices", []))}
    else:
        out = {"type": "string"}
        for key, word in (("min_length", "minLength"), ("max_length", "maxLength")):
            if key in field:
                out[word] = field[key]
    text = " ".join(part for part in (field.get("label"), field.get("help")) if part)
    if text:
        out["description"] = text
    if "default" in field:
        out["default"] = field["default"]
    return out


def template_tool(template, project: str, inventory: str) -> dict | None:
    """A job template as a tool: its questions are the parameters.

    A template that cannot start without a secret is not offered: a secret
    answer goes from a person to fleet-secret, never through an agent. An
    optional secret question is left out, and the run goes without it.
    """
    survey = store.loads(template["survey"], [])
    if any(field["type"] == "secret" and field.get("required") for field in survey):
        return None
    asked = [field for field in survey if field["type"] != "secret"]
    slug = re.sub(r"[^a-z0-9]+", "_", template["name"].lower()).strip("_")[:32]
    answers = {
        "type": "object",
        "properties": {field["name"]: _field_schema(field) for field in asked},
        "required": [field["name"] for field in asked if field.get("required")],
        "additionalProperties": False,
    }
    limit = template["limit_hosts"]
    return {
        # The identifier keeps two templates with look-alike names apart.
        "name": f"launch_{slug}_{template['id'][-6:]}".replace("__", "_"),
        "description": (f"Launch the job template {template['name']!r}: playbook"
                        f" {template['playbook']} of project {project!r} on inventory"
                        f" {inventory!r}. Returns the run: follow it with run_result."),
        "command": "run.start",
        "fixed": {"template_id": template["id"]},
        "inputSchema": {
            "type": "object",
            "properties": {
                "answers": answers,
                "limit": {"type": "string", "maxLength": 512, "description":
                          "Host pattern that narrows the hosts of the template"
                          + (f" ({limit})" if limit else "") + "; it cannot widen them."},
                "check": {"type": "boolean", "description":
                          "Dry run: report what would change, change nothing."},
            },
            "required": ["answers"] if answers["required"] else [],
            "additionalProperties": False,
        },
    }


@command("mcp.tools", role="viewer", tool=False)
def mcp_tools(ctx: Context, params: Values) -> dict:
    """The tools the caller may use: for fleet-mcp, which offers them to an agent.

    Each one says which command it calls, with which fixed parameters, and
    its class (cli.AGENT_CLASSES).
    """
    role = ctx.caller.role
    tools = []
    for name, spec in sorted(COMMANDS.items()):
        if spec.tool and role_at_least(role, spec.role):
            tools.append({
                "name": _tool_name(name),
                "description": " ".join((spec.handler.__doc__ or name).split()),
                "command": name, "fixed": {}, "inputSchema": spec.schema(),
                "readOnly": not spec.mutating, "class": spec.agent,
            })
    if role_at_least(role, COMMANDS["run.start"].role):
        projects = {row["id"]: row["name"] for row in ctx.conn.execute(
            "SELECT id, name FROM projects")}
        inventories = {row["id"]: row["name"] for row in ctx.conn.execute(
            "SELECT id, name FROM inventories")}
        for template in ctx.conn.execute("SELECT * FROM templates ORDER BY name COLLATE NOCASE"):
            tool = template_tool(template, projects.get(template["project_id"], ""),
                                 inventories.get(template["inventory_id"], ""))
            if tool is not None:
                tools.append({**tool, "readOnly": False, "class": COMMANDS["run.start"].agent})
    return {"tools": tools, "role": role, "user": ctx.caller.user}


def summarize(lines) -> list[dict]:
    """The view by task of a run, from its events: for each task how many
    hosts went each way, and what was said where it did not succeed."""
    tasks: dict[str, dict] = {}
    play = ""
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("event", ""))
        uuid = event.get("task_uuid")
        if kind == "play_start":
            play = str(event.get("play", ""))
        elif kind == "task_start" and uuid not in tasks and len(tasks) < _MAX_TASKS:
            tasks[uuid] = {"task": str(event.get("task", "")), "play": play, "ok": 0,
                           "changed": 0, "failed": 0, "unreachable": 0, "skipped": 0,
                           "problems": {}}
        elif kind.startswith("host_") and uuid in tasks:
            task = tasks[uuid]
            outcome = kind[5:]
            if outcome == "ok" and event.get("changed"):
                outcome = "changed"
            if outcome not in ("ok", "changed", "failed", "unreachable", "skipped"):
                continue
            task[outcome] += 1
            if outcome in ("failed", "unreachable") and len(task["problems"]) < _MAX_PROBLEMS:
                result = event.get("result") if isinstance(event.get("result"), dict) else {}
                said = result.get("msg") or result.get("stderr") or ""
                if not isinstance(said, str):
                    said = json.dumps(said, default=str)
                task["problems"][str(event.get("host", ""))] = said[:_MAX_MESSAGE]
    return list(tasks.values())


@command("run.result", role="operator",
         params={
             "id": Id("The run."),
             "wait": Int(0, 50, "Seconds to wait for the run to end before answering.",
                         default=0),
         })
def run_result(ctx: Context, params: Values) -> dict:
    """How a run went: its status, the outcome per host and, task by task,
    how many hosts went each way and what was said where one failed. With
    wait it answers when the run ends or after that many seconds; "done"
    tells which. This is the way to follow a run; its raw output is run.log."""
    run_id = params["id"]
    deadline = time.monotonic() + params["wait"]
    while True:
        jobs.reconcile(ctx)
        row = store.fetch(ctx.conn, "runs", run_id, "run")
        if row["status"] not in jobs.ACTIVE or time.monotonic() >= deadline:
            break
        time.sleep(1)
    try:
        with jobs.open_run_file(jobs.run_dir(run_id), "events.jsonl", "r", encoding="utf-8",
                                errors="replace") as events:
            tasks = summarize(events)
    except OSError:
        tasks = []
    return {**run_row(row), "done": row["status"] not in jobs.ACTIVE, "tasks": tasks}

