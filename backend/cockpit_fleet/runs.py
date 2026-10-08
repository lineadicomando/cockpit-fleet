"""Commands to launch, follow and cancel runs."""

import json
import shutil

from . import jobs, store
from .cli import Context, Params, command
from .errors import BadRequest
from .resources import place_inventory, template_vaults
from .validators import check_limit

_CHUNK = 256 * 1024
STATUSES = ("queued", "running", "successful", "failed", "canceled", "error")


def _run(row) -> dict:
    data = {key: row[key] for key in ("id", "kind", "status", "title", "template_id", "project_id",
                                      "inventory_id", "created_by", "created_at", "started_at",
                                      "finished_at", "rc", "revision", "error")}
    data["options"] = store.loads(row["options"], {})
    data["stats"] = store.loads(row["stats"], {})
    return data


@command("run.start", role="operator", mutating=True)
def run_start(ctx: Context, params: Params) -> dict:
    """Launch a job template. The caller may narrow it, not widen it."""
    template_id = params.ident("template_id")
    limit = params.string("limit", "", 512)
    check = params.boolean("check")
    diff = params.boolean("diff")
    params.done()

    template = store.fetch(ctx.conn, "templates", template_id, "template")
    project = store.fetch(ctx.conn, "projects", template["project_id"], "project")
    inventory = store.fetch(ctx.conn, "inventories", template["inventory_id"], "inventory")
    limits = [template["limit_hosts"], check_limit(limit) if limit else ""]

    spec = {
        "kind": "playbook",
        "playbook": template["playbook"],
        # Both limits apply: a host must match the template's and the caller's.
        "limit": ":&".join(value for value in limits if value),
        "check": check,
        "diff": diff or check,
        "become": bool(template["become"]),
        "forks": template["forks"] or ctx.config.default_forks,
        "verbosity": template["verbosity"],
        "tags": store.loads(template["tags"], []),
        "skip_tags": store.loads(template["skip_tags"], []),
        "extra_vars": store.loads(template["extra_vars"], {}),
    }
    machine = None
    if template["machine_credential_id"]:
        machine = store.fetch(ctx.conn, "credentials", template["machine_credential_id"],
                              "credential")
        spec["user"] = machine["username"]

    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        jobs.snapshot(jobs.project_tree(project, ctx.config), directory / "project")
        spec.update(place_inventory(ctx, inventory, directory, project["id"]))
        if machine:
            jobs.add_secret(directory, machine["id"], "private_key", "private_key")
            jobs.add_secret(directory, machine["id"], "key_passphrase", "key_passphrase")
            jobs.add_secret(directory, machine["id"], "password", "connection_password")
            jobs.add_secret(directory, machine["id"], "become_password", "become_password")
        spec["vaults"] = []
        for index, vault in enumerate(template_vaults(ctx, template_id)):
            jobs.add_secret(directory, vault["id"], "vault_password", f"vault_password_{index}")
            spec["vaults"].append({"file": f"vault_password_{index}", "id": vault["vault_id"]})
        jobs.write_known_hosts(ctx.conn, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    options = {"limit": limit, "check": check, "diff": spec["diff"]}
    jobs.enqueue(ctx, {"id": run_id, "kind": "playbook", "title": template["name"],
                       "template_id": template_id, "project_id": project["id"],
                       "inventory_id": inventory["id"], "revision": project["revision"],
                       "options": json.dumps(options)}, spec, directory)
    return _run(store.fetch(ctx.conn, "runs", run_id, "run"))


@command("run.cancel", role="operator", mutating=True)
def run_cancel(ctx: Context, params: Params) -> dict:
    run_id = params.ident("id")
    params.done()
    return {"status": jobs.cancel(ctx, run_id)}


@command("run.list", role="viewer")
def run_list(ctx: Context, params: Params) -> dict:
    limit = params.integer("limit", 50, 1, 200)
    before = params.integer("before", None, 0, 2**62)
    status = params.string("status", "", 20)
    kind = params.string("kind", "", 20)
    params.done()
    if status and status not in STATUSES:
        raise BadRequest(f"status must be one of: {', '.join(STATUSES)}")
    jobs.reconcile(ctx)
    rows = ctx.conn.execute(
        "SELECT rowid, * FROM runs WHERE (? IS NULL OR rowid < ?) AND (? = '' OR status = ?)"
        " AND (? = '' OR kind = ?) ORDER BY rowid DESC LIMIT ?",
        (before, before, status, status, kind, kind, limit),
    ).fetchall()
    return {"runs": [{**_run(row), "cursor": row["rowid"]} for row in rows],
            "more": len(rows) == limit}


@command("run.get", role="viewer")
def run_get(ctx: Context, params: Params) -> dict:
    run_id = params.ident("id")
    params.done()
    jobs.reconcile(ctx)
    return _run(store.fetch(ctx.conn, "runs", run_id, "run"))


def _read_from(ctx: Context, params: Params, name: str) -> dict:
    """A chunk of one of the run's files, from a byte offset, for polling."""
    run_id = params.ident("id")
    offset = params.integer("offset", 0, 0, 2**62)
    params.done()
    store.fetch(ctx.conn, "runs", run_id, "run")
    try:
        with open(jobs.run_dir(run_id) / name, "rb") as source:
            source.seek(offset)
            data = source.read(_CHUNK)
    except FileNotFoundError:
        data = b""
    if name.endswith(".jsonl"):
        # Whole lines only: the last one may still be being written.
        data = data[: data.rfind(b"\n") + 1]
    return {"text": data.decode(errors="replace"), "offset": offset + len(data),
            "more": len(data) == _CHUNK}


@command("run.log", role="operator")
def run_log(ctx: Context, params: Params) -> dict:
    return _read_from(ctx, params, "stdout.log")


@command("run.events", role="operator")
def run_events(ctx: Context, params: Params) -> dict:
    return _read_from(ctx, params, "events.jsonl")


@command("run.reconcile", role="admin")
def run_reconcile(ctx: Context, params: Params) -> dict:
    """Called when a run unit ends, to record the outcome and start what waits."""
    params.done()
    jobs.reconcile(ctx)
    return {}
