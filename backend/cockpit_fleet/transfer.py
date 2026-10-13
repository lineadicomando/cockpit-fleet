"""Export and import of the configuration: credentials, projects, inventories,
trusted host keys and job templates, as one JSON document.

No secret is in it: a credential travels as its name and its kind, and its
secrets are entered again where it is imported. Neither are the files of a
project that has no source elsewhere (written in the interface, uploaded).
Things refer to each other by name, since identifiers differ between two
installations.
"""

from . import audit, resources, store
from .cli import COMMANDS, Context, Obj, Values, command
from .errors import BadRequest, FleetError

FORMAT = "cockpit-fleet-config"
VERSION = 1
SECTIONS = ("credentials", "projects", "inventories", "host_keys", "templates")
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
    document["inventories"] = [
        {"name": row["name"], "kind": row["kind"], "project": projects.get(row["project_id"], ""),
         "path": row["path"], "format": row["format"], "content": row["content"],
         "vault_credential": credentials.get(row["vault_credential_id"], "")}
        for row in conn.execute("SELECT * FROM inventories ORDER BY name COLLATE NOCASE")]
    document["host_keys"] = [
        {"host": row["host"], "key_type": row["key_type"], "key": row["key"]}
        for row in conn.execute("SELECT * FROM host_keys ORDER BY host, key_type")]
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
            "survey": store.loads(row["survey"], []), "forks": row["forks"],
            "verbosity": row["verbosity"], "become": bool(row["become"]),
        })
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
                         spec.redact, self.ctx.via)
            raise
        audit.record(self.ctx.conn, self.ctx.caller, name, params, "ok", redact=spec.redact,
                     via=self.ctx.via)

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
        else:
            params.update(format=item.get("format") or "yaml", content=item.get("content") or "")
        self._create("inventory.create", params)

    def host_key(self, item: dict) -> str:
        params = {key: item.get(key) for key in ("host", "key_type", "key")}
        row = self.ctx.conn.execute("SELECT key FROM host_keys WHERE host = ? AND key_type = ?",
                                    (params["host"], params["key_type"])).fetchone()
        if row is not None and row["key"] == params["key"]:
            return "exists"
        # A different key than the one trusted here is refused, as from the interface.
        self._create("hostkey.add", params)
        return "created"

    def template(self, item: dict) -> None:
        vaults = item.get("vault_credentials") or []
        if not isinstance(vaults, list):
            raise BadRequest("vault_credentials must be a list")
        params = {
            "name": item.get("name"), "playbook": item.get("playbook"),
            "project_id": self._id("projects", item.get("project"), "project", True),
            "inventory_id": self._id("inventories", item.get("inventory"), "inventory", True),
            "machine_credential_id": self._id("credentials", item.get("machine_credential"),
                                              "credential"),
            "vault_credential_ids": [self._id("credentials", name, "credential", True)
                                     for name in vaults],
        }
        for key in ("limit_hosts", "tags", "skip_tags", "extra_vars", "survey", "verbosity",
                    "become"):
            if key in item:
                params[key] = item[key]
        if item.get("forks") is not None:
            params["forks"] = item["forks"]
        self._create("template.create", params)


@command("config.import", role="admin", mutating=True, redact=("document",),
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
    work._each("templates", document.get("templates", []), "templates", work.template)
    counts = {outcome: sum(1 for entry in work.report if entry["outcome"] == outcome)
              for outcome in ("created", "exists", "failed")}
    return {"report": work.report, **counts}
