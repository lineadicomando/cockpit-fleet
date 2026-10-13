"""Commands on the files of a project: read any project, edit a managed one.

A managed project is written from the interface. Its files are the work tree
of a git repository that is kept next to the tree, not inside it: nothing in
the tree is git's own, and no path given by the interface can reach it. Every
change is one commit, with the caller as its author.

git runs here, outside the sandbox, which a fetched project never gets: the
difference is that this repository is created by the backend and only holds
what administrators wrote. It reads no configuration but its own, and its
hooks are off.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from . import jobs, paths, store
from .cli import Context, Id, Int, Str, Values, command
from .errors import BadRequest, Conflict, NotFound, Unavailable
from .names import check_id
from .validators import check_file_path

# What the editor takes: a playbook, not an archive.
MAX_FILE = 512 * 1024
MAX_LISTED = 5000
MAX_DIFF = 256 * 1024

_REVISION_RE = re.compile(r"[0-9a-f]{7,64}")
# A play among the top-level items of a list: this is what tells a playbook
# from a file of tasks or of variables.
_PLAY_RE = re.compile(r"^(- | {2})(hosts|(ansible\.builtin\.)?import_playbook) *:", re.MULTILINE)

_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    # A file may be called "*.yml": a path is a path, never a pattern.
    "GIT_LITERAL_PATHSPECS": "1",
}


def _home(project_id: str) -> Path:
    return paths.PROJECTS_DIR / check_id(project_id, "project id")


def _git(project_id: str, *args: str, env: dict | None = None, check: bool = True) -> str | None:
    """Output of one git command on a managed project; None if it fails and may."""
    home = _home(project_id)
    argv = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "gc.autoDetach=false",
            f"--git-dir={home / 'repo.git'}", f"--work-tree={home / 'tree'}", *args]
    try:
        result = subprocess.run(
            argv, capture_output=True, encoding="utf-8", errors="replace", timeout=120,
            check=False, stdin=subprocess.DEVNULL, cwd=home / "tree",
            env={**_GIT_ENV, "HOME": str(home), **(env or {})},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(f"cannot run git: {error}") from error
    if result.returncode != 0:
        if check:
            raise Unavailable(f"git {args[0]} failed: {result.stderr.strip()[-500:]}")
        return None
    return result.stdout


def init_repository(project_id: str) -> None:
    """The empty tree and the repository of a new managed project."""
    home = _home(project_id)
    (home / "tree").mkdir(mode=0o700, parents=True)
    _git(project_id, "init", "--quiet", "--initial-branch=main")


@contextmanager
def _locked(project_id: str):
    """One change at a time: a commit holds what one caller wrote."""
    with open(_home(project_id) / "lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _identity(ctx: Context) -> dict:
    host = socket.gethostname() or "localhost"
    user = re.sub(r"[<>\s]+", "-", ctx.caller.user)
    return {
        "GIT_AUTHOR_NAME": user, "GIT_AUTHOR_EMAIL": f"{user}@{host}",
        "GIT_COMMITTER_NAME": paths.SERVICE_USER,
        "GIT_COMMITTER_EMAIL": f"{paths.SERVICE_USER}@{host}",
    }


def _commit(ctx: Context, project, message: str) -> str:
    """Commit whatever differs in the tree; returns the project's revision."""
    project_id = project["id"]
    _git(project_id, "add", "--all")
    if not _git(project_id, "status", "--porcelain"):
        return project["revision"]  # written again as it was
    _git(project_id, "commit", "--quiet", f"--message={message}", env=_identity(ctx))
    revision = _git(project_id, "rev-parse", "HEAD").strip()
    store.update(ctx.conn, "projects", project_id,
                 {"revision": revision, "synced_at": store.now()}, "project")
    return revision


def _message(params: Values, default: str) -> str:
    text = params["message"].strip()
    return store.check_name(text, "message") if text else default


def _locate(tree: Path, path: PurePosixPath) -> Path:
    """TREE/PATH, which is a regular file or does not exist yet.

    A symbolic link on the way or at the end is refused rather than followed:
    it may lead out of the project.
    """
    current = tree
    for index, part in enumerate(path.parts):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except OSError as error:
            raise BadRequest(f"invalid path: {error.strerror}") from error
        last = index == len(path.parts) - 1
        if not (stat.S_ISREG(mode) if last else stat.S_ISDIR(mode)):
            raise Conflict(f"{part} is not a {'file' if last else 'directory'}")
    return tree / path


