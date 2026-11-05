"""Commands to launch, follow and cancel runs."""

import json
import shutil

from . import approvals, jobs, store, survey
from . import variables as varsets
from .cli import Bool, Context, Id, Int, Obj, Str, Strs, Values, command
from .errors import BadRequest, Conflict, Forbidden
from .resources import credential_of_kind, place_inventory, template_vaults, vault_ids
from .validators import check_limit, check_module

_CHUNK = 256 * 1024
STATUSES = ("pending_approval", "queued", "running", "successful", "failed", "canceled", "error",
            "rejected", "expired")


def run_row(row) -> dict:
    data = {key: row[key] for key in ("id", "kind", "status", "title", "template_id", "project_id",
                                      "inventory_id", "created_by", "created_at", "started_at",
                                      "finished_at", "rc", "revision", "error", "schedule_id",
                                      "priority", "pruned_at", "workflow_id", "workflow_run_id",
                                      "workflow_node")}
    data["options"] = store.loads(row["options"], {})
    data["stats"] = store.loads(row["stats"], {})
    return data


@command("run.start", role="operator", mutating=True,
         params={
             "template_id": Id(),
             "limit": Str(default="", max_length=512),
             "check": Bool(),
             "diff": Bool(),
             "answers": Obj(),
             "serial": Bool("Wait in the queue while another run is running on the same"
                            " inventory, or one launched before it is queued there,"
                            " whatever their outcome"),
         })
def run_start(ctx: Context, params: Values) -> dict:
    """Launch a job template. The caller may narrow it, not widen it. A run of a
    template that needs approval waits, as pending_approval, until another user
    approves it. One launched in series waits in the queue while another run is
    running on its inventory, or one launched before it is queued there."""
    return launch(ctx, params["template_id"], params["limit"], params["check"], params["diff"],
                  params["answers"], serial=params["serial"])


