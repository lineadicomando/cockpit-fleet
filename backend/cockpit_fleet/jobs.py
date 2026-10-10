"""Life cycle of a job: prepare its directory, queue it, start it, collect it.

There is no daemon keeping track of jobs. The state lives in the runs table
and is brought up to date by reconcile(), which is called when a run unit
ends (cockpit-fleet-finish@.service) and whenever runs are read.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from . import journal, paths, store
from .errors import BadRequest, Conflict, FleetError, Unavailable
from .names import check_id

MAX_PROJECT_FILES = 20_000
MAX_PROJECT_BYTES = 500 * 1024 * 1024
# A unit that is neither active nor finished this long after its start is lost.
_START_GRACE = 20

ACTIVE = ("queued", "running")


def run_dir(run_id: str) -> Path:
    return paths.RUNS_DIR / check_id(run_id, "run id")


def secret_name(credential_id: str, field: str) -> str:
    return f"c-{credential_id}-{field}"


def secret_file(credential_id: str, field: str) -> Path:
    return paths.SECRETS_DIR / (secret_name(credential_id, field) + ".cred")


def create_dir(run_id: str) -> Path:
    directory = run_dir(run_id)
    directory.mkdir(mode=0o700)
    (directory / "creds").mkdir(mode=0o700)
    return directory


def add_secret(directory: Path, credential_id: str | None, field: str, purpose: str) -> None:
    """Give the run the ciphertext of one secret, if that secret is set."""
    if credential_id is None:
        return
    source = secret_file(credential_id, field)
    if source.is_file():
        shutil.copyfile(source, directory / "creds" / purpose)


def take_answer(directory: Path, ticket: str, purpose: str) -> None:
    """Move the ciphertext of a secret answer into the run. It serves one run."""
    source = paths.ANSWERS_DIR / f"{ticket}.cred"
    try:
        shutil.copyfile(source, directory / "creds" / purpose)
        source.unlink()
    except FileNotFoundError:
        raise BadRequest(
            "a secret answer has expired or was already used: enter it again") from None


def project_tree(project, config) -> Path:
    """Where the files of a project are on this machine."""
    if project["kind"] in ("git", "managed", "archive"):
        tree = paths.PROJECTS_DIR / project["id"] / "tree"
        if not tree.is_dir():
            raise Conflict("this project has no files yet: upload an archive"
                           if project["kind"] == "archive"
                           else "this project has not been synchronised yet")
        return tree
    tree = Path(project["path"]).resolve()
    roots = [Path(root).resolve() for root in config.local_project_roots]
    if not any(tree == root or tree.is_relative_to(root) for root in roots):
        raise Conflict(f"{project['path']} is not under a directory allowed in config.toml")
    if not tree.is_dir():
        raise Conflict(f"{project['path']} is not a directory")
    return tree


def snapshot(tree: Path, destination: Path) -> None:
    """Copy a project for one run. Symbolic links are copied, never followed."""
    files = size = 0
    for current, directories, names in os.walk(tree):
        directories[:] = [name for name in directories if name != ".git"]
        target = destination / os.path.relpath(current, tree)
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in names:
            source = Path(current) / name
            files += 1
            if not source.is_symlink():
                if not source.is_file():
                    continue  # sockets, devices, fifos
                size += source.stat().st_size
            if files > MAX_PROJECT_FILES or size > MAX_PROJECT_BYTES:
                raise Conflict("the project is too large to be copied for a run")
            shutil.copy2(source, target / name, follow_symlinks=False)
        for name in directories:
            source = Path(current) / name
            if source.is_symlink():
                os.symlink(os.readlink(source), target / name)
        directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]


def write_known_hosts(conn, directory: Path) -> None:
    rows = conn.execute("SELECT host, key_type, key FROM host_keys ORDER BY host")
    text = "".join(f"{row['host']} {row['key_type']} {row['key']}\n" for row in rows)
    (directory / "known_hosts").write_text(text)


def enqueue(ctx, run: dict, spec: dict, directory: Path) -> None:
    (directory / "spec.json").write_text(json.dumps(spec))
    store.insert(ctx.conn, "runs", {**run, "status": "queued", "created_by": ctx.caller.user,
                                    "created_at": store.now()}, "run")
    dispatch(ctx)


def _fleet_unit(verb: str, run_id: str) -> None:
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", str(paths.LIBEXEC_DIR / "fleet-unit"), verb, run_id],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise Unavailable(result.stderr.strip() or f"cannot {verb} the run unit")


def dispatch(ctx) -> None:
    """Start queued runs while fewer than the configured maximum are running."""
    conn = ctx.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        running = conn.execute("SELECT count(*) FROM runs WHERE status = 'running'").fetchone()[0]
        free = max(ctx.config.max_concurrent_runs - running, 0)
        chosen = [row["id"] for row in conn.execute(
            "SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at LIMIT ?", (free,))]
        for run_id in chosen:
            conn.execute("UPDATE runs SET status = 'running', started_at = ? WHERE id = ?",
                         (store.now(), run_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    for run_id in chosen:
        try:
            _fleet_unit("start", run_id)
        except FleetError as error:
            _finish(ctx, run_id, "error", None, str(error))


def _unit_active(run_id: str) -> bool:
    result = subprocess.run(
        ["/usr/bin/systemctl", "is-active", "--quiet", paths.RUN_UNIT.format(run_id)],
        check=False, stdin=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _stats(directory: Path) -> dict:
    """Per-host totals from the last line of the events file, if the play got there."""
    try:
        with open(directory / "events.jsonl", "rb") as events:
            events.seek(0, os.SEEK_END)
            events.seek(max(events.tell() - 256 * 1024, 0))
            last = events.read().splitlines()[-1]
        event = json.loads(last)
    except (OSError, ValueError, IndexError):
        return {}
    return event.get("hosts", {}) if event.get("event") == "stats" else {}


def _finish(ctx, run_id: str, status: str, rc: int | None, error: str = "",
            extra: dict | None = None) -> None:
    values = {"status": status, "rc": rc, "error": error, "finished_at": store.now(),
              **(extra or {})}
    store.update(ctx.conn, "runs", run_id, values, "run")
    journal.send(f"run {run_id}: {status}", 6 if status == "successful" else 4,
                 FLEET_RUN=run_id, FLEET_STATUS=status)


def _collect(ctx, run) -> None:
    """Bring one 'running' row up to date with what its unit left behind."""
    run_id = run["id"]
    directory = run_dir(run_id)
    if _unit_active(run_id):
        return
    try:
        result = json.loads((directory / "result.json").read_text())
    except (OSError, ValueError):
        if store.now() - (run["started_at"] or 0) < _START_GRACE:
            return  # the unit may not have started yet
        if run["cancel_requested"]:
            _finish(ctx, run_id, "canceled", None)
        else:
            _finish(ctx, run_id, "error", None, "the run ended without a result")
        return

    rc = result.get("rc")
    if run["cancel_requested"] and (rc != 0 or result.get("terminated")):
        status = "canceled"
    else:
        status = "successful" if rc == 0 else "failed"
    extra = {"stats": json.dumps(_stats(directory))}
    error = str(result.get("error", ""))
    if run["kind"] == "syntax" and "lint_rc" in result:
        # ansible-lint ran too: what it found does not make the check fail.
        lint = "clean" if result["lint_rc"] == 0 else "findings"
        extra["options"] = json.dumps({**store.loads(run["options"], {}), "lint": lint})

    try:
        if status == "successful" and run["kind"] == "inventory":
            _store_inventory(ctx, run, directory)
        elif run["kind"] == "inventory":
            failure = {"parse_error": _log_tail(directory), "parsed_at": store.now()}
            store.update(ctx.conn, "inventories", run["inventory_id"], failure, "inventory")
        if status == "successful" and run["kind"] == "sync":
            revision = str(result.get("revision", ""))
            extra["revision"] = _store_checkout(ctx, run, directory, revision)
        elif run["kind"] == "unpack" and status == "successful":
            # The digest fleetctl took of what it received, not a claim of the job.
            revision = str(store.loads(run["options"], {}).get("sha256", ""))
            extra["revision"] = _store_checkout(ctx, run, directory, revision)
        elif run["kind"] in ("sync", "unpack") and run["project_id"]:
            store.update(ctx.conn, "projects", run["project_id"],
                         {"sync_error": _log_tail(directory)}, "project")
    except (FleetError, OSError) as failure:
        status, error = "error", str(failure)

    _finish(ctx, run_id, status, rc, error, extra)
    # The copy of the project and the ciphertext are only needed while it runs.
    for leftover in ("project", "creds", "checkout", "checkout.unpack", "inventory"):
        shutil.rmtree(directory / leftover, ignore_errors=True)
    (directory / "archive").unlink(missing_ok=True)


def _log_tail(directory: Path, limit: int = 2000) -> str:
    try:
        return (directory / "stdout.log").read_text(errors="replace")[-limit:]
    except OSError:
        return "no output"


def _store_inventory(ctx, run, directory: Path) -> None:
    hosts = parse_inventory(json.loads((directory / "inventory.json").read_text()))
    store.update(ctx.conn, "inventories", run["inventory_id"],
                 {"hosts": json.dumps(hosts), "parsed_at": store.now(), "parse_error": ""},
                 "inventory")


def parse_inventory(listing: dict) -> list[dict]:
    """Hosts with their groups, from `ansible-inventory --list`.

    Host variables may hold secrets, so only the connection address is kept.
    """
    hostvars = listing.get("_meta", {}).get("hostvars", {})
    groups: dict[str, list[str]] = {}

    def members(group: str, seen: tuple[str, ...] = ()) -> set[str]:
        entry = listing.get(group, {})
        found = set(entry.get("hosts", []))
        for child in entry.get("children", []):
            if child not in seen:
                found |= members(child, (*seen, group))
        return found

    for group in listing:
        if group in ("_meta", "all"):
            continue
        for host in members(group):
            groups.setdefault(host, []).append(group)

    names = set(hostvars) | set(groups) | members("all")
    def port(name: str) -> int | None:
        value = hostvars.get(name, {}).get("ansible_port")
        return value if isinstance(value, int) and 0 < value < 65536 else None

    return [
        {
            "name": name,
            "address": str(hostvars.get(name, {}).get("ansible_host", "")),
            "port": port(name),
            "connection": str(hostvars.get(name, {}).get("ansible_connection", "")),
            "groups": sorted(group for group in groups.get(name, []) if group != "ungrouped"),
        }
        for name in sorted(names)
    ]


def _store_checkout(ctx, run, directory: Path, revision: str) -> str:
    """Replace the project's files with the tree a sync or unpack job left."""
    if not run["project_id"]:
        raise Conflict("the project was deleted meanwhile")
    # What a job left is not trusted: a link here would become the tree.
    if not stat.S_ISDIR(os.lstat(directory / "checkout").st_mode):
        raise Conflict("the job did not leave a directory")
    home = paths.PROJECTS_DIR / check_id(run["project_id"], "project id")
    home.mkdir(mode=0o700, exist_ok=True)
    old = home / "tree.old"
    shutil.rmtree(old, ignore_errors=True)
    if (home / "tree").exists():
        (home / "tree").rename(old)
    (directory / "checkout").rename(home / "tree")
    shutil.rmtree(old, ignore_errors=True)
    store.update(ctx.conn, "projects", run["project_id"],
                 {"revision": revision, "synced_at": store.now(), "sync_error": ""}, "project")
    return revision


def reconcile(ctx) -> None:
    for run in ctx.conn.execute("SELECT * FROM runs WHERE status = 'running'").fetchall():
        _collect(ctx, run)
    dispatch(ctx)


def cancel(ctx, run_id: str) -> str:
    run = store.fetch(ctx.conn, "runs", run_id, "run")
    if run["status"] == "queued":
        _finish(ctx, run_id, "canceled", None)
        shutil.rmtree(run_dir(run_id), ignore_errors=True)
        return "canceled"
    if run["status"] != "running":
        raise BadRequest("this run has already ended")
    store.update(ctx.conn, "runs", run_id, {"cancel_requested": 1}, "run")
    _fleet_unit("stop", run_id)
    reconcile(ctx)
    return store.fetch(ctx.conn, "runs", run_id, "run")["status"]
