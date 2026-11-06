"""Export and import of the configuration: credentials, projects, inventories,
trusted host keys, sets of variables, job templates, workflows, schedules, checks, notification
channels and their rules, as one JSON document.

No secret is in it: a credential or a channel travels without its secrets,
which are entered again where it is imported. Neither are the files of a
project that has no source elsewhere (written in the interface, uploaded);
those of an inventory written in the interface are.
Things refer to each other by name, since identifiers differ between two
installations.
"""

from . import audit, files, resources, store, variables, workflows
from .cli import COMMANDS, Context, Obj, Values, command
from .errors import BadRequest, FleetError

FORMAT = "cockpit-fleet-config"
VERSION = 1
SECTIONS = ("credentials", "projects", "inventories", "host_keys", "variable_sets", "templates",
            "workflows", "schedules", "checks", "channels", "rules")
# What the scope of a rule names, by table.
_SCOPES = {"project": "projects", "template": "templates", "schedule": "schedules",
           "inventory": "inventories", "group": "inventories", "check": "checks"}
_MAX_ITEMS = 2000
_MAX_IMPORT = 8 * 1024 * 1024


def _names(ctx: Context, table: str) -> dict[str, str]:
    """Identifier -> name of every row of a table."""
    return {row["id"]: row["name"] for row in ctx.conn.execute(f"SELECT id, name FROM {table}")}  # noqa: S608


