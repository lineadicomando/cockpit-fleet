"""Life cycle of a job: prepare its directory, queue it, start it, collect it.

There is no daemon keeping track of jobs. The state lives in the runs table
and is brought up to date by reconcile(), which is called when a run unit
ends (cockpit-fleet-finish@.service) and whenever runs are read.
"""

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import traceback
from pathlib import Path

from . import journal, notify, paths, store
from .errors import BadRequest, Conflict, FleetError, Unavailable
from .names import check_id
from .runner import CHECK_RCS
from .validators import check_key_type, check_known_host

MAX_PROJECT_FILES = 20_000
MAX_PROJECT_BYTES = 500 * 1024 * 1024
# A unit that is neither active nor finished this long after its start is lost.
_START_GRACE = 20

ACTIVE = ("queued", "running")
# Prepared, and held until someone approves it (see approvals.py).
WAITING = "pending_approval"

# Where a project may list the collections it needs; the first one found counts.
REQUIREMENTS = ("collections/requirements.yml", "collections/requirements.yaml",
                "requirements.yml", "requirements.yaml")
MAX_REQUIREMENTS = 256 * 1024
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


def run_dir(run_id: str) -> Path:
    return paths.RUNS_DIR / check_id(run_id, "run id")


# What a run leaves of its own beyond its output: result.json, inventory.json.
MAX_RUN_FILE = 64 * 1024 * 1024


