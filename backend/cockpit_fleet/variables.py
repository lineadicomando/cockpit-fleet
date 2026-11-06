"""Sets of variables: named objects of extra variables that job templates
take, several in order, before their own, so that a value common to many
templates is written once.

They are extra variables like a template's own, which viewers see: not a
place for secrets, which go in vault credentials or in a survey's secret
questions.
"""

import json
import re

from . import store
from .cli import Context, Id, Obj, Str, Values, command
from .errors import BadRequest

MAX_SIZE = 64 * 1024
MAX_PER_TEMPLATE = 20
# A name Ansible takes for a variable.
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


def check_variables(value: dict, what: str = "variables") -> dict:
    """An object of variables, by names Ansible takes, of a bounded size."""
    for name in value:
        if not _NAME_RE.fullmatch(name):
            raise BadRequest(f"invalid variable name in {what}: {name!r}")
    if len(json.dumps(value)) > MAX_SIZE:
        raise BadRequest(f"{what} is too large")
    return value


def template_sets(conn, template_id: str) -> list:
    """The sets of a template, in the order they apply."""
    return conn.execute(
        "SELECT s.* FROM template_variable_sets t JOIN variable_sets s ON s.id = t.set_id"
        " WHERE t.template_id = ? ORDER BY t.position", (template_id,)).fetchall()


def check_set_ids(conn, ids: list[str]) -> list[str]:
    """The sets a template is given: each once, each one that exists."""
    if len(set(ids)) != len(ids):
        raise BadRequest("a set of variables is listed twice")
    if len(ids) > MAX_PER_TEMPLATE:
        raise BadRequest(f"a template takes at most {MAX_PER_TEMPLATE} sets of variables")
    for set_id in ids:
        store.fetch(conn, "variable_sets", set_id, "set of variables")
    return ids


def effective(conn, template) -> tuple[dict, list[dict]]:
    """The extra variables of a run of a template: its sets in order, a later
    one winning, then its own; and which sets they were."""
    merged: dict = {}
    used = []
    for row in template_sets(conn, template["id"]):
        merged.update(store.loads(row["variables"], {}))
        used.append({"id": row["id"], "name": row["name"]})
    merged.update(store.loads(template["extra_vars"], {}))
    return merged, used


def _set(conn, row) -> dict:
    templates = conn.execute(
        "SELECT t.id, t.name FROM template_variable_sets v JOIN templates t"
        " ON t.id = v.template_id WHERE v.set_id = ? ORDER BY t.name COLLATE NOCASE",
        (row["id"],)).fetchall()
    return {"id": row["id"], "name": row["name"], "description": row["description"],
            "variables": store.loads(row["variables"], {}),
            "templates": [{"id": t["id"], "name": t["name"]} for t in templates],
            "updated_at": row["updated_at"]}


_PARAMS = {
    "name": Str(required=True, max_length=200),
    "description": Str("What the set is for.", default="", max_length=1000),
    "variables": Obj("The variables, as an object of extra variables: not for secrets."),
}


def _values(params: Values) -> dict:
    return {"name": store.check_name(params["name"]),
            "description": params["description"].strip(),
            "variables": json.dumps(check_variables(params["variables"])),
            "updated_at": store.now()}


@command("varset.list", role="viewer")
def varset_list(ctx: Context, params: Values) -> dict:
    """The sets of variables, with their variables and the job templates that use them."""
    rows = ctx.conn.execute("SELECT * FROM variable_sets ORDER BY name COLLATE NOCASE").fetchall()
    return {"variable_sets": [_set(ctx.conn, row) for row in rows]}


@command("varset.create", role="admin", mutating=True, params=_PARAMS)
def varset_create(ctx: Context, params: Values) -> dict:
    """Create a set of variables that job templates can take."""
    set_id = store.new_id("v")
    store.insert(ctx.conn, "variable_sets", {"id": set_id, "created_at": store.now(),
                                             **_values(params)}, "set of variables")
    return _set(ctx.conn, store.fetch(ctx.conn, "variable_sets", set_id, "set of variables"))


@command("varset.update", role="admin", mutating=True, params={"id": Id(), **_PARAMS})
def varset_update(ctx: Context, params: Values) -> dict:
    """Change a set of variables: the next runs of the templates that use it get it."""
    store.fetch(ctx.conn, "variable_sets", params["id"], "set of variables")
    store.update(ctx.conn, "variable_sets", params["id"], _values(params), "set of variables")
    return _set(ctx.conn, store.fetch(ctx.conn, "variable_sets", params["id"],
                                      "set of variables"))


@command("varset.delete", role="admin", mutating=True, agent="code", params={"id": Id()})
def varset_delete(ctx: Context, params: Values) -> dict:
    """Delete a set of variables that no job template uses."""
    store.delete(ctx.conn, "variable_sets", params["id"], "set of variables")
    return {}