@command("config.export", role="admin")
def config_export(ctx: Context, params: Values) -> dict:
    """The whole configuration as one document, by name and without secrets."""
    conn = ctx.conn
    credentials = _names(ctx, "credentials")
    projects = _names(ctx, "projects")
    inventories = _names(ctx, "inventories")
    document: dict = {"format": FORMAT, "version": VERSION, "exported_at": store.now()}
    document["credentials"] = [
        {"name": row["name"], "kind": row["kind"], "username": row["username"],
         "vault_id": row["vault_id"]}
        for row in conn.execute("SELECT * FROM credentials ORDER BY name COLLATE NOCASE")]
    document["projects"] = [
        {"name": row["name"], "kind": row["kind"], "path": row["path"], "url": row["url"],
         "ref": row["ref"], "credential": credentials.get(row["credential_id"], "")}
        for row in conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")]
    document["inventories"] = []
    for row in conn.execute("SELECT * FROM inventories ORDER BY name COLLATE NOCASE").fetchall():
        item = {"name": row["name"], "kind": row["kind"],
                "project": projects.get(row["project_id"], ""), "path": row["path"],
                "format": row["format"], "content": "",
                "vault_credential": credentials.get(row["vault_credential_id"], "")}
        if row["kind"] == "managed":
            # The whole tree; content, its main file, for an import of before trees.
            item["files"] = files.tree_files(ctx, row)
            item["content"] = files.main_text(ctx, row)
        document["inventories"].append(item)
    document["host_keys"] = [
        {"host": row["host"], "key_type": row["key_type"], "key": row["key"]}
        for row in conn.execute("SELECT * FROM host_keys ORDER BY host, key_type")]
    document["variable_sets"] = [
        {"name": row["name"], "description": row["description"],
         "variables": store.loads(row["variables"], {})}
        for row in conn.execute("SELECT * FROM variable_sets ORDER BY name COLLATE NOCASE")]
    document["templates"] = []
    for row in conn.execute("SELECT * FROM templates ORDER BY name COLLATE NOCASE").fetchall():
        document["templates"].append({
            "name": row["name"], "project": projects.get(row["project_id"], ""),
            "playbook": row["playbook"], "inventory": inventories.get(row["inventory_id"], ""),
            "machine_credential": credentials.get(row["machine_credential_id"], ""),
            "vault_credentials": [credentials.get(vault["id"], "")
                                  for vault in resources.template_vaults(ctx, row["id"])],
            "limit_hosts": row["limit_hosts"], "tags": store.loads(row["tags"], []),
            "skip_tags": store.loads(row["skip_tags"], []),
            "extra_vars": store.loads(row["extra_vars"], {}),
            "variable_sets": [one["name"]
                              for one in variables.template_sets(conn, row["id"])],
            "survey": store.loads(row["survey"], []), "forks": row["forks"],
            "verbosity": row["verbosity"], "become": bool(row["become"]),
            # needs_approval too, for a controller from before modes: there
            # confirm becomes another person, which asks more, not less.
            "approval": row["approval"], "needs_approval": row["approval"] != "none",
        })
    templates = _names(ctx, "templates")
    document["workflows"] = [
        {"name": row["name"], "description": row["description"],
         "nodes": [{**{key: value for key, value in workflows.node_out(node).items()
                       if key != "template_id"},
                    "template": templates.get(node["template_id"], "")}
                   for node in store.loads(row["nodes"], [])],
         "edges": store.loads(row["edges"], [])}
        for row in conn.execute("SELECT * FROM workflows ORDER BY name COLLATE NOCASE")]
    document["schedules"] = [
        {"name": row["name"], "template": templates.get(row["template_id"], ""),
         "calendar": row["calendar"], "timezone": row["timezone"],
         "enabled": bool(row["enabled"]), "limit": row["limit_hosts"],
         "check": bool(row["check_mode"]), "diff": bool(row["diff"]),
         "answers": store.loads(row["answers"], {}), "overlap": row["overlap"],
         "catch_up": bool(row["catch_up"]), "priority": row["priority"],
         "random_delay": row["random_delay"]}
        for row in conn.execute("SELECT * FROM schedules ORDER BY name COLLATE NOCASE")]
    document["checks"] = [
        {"name": row["name"], "kind": row["kind"],
         "inventory": inventories.get(row["inventory_id"], ""),
         "group": row["host_group"], "interval": row["interval"],
         "retry_interval": row["retry_interval"], "max_attempts": row["max_attempts"],
         "params": store.loads(row["params"], {}),
         "thresholds": store.loads(row["thresholds"], {}),
         "machine_credential": credentials.get(row["machine_credential_id"], ""),
         "become": bool(row["become"]), "enabled": bool(row["enabled"]),
         **({"project": projects.get(row["project_id"], ""), "playbook": row["playbook"]}
            if row["project_id"] else {})}
        for row in conn.execute("SELECT * FROM checks ORDER BY name COLLATE NOCASE")]
    document["channels"] = [
        {"name": row["name"], "kind": row["kind"], "settings": store.loads(row["settings"], {}),
         "language": row["language"], "enabled": bool(row["enabled"]),
         "window": row["group_window"], "max_per_hour": row["max_per_hour"]}
        for row in conn.execute("SELECT * FROM channels ORDER BY name COLLATE NOCASE")]
    channels = _names(ctx, "channels")
    scopes = {scope: _names(ctx, table) for scope, table in _SCOPES.items()}
    document["rules"] = [
        {"name": row["name"], "channel": channels.get(row["channel_id"], ""),
         "events": store.loads(row["events"], []), "scope": row["scope"],
         "scope_name": scopes.get(row["scope"], {}).get(row["scope_id"], ""),
         "group": row["scope_group"], "enabled": bool(row["enabled"])}
        for row in conn.execute("SELECT * FROM notify_rules ORDER BY name COLLATE NOCASE")]
    return document


class _Importer:
    """Creates what a document lists and is not here yet, one thing at a time.

    Each thing goes through the command that creates it from the interface,
    so it meets the same checks and leaves the same audit record.
    """

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.report: list[dict] = []

    def _id(self, table: str, name: object, what: str, required: bool = False) -> str | None:
        """The identifier here of what the document calls by name."""
        if name in (None, ""):
            if required:
                raise BadRequest(f"the {what} is missing")
            return None
        row = self.ctx.conn.execute(f"SELECT id FROM {table} WHERE name = ?",  # noqa: S608
                                    (name,)).fetchone()
        if row is None:
            raise BadRequest(f"no {what} called {name!r}")
        return row["id"]

    def _create(self, name: str, params: dict) -> None:
        spec = COMMANDS[name]
        try:
            spec.call(self.ctx, params)
        except FleetError as error:
            audit.record(self.ctx.conn, self.ctx.caller, name, params, "error", str(error),
                         spec.redact, self.ctx.via, profile=self.ctx.profile,
                         session=self.ctx.session)
            raise
        audit.record(self.ctx.conn, self.ctx.caller, name, params, "ok", redact=spec.redact,
                     via=self.ctx.via, profile=self.ctx.profile, session=self.ctx.session)

    def _each(self, section: str, items: object, table: str | None, create) -> None:
        if not isinstance(items, list) or len(items) > _MAX_ITEMS:
            raise BadRequest(f"{section} must be a list of at most {_MAX_ITEMS} entries")
        for item in items:
            label = ""
            try:
                if not isinstance(item, dict):
                    raise BadRequest("not an object")
                label = str(item.get("name") or item.get("host") or "")[:200]
                if table and self.ctx.conn.execute(
                        f"SELECT 1 FROM {table} WHERE name = ?", (label,)).fetchone():  # noqa: S608
                    outcome, detail = "exists", ""
                else:
                    outcome, detail = create(item) or "created", ""
            except FleetError as error:
                outcome, detail = "failed", str(error)
            self.report.append({"section": section, "name": label, "outcome": outcome,
                                "detail": detail})

    def credential(self, item: dict) -> None:
        self._create("credential.create", {key: item[key] for key in
                                           ("name", "kind", "username", "vault_id") if key in item})

    def project(self, item: dict) -> None:
        kind = item.get("kind")
        params = {"name": item.get("name"), "kind": kind}
        if kind == "local":
            params["path"] = item.get("path")
        elif kind == "git":
            params.update(url=item.get("url"), ref=item.get("ref") or "main",
                          credential_id=self._id("credentials", item.get("credential"),
                                                 "credential"))
        self._create("project.create", params)

    def inventory(self, item: dict) -> None:
        kind = item.get("kind")
        params = {"name": item.get("name"), "kind": kind,
                  "vault_credential_id": self._id("credentials", item.get("vault_credential"),
                                                  "credential")}
        if kind == "file":
            params.update(project_id=self._id("projects", item.get("project"), "project", True),
                          path=item.get("path"))
        elif isinstance(item.get("files"), list):
            params.update(format=item.get("format") or "yaml", path=item.get("path") or "",
                          files=item["files"])
        else:
            params.update(format=item.get("format") or "yaml", content=item.get("content") or "")
        self._create("inventory.create", params)

    def host_key(self, item: dict) -> str:
        params = {key: item.get(key) for key in ("host", "key_type", "key")}
        if not all(isinstance(value, str) for value in params.values()):
            raise BadRequest("host, key_type and key must be strings")
        row = self.ctx.conn.execute("SELECT key FROM host_keys WHERE host = ? AND key_type = ?",
                                    (params["host"], params["key_type"])).fetchone()
        if row is not None and row["key"] == params["key"]:
            return "exists"
        # A different key than the one trusted here is refused, as from the interface.
        self._create("hostkey.add", params)
        return "created"

    def variable_set(self, item: dict) -> None:
        self._create("varset.create", {key: item[key] for key in
                                       ("name", "description", "variables") if key in item})

    def template(self, item: dict) -> None:
        vaults = item.get("vault_credentials") or []
        if not isinstance(vaults, list):
            raise BadRequest("vault_credentials must be a list")
        sets = item.get("variable_sets") or []
        if not isinstance(sets, list):
            raise BadRequest("variable_sets must be a list")
        params = {
            "name": item.get("name"), "playbook": item.get("playbook"),
            "project_id": self._id("projects", item.get("project"), "project", True),
            "inventory_id": self._id("inventories", item.get("inventory"), "inventory", True),
            "machine_credential_id": self._id("credentials", item.get("machine_credential"),
                                              "credential"),
            "vault_credential_ids": [self._id("credentials", name, "credential", True)
                                     for name in vaults],
            "variable_set_ids": [self._id("variable_sets", name, "set of variables", True)
                                 for name in sets],
        }
        for key in ("limit_hosts", "tags", "skip_tags", "extra_vars", "survey", "verbosity",
                    "become", "approval", "needs_approval"):
            if key in item:
                params[key] = item[key]
        if item.get("forks") is not None:
            params["forks"] = item["forks"]
        self._create("template.create", params)

    def workflow(self, item: dict) -> None:
        nodes = item.get("nodes") or []
        if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
            raise BadRequest("nodes must be a list of steps")
        steps = [{**{key: value for key, value in node.items() if key != "template"},
                  "template_id": self._id("templates", node.get("template"), "template", True)}
                 for node in nodes]
        self._create("workflow.create", {"name": item.get("name"), "nodes": steps,
                                         "edges": item.get("edges") or [],
                                         "description": item.get("description") or ""})

    def schedule(self, item: dict) -> None:
        params = {"name": item.get("name"), "calendar": item.get("calendar"),
                  "template_id": self._id("templates", item.get("template"), "template", True)}
        for key in ("timezone", "enabled", "limit", "check", "diff", "answers", "overlap",
                    "catch_up", "priority", "random_delay"):
            if key in item:
                params[key] = item[key]
        self._create("schedule.create", params)

    def check(self, item: dict) -> None:
        params = {"name": item.get("name"), "kind": item.get("kind"),
                  "inventory_id": self._id("inventories", item.get("inventory"), "inventory", True),
                  "machine_credential_id": self._id("credentials", item.get("machine_credential"),
                                                    "credential")}
        for key in ("group", "interval", "retry_interval", "max_attempts", "params", "thresholds",
                    "become", "enabled", "playbook"):
            if key in item:
                params[key] = item[key]
        if item.get("project"):
            params["project_id"] = self._id("projects", item.get("project"), "project", True)
        self._create("check.create", params)

    def channel(self, item: dict) -> None:
        self._create("channel.create", {key: item[key] for key in
                                        ("name", "kind", "settings", "language", "enabled",
                                         "window", "max_per_hour") if key in item})

    def rule(self, item: dict) -> None:
        scope = item.get("scope") or "all"
        params = {"name": item.get("name"), "scope": scope,
                  "channel_id": self._id("channels", item.get("channel"), "channel", True)}
        if scope in _SCOPES:
            params["scope_id"] = self._id(_SCOPES[scope], item.get("scope_name"), scope, True)
        for key in ("events", "enabled"):
            if key in item:
                params[key] = item[key]
        if scope == "group":
            params["group"] = item.get("group") or ""
        self._create("rule.create", params)


@command("config.import", role="admin", mutating=True, agent="code", redact=("document",),
         max_input=_MAX_IMPORT,
         params={
             "document": Obj(),
         })
def config_import(ctx: Context, params: Values) -> dict:
    """Create what an exported document lists and is not here yet.

    What already exists, by name, is left as it is. One thing that cannot be
    created does not stop the others: the answer says what happened to each.
    """
    document = params["document"]
    if document.get("format") != FORMAT:
        raise BadRequest("this is not a configuration exported by cockpit-fleet")
    if document.get("version") != VERSION:
        raise BadRequest(f"unsupported version of the document: {document.get('version')!r}")
    work = _Importer(ctx)
    work._each("credentials", document.get("credentials", []), "credentials", work.credential)
    work._each("projects", document.get("projects", []), "projects", work.project)
    work._each("inventories", document.get("inventories", []), "inventories", work.inventory)
    work._each("host_keys", document.get("host_keys", []), None, work.host_key)
    work._each("variable_sets", document.get("variable_sets", []), "variable_sets",
               work.variable_set)
    work._each("templates", document.get("templates", []), "templates", work.template)
    work._each("workflows", document.get("workflows", []), "workflows", work.workflow)
    work._each("schedules", document.get("schedules", []), "schedules", work.schedule)
    work._each("checks", document.get("checks", []), "checks", work.check)
    work._each("channels", document.get("channels", []), "channels", work.channel)
    work._each("rules", document.get("rules", []), "notify_rules", work.rule)
    counts = {outcome: sum(1 for entry in work.report if entry["outcome"] == outcome)
              for outcome in ("created", "exists", "failed")}
    return {"report": work.report, **counts}