def _read(target: Path) -> bytes:
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise NotFound(f"no such file: {target.name}") from None
    with os.fdopen(descriptor, "rb") as source:
        data = source.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise Conflict(f"{target.name} is larger than {MAX_FILE // 1024} kB")
    return data


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prune(tree: Path, directory: Path) -> None:
    """Remove the directories a deleted file left empty: git does not keep them."""
    while directory != tree:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _project(ctx: Context, params: Values, managed: bool = False):
    project = store.fetch(ctx.conn, "projects", params["id"], "project")
    if managed and project["kind"] != "managed":
        raise Conflict("only a project written in the interface can be changed here")
    return project


def looks_like_playbook(path: str, content: str) -> bool:
    """A YAML file whose text is a list with a play in it."""
    if not path.endswith((".yml", ".yaml")):
        return False
    lines = (line for line in content.splitlines()
             if line.strip() and line.strip() != "---" and not line.lstrip().startswith("#"))
    return next(lines, "").startswith("- ") and bool(_PLAY_RE.search(content))


def _queue_check(ctx: Context, project, path: PurePosixPath) -> str:
    """Queue a job that checks the syntax of one playbook of a project."""
    tree = jobs.project_tree(project, ctx.config)
    if not _locate(tree, path).is_file():
        raise NotFound(f"no such file: {path}")
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        jobs.snapshot(tree, directory / "project")
        jobs.place_collections(project, directory)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    jobs.enqueue(ctx, {"id": run_id, "kind": "syntax", "title": f"{project['name']}: {path}",
                       "project_id": project["id"], "revision": project["revision"],
                       "options": json.dumps({"path": str(path)})},
                 {"kind": "syntax", "playbook": str(path)}, directory)
    return run_id


@command("file.list", role="admin", params={"id": Id()})
def file_list(ctx: Context, params: Values) -> dict:
    """Every file of a project, with its size."""
    project = _project(ctx, params)
    tree = jobs.project_tree(project, ctx.config)
    found: list[dict] = []
    for current, directories, names in os.walk(tree):
        directories[:] = sorted(name for name in directories if name.casefold() != ".git")
        for name in sorted(names):
            source = Path(current) / name
            try:
                status = os.lstat(source)
                relative = str(source.relative_to(tree))
                relative.encode()
            except (OSError, UnicodeEncodeError):
                continue
            if stat.S_ISREG(status.st_mode):
                found.append({"path": relative, "size": status.st_size})
        if len(found) > MAX_LISTED:
            break
    return {"files": found[:MAX_LISTED], "truncated": len(found) > MAX_LISTED,
            "editable": project["kind"] == "managed"}


@command("file.read", role="admin",
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
         })
def file_read(ctx: Context, params: Values) -> dict:
    """The content of one file of a project, with the digest a write must quote."""
    project = _project(ctx, params)
    path = check_file_path(params["path"])
    data = _read(_locate(jobs.project_tree(project, ctx.config), path))
    try:
        content = data.decode()
    except UnicodeDecodeError:
        raise Conflict(f"{path.name} is not a text file") from None
    # The digest is what a later write says it started from.
    return {"path": str(path), "content": content, "sha256": _digest(data)}


@command("file.write", role="admin", mutating=True, redact=("content",),
         params={
             "id": Id(),
             "content": Str(required=True, max_length=MAX_FILE),
             "expected": Str(required=True, max_length=64),
             "path": Str(required=True, max_length=1024),
             "message": Str(default="", max_length=200),
         })
def file_write(ctx: Context, params: Values) -> dict:
    """Create or replace one file of a managed project, as one commit.

    `expected` is the digest of the content the change started from, empty for
    a new file: a file that someone else changed meanwhile is not overwritten.
    """
    project = _project(ctx, params, managed=True)
    path = check_file_path(params["path"])
    content = params["content"]
    expected = params["expected"]
    message = _message(params, "")
    try:
        data = content.encode()
    except UnicodeEncodeError:
        raise BadRequest("content is not valid text") from None
    if len(data) > MAX_FILE:
        raise BadRequest(f"content is larger than {MAX_FILE // 1024} kB")

    tree = jobs.project_tree(project, ctx.config)
    with _locked(project["id"]):
        target = _locate(tree, path)
        exists = target.is_file()
        current = _digest(_read(target)) if exists else ""
        if exists and not expected:
            raise Conflict(f"{path} already exists")
        if current != expected:
            raise Conflict(f"{path} was changed by someone else: reload it" if exists
                           else f"{path} was deleted by someone else")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.fleet-tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
        project = store.fetch(ctx.conn, "projects", project["id"], "project")
        revision = _commit(ctx, project, message or f"{'Update' if exists else 'Add'} {path}")

    check = None
    if looks_like_playbook(str(path), content):
        project = store.fetch(ctx.conn, "projects", project["id"], "project")
        check = _queue_check(ctx, project, path)
    # A requirements file that changed: what it lists now is installed.
    collections = None
    if str(path) in jobs.REQUIREMENTS:
        collections = jobs.install_missing(ctx, project["id"])
    return {"revision": revision, "sha256": _digest(data), "check_run_id": check,
            "collections_run_id": collections}