def open_run_file(directory: Path, name: str, mode: str = "rb", **options):
    """A file in a run's directory, opened for reading. The run may write
    there, so it may have put a link in its place, which is not followed, or
    a FIFO, which is not waited on: either is as if the file were missing."""
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=dir_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise FileNotFoundError(errno.ENOENT, "not a regular file", name) from None
        raise
    finally:
        os.close(dir_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise FileNotFoundError(errno.ENOENT, "not a regular file", name)
    os.set_blocking(fd, True)
    return os.fdopen(fd, mode, **options)


def read_run_file(directory: Path, name: str, limit: int = MAX_RUN_FILE) -> bytes:
    """The whole of a small file of a run, refused above LIMIT bytes."""
    with open_run_file(directory, name) as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{name} is larger than {limit} bytes")
    return data


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
    # Taken first by a rename, which only one of two launches with it can do;
    # then copied, as /run is another file system, and the copy the account's.
    claimed = paths.ANSWERS_DIR / f".{ticket}.{os.getpid()}"
    try:
        os.rename(source, claimed)
    except FileNotFoundError:
        raise BadRequest(
            "a secret answer has expired or was already used: enter it again") from None
    try:
        shutil.copyfile(claimed, directory / "creds" / purpose)
    finally:
        claimed.unlink(missing_ok=True)


# What the units of the service (the scheduler, the end of a run) do not see:
# ProtectHome and PrivateTmp hide it. A project there would work when launched
# from the interface, through sudo, and fail whenever a timer started it.
HIDDEN_FROM_UNITS = ("/home", "/root", "/run/user", "/tmp", "/var/tmp")  # noqa: S108


def hidden_from_units(path: str | Path) -> str | None:
    """The directory of HIDDEN_FROM_UNITS that PATH, or where it leads, is in."""
    for candidate in (Path(path), Path(path).resolve()):
        for hidden in HIDDEN_FROM_UNITS:
            if candidate == Path(hidden) or candidate.is_relative_to(hidden):
                return hidden
    return None


def project_tree(project, config) -> Path:
    """Where the files of a project are on this machine."""
    if project["kind"] in ("git", "managed", "archive"):
        # The account's own: resolved only so that a link of the system on the
        # way (/var, say) does not stop snapshot(), which follows none.
        tree = (paths.PROJECTS_DIR / project["id"] / "tree").resolve()
        if not tree.is_dir():
            raise Conflict("this project has no files yet: upload an archive"
                           if project["kind"] == "archive"
                           else "this project has not been synchronised yet")
        return tree
    hidden = hidden_from_units(project["path"])
    if hidden:
        raise Conflict(f"{project['path']} is under {hidden}, which the scheduler and the"
                       " other units of Fleet cannot see: move it under a directory of"
                       " local_roots, or bind-mount it there to keep it where it is")
    tree = Path(project["path"]).resolve()
    roots = [Path(root).resolve() for root in config.local_project_roots]
    if not any(tree == root or tree.is_relative_to(root) for root in roots):
        raise Conflict(f"{project['path']} is not under a directory allowed in config.toml")
    if not tree.is_dir():
        raise Conflict(f"{project['path']} is not a directory that the service account"
                       " can see")
    return tree


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def open_tree(tree: Path) -> int:
    """A directory, opened part by part from /, following no link: TREE is a
    resolved path, and what changed since it was resolved is refused."""
    fd = os.open("/", _DIR_FLAGS)
    try:
        for part in tree.parts[1:]:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def snapshot(tree: Path, destination: Path, what: str = "project") -> list[str]:
    """Copy a project for one run. Symbolic links are copied, never followed:
    each directory and file is opened from the one that holds it, so a local
    tree that its owner changes while it is copied cannot lead outside it.

    What the service account cannot read (logs a tool of the owner left in a
    local project, say) is left out rather than failing the run, and told at
    the start of the run's output, in the directory DESTINATION is in, as
    paths of WHAT. Returns the paths left out, relative to the tree."""
    counts = [0, 0]
    skipped: list[str] = []
    try:
        fd = open_tree(tree)
    except OSError as error:
        raise Conflict(f"cannot read the {what}: {error.strerror}") from None
    try:
        _copy_tree(fd, destination, counts, skipped, "")
    finally:
        os.close(fd)
    if skipped:
        _tell_skipped(destination.parent, skipped, what)
    return skipped


# How many of the paths left out a run's output names.
SKIPPED_SHOWN = 20


def _tell_skipped(directory: Path, skipped: list[str], what: str) -> None:
    lines = [f"fleet: {len(skipped)} paths of the {what} were left out of this run:"
             " the service account cannot read them\n"]
    lines += [f"  {path}\n" for path in skipped[:SKIPPED_SHOWN]]
    if len(skipped) > SKIPPED_SHOWN:
        lines.append(f"  and {len(skipped) - SKIPPED_SHOWN} more\n")
    fd = os.open(directory / "stdout.log",
                 os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", errors="replace") as log:
        log.write("".join(lines))


def _copy_tree(source: int, target: Path, counts: list[int], skipped: list[str],
               prefix: str) -> None:
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.scandir(source) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        try:
            info = os.stat(name, dir_fd=source, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except PermissionError:  # a directory readable but not searchable
            skipped.append(prefix + name)
            continue
        if stat.S_ISDIR(info.st_mode):
            if name == ".git":
                continue
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=source)
            except PermissionError:
                skipped.append(prefix + name + "/")
                continue
            try:
                _copy_tree(child, target / name, counts, skipped, prefix + name + "/")
            finally:
                os.close(child)
            continue
        if not (stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode)):
            continue  # sockets, devices, fifos
        counts[0] += 1
        if stat.S_ISLNK(info.st_mode):
            os.symlink(os.readlink(name, dir_fd=source), target / name)
        else:
            try:
                _copy_file(source, name, target / name, counts)
            except PermissionError:
                skipped.append(prefix + name)
        if counts[0] > MAX_PROJECT_FILES or counts[1] > MAX_PROJECT_BYTES:
            raise Conflict("the project is too large to be copied for a run")


def _copy_file(source: int, name: str, target: Path, counts: list[int]) -> None:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=source)
    with os.fdopen(fd, "rb") as reader:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return  # changed into something else since it was listed
        counts[1] += info.st_size
        if counts[1] > MAX_PROJECT_BYTES:
            raise Conflict("the project is too large to be copied for a run")
        out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      info.st_mode & 0o777)
        with os.fdopen(out, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns), follow_symlinks=False)


