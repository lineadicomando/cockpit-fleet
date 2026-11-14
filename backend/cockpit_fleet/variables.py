"""Sets of variables: named objects of extra variables that job templates
and inventories take, several in order, so that a value common to many
templates is written once.

Their variables are extra variables like a template's own, which viewers
see. A set also has secret variables: their names are here, their values
are encrypted by fleet-secret (as s-SLOT, a slot chosen here) and reach
a run as the secrets of a credential do, through the run's credentials,
never through fleetctl.
"""

import json
import re
import secrets as tokens
import shutil
from pathlib import Path

from . import paths, store
from .cli import Context, Id, Obj, Str, Strs, Values, command
from .errors import BadRequest

MAX_SIZE = 64 * 1024
MAX_PER_TEMPLATE = 20
MAX_SECRETS = 30  # in a set, and in a run: each is a file of its credentials
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


def inventory_sets(conn, inventory_id: str) -> list:
    """The sets of an inventory, in the order they apply."""
    return conn.execute(
        "SELECT s.* FROM inventory_variable_sets i JOIN variable_sets s ON s.id = i.set_id"
        " WHERE i.inventory_id = ? ORDER BY i.position", (inventory_id,)).fetchall()


def check_set_ids(conn, ids: list[str], what: str = "a template") -> list[str]:
    """The sets a template or an inventory is given: each once, each one that exists."""
    if len(set(ids)) != len(ids):
        raise BadRequest("a set of variables is listed twice")
    if len(ids) > MAX_PER_TEMPLATE:
        raise BadRequest(f"{what} takes at most {MAX_PER_TEMPLATE} sets of variables")
    for set_id in ids:
        store.fetch(conn, "variable_sets", set_id, "set of variables")
    return ids


def save_inventory_sets(conn, inventory_id: str, ids: list[str]) -> None:
    """Give an inventory its sets, in order, inside the caller's transaction."""
    conn.execute("DELETE FROM inventory_variable_sets WHERE inventory_id = ?", (inventory_id,))
    for position, set_id in enumerate(ids):
        conn.execute("INSERT INTO inventory_variable_sets (inventory_id, set_id, position)"
                     " VALUES (?, ?, ?)", (inventory_id, set_id, position))


def secret_name(slot: str) -> str:
    return f"s-{slot}"


def secret_file(slot: str) -> Path:
    return paths.SECRETS_DIR / f"{secret_name(slot)}.cred"


class Effective:
    """What sets and a template's own variables give a run: the plain
    variables, the secret ones (name -> slot, of the set that wins each),
    and which sets they came from."""

    def __init__(self) -> None:
        self.variables: dict = {}
        self.secrets: dict[str, tuple[str, str]] = {}
        self.sets: list[dict] = []

    def take_set(self, row) -> None:
        for name, value in store.loads(row["variables"], {}).items():
            self.secrets.pop(name, None)
            self.variables[name] = value
        for name, slot in store.loads(row["secrets"], {}).items():
            self.variables.pop(name, None)
            self.secrets[name] = (slot, row["name"])
        self.sets.append({"id": row["id"], "name": row["name"]})

    def take_variables(self, values: dict) -> None:
        for name, value in values.items():
            self.secrets.pop(name, None)
            self.variables[name] = value

    def place_secrets(self, directory: Path) -> dict[str, str]:
        """Copy the ciphertext of each secret variable into the run's
        credentials; by name, the file each is in there. One whose value
        was never set stops the launch: a run must not go on without it."""
        if len(self.secrets) > MAX_SECRETS:
            raise BadRequest(f"a run takes at most {MAX_SECRETS} secret variables")
        placed = {}
        for index, (name, (slot, set_name)) in enumerate(sorted(self.secrets.items())):
            source = secret_file(slot)
            if not source.is_file():
                raise BadRequest(f"the secret variable {name} of the set {set_name!r}"
                                 " has no value")
            purpose = f"secret_var_{index}"
            shutil.copyfile(source, directory / "creds" / purpose)
            placed[name] = purpose
        return placed