def launch(ctx: Context, template_id: str, limit: str, check: bool, diff: bool, answers: dict,
           schedule=None, created_by: str | None = None, workflow: dict | None = None,
           serial: bool = False) -> dict:
    """Queue a run of a template: what run.start does, what a schedule does
    at its time, with the answers stored with it, and what a workflow does for
    one of its steps, with what the steps before it passed on."""
    template = store.fetch(ctx.conn, "templates", template_id, "template")
    project = store.fetch(ctx.conn, "projects", template["project_id"], "project")
    inventory = store.fetch(ctx.conn, "inventories", template["inventory_id"], "inventory")
    limits = [template["limit_hosts"], check_limit(limit) if limit else ""]
    variables, tickets = survey.answer(store.loads(template["survey"], []), answers)
    extra_vars, sets = varsets.effective(ctx.conn, template)

    spec = {
        "kind": "playbook",
        "playbook": template["playbook"],
        # Both limits apply: a host must match the template's and the caller's.
        # Not joined into one pattern, which Ansible would read as a union of
        # their plain terms: fleet-run resolves each and keeps the hosts in both.
        **({"limits": [value for value in limits if value]}
           if all(limits) else {"limit": "".join(limits)}),
        "check": check,
        "diff": diff or check,
        "become": bool(template["become"]),
        "forks": template["forks"] or ctx.config.default_forks,
        "verbosity": template["verbosity"],
        "tags": store.loads(template["tags"], []),
        "skip_tags": store.loads(template["skip_tags"], []),
        "extra_vars": extra_vars,
        "survey_vars": variables,
        "survey_secrets": {},
        "workflow_vars": workflow["vars"] if workflow else {},
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
        jobs.place_collections(project, directory, required=True)
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
        for index, (name, ticket) in enumerate(tickets.items()):
            jobs.take_answer(directory, ticket, f"survey_{index}")
            spec["survey_secrets"][name] = f"survey_{index}"
        jobs.write_known_hosts(ctx.conn, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    options = {"limit": limit, "check": check, "diff": spec["diff"], "answers": variables,
               # Which secrets were given; their values are in no record.
               "secret_answers": sorted(tickets)}
    if sets:
        # Which sets of variables it took, by name as they were then.
        options["variable_sets"] = sets
    if serial:
        options["serial"] = True
    run = {"id": run_id, "kind": "playbook", "title": template["name"],
           "template_id": template_id, "project_id": project["id"],
           "inventory_id": inventory["id"], "revision": project["revision"],
           "options": json.dumps(options)}
    if schedule is not None:
        run.update(schedule_id=schedule["id"], priority=schedule["priority"])
    if created_by:
        run["created_by"] = created_by
    if workflow:
        run.update(workflow_run_id=workflow["run_id"], workflow_node=workflow["node"],
                   title=workflow["title"] or template["name"])
    if template["needs_approval"] or (workflow and workflow["needs_approval"]):
        jobs.queue(ctx, run, spec, directory, jobs.WAITING)
        approvals.request(ctx, run_id)
    else:
        jobs.enqueue(ctx, run, spec, directory)
    return run_row(store.fetch(ctx.conn, "runs", run_id, "run"))


@command("run.adhoc", role="admin", mutating=True,
         params={
             "inventory_id": Id(),
             "project_id": Id(required=False),
             "free_form": Str(default="", max_length=16 * 1024),
             "args": Obj(),
             "become": Bool(),
             "check": Bool(),
             "diff": Bool(),
             "forks": Int(1, 200),
             "vault_credential_ids": Strs(max_items=20),
             "module": Str(required=True, max_length=200),
             "limit": Str(required=True, max_length=512),
             "machine_credential_id": Id(required=False),
             "serial": Bool("Wait in the queue while another run is running on the same"
                            " inventory, or one launched before it is queued there,"
                            " whatever their outcome"),
         })
def run_adhoc(ctx: Context, params: Values) -> dict:
    """Run one module on hosts of an inventory, without a template.

    The module must be among those config.toml allows. Its arguments are kept
    with the run and in the audit record, in clear: they are not for secrets.
    """
    inventory_id = params["inventory_id"]
    project_id = params["project_id"]
    module = check_module(params["module"])
    free_form = params["free_form"]
    args = params["args"]
    limit = check_limit(params["limit"])
    machine_id = credential_of_kind(ctx, params["machine_credential_id"], "machine")
    vaults = vault_ids(ctx, params)
    become = params["become"]
    check = params["check"]
    diff = params["diff"]
    forks = params["forks"] or ctx.config.default_forks
    if forks > ctx.config.max_forks:
        raise BadRequest(f"forks cannot exceed {ctx.config.max_forks} (config.toml)")

    if module not in ctx.config.adhoc_modules:
        raise Conflict(f"{module} is not among the modules config.toml allows"
                       " for ad-hoc commands ([adhoc] modules)")
    if len(json.dumps(args)) > 64 * 1024:
        raise BadRequest("args is too large")
    inventory = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    if inventory["kind"] == "file" and project_id is None:
        # What stands beside it, and the collections of its project.
        project_id = inventory["project_id"]
    project = store.fetch(ctx.conn, "projects", project_id, "project") if project_id else None

    spec = {"kind": "adhoc", "module": module, "free_form": free_form, "args": args,
            "limit": limit, "check": check, "diff": diff or check, "become": become,
            "forks": forks, "vaults": []}
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        if project:
            # For its collections and for what stands beside its inventory.
            jobs.snapshot(jobs.project_tree(project, ctx.config), directory / "project")
            jobs.place_collections(project, directory, required=True)
        else:
            (directory / "project").mkdir(mode=0o700)
        spec.update(place_inventory(ctx, inventory, directory, project_id))
        if machine_id:
            machine = store.fetch(ctx.conn, "credentials", machine_id, "credential")
            spec["user"] = machine["username"]
            jobs.add_secret(directory, machine_id, "private_key", "private_key")
            jobs.add_secret(directory, machine_id, "key_passphrase", "key_passphrase")
            jobs.add_secret(directory, machine_id, "password", "connection_password")
            jobs.add_secret(directory, machine_id, "become_password", "become_password")
        for index, vault_id in enumerate(vaults):
            vault = store.fetch(ctx.conn, "credentials", vault_id, "credential")
            jobs.add_secret(directory, vault_id, "vault_password", f"vault_password_{index}")
            spec["vaults"].append({"file": f"vault_password_{index}", "id": vault["vault_id"]})
        jobs.write_known_hosts(ctx.conn, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    options = {"module": module, "free_form": free_form, "args": args, "limit": limit,
               "check": check, "diff": spec["diff"], "become": become}
    if params["serial"]:
        options["serial"] = True
    jobs.enqueue(ctx, {"id": run_id, "kind": "adhoc", "title": f"{module} [{limit}]",
                       "project_id": project_id, "inventory_id": inventory_id,
                       "revision": project["revision"] if project else "",
                       "options": json.dumps(options)}, spec, directory)
    return run_row(store.fetch(ctx.conn, "runs", run_id, "run"))


@command("run.cancel", role="operator", mutating=True,
         params={
             "id": Id(),
         })
def run_cancel(ctx: Context, params: Values) -> dict:
    """Cancel a run that is queued, running or waiting for approval."""
    run_id = params["id"]
    return {"status": jobs.cancel(ctx, run_id)}


@command("run.list", role="viewer",
         params={
             "limit": Int(1, 200, default=50),
             "before": Int(0, 2**62),
             "status": Str(default="", max_length=20),
             "kind": Str(default="", max_length=20),
         })
def run_list(ctx: Context, params: Values) -> dict:
    """The runs, newest first, with their status and outcome per host. Check
    cycles are listed only when asked for by their kind, check."""
    limit = params["limit"]
    before = params["before"]
    status = params["status"]
    kind = params["kind"]
    if status and status not in STATUSES:
        raise BadRequest(f"status must be one of: {', '.join(STATUSES)}")
    jobs.reconcile(ctx)
    rows = ctx.conn.execute(
        "SELECT rowid, * FROM runs WHERE (? IS NULL OR rowid < ?) AND (? = '' OR status = ?)"
        " AND (? = '' AND kind != 'check' OR kind = ?) ORDER BY rowid DESC LIMIT ?",
        (before, before, status, status, kind, kind, limit),
    ).fetchall()
    return {"runs": [{**run_row(row), "cursor": row["rowid"],
                      "waiting_for": jobs.waiting_for(ctx.conn, row["id"])
                      if row["status"] == "queued" else None} for row in rows],
            "more": len(rows) == limit}


@command("run.get", role="viewer",
         params={
             "id": Id(),
         })
def run_get(ctx: Context, params: Values) -> dict:
    """One run: its status, its options and its outcome per host, and its
    approval when it needed one."""
    run_id = params["id"]
    jobs.reconcile(ctx)
    run = run_row(store.fetch(ctx.conn, "runs", run_id, "run"))
    run["waiting_for"] = jobs.waiting_for(ctx.conn, run_id)
    approval = approvals.of_run(ctx, run_id)
    if approval:
        run["approval"] = {**approval, "can_decide": run["status"] == jobs.WAITING
                           and not approval["decision"]
                           and approvals.may_decide(ctx, approval["requested_by"])}
    return run


def _read_from(ctx: Context, params: Values, name: str) -> dict:
    """A chunk of one of the run's files, from a byte offset, for polling."""
    run_id = params["id"]
    offset = params["offset"]
    run = store.fetch(ctx.conn, "runs", run_id, "run")
    if (run["kind"] == "inventory" or run["kind"] == "syntax" and run["inventory_id"]) \
            and ctx.caller.role != "admin":
        # Ansible quotes the lines it could not read, and they may hold passwords.
        raise Forbidden("the output of reading an inventory is for administrators")
    try:
        with jobs.open_run_file(jobs.run_dir(run_id), name) as source:
            source.seek(offset)
            data = source.read(_CHUNK)
    except FileNotFoundError:
        data = b""
    if name.endswith(".jsonl"):
        # Whole lines only: the last one may still be being written.
        data = data[: data.rfind(b"\n") + 1]
    return {"text": data.decode(errors="replace"), "offset": offset + len(data),
            "more": len(data) == _CHUNK}


_CHUNK_PARAMS = {"id": Id(), "offset": Int(0, 2**62, default=0)}


@command("run.log", role="operator", params=_CHUNK_PARAMS)
def run_log(ctx: Context, params: Values) -> dict:
    """The raw output of a run, in chunks from a byte offset. It is written by the
    nodes and may be long: prefer run.result, and read this only when asked."""
    return _read_from(ctx, params, "stdout.log")


@command("run.events", role="operator", params=_CHUNK_PARAMS, tool=False)
def run_events(ctx: Context, params: Values) -> dict:
    """The events of a run as JSON lines, in chunks from a byte offset."""
    return _read_from(ctx, params, "events.jsonl")


@command("run.reconcile", role="admin", tool=False)
def run_reconcile(ctx: Context, params: Values) -> dict:
    """Called when a run unit ends, to record the outcome and start what waits."""
    jobs.reconcile(ctx)
    return {}