def requirements(tree: Path) -> tuple[str, str] | None:
    """The requirements file of a project and the digest of its content.

    It is read here, outside the sandbox, so a symbolic link is not followed.
    """
    for name in REQUIREMENTS:
        target = tree / name
        try:
            if target.parent.is_symlink():
                continue
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            continue
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                continue
            data = source.read(MAX_REQUIREMENTS + 1)
        if len(data) > MAX_REQUIREMENTS:
            raise Conflict(f"{name} is larger than {MAX_REQUIREMENTS // 1024} kB")
        return name, hashlib.sha256(data).hexdigest()
    return None


def collections_home(project_id: str) -> Path:
    """Where the collections installed for a project are kept, beside its tree:
    one directory, named after the digest of the requirements file it answers."""
    return paths.PROJECTS_DIR / check_id(project_id, "project id") / "collections"


def collections_state(project, config) -> dict:
    """Whether a project asks for collections, and whether they are installed."""
    try:
        found = requirements(project_tree(project, config))
    except FleetError:
        found = None
    if found is None:
        return {"file": "", "state": "none"}
    installed = (collections_home(project["id"]) / found[1]).is_dir()
    return {"file": found[0], "state": "installed" if installed else "missing"}


def place_collections(project, directory: Path, required: bool = False) -> None:
    """Give a job a copy of the collections installed for its project.

    The requirements file is the one of the job's own copy of the project.
    A playbook does not start without what its project asks for; a check or
    an inventory listing goes on with what there is.
    """
    found = requirements(directory / "project")
    if found is None:
        return
    source = collections_home(project["id"]) / found[1]
    if not source.is_dir():
        if required:
            raise Conflict(f"the collections listed in {found[0]} are not installed yet:"
                           " install them from the list of projects")
        return
    try:
        snapshot(source, directory / "collections", "collections")
    except OSError:
        raise Conflict("the collections of this project are being replaced: try again") from None


