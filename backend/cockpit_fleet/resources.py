"""Commands for the things a run is made of: credentials, projects,
inventories, host keys and job templates."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import unicodedata
from pathlib import Path, PurePosixPath

from . import approvals, files, jobs, paths, store, survey, variables
from .cli import Bool, Choice, Context, Id, Int, Obj, Objs, Str, Strs, Values, command
from .config import MAX_UPLOAD_MB
from .errors import BadRequest, Conflict, NotFound, Unavailable
from .names import check_id
from .validators import (
    check_file_path,
    check_git_ref,
    check_git_url,
    check_host,
    check_int,
    check_key_type,
    check_known_host,
    check_limit,
    check_relative_path,
    check_tag,
    check_user,
    check_vault_id,
)

# Secret fields a credential of each kind may have.
CREDENTIAL_FIELDS = {
    "machine": ("private_key", "key_passphrase", "password", "become_password"),
    "vault": ("vault_password",),
    "source": ("private_key", "key_passphrase", "token"),
}


# --- credentials ---------------------------------------------------------------

def _credential(row) -> dict:
    fields = CREDENTIAL_FIELDS[row["kind"]]
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "username": row["username"],
        "vault_id": row["vault_id"],
        # Which secrets exist; their values are never available here.
        "secrets": {field: jobs.secret_file(row["id"], field).is_file() for field in fields},
        "secret_names": {field: jobs.secret_name(row["id"], field) for field in fields},
    }


def credential_of_kind(ctx: Context, credential_id: str | None, kind: str) -> str | None:
    if credential_id is None:
        return None
    row = store.fetch(ctx.conn, "credentials", credential_id, "credential")
    if row["kind"] != kind:
        raise BadRequest(f"credential {row['name']!r} is not a {kind} credential")
    return credential_id


@command("credential.list", role="viewer")
def credential_list(ctx: Context, params: Values) -> dict:
    """The credentials, with which of their secrets are set; never the values."""
    rows = ctx.conn.execute("SELECT * FROM credentials ORDER BY name COLLATE NOCASE")
    return {"credentials": [_credential(row) for row in rows]}


_CREDENTIAL_PARAMS = {
    "name": Str(required=True, max_length=200),
    "username": Str(default="", max_length=64),
    "vault_id": Str(default="", max_length=64),
}


def _credential_values(params: Values, kind: str) -> dict:
    name = store.check_name(params["name"])
    username = params["username"]
    vault_id = params["vault_id"]
    if username:
        check_user(username)
    if vault_id:
        check_vault_id(vault_id)
    return {"name": name, "username": "" if kind == "vault" else username,
            "vault_id": vault_id if kind == "vault" else ""}


@command("credential.create", role="admin", mutating=True, agent="code",
         params={"kind": Choice(tuple(CREDENTIAL_FIELDS)), **_CREDENTIAL_PARAMS})
def credential_create(ctx: Context, params: Values) -> dict:
    """Create a credential. Its secrets are entered afterwards, by a person."""
    kind = params["kind"]
    values = _credential_values(params, kind)
    credential_id = store.new_id("c")
    store.insert(ctx.conn, "credentials", {"id": credential_id, "kind": kind,
                                           "created_at": store.now(), **values}, "credential")
    return _credential(store.fetch(ctx.conn, "credentials", credential_id, "credential"))


@command("credential.update", role="admin", mutating=True, agent="code",
         params={"id": Id(), **_CREDENTIAL_PARAMS})
def credential_update(ctx: Context, params: Values) -> dict:
    """Change the name and the non-secret fields; secrets go through fleet-secret."""
    credential_id = params["id"]
    row = store.fetch(ctx.conn, "credentials", credential_id, "credential")
    values = _credential_values(params, row["kind"])
    store.update(ctx.conn, "credentials", credential_id, values, "credential")
    return _credential(store.fetch(ctx.conn, "credentials", credential_id, "credential"))


@command("credential.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def credential_delete(ctx: Context, params: Values) -> dict:
    """Delete a credential whose secrets have been deleted."""
    credential_id = params["id"]
    row = _credential(store.fetch(ctx.conn, "credentials", credential_id, "credential"))
    if any(row["secrets"].values()):
        raise Conflict("delete the secrets of this credential first")
    store.delete(ctx.conn, "credentials", credential_id, "credential")
    return {}


# --- projects --------------------------------------------------------------------

def _project(ctx: Context, row) -> dict:
    data = {key: row[key] for key in ("id", "name", "kind", "path", "url", "ref", "credential_id",
                                      "revision", "synced_at", "sync_error")}
    # What its requirements file asks for, if it has one.
    data["collections"] = {**jobs.collections_state(row, ctx.config),
                           "installed_at": row["collections_at"],
                           "error": row["collections_error"]}
    return data


@command("project.list", role="viewer")
def project_list(ctx: Context, params: Values) -> dict:
    """The projects, with their revision and the state of their collections."""
    jobs.reconcile(ctx)  # a finished sync job may have a new revision to show
    rows = ctx.conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")
    return {"projects": [_project(ctx, row) for row in rows],
            "local_roots": list(ctx.config.local_project_roots),
            "max_upload_mb": ctx.config.max_upload_mb}


# Which of these a project takes depends on where its files come from.
_PROJECT_PARAMS = {
    "name": Str(required=True, max_length=200),
    "path": Str(max_length=1024),
    "url": Str(max_length=500),
    "ref": Str(max_length=200),
    "credential_id": Id(required=False),
}


def _project_values(ctx: Context, params: Values, kind: str) -> dict:
    values: dict = {"name": store.check_name(params["name"])}
    params.unused(*{"local": ("url", "ref", "credential_id"), "git": ("path",)}.get(
        kind, ("path", "url", "ref", "credential_id")))
    if kind == "local":
        values["path"] = params.need("path")
        if not values["path"].startswith("/"):
            raise BadRequest("path must be absolute")
        jobs.project_tree({"kind": "local", **values}, ctx.config)
    elif kind == "git":
        values["url"] = check_git_url(params.need("url"))
        values["ref"] = check_git_ref("main" if params["ref"] is None else params["ref"])
        credential_id = params["credential_id"]
        values["credential_id"] = credential_of_kind(ctx, credential_id, "source")
    return values


@command("project.create", role="admin", mutating=True, agent="code",
         params={"kind": Choice(("local", "git", "managed", "archive")), **_PROJECT_PARAMS})
def project_create(ctx: Context, params: Values) -> dict:
    """Create a project: a git repository, a local directory, or an empty one to write or upload."""
    kind = params["kind"]
    values = _project_values(ctx, params, kind)
    project_id = store.new_id("p")
    store.insert(ctx.conn, "projects", {"id": project_id, "kind": kind,
                                        "created_at": store.now(), **values}, "project")
    if kind == "managed":
        # Written in the interface: it starts as an empty repository.
        try:
            files.init_repository(project_id)
        except BaseException:
            ctx.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            shutil.rmtree(paths.PROJECTS_DIR / project_id, ignore_errors=True)
            raise
    return _project(ctx, store.fetch(ctx.conn, "projects", project_id, "project"))


@command("project.update", role="admin", mutating=True, agent="code",
         params={"id": Id(), **_PROJECT_PARAMS})
def project_update(ctx: Context, params: Values) -> dict:
    """Change a project; where its files come from cannot change."""
    project_id = params["id"]
    row = store.fetch(ctx.conn, "projects", project_id, "project")
    values = _project_values(ctx, params, row["kind"])
    store.update(ctx.conn, "projects", project_id, values, "project")
    return _project(ctx, store.fetch(ctx.conn, "projects", project_id, "project"))


@command("project.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def project_delete(ctx: Context, params: Values) -> dict:
    """Delete a project and its files on the controller."""
    project_id = params["id"]
    store.delete(ctx.conn, "projects", project_id, "project")
    shutil.rmtree(paths.PROJECTS_DIR / project_id, ignore_errors=True)
    return {}


@command("project.sync", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def project_sync(ctx: Context, params: Values) -> dict:
    """Queue a job that fetches a git project again; returns the run to follow."""
    project_id = params["id"]
    project = store.fetch(ctx.conn, "projects", project_id, "project")
    if project["kind"] != "git":
        raise BadRequest("only git projects are synchronised")
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    credential = None
    if project["credential_id"]:
        credential = store.fetch(ctx.conn, "credentials", project["credential_id"], "credential")
        jobs.add_secret(directory, credential["id"], "private_key", "git_private_key")
        jobs.add_secret(directory, credential["id"], "key_passphrase", "git_key_passphrase")
        jobs.add_secret(directory, credential["id"], "token", "git_token")
    jobs.write_known_hosts(ctx.conn, directory)
    spec = {"kind": "sync", "url": project["url"], "ref": project["ref"],
            "user": credential["username"] if credential else "", "proxy": ctx.config.proxy}
    jobs.enqueue(ctx, {"id": run_id, "kind": "sync", "title": project["name"],
                       "project_id": project_id}, spec, directory)
    return {"run_id": run_id}


@command("project.collections", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def project_collections(ctx: Context, params: Values) -> dict:
    """Queue a job that installs the collections a project asks for.

    Also when they are installed already: a requirement may name a branch,
    and this is how what it points to now is fetched.
    """
    project_id = params["id"]
    project = store.fetch(ctx.conn, "projects", project_id, "project")
    return {"run_id": jobs.queue_collections(ctx, project)}


# The archive travels as base64 in the JSON on stdin, like every parameter.
_MAX_UPLOAD_TEXT = MAX_UPLOAD_MB * 1024 * 1024 * 4 // 3 + 4


@command("project.upload", role="admin", mutating=True, agent="code", redact=("data",),
         tool=False,
         max_input=_MAX_UPLOAD_TEXT + 64 * 1024,
         params={
             "id": Id(),
             "filename": Str(default="", max_length=1024),
             "data": Str(required=True, max_length=_MAX_UPLOAD_TEXT),
         })
def project_upload(ctx: Context, params: Values) -> dict:
    """Take an archive as the new files of a project; a job unpacks it.

    Nothing of the archive is read here but its bytes, to count and to digest
    them: the digest is the revision the project gets if the job succeeds.
    """
    project_id = params["id"]
    filename = params["filename"]
    data = params["data"]
    project = store.fetch(ctx.conn, "projects", project_id, "project")
    if project["kind"] != "archive":
        raise BadRequest("only a project made of an archive takes an upload")
    try:
        content = base64.b64decode(data, validate=True)
    except ValueError as error:
        raise BadRequest("data is not valid base64") from error
    if not content:
        raise BadRequest("the archive is empty")
    if len(content) > ctx.config.max_upload_mb * 1024 * 1024:
        raise BadRequest(f"the archive is larger than {ctx.config.max_upload_mb} MB"
                         " (projects.max_upload_mb in config.toml)")
    digest = hashlib.sha256(content).hexdigest()
    # Shown with the run, nothing else: the format is read from the content.
    shown = "".join(ch for ch in PurePosixPath(filename.replace("\\", "/")).name
                    if unicodedata.category(ch)[0] != "C")[:200]

    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        (directory / "archive").write_bytes(content)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    options = {"filename": shown, "size": len(content), "sha256": digest}
    jobs.enqueue(ctx, {"id": run_id, "kind": "unpack", "title": project["name"],
                       "project_id": project_id, "options": json.dumps(options)},
                 {"kind": "unpack"}, directory)
    return {"run_id": run_id, **options}


@command("project.files", role="admin",
         params={
             "id": Id(),
         })
def project_files(ctx: Context, params: Values) -> dict:
    """YAML and INI files of a project and its directories, to choose a playbook
    or an inventory from (an inventory can be a directory of sources)."""
    project_id = params["id"]
    tree = jobs.project_tree(store.fetch(ctx.conn, "projects", project_id, "project"), ctx.config)
    found, folders = [], []
    for current, directories, names in os.walk(tree):
        relative = Path(current).relative_to(tree)
        if relative.parts:
            folders.append(str(relative))
        directories[:] = sorted(d for d in directories
                                if not d.startswith(".") and d not in ("roles", "node_modules")
                                and len(relative.parts) < 3)
        for name in sorted(names):
            if name.endswith((".yml", ".yaml", ".ini")) or name == "hosts":
                found.append(str(relative / name))
        if len(found) > 500 or len(folders) > 500:
            break
    return {"files": found[:500], "directories": folders[:500]}


# --- inventories -------------------------------------------------------------------

# What others than administrators see of why an inventory could not be read.
NOT_READ = "the inventory could not be read: an administrator can see why"


def _inventory(row, with_hosts: bool = False, admin: bool = True) -> dict:
    hosts = store.loads(row["hosts"], [])
    data = {key: row[key] for key in ("id", "name", "kind", "project_id", "path", "format",
                                      "vault_credential_id", "parsed_at", "parse_error",
                                      "revision")}
    if not admin and data["parse_error"]:
        # Ansible quotes the lines it could not read, and they may hold passwords.
        data["parse_error"] = NOT_READ
    data["host_count"] = len(hosts)
    if with_hosts:
        data["hosts"] = hosts
    return data


# A file inventory takes the project and the path, a written one the rest:
# its path is the source inside its tree, empty for all of it.
_INVENTORY_PARAMS = {
    "vault_credential_id": Id(required=False),
    "project_id": Id(required=False),
    "path": Str("For a file inventory, its file or directory in the project; for one written"
                " here, the file or directory of its tree Ansible reads, empty for all of it",
                max_length=1024),
    "format": Choice(("yaml", "ini"), default="yaml"),
    "content": Str("For an inventory written here, the text of its main file: hosts.yml,"
                   " or hosts for INI", max_length=512 * 1024),
}
# The files an inventory written here is created with, as file.change writes them.
_INVENTORY_FILES = Objs("For an inventory written here, files of its tree to create it with:"
                        " path, and content (text) or data (base64)",
                        max_items=files.MAX_LISTED)


def _inventory_values(ctx: Context, params: Values, kind: str, row=None) -> dict:
    """What a create, or an update of ROW, writes; a format not given is kept."""
    values: dict = {"vault_credential_id":
                    credential_of_kind(ctx, params["vault_credential_id"], "vault")}
    if kind == "file":
        params.unused("format", "content")
        values["project_id"] = params.need("project_id")
        store.fetch(ctx.conn, "projects", values["project_id"], "project")
        values["path"] = str(check_relative_path(params.need("path"), "path"))
    else:
        params.unused("project_id")
        values["format"] = row["format"] if row is not None and "format" not in params.given \
            else params["format"]
        if params["path"] is not None:
            values["path"] = str(check_file_path(params["path"], "path")) if params["path"] else ""
    return values


def _write_main(ctx: Context, inventory, content: str, operations: list[dict]) -> None:
    """Write the main file of a managed inventory, and the other files given,
    as one commit; the main file of the other format goes."""
    main = files.MAIN_FILES[inventory["format"]]
    tree = files.inventory_tree(ctx, inventory)
    changes = list(operations)
    if content is not None:
        current = tree / main
        expected = hashlib.sha256(current.read_bytes()).hexdigest() \
            if current.is_file() and not current.is_symlink() else ""
        changes.append({"op": "write", "path": main, "content": content, "expected": expected})
        other = next(name for name in files.MAIN_FILES.values() if name != main)
        if (tree / other).is_file() and not (tree / other).is_symlink():
            changes.append({"op": "delete", "path": other})
    if changes:
        checked = [files.check_operation(index, item) for index, item in enumerate(changes)]
        files.change(ctx, store.fetch(ctx.conn, "inventories", inventory["id"], "inventory"),
                     checked, "", reread=False)


@command("inventory.list", role="viewer")
def inventory_list(ctx: Context, params: Values) -> dict:
    """The inventories, with how many hosts each was last read to have."""
    jobs.reconcile(ctx)
    rows = ctx.conn.execute("SELECT * FROM inventories ORDER BY name COLLATE NOCASE")
    return {"inventories": [_inventory(row, admin=ctx.caller.role == "admin") for row in rows]}


@command("inventory.get", role="viewer",
         params={
             "id": Id(),
         })
def inventory_get(ctx: Context, params: Values) -> dict:
    """One inventory with its hosts and groups, as last read; for an
    administrator, the text of the main file of one written here."""
    inventory_id = params["id"]
    jobs.reconcile(ctx)
    row = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    data = _inventory(row, with_hosts=True, admin=ctx.caller.role == "admin")
    # The text of a managed inventory may hold connection passwords.
    data["content"] = files.main_text(ctx, row) \
        if row["kind"] == "managed" and ctx.caller.role == "admin" else ""
    if row["kind"] == "managed":
        data["revision"] = store.fetch(ctx.conn, "inventories", inventory_id,
                                       "inventory")["revision"]
    return data


_FILES_REDACTED = ("content", "files[].content", "files[].data")


@command("inventory.create", role="admin", mutating=True, redact=_FILES_REDACTED,
         max_input=files.MAX_BUNDLE * 4 // 3 + 64 * 1024,
         params={"name": Str(required=True, max_length=200),
                 "kind": Choice(("file", "managed")), **_INVENTORY_PARAMS,
                 "files": _INVENTORY_FILES})
def inventory_create(ctx: Context, params: Values) -> dict:
    """Create an inventory: a path of a project, or a tree of files written
    here (a main file, hosts.yml or hosts, with group_vars and host_vars
    beside it, say), which file.list and file.change work on by its id."""
    name = store.check_name(params["name"])
    kind = params["kind"]
    if kind == "file" and params["files"]:
        raise BadRequest("unknown parameter: files")
    values = _inventory_values(ctx, params, kind)
    operations = []
    for index, item in enumerate(params["files"]):
        if not isinstance(item, dict) or "op" in item or "expected" in item:
            raise BadRequest(f"files[{index}] must have path, and content or data")
        operations.append({"op": "write", **item})
    inventory_id = store.new_id("i")
    store.insert(ctx.conn, "inventories", {"id": inventory_id, "name": name, "kind": kind,
                                           "created_at": store.now(), **values}, "inventory")
    if kind == "managed":
        row = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
        try:
            _write_main(ctx, row, params["content"], operations)
        except BaseException:
            store.delete(ctx.conn, "inventories", inventory_id, "inventory")
            shutil.rmtree(paths.INVENTORIES_DIR / inventory_id, ignore_errors=True)
            raise
    return _inventory(store.fetch(ctx.conn, "inventories", inventory_id, "inventory"))


@command("inventory.update", role="admin", mutating=True, redact=("content",),
         params={"id": Id(), "name": Str(max_length=200), **_INVENTORY_PARAMS})
def inventory_update(ctx: Context, params: Values) -> dict:
    """Change an inventory; for one written here, content replaces the text
    of its main file, as a commit."""
    inventory_id = params["id"]
    row = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    name = store.check_name(row["name"] if params["name"] is None else params["name"])
    values = _inventory_values(ctx, params, row["kind"], row)
    store.update(ctx.conn, "inventories", inventory_id, {"name": name, **values}, "inventory")
    row = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    if row["kind"] == "managed":
        _write_main(ctx, row, params["content"], [])
    return _inventory(store.fetch(ctx.conn, "inventories", inventory_id, "inventory"))


@command("inventory.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def inventory_delete(ctx: Context, params: Values) -> dict:
    """Delete an inventory that no template uses, and the files of one
    written here."""
    inventory_id = params["id"]
    store.delete(ctx.conn, "inventories", inventory_id, "inventory")
    shutil.rmtree(paths.INVENTORIES_DIR / check_id(inventory_id, "inventory id"),
                  ignore_errors=True)
    return {}


def place_inventory(ctx: Context, inventory, directory: Path, project_id: str | None,
                    tree: Path | None = None) -> dict:
    """Make an inventory available to a job; returns the spec keys that point to it.

    A file inventory of the project the job copies is read there. One of
    another project gets a copy of what it reads, apart, as inventory/: its
    directory, or the directory its file is in (with the group_vars and
    host_vars beside it), at the same path; the whole project for a file at
    its top. PROJECT_ID None: the caller copied the inventory's own project.
    TREE is where the files of a managed inventory are read, if not its own
    (a draft of it).
    """
    if inventory["kind"] == "managed":
        # A copy of its tree, as a project is copied: runs share nothing they write.
        jobs.snapshot(tree or files.inventory_tree(ctx, inventory), directory / "inventory",
                      "inventory")
        return {"inventory": inventory["path"] or ".", "inventory_source": "managed"}
    if project_id is not None and inventory["project_id"] != project_id:
        other = store.fetch(ctx.conn, "projects", inventory["project_id"], "project")
        tree = jobs.project_tree(other, ctx.config)
        path = PurePosixPath(inventory["path"])
        # Opened part by part from /, following no link: a link on the way is refused.
        scope = path if (tree / path).is_dir() and not (tree / path).is_symlink() \
            else path.parent
        jobs.snapshot(tree / scope, directory / "inventory" / scope, "inventory", directory)
        return {"inventory": inventory["path"], "inventory_source": "copy",
                "inventory_scripts": ctx.config.inventory_scripts}
    return {"inventory": inventory["path"], "inventory_source": "project",
            "inventory_scripts": ctx.config.inventory_scripts}


@command("inventory.parse", role="operator", mutating=True,
         params={
             "id": Id(),
         })
def inventory_parse(ctx: Context, params: Values) -> dict:
    """Queue a job that lists the hosts and groups of an inventory."""
    inventory = store.fetch(ctx.conn, "inventories", params["id"], "inventory")
    return {"run_id": queue_parse(ctx, inventory)}


def queue_parse(ctx: Context, inventory, created_by: str | None = None,
                start: bool = True, syntax: bool = False, tree: Path | None = None,
                draft: str = "", revision: str | None = None) -> str:
    """Queue the job that lists the hosts and groups of an inventory. With
    SYNTAX, a syntax check instead: the same reading, where any source that
    cannot be read fails it, and the hosts listed are not kept; of the files
    in TREE, at REVISION, for a DRAFT of a managed inventory."""
    inventory_id = inventory["id"]
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        if inventory["kind"] == "file":
            project = store.fetch(ctx.conn, "projects", inventory["project_id"], "project")
            jobs.snapshot(jobs.project_tree(project, ctx.config), directory / "project")
            jobs.place_collections(project, directory)
        else:
            (directory / "project").mkdir(mode=0o700)
        spec = {"kind": "syntax" if syntax else "inventory",
                **place_inventory(ctx, inventory, directory, None, tree)}
        jobs.add_secret(directory, inventory["vault_credential_id"],
                        "vault_password", "vault_password")
        jobs.write_known_hosts(ctx.conn, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    run = {"id": run_id, "kind": spec["kind"],
           "title": inventory["name"] + (" (draft)" if draft else ""),
           "inventory_id": inventory_id, "project_id": inventory["project_id"]}
    if syntax:
        run["revision"] = revision or inventory["revision"]
    if draft:
        run["options"] = json.dumps({"draft": draft})
    if created_by:
        run["created_by"] = created_by
    (jobs.enqueue if start else jobs.queue)(ctx, run, spec, directory)
    return run_id


# --- host keys -------------------------------------------------------------------

def _known_host(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _fingerprint(key: str) -> str:
    try:
        digest = hashlib.sha256(base64.b64decode(key, validate=True)).digest()
    except ValueError as error:
        raise BadRequest("the key is not valid base64") from error
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


@command("hostkey.list", role="viewer")
def hostkey_list(ctx: Context, params: Values) -> dict:
    """The SSH host keys trusted for runs, with their fingerprints."""
    rows = ctx.conn.execute("SELECT * FROM host_keys ORDER BY host, key_type")
    return {"keys": [{"id": row["id"], "host": row["host"], "key_type": row["key_type"],
                      "fingerprint": _fingerprint(row["key"]), "added_by": row["added_by"],
                      "added_at": row["added_at"]} for row in rows]}


_SSH_CONNECTIONS = ("", "ssh", "smart", "paramiko", "ansible.builtin.ssh")
MAX_SCAN_HOSTS = 1000


def _keyscan(hosts: list[str], port: int) -> list[tuple[str, str, str]]:
    """(known_hosts name, key type, key) for every key the hosts offer on a port."""
    try:
        result = subprocess.run(
            ["/usr/bin/ssh-keyscan", "-T", "5", "-p", str(port), "--", *hosts],
            capture_output=True, text=True, timeout=60 + len(hosts), check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(f"cannot scan: {error}") from error
    found = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and not line.startswith("#"):
            found.append((parts[0], parts[1], parts[2]))
    return found


def _key_state(ctx: Context, host: str, key_type: str, key: str) -> str:
    """new, trusted (the same key is already trusted) or changed (a different one is)."""
    row = ctx.conn.execute("SELECT key FROM host_keys WHERE host = ? AND key_type = ?",
                           (host, key_type)).fetchone()
    if row is None:
        return "new"
    return "trusted" if row["key"] == key else "changed"


@command("hostkey.scan", role="admin",
         params={
             "inventory_id": Id(required=False),
             "host": Str(max_length=253),
             "port": Int(1, 65535, default=22),
         })
def hostkey_scan(ctx: Context, params: Values) -> dict:
    """Ask one host, or every SSH host of an inventory, for its keys.

    Nothing is trusted until hostkey.add. Each key says whether it is new,
    already trusted, or different from the one trusted so far.
    """
    inventory_id = params["inventory_id"]
    if inventory_id is not None:
        params.unused("host", "port")
        jobs.reconcile(ctx)
        inventory = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
        targets: dict[int, list[str]] = {}
        for host in store.loads(inventory["hosts"], []):
            if host.get("connection", "") not in _SSH_CONNECTIONS:
                continue
            try:
                address = check_host(host.get("address") or host["name"])
            except BadRequest:
                continue
            targets.setdefault(host.get("port") or 22, []).append(address)
        if sum(len(hosts) for hosts in targets.values()) > MAX_SCAN_HOSTS:
            raise BadRequest(f"more than {MAX_SCAN_HOSTS} hosts: scan a smaller inventory")
    else:
        host = check_host(params.need("host"))
        targets = {params["port"]: [host]}

    keys, unanswered = [], []
    for port, hosts in sorted(targets.items()):
        unique = sorted(set(hosts))
        answered = set()
        for name, key_type, key in _keyscan(unique, port):
            answered.add(name)
            keys.append({"host": name, "key_type": key_type, "key": key,
                         "fingerprint": _fingerprint(key),
                         "state": _key_state(ctx, name, key_type, key)})
        unanswered += [host for host in unique if _known_host(host, port) not in answered]
    if not keys and inventory_id is None:
        raise NotFound(f"{unanswered[0]} did not offer any SSH host key")
    return {"keys": keys, "unanswered": unanswered}


@command("hostkey.add", role="admin", mutating=True, agent="code",
         params={
             "host": Str(required=True, max_length=300),
             "key_type": Str(required=True, max_length=64),
             "key": Str(required=True, max_length=8192),
             "replace": Bool(),
         })
def hostkey_add(ctx: Context, params: Values) -> dict:
    """Trust one SSH host key. A key that differs from the trusted one needs replace."""
    host = params["host"]
    key_type = params["key_type"]
    key = params["key"]
    replace = params["replace"]
    check_known_host(host)
    check_key_type(key_type)
    _fingerprint(key)
    # A different key for a host already trusted is what an attack looks like:
    # it is only accepted when asked for explicitly.
    if _key_state(ctx, host, key_type, key) == "changed" and not replace:
        raise Conflict(f"{host} is trusted with a different {key_type} key")
    ctx.conn.execute(
        "INSERT INTO host_keys (host, key_type, key, added_by, added_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (host, key_type) DO UPDATE SET key = excluded.key,"
        " added_by = excluded.added_by, added_at = excluded.added_at",
        (host, key_type, key, ctx.caller.user, store.now()),
    )
    return {}


@command("hostkey.remove", role="admin", mutating=True, agent="code",
         params={
             "id": Int(1, 2**62),
         })
def hostkey_remove(ctx: Context, params: Values) -> dict:
    """Stop trusting one SSH host key."""
    key_id = params["id"]
    if ctx.conn.execute("DELETE FROM host_keys WHERE id = ?", (key_id,)).rowcount == 0:
        raise NotFound("no such host key")
    return {}


# --- job templates -----------------------------------------------------------------

def template_vaults(ctx: Context, template_id: str) -> list:
    """The vault credentials of a template, in the order they are offered to Ansible."""
    return ctx.conn.execute(
        "SELECT c.id, c.vault_id FROM template_vaults v"
        " JOIN credentials c ON c.id = v.credential_id"
        " WHERE v.template_id = ? ORDER BY v.position", (template_id,)).fetchall()


_LAST_RUN = ("SELECT r.template_id, r.id, r.status, r.created_at, r.finished_at FROM runs r"
             " JOIN (SELECT MAX(rowid) AS last FROM runs WHERE template_id IS NOT NULL{only}"
             " GROUP BY template_id) m ON r.rowid = m.last")


def _last_runs(ctx: Context, template_id: str | None = None) -> dict:
    """The last run of each template, or of one, by template id."""
    if template_id is None:
        rows = ctx.conn.execute(_LAST_RUN.format(only="")).fetchall()
    else:
        rows = ctx.conn.execute(_LAST_RUN.format(only=" AND template_id = ?"),
                                (template_id,)).fetchall()
    return {row["template_id"]: {key: row[key] for key in ("id", "status", "created_at",
                                                           "finished_at")} for row in rows}


def _template(ctx: Context, row, last_runs: dict | None = None) -> dict:
    if last_runs is None:
        last_runs = _last_runs(ctx, row["id"])
    data = {key: row[key] for key in ("id", "name", "project_id", "playbook", "inventory_id",
                                      "machine_credential_id", "limit_hosts", "forks",
                                      "verbosity")}
    data["vault_credential_ids"] = [vault["id"] for vault in template_vaults(ctx, row["id"])]
    data["become"] = bool(row["become"])
    data["approval"] = row["approval"]
    # What it was before approval had modes: still given, for whoever reads it.
    data["needs_approval"] = row["approval"] != "none"
    data["tags"] = store.loads(row["tags"], [])
    data["skip_tags"] = store.loads(row["skip_tags"], [])
    data["extra_vars"] = store.loads(row["extra_vars"], {})
    data["variable_set_ids"] = [one["id"] for one in variables.template_sets(ctx.conn, row["id"])]
    data["survey"] = store.loads(row["survey"], [])
    data["last_run"] = last_runs.get(row["id"])
    return data


_TEMPLATE_PARAMS = {
    "name": Str(required=True, max_length=200),
    "project_id": Id(),
    "playbook": Str(required=True, max_length=1024),
    "inventory_id": Id(),
    "machine_credential_id": Id(required=False),
    "vault_credential_ids": Strs(max_items=20),
    "limit_hosts": Str(default="", max_length=512),
    "tags": Strs(),
    "skip_tags": Strs(),
    "extra_vars": Obj(),
    "variable_set_ids": Strs("Sets of variables the template takes before its own extra"
                             " variables, in order: a later one wins.",
                             max_items=variables.MAX_PER_TEMPLATE),
    "survey": Objs(max_items=survey.MAX_FIELDS),
    "forks": Int(1, 200),
    "verbosity": Int(0, 4, default=0),
    "become": Bool(),
    "approval": Choice(approvals.MODES, "Whether each run waits for an approval: none;"
                       " confirm, which who launched it may give too; other, another person"
                       " (an administrator may approve their own, unless config.toml says"
                       " otherwise)", default="none"),
    "needs_approval": Bool("Deprecated: true is approval other"),
}


def _template_values(ctx: Context, params: Values) -> dict:
    name = store.check_name(params["name"])
    project_id = params["project_id"]
    inventory_id = params["inventory_id"]
    store.fetch(ctx.conn, "projects", project_id, "project")
    store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    limit = params["limit_hosts"]
    forks = params["forks"]
    if forks is not None and forks > ctx.config.max_forks:
        raise BadRequest(f"forks cannot exceed {ctx.config.max_forks} (config.toml)")
    extra_vars = variables.check_variables(params["extra_vars"], "extra_vars")
    return {
        "name": name,
        "project_id": project_id,
        "playbook": str(check_relative_path(params["playbook"],
                                            "playbook")),
        "inventory_id": inventory_id,
        "machine_credential_id":
            credential_of_kind(ctx, params["machine_credential_id"], "machine"),
        "limit_hosts": check_limit(limit) if limit else "",
        "tags": json.dumps([check_tag(tag) for tag in params["tags"]]),
        "skip_tags": json.dumps([check_tag(tag) for tag in params["skip_tags"]]),
        "extra_vars": json.dumps(extra_vars),
        "survey": json.dumps(survey.check_survey(params["survey"])),
        "forks": forks,
        "verbosity": check_int(params["verbosity"], "verbosity", 0, 4),
        "become": int(params["become"]),
        "approval": approvals.mode_of(
            {key: params[key] for key in ("approval", "needs_approval") if key in params.given},
            "a template"),
        "updated_at": store.now(),
    }


def vault_ids(ctx: Context, params: Values) -> list[str]:
    ids = params["vault_credential_ids"]
    if len(set(ids)) != len(ids):
        raise BadRequest("a vault credential is listed twice")
    labels = []
    for credential_id in ids:
        try:
            check_id(credential_id, "credential id")
        except ValueError as error:
            raise BadRequest(str(error)) from error
        credential_of_kind(ctx, credential_id, "vault")
        labels.append(store.fetch(ctx.conn, "credentials", credential_id, "credential")["vault_id"])
    if len(ids) > 1 and len(set(labels)) != len(labels):
        raise BadRequest("vault credentials used together need different vault identifiers")
    return ids


def _save_template(ctx: Context, template_id: str, values: dict, vaults: list[str],
                   create: bool, sets: list[str]) -> dict:
    conn = ctx.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        if create:
            store.insert(conn, "templates", {"id": template_id, "created_at": store.now(),
                                             **values}, "template")
        else:
            store.update(conn, "templates", template_id, values, "template")
            conn.execute("DELETE FROM template_vaults WHERE template_id = ?", (template_id,))
            conn.execute("DELETE FROM template_variable_sets WHERE template_id = ?",
                         (template_id,))
        for position, credential_id in enumerate(vaults):
            conn.execute("INSERT INTO template_vaults (template_id, credential_id, position)"
                         " VALUES (?, ?, ?)", (template_id, credential_id, position))
        for position, set_id in enumerate(sets):
            conn.execute("INSERT INTO template_variable_sets (template_id, set_id, position)"
                         " VALUES (?, ?, ?)", (template_id, set_id, position))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return _template(ctx, store.fetch(conn, "templates", template_id, "template"))


@command("template.list", role="viewer")
def template_list(ctx: Context, params: Values) -> dict:
    """The job templates: playbook, inventory, credentials, the questions each asks,
    and its last run (id, status, created_at, finished_at), or null."""
    rows = ctx.conn.execute("SELECT * FROM templates ORDER BY name COLLATE NOCASE").fetchall()
    last_runs = _last_runs(ctx)
    return {"templates": [_template(ctx, row, last_runs) for row in rows]}


@command("template.create", role="admin", mutating=True, params=_TEMPLATE_PARAMS)
def template_create(ctx: Context, params: Values) -> dict:
    """Create a job template."""
    values = _template_values(ctx, params)
    vaults = vault_ids(ctx, params)
    sets = variables.check_set_ids(ctx.conn, params["variable_set_ids"])
    return _save_template(ctx, store.new_id("t"), values, vaults, create=True, sets=sets)


@command("template.update", role="admin", mutating=True,
         params={"id": Id(), **_TEMPLATE_PARAMS})
def template_update(ctx: Context, params: Values) -> dict:
    """Change a job template."""
    template_id = params["id"]
    store.fetch(ctx.conn, "templates", template_id, "template")
    values = _template_values(ctx, params)
    vaults = vault_ids(ctx, params)
    sets = variables.check_set_ids(ctx.conn, params["variable_set_ids"])
    return _save_template(ctx, template_id, values, vaults, create=False, sets=sets)


@command("template.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
         })
def template_delete(ctx: Context, params: Values) -> dict:
    """Delete a job template; its past runs stay."""
    template_id = params["id"]
    store.delete(ctx.conn, "templates", template_id, "template")
    return {}
