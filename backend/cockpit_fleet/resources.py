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

from . import files, jobs, paths, store, survey
from .cli import Context, Params, command
from .config import MAX_UPLOAD_MB
from .errors import BadRequest, Conflict, NotFound, Unavailable
from .names import check_id
from .validators import (
    check_git_ref,
    check_git_url,
    check_host,
    check_int,
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


def _credential_of_kind(ctx: Context, credential_id: str | None, kind: str) -> str | None:
    if credential_id is None:
        return None
    row = store.fetch(ctx.conn, "credentials", credential_id, "credential")
    if row["kind"] != kind:
        raise BadRequest(f"credential {row['name']!r} is not a {kind} credential")
    return credential_id


@command("credential.list", role="viewer")
def credential_list(ctx: Context, params: Params) -> dict:
    params.done()
    rows = ctx.conn.execute("SELECT * FROM credentials ORDER BY name COLLATE NOCASE")
    return {"credentials": [_credential(row) for row in rows]}


def _credential_values(params: Params, kind: str) -> dict:
    name = store.check_name(params.string("name", max_length=200))
    username = params.string("username", "", 64)
    vault_id = params.string("vault_id", "", 64)
    if username:
        check_user(username)
    if vault_id:
        check_vault_id(vault_id)
    return {"name": name, "username": "" if kind == "vault" else username,
            "vault_id": vault_id if kind == "vault" else ""}


@command("credential.create", role="admin", mutating=True)
def credential_create(ctx: Context, params: Params) -> dict:
    kind = params.choice("kind", tuple(CREDENTIAL_FIELDS))
    values = _credential_values(params, kind)
    params.done()
    credential_id = store.new_id("c")
    store.insert(ctx.conn, "credentials", {"id": credential_id, "kind": kind,
                                           "created_at": store.now(), **values}, "credential")
    return _credential(store.fetch(ctx.conn, "credentials", credential_id, "credential"))


@command("credential.update", role="admin", mutating=True)
def credential_update(ctx: Context, params: Params) -> dict:
    """Change the name and the non-secret fields; secrets go through fleet-secret."""
    credential_id = params.ident("id")
    row = store.fetch(ctx.conn, "credentials", credential_id, "credential")
    values = _credential_values(params, row["kind"])
    params.done()
    store.update(ctx.conn, "credentials", credential_id, values, "credential")
    return _credential(store.fetch(ctx.conn, "credentials", credential_id, "credential"))


@command("credential.delete", role="admin", mutating=True)
def credential_delete(ctx: Context, params: Params) -> dict:
    credential_id = params.ident("id")
    params.done()
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
def project_list(ctx: Context, params: Params) -> dict:
    params.done()
    jobs.reconcile(ctx)  # a finished sync job may have a new revision to show
    rows = ctx.conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")
    return {"projects": [_project(ctx, row) for row in rows],
            "local_roots": list(ctx.config.local_project_roots),
            "max_upload_mb": ctx.config.max_upload_mb}


def _project_values(ctx: Context, params: Params, kind: str) -> dict:
    values: dict = {"name": store.check_name(params.string("name", max_length=200))}
    if kind == "local":
        values["path"] = params.string("path", max_length=1024)
        if not values["path"].startswith("/"):
            raise BadRequest("path must be absolute")
        jobs.project_tree({"kind": "local", **values}, ctx.config)
    elif kind == "git":
        values["url"] = check_git_url(params.string("url", max_length=500))
        values["ref"] = check_git_ref(params.string("ref", "main", 200))
        credential_id = params.optional_id("credential_id")
        values["credential_id"] = _credential_of_kind(ctx, credential_id, "source")
    return values


@command("project.create", role="admin", mutating=True)
def project_create(ctx: Context, params: Params) -> dict:
    kind = params.choice("kind", ("local", "git", "managed", "archive"))
    values = _project_values(ctx, params, kind)
    params.done()
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


@command("project.update", role="admin", mutating=True)
def project_update(ctx: Context, params: Params) -> dict:
    """Change a project; where its files come from cannot change."""
    project_id = params.ident("id")
    row = store.fetch(ctx.conn, "projects", project_id, "project")
    values = _project_values(ctx, params, row["kind"])
    params.done()
    store.update(ctx.conn, "projects", project_id, values, "project")
    return _project(ctx, store.fetch(ctx.conn, "projects", project_id, "project"))


@command("project.delete", role="admin", mutating=True)
def project_delete(ctx: Context, params: Params) -> dict:
    project_id = params.ident("id")
    params.done()
    store.delete(ctx.conn, "projects", project_id, "project")
    shutil.rmtree(paths.PROJECTS_DIR / project_id, ignore_errors=True)
    return {}


@command("project.sync", role="admin", mutating=True)
def project_sync(ctx: Context, params: Params) -> dict:
    project_id = params.ident("id")
    params.done()
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


@command("project.collections", role="admin", mutating=True)
def project_collections(ctx: Context, params: Params) -> dict:
    """Queue a job that installs the collections a project asks for.

    Also when they are installed already: a requirement may name a branch,
    and this is how what it points to now is fetched.
    """
    project_id = params.ident("id")
    params.done()
    project = store.fetch(ctx.conn, "projects", project_id, "project")
    return {"run_id": jobs.queue_collections(ctx, project)}


# The archive travels as base64 in the JSON on stdin, like every parameter.
_MAX_UPLOAD_TEXT = MAX_UPLOAD_MB * 1024 * 1024 * 4 // 3 + 4


@command("project.upload", role="admin", mutating=True, redact=("data",),
         max_input=_MAX_UPLOAD_TEXT + 64 * 1024)
def project_upload(ctx: Context, params: Params) -> dict:
    """Take an archive as the new files of a project; a job unpacks it.

    Nothing of the archive is read here but its bytes, to count and to digest
    them: the digest is the revision the project gets if the job succeeds.
    """
    project_id = params.ident("id")
    filename = params.string("filename", "", 1024)
    data = params.string("data", max_length=_MAX_UPLOAD_TEXT)
    params.done()
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


@command("project.files", role="admin")
def project_files(ctx: Context, params: Params) -> dict:
    """YAML files of a project, to choose a playbook or an inventory from."""
    project_id = params.ident("id")
    params.done()
    tree = jobs.project_tree(store.fetch(ctx.conn, "projects", project_id, "project"), ctx.config)
    found = []
    for current, directories, names in os.walk(tree):
        depth = len(Path(current).relative_to(tree).parts)
        directories[:] = sorted(d for d in directories
                                if not d.startswith(".") and d not in ("roles", "node_modules")
                                and depth < 3)
        for name in sorted(names):
            if name.endswith((".yml", ".yaml", ".ini")) or name == "hosts":
                found.append(str((Path(current) / name).relative_to(tree)))
        if len(found) > 500:
            break
    return {"files": found[:500]}


# --- inventories -------------------------------------------------------------------

def _inventory(row, with_hosts: bool = False) -> dict:
    hosts = store.loads(row["hosts"], [])
    data = {key: row[key] for key in ("id", "name", "kind", "project_id", "path", "format",
                                      "vault_credential_id", "parsed_at", "parse_error")}
    data["host_count"] = len(hosts)
    if with_hosts:
        data["hosts"] = hosts
        data["content"] = row["content"]
    return data


def _inventory_values(ctx: Context, params: Params, kind: str) -> dict:
    values: dict = {"vault_credential_id":
                    _credential_of_kind(ctx, params.optional_id("vault_credential_id"), "vault")}
    if kind == "file":
        values["project_id"] = params.ident("project_id")
        store.fetch(ctx.conn, "projects", values["project_id"], "project")
        values["path"] = str(check_relative_path(params.string("path", max_length=1024), "path"))
    else:
        values["format"] = params.choice("format", ("yaml", "ini"), "yaml")
        values["content"] = params.string("content", max_length=512 * 1024)
    return values


@command("inventory.list", role="viewer")
def inventory_list(ctx: Context, params: Params) -> dict:
    params.done()
    jobs.reconcile(ctx)
    rows = ctx.conn.execute("SELECT * FROM inventories ORDER BY name COLLATE NOCASE")
    return {"inventories": [_inventory(row) for row in rows]}


@command("inventory.get", role="viewer")
def inventory_get(ctx: Context, params: Params) -> dict:
    inventory_id = params.ident("id")
    params.done()
    jobs.reconcile(ctx)
    row = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    data = _inventory(row, with_hosts=True)
    if ctx.caller.role != "admin":
        # The text of a managed inventory may hold connection passwords.
        data["content"] = ""
    return data


@command("inventory.create", role="admin", mutating=True, redact=("content",))
def inventory_create(ctx: Context, params: Params) -> dict:
    name = store.check_name(params.string("name", max_length=200))
    kind = params.choice("kind", ("file", "managed"))
    values = _inventory_values(ctx, params, kind)
    params.done()
    inventory_id = store.new_id("i")
    store.insert(ctx.conn, "inventories", {"id": inventory_id, "name": name, "kind": kind,
                                           "created_at": store.now(), **values}, "inventory")
    return _inventory(store.fetch(ctx.conn, "inventories", inventory_id, "inventory"))


@command("inventory.update", role="admin", mutating=True, redact=("content",))
def inventory_update(ctx: Context, params: Params) -> dict:
    inventory_id = params.ident("id")
    row = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    name = store.check_name(params.string("name", row["name"], 200))
    values = _inventory_values(ctx, params, row["kind"])
    params.done()
    store.update(ctx.conn, "inventories", inventory_id, {"name": name, **values}, "inventory")
    return _inventory(store.fetch(ctx.conn, "inventories", inventory_id, "inventory"))


@command("inventory.delete", role="admin", mutating=True)
def inventory_delete(ctx: Context, params: Params) -> dict:
    inventory_id = params.ident("id")
    params.done()
    store.delete(ctx.conn, "inventories", inventory_id, "inventory")
    return {}


def place_inventory(ctx: Context, inventory, directory: Path, project_id: str | None) -> dict:
    """Make an inventory available to a job; returns the spec keys that point to it.

    A file inventory needs its project copied into the run directory, unless the
    caller already did that for the same project.
    """
    if inventory["kind"] == "managed":
        name = "hosts.yml" if inventory["format"] == "yaml" else "hosts"
        (directory / "inventory").mkdir(mode=0o700)
        (directory / "inventory" / name).write_text(inventory["content"])
        return {"inventory": name, "inventory_source": "managed"}
    if project_id is not None and inventory["project_id"] != project_id:
        raise Conflict("this inventory is a file of another project")
    return {"inventory": inventory["path"], "inventory_source": "project"}


@command("inventory.parse", role="operator", mutating=True)
def inventory_parse(ctx: Context, params: Params) -> dict:
    """Queue a job that lists the hosts and groups of an inventory."""
    inventory_id = params.ident("id")
    params.done()
    inventory = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        if inventory["kind"] == "file":
            project = store.fetch(ctx.conn, "projects", inventory["project_id"], "project")
            jobs.snapshot(jobs.project_tree(project, ctx.config), directory / "project")
            jobs.place_collections(project, directory)
        else:
            (directory / "project").mkdir(mode=0o700)
        spec = {"kind": "inventory", **place_inventory(ctx, inventory, directory, None)}
        jobs.add_secret(directory, inventory["vault_credential_id"],
                        "vault_password", "vault_password")
        jobs.write_known_hosts(ctx.conn, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    jobs.enqueue(ctx, {"id": run_id, "kind": "inventory", "title": inventory["name"],
                       "inventory_id": inventory_id, "project_id": inventory["project_id"]},
                 spec, directory)
    return {"run_id": run_id}


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
def hostkey_list(ctx: Context, params: Params) -> dict:
    params.done()
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


@command("hostkey.scan", role="admin")
def hostkey_scan(ctx: Context, params: Params) -> dict:
    """Ask one host, or every SSH host of an inventory, for its keys.

    Nothing is trusted until hostkey.add. Each key says whether it is new,
    already trusted, or different from the one trusted so far.
    """
    inventory_id = params.optional_id("inventory_id")
    if inventory_id is not None:
        params.done()
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
        host = check_host(params.string("host", max_length=253))
        targets = {params.integer("port", 22, 1, 65535): [host]}
        params.done()

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


@command("hostkey.add", role="admin", mutating=True)
def hostkey_add(ctx: Context, params: Params) -> dict:
    host = params.string("host", max_length=300)
    key_type = params.string("key_type", max_length=64)
    key = params.string("key", max_length=8192)
    replace = params.boolean("replace")
    params.done()
    bare = host[1:].split("]:")[0] if host.startswith("[") else host
    check_host(bare)
    if not key_type.replace("-", "").replace("@", "").replace(".", "").isalnum():
        raise BadRequest("invalid key type")
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


@command("hostkey.remove", role="admin", mutating=True)
def hostkey_remove(ctx: Context, params: Params) -> dict:
    key_id = params.integer("id", None, 1, 2**62)
    params.done()
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


def _template(ctx: Context, row) -> dict:
    data = {key: row[key] for key in ("id", "name", "project_id", "playbook", "inventory_id",
                                      "machine_credential_id", "limit_hosts", "forks",
                                      "verbosity")}
    data["vault_credential_ids"] = [vault["id"] for vault in template_vaults(ctx, row["id"])]
    data["become"] = bool(row["become"])
    data["tags"] = store.loads(row["tags"], [])
    data["skip_tags"] = store.loads(row["skip_tags"], [])
    data["extra_vars"] = store.loads(row["extra_vars"], {})
    data["survey"] = store.loads(row["survey"], [])
    return data


def _template_values(ctx: Context, params: Params) -> dict:
    name = store.check_name(params.string("name", max_length=200))
    project_id = params.ident("project_id")
    inventory_id = params.ident("inventory_id")
    store.fetch(ctx.conn, "projects", project_id, "project")
    inventory = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
    if inventory["kind"] == "file" and inventory["project_id"] != project_id:
        raise BadRequest("a file inventory can only be used with its own project")
    limit = params.string("limit_hosts", "", 512)
    forks = params.integer("forks", None, 1, 200)
    if forks is not None and forks > ctx.config.max_forks:
        raise BadRequest(f"forks cannot exceed {ctx.config.max_forks} (config.toml)")
    extra_vars = params.mapping("extra_vars")
    if len(json.dumps(extra_vars)) > 64 * 1024:
        raise BadRequest("extra_vars is too large")
    return {
        "name": name,
        "project_id": project_id,
        "playbook": str(check_relative_path(params.string("playbook", max_length=1024),
                                            "playbook")),
        "inventory_id": inventory_id,
        "machine_credential_id":
            _credential_of_kind(ctx, params.optional_id("machine_credential_id"), "machine"),
        "limit_hosts": check_limit(limit) if limit else "",
        "tags": json.dumps([check_tag(tag) for tag in params.strings("tags")]),
        "skip_tags": json.dumps([check_tag(tag) for tag in params.strings("skip_tags")]),
        "extra_vars": json.dumps(extra_vars),
        "survey": json.dumps(survey.check_survey(params.objects("survey", survey.MAX_FIELDS))),
        "forks": forks,
        "verbosity": check_int(params.integer("verbosity", 0, 0, 4), "verbosity", 0, 4),
        "become": int(params.boolean("become")),
        "updated_at": store.now(),
    }


def _vault_ids(ctx: Context, params: Params) -> list[str]:
    ids = params.strings("vault_credential_ids", 20)
    if len(set(ids)) != len(ids):
        raise BadRequest("a vault credential is listed twice")
    labels = []
    for credential_id in ids:
        try:
            check_id(credential_id, "credential id")
        except ValueError as error:
            raise BadRequest(str(error)) from error
        _credential_of_kind(ctx, credential_id, "vault")
        labels.append(store.fetch(ctx.conn, "credentials", credential_id, "credential")["vault_id"])
    if len(ids) > 1 and len(set(labels)) != len(labels):
        raise BadRequest("vault credentials used together need different vault identifiers")
    return ids


def _save_template(ctx: Context, template_id: str, values: dict, vaults: list[str],
                   create: bool) -> dict:
    conn = ctx.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        if create:
            store.insert(conn, "templates", {"id": template_id, "created_at": store.now(),
                                             **values}, "template")
        else:
            store.update(conn, "templates", template_id, values, "template")
            conn.execute("DELETE FROM template_vaults WHERE template_id = ?", (template_id,))
        for position, credential_id in enumerate(vaults):
            conn.execute("INSERT INTO template_vaults (template_id, credential_id, position)"
                         " VALUES (?, ?, ?)", (template_id, credential_id, position))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return _template(ctx, store.fetch(conn, "templates", template_id, "template"))


@command("template.list", role="viewer")
def template_list(ctx: Context, params: Params) -> dict:
    params.done()
    rows = ctx.conn.execute("SELECT * FROM templates ORDER BY name COLLATE NOCASE").fetchall()
    return {"templates": [_template(ctx, row) for row in rows]}


@command("template.create", role="admin", mutating=True)
def template_create(ctx: Context, params: Params) -> dict:
    values = _template_values(ctx, params)
    vaults = _vault_ids(ctx, params)
    params.done()
    return _save_template(ctx, store.new_id("t"), values, vaults, create=True)


@command("template.update", role="admin", mutating=True)
def template_update(ctx: Context, params: Params) -> dict:
    template_id = params.ident("id")
    store.fetch(ctx.conn, "templates", template_id, "template")
    values = _template_values(ctx, params)
    vaults = _vault_ids(ctx, params)
    params.done()
    return _save_template(ctx, template_id, values, vaults, create=False)


@command("template.delete", role="admin", mutating=True)
def template_delete(ctx: Context, params: Params) -> dict:
    template_id = params.ident("id")
    params.done()
    store.delete(ctx.conn, "templates", template_id, "template")
    return {}