@command("file.delete", role="admin", mutating=True,
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
             "message": Str(default="", max_length=200),
         })
def file_delete(ctx: Context, params: Values) -> dict:
    """Delete one file of a project written in the interface, as a commit."""
    project = _project(ctx, params, managed=True)
    path = check_file_path(params["path"])
    message = _message(params, f"Delete {path}")
    tree = jobs.project_tree(project, ctx.config)
    with _locked(project["id"]):
        target = _locate(tree, path)
        try:
            target.unlink()
        except FileNotFoundError:
            raise NotFound(f"no such file: {path}") from None
        _prune(tree, target.parent)
        project = store.fetch(ctx.conn, "projects", project["id"], "project")
        return {"revision": _commit(ctx, project, message)}


@command("file.move", role="admin", mutating=True,
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
             "to": Str(required=True, max_length=1024),
             "message": Str(default="", max_length=200),
         })
def file_move(ctx: Context, params: Values) -> dict:
    """Rename one file, or move it to another directory of the project."""
    project = _project(ctx, params, managed=True)
    path = check_file_path(params["path"])
    to = check_file_path(params["to"], "to")
    message = _message(params, f"Rename {path} to {to}")
    tree = jobs.project_tree(project, ctx.config)
    with _locked(project["id"]):
        source, target = _locate(tree, path), _locate(tree, to)
        if not source.is_file():
            raise NotFound(f"no such file: {path}")
        if os.path.lexists(target):
            raise Conflict(f"{to} already exists")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source.rename(target)
        _prune(tree, source.parent)
        project = store.fetch(ctx.conn, "projects", project["id"], "project")
        return {"revision": _commit(ctx, project, message)}


@command("file.history", role="admin",
         params={
             "id": Id(),
             "path": Str(default="", max_length=1024),
             "limit": Int(1, 500, default=50),
         })
def file_history(ctx: Context, params: Values) -> dict:
    """The changes of a managed project, newest first; of one file if asked."""
    project = _project(ctx, params, managed=True)
    path = params["path"]
    limit = params["limit"]
    selection = ["--", str(check_file_path(path))] if path else []
    # Nothing committed yet is not an error: git says so by failing.
    output = _git(project["id"], "log", f"--max-count={limit}", "-z",
                  "--format=%H%x1f%an%x1f%at%x1f%s", *selection, check=False) or ""
    commits = []
    for record in output.split("\0"):
        fields = record.strip("\n").split("\x1f")
        if len(fields) == 4:
            commits.append({"revision": fields[0], "author": fields[1], "time": int(fields[2]),
                            "subject": fields[3]})
    return {"commits": commits}


@command("file.diff", role="admin",
         params={
             "id": Id(),
             "revision": Str(required=True, max_length=64),
         })
def file_diff(ctx: Context, params: Values) -> dict:
    """What one change of a managed project did, as a unified diff."""
    project = _project(ctx, params, managed=True)
    revision = params["revision"]
    if not _REVISION_RE.fullmatch(revision):
        raise BadRequest("revision must be a commit id")
    text = _git(project["id"], "show", "--format=", "--no-color", "--no-ext-diff", "--no-textconv",
                f"{revision}^{{commit}}", check=False)
    if text is None:
        raise NotFound(f"no such revision: {revision}")
    return {"text": text[:MAX_DIFF], "cut": len(text) > MAX_DIFF}


@command("file.check", role="admin", mutating=True,
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
         })
def file_check(ctx: Context, params: Values) -> dict:
    """Queue a syntax check of one playbook, of any kind of project."""
    project = _project(ctx, params)
    path = check_file_path(params["path"])
    return {"run_id": _queue_check(ctx, project, path)}