def queue_collections(ctx, project, created_by: str | None = None, start: bool = True) -> str:
    """Queue a job that installs what the requirements file of a project lists."""
    tree = project_tree(project, ctx.config)
    run_id = store.new_id("r")
    directory = create_dir(run_id)
    try:
        snapshot(tree, directory / "project")
        found = requirements(directory / "project")
        if found is None:
            raise Conflict("this project has no requirements file: " + ", ".join(REQUIREMENTS))
        write_known_hosts(ctx.conn, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    path, digest = found
    run = {"id": run_id, "kind": "galaxy", "title": project["name"],
           "project_id": project["id"], "revision": project["revision"],
           # The digest names what the job leaves: taken here, not by the job.
           "options": json.dumps({"path": path, "sha256": digest})}
    if created_by:
        run["created_by"] = created_by
    spec = {"kind": "galaxy", "requirements": path, "proxy": ctx.config.proxy}
    (enqueue if start else queue)(ctx, run, spec, directory)
    return run_id


def install_missing(ctx, project_id: str | None, created_by: str | None = None,
                    start: bool = True) -> str | None:
    """Queue the installation when the files of a project changed and what it
    asks for now is not there; returns the job, if one was queued."""
    row = ctx.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None or collections_state(row, ctx.config)["state"] != "missing":
        return None
    if ctx.conn.execute("SELECT 1 FROM runs WHERE kind = 'galaxy' AND project_id = ?"
                        " AND status IN ('queued', 'running')", (project_id,)).fetchone():
        return None
    return queue_collections(ctx, row, created_by, start)


_KEY_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def _known_host_line(row) -> str:
    """One line of known_hosts, or nothing for a row stored before hostkey.add
    checked what it does now: such a row could trust its key for other hosts."""
    try:
        check_known_host(row["host"])
        check_key_type(row["key_type"])
    except BadRequest:
        return ""
    if not isinstance(row["key"], str) or not _KEY_RE.fullmatch(row["key"]):
        return ""
    return f"{row['host']} {row['key_type']} {row['key']}\n"


def write_known_hosts(conn, directory: Path) -> None:
    rows = conn.execute("SELECT host, key_type, key FROM host_keys ORDER BY host")
    (directory / "known_hosts").write_text("".join(_known_host_line(row) for row in rows))


def queue(ctx, run: dict, spec: dict, directory: Path, status: str = "queued") -> None:
    """Record a job as queued, without starting anything: dispatch() will.
    One that waits for an approval is recorded as such, and dispatch() leaves
    it alone until it is queued."""
    (directory / "spec.json").write_text(json.dumps(spec))
    store.insert(ctx.conn, "runs", {"created_by": ctx.caller.user, **run, "status": status,
                                    "created_at": store.now()}, "run")


def enqueue(ctx, run: dict, spec: dict, directory: Path) -> None:
    queue(ctx, run, spec, directory)
    dispatch(ctx)


# How long systemctl, and fleet-unit, may take to answer.
_UNIT_TIMEOUT = 30


def _fleet_unit(verb: str, run_id: str) -> None:
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", str(paths.LIBEXEC_DIR / "fleet-unit"), verb, run_id],
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
            timeout=_UNIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(f"cannot {verb} the run unit: {error}") from error
    if result.returncode != 0:
        raise Unavailable(result.stderr.strip() or f"cannot {verb} the run unit")


def lanes(config) -> list[tuple[str, str, int]]:
    """The lanes of the dispatch: a name, which runs are in it, how many of
    them may run at once."""
    slow = "coalesce(json_extract(options, '$.slow'), 0)"
    # A workflow run has no unit of its own: only its steps count.
    return [("runs", "kind NOT IN ('check', 'workflow')", config.max_concurrent_runs),
            ("checks", f"kind = 'check' AND {slow} = 0", config.checks_max_concurrent),
            ("slow", f"kind = 'check' AND {slow} != 0", config.checks_max_concurrent)]


def dispatch(ctx) -> None:
    """Start queued runs while fewer than the configured maximum are running:
    those of a higher priority first, then the oldest. Check cycles have a
    limit of their own, and do not count against that of the other runs;
    cycles of the slow kinds have another, so as not to hold the others back."""
    conn = ctx.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        chosen = []
        for _name, lane, limit in lanes(ctx.config):
            running = conn.execute(f"SELECT count(*) FROM runs WHERE status = 'running'"  # noqa: S608
                                   f" AND {lane}").fetchone()[0]
            chosen += [row["id"] for row in conn.execute(
                f"SELECT id FROM runs WHERE status = 'queued' AND {lane}"  # noqa: S608
                " ORDER BY priority DESC, created_at LIMIT ?", (max(limit - running, 0),))]
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
            end_unstarted(ctx, run_id, "error", str(error))


def _unit_active(run_id: str) -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", paths.RUN_UNIT.format(run_id)],
            check=False, stdin=subprocess.DEVNULL, timeout=_UNIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(f"cannot ask systemd about the run unit: {error}") from error
    return result.returncode == 0


# What a playbook may pass on to the steps after it, as JSON.
MAX_ARTIFACTS = 64 * 1024


def _last_stats(directory: Path) -> dict:
    """The last line of the events file, if the play got to its stats."""
    try:
        with open_run_file(directory, "events.jsonl") as events:
            events.seek(0, os.SEEK_END)
            events.seek(max(events.tell() - 256 * 1024, 0))
            last = events.read().splitlines()[-1]
        event = json.loads(last)
    except (OSError, ValueError, IndexError):
        return {}
    return event if isinstance(event, dict) and event.get("event") == "stats" else {}


def _stats(directory: Path) -> dict:
    """Per-host totals from the last line of the events file, if the play got there."""
    return _last_stats(directory).get("hosts", {})


def _artifacts(directory: Path) -> str:
    """What the playbook set with set_stats, as JSON: too much is dropped."""
    found = _last_stats(directory).get("artifacts", {})
    if not isinstance(found, dict):
        found = {}
    # Only what the next steps can take as variables: a name Ansible reads
    # itself, or no name at all, would make the next step fail.
    from .survey import check_variable
    kept = {}
    for name, value in found.items():
        try:
            kept[check_variable(name)] = value
        except BadRequest:
            continue
    text = json.dumps(kept)
    return text if len(text) <= MAX_ARTIFACTS else "{}"


def _finish(ctx, run_id: str, status: str, rc: int | None, error: str = "",
            extra: dict | None = None) -> None:
    values = {"status": status, "rc": rc, "error": error, "finished_at": store.now(),
              **(extra or {})}
    store.update(ctx.conn, "runs", run_id, values, "run")
    journal.send(f"run {run_id}: {status}", 6 if status == "successful" else 4,
                 FLEET_RUN=run_id, FLEET_STATUS=status)
    try:
        notify.run_ended(ctx, run_id)
    except Exception:  # noqa: BLE001  (a rule must not keep a run from being recorded)
        journal.send(f"run {run_id}: cannot queue its notifications", 3, FLEET_RUN=run_id,
                     FLEET_TRACEBACK=traceback.format_exc())
    row = ctx.conn.execute("SELECT workflow_run_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is not None and row["workflow_run_id"]:
        _advance(ctx, row["workflow_run_id"])


def finish(ctx, run_id: str, status: str, error: str = "") -> None:
    """Record the end of a run that has no unit: a workflow run."""
    _finish(ctx, run_id, status, None, error)


def _advance(ctx, workflow_run_id: str) -> None:
    from . import workflows  # it builds on this module
    try:
        workflows.advance(ctx, workflow_run_id)
    except Exception:  # noqa: BLE001  (the tick takes it up again)
        journal.send(f"workflow run {workflow_run_id}: cannot advance", 3,
                     FLEET_RUN=workflow_run_id, FLEET_TRACEBACK=traceback.format_exc())


def _collect(ctx, run) -> None:
    """Bring one 'running' row up to date with what its unit left behind."""
    run_id = run["id"]
    directory = run_dir(run_id)
    if _unit_active(run_id):
        return
    try:
        result = json.loads(read_run_file(directory, "result.json", 1024 * 1024))
    except (OSError, ValueError):
        if store.now() - (run["started_at"] or 0) < _START_GRACE:
            return  # the unit may not have started yet
        if run["cancel_requested"]:
            _finish(ctx, run_id, "canceled", None)
        else:
            _finish(ctx, run_id, "error", None, "the run ended without a result")
        return

    rc = result.get("rc")
    done = CHECK_RCS if run["kind"] == "check" else (0,)
    if run["cancel_requested"] and (rc != 0 or result.get("terminated")):
        status = "canceled"
    else:
        status = "successful" if rc in done and not result.get("error") else "failed"
    extra = {"stats": json.dumps(_stats(directory))}
    if run["kind"] == "playbook":
        extra["artifacts"] = _artifacts(directory)
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
        if run["kind"] == "check":
            from . import checks  # it builds on this module
            checks.ingest(ctx, run, directory, error)
        if status == "successful" and run["kind"] == "galaxy":
            _store_collections(ctx, run, directory)
        elif run["kind"] == "galaxy" and run["project_id"]:
            store.update(ctx.conn, "projects", run["project_id"],
                         {"collections_error": _log_tail(directory)}, "project")
    except (FleetError, OSError) as failure:
        status, error = "error", str(failure)
        if run["kind"] == "galaxy" and run["project_id"]:
            store.update(ctx.conn, "projects", run["project_id"],
                         {"collections_error": error}, "project")

    _finish(ctx, run_id, status, rc, error, extra)
    if run["kind"] == "check":
        from . import checks
        checks.record_cycle(ctx, run, result, status)
    # The copy of the project and the ciphertext are only needed while it runs.
    for leftover in ("project", "creds", "checkout", "checkout.unpack", "inventory",
                     "collections", "galaxy", "scratch"):
        shutil.rmtree(directory / leftover, ignore_errors=True)
    (directory / "archive").unlink(missing_ok=True)

    if status == "successful" and run["kind"] in ("sync", "unpack", "galaxy"):
        # New files may ask for collections that are not here: reconcile()
        # starts the job that installs them. After an installation, this is
        # for a requirements file that changed while it ran.
        try:
            install_missing(ctx, run["project_id"], run["created_by"], start=False)
        except (FleetError, OSError) as failure:
            store.update(ctx.conn, "projects", run["project_id"],
                         {"collections_error": str(failure)}, "project")
    if status == "successful" and run["kind"] in ("sync", "unpack"):
        # Inventories that are files of the project may have changed with it.
        from . import files  # it builds on this module
        try:
            project = store.fetch(ctx.conn, "projects", run["project_id"], "project")
            files.reread_inventories(ctx, project, None, run["created_by"], start=False)
        except (FleetError, OSError) as failure:
            journal.send(f"inventories of {run['project_id']} not read again: {failure}", 4)


def _log_tail(directory: Path, limit: int = 2000) -> str:
    try:
        with open_run_file(directory, "stdout.log") as source:
            source.seek(0, os.SEEK_END)
            source.seek(max(source.tell() - 4 * limit, 0))
            return source.read().decode(errors="replace")[-limit:]
    except OSError:
        return "no output"


def _store_inventory(ctx, run, directory: Path) -> None:
    from . import checks  # it builds on this module
    hosts = parse_inventory(json.loads(read_run_file(directory, "inventory.json")))
    store.update(ctx.conn, "inventories", run["inventory_id"],
                 {"hosts": json.dumps(hosts), "parsed_at": store.now(), "parse_error": ""},
                 "inventory")
    checks.sync_hosts(ctx, run["inventory_id"], hosts)


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


def _store_collections(ctx, run, directory: Path) -> None:
    """Keep what a galaxy job installed, in place of what was kept before."""
    if not run["project_id"]:
        raise Conflict("the project was deleted meanwhile")
    digest = str(store.loads(run["options"], {}).get("sha256", ""))
    if not _DIGEST_RE.fullmatch(digest):
        raise Conflict("the job does not say which requirements it installed")
    source = directory / "galaxy"
    if not os.path.lexists(source):
        # Nothing was installed: the controller already has all that is asked for.
        source.mkdir(mode=0o700)
    if not stat.S_ISDIR(os.lstat(source).st_mode):
        raise Conflict("the job did not leave a directory")
    files = size = 0
    for current, _directories, names in os.walk(source):
        for name in names:
            files += 1
            size += os.lstat(os.path.join(current, name)).st_size
    if files > MAX_PROJECT_FILES or size > MAX_PROJECT_BYTES:
        raise Conflict("the collections are too large to be copied for a run")
    home = collections_home(run["project_id"])
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old in home.iterdir():
        shutil.rmtree(old, ignore_errors=True)
    source.rename(home / digest)
    store.update(ctx.conn, "projects", run["project_id"],
                 {"collections_at": store.now(), "collections_error": ""}, "project")


def reconcile(ctx) -> None:
    # One at a time: the unit that ends and a page that reads the runs get here
    # together, and what a job left can be taken only once. The rows are read
    # under the lock, so the second one finds the run already recorded.
    descriptor = os.open(paths.RUNS_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        for run in ctx.conn.execute("SELECT * FROM runs WHERE status = 'running'"
                                    " AND kind != 'workflow'").fetchall():
            _collect(ctx, run)
    finally:
        os.close(descriptor)
    # A workflow whose step ended while nothing advanced it, after a crash.
    for row in ctx.conn.execute("SELECT id FROM runs WHERE status = 'running'"
                                " AND kind = 'workflow'").fetchall():
        _advance(ctx, row["id"])
    dispatch(ctx)


def end_unstarted(ctx, run_id: str, status: str, error: str = "") -> None:
    """Record the end of a run that never started, and remove what was
    prepared for it: the ciphertext of its secrets above all."""
    _finish(ctx, run_id, status, None, error)
    shutil.rmtree(run_dir(run_id), ignore_errors=True)


def cancel(ctx, run_id: str) -> str:
    run = store.fetch(ctx.conn, "runs", run_id, "run")
    if run["kind"] == "workflow" and run["status"] == "running":
        from . import workflows
        return workflows.cancel(ctx, run_id)
    if run["status"] in ("queued", WAITING):
        end_unstarted(ctx, run_id, "canceled")
        return "canceled"
    if run["status"] != "running":
        raise BadRequest("this run has already ended")
    store.update(ctx.conn, "runs", run_id, {"cancel_requested": 1}, "run")
    _fleet_unit("stop", run_id)
    reconcile(ctx)
    return store.fetch(ctx.conn, "runs", run_id, "run")["status"]