def effective(conn, template, inventory_id: str | None = None) -> Effective:
    """The variables of a run of a template: the sets of its inventory, then
    its own sets, in order, a later one winning, then its own variables."""
    result = Effective()
    for row in inventory_sets(conn, inventory_id or template["inventory_id"]):
        result.take_set(row)
    for row in template_sets(conn, template["id"]):
        result.take_set(row)
    result.take_variables(store.loads(template["extra_vars"], {}))
    return result


def of_inventory(conn, inventory_id: str) -> Effective:
    """The variables a job on an inventory gets from its sets alone: an
    ad-hoc command, a check cycle."""
    result = Effective()
    for row in inventory_sets(conn, inventory_id):
        result.take_set(row)
    return result


def _set(conn, row) -> dict:
    templates = conn.execute(
        "SELECT t.id, t.name FROM template_variable_sets v JOIN templates t"
        " ON t.id = v.template_id WHERE v.set_id = ? ORDER BY t.name COLLATE NOCASE",
        (row["id"],)).fetchall()
    inventories = conn.execute(
        "SELECT i.id, i.name FROM inventory_variable_sets v JOIN inventories i"
        " ON i.id = v.inventory_id WHERE v.set_id = ? ORDER BY i.name COLLATE NOCASE",
        (row["id"],)).fetchall()
    return {"id": row["id"], "name": row["name"], "description": row["description"],
            "variables": store.loads(row["variables"], {}),
            # Which secret variables it has and whether each is set; never a value.
            "secrets": [{"name": name, "secret_name": secret_name(slot),
                         "set": secret_file(slot).is_file()}
                        for name, slot in sorted(store.loads(row["secrets"], {}).items())],
            "templates": [{"id": t["id"], "name": t["name"]} for t in templates],
            "inventories": [{"id": i["id"], "name": i["name"]} for i in inventories],
            "updated_at": row["updated_at"]}


_PARAMS = {
    "name": Str(required=True, max_length=200),
    "description": Str("What the set is for.", default="", max_length=1000),
    "variables": Obj("The variables, as an object of extra variables: not for secrets,"
                     " which viewers would see."),
    "secrets": Strs("Names of secret variables. Their values are set apart, never"
                    " through this command, and can never be read back; a name"
                    " no longer listed loses its value. Left out of an update:"
                    " unchanged.", max_items=MAX_SECRETS),
}


def _values(params: Values, old: dict | None = None) -> dict:
    variables = check_variables(params["variables"])
    old = old or {}
    # Left out of an update, they stay as they are: a client that knows
    # nothing of them must not drop their values.
    names = params["secrets"] if "secrets" in params.given or not old else list(old)
    if len(set(names)) != len(names):
        raise BadRequest("a secret variable is listed twice")
    for name in names:
        if not _NAME_RE.fullmatch(name):
            raise BadRequest(f"invalid secret variable name: {name!r}")
        if name in variables:
            raise BadRequest(f"{name} is both a variable and a secret variable")
    # A name kept keeps its value: its slot stays.
    secrets = {name: old.get(name) or tokens.token_hex(8) for name in names}
    return {"name": store.check_name(params["name"]),
            "description": params["description"].strip(),
            "variables": json.dumps(variables),
            "secrets": json.dumps(secrets),
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
    """Change a set of variables: the next runs of the templates and
    inventories that use it get it."""
    row = store.fetch(ctx.conn, "variable_sets", params["id"], "set of variables")
    store.update(ctx.conn, "variable_sets", params["id"],
                 _values(params, store.loads(row["secrets"], {})), "set of variables")
    return _set(ctx.conn, store.fetch(ctx.conn, "variable_sets", params["id"],
                                      "set of variables"))


@command("varset.delete", role="admin", mutating=True, agent="code", params={"id": Id()})
def varset_delete(ctx: Context, params: Values) -> dict:
    """Delete a set of variables that no job template or inventory uses."""
    store.delete(ctx.conn, "variable_sets", params["id"], "set of variables")
    return {}
