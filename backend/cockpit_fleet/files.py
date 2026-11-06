"""Commands on the files of a project: read any project, edit a managed one;
and on the files of an inventory written in the interface.

A managed project is written from the interface, and so is a managed
inventory: both are a container of files, told apart by their id. Its files are the work tree
of a git repository that is kept next to the tree, not inside it: nothing in
the tree is git's own, and no path given by the interface can reach it. Every
change is one commit, with the caller as its author: one file written, or a
list of changes to files and directories that are made all together or not
at all.

git runs here, outside the sandbox, which a fetched project never gets: the
difference is that this repository is created by the backend and only holds
what administrators wrote. It reads no configuration but its own, and its
hooks are off.

At a terminal an administrator clones it, as a git bundle, and pushes back
commits made on top of it, in another bundle: they are taken only as a fast
forward of what the project has, file by file under the rules of the editor.

An agent never writes to main: what it writes goes to a draft, the branch
drafts/SESSION of the same repository, checked out in a work tree of its own
beside the project's (drafts/SESSION/tree, with its own index), which no run
reads. A person reviews it and takes all or part of it into main
(drafts.py), as a commit of theirs.
"""

import base64
import binascii
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import jobs, paths, store
from .autonomy import SESSION_RE
from .cli import Context, Id, Int, Objs, Str, Values, command, is_agent
from .config import MAX_UPLOAD_MB
from .errors import BadRequest, Conflict, Forbidden, NotFound, Unavailable
from .names import check_id
from .validators import check_file_path

# One file of a project: a playbook, or a small installer it copies to nodes,
# not an archive.
MAX_FILE = 1024 * 1024
MAX_LISTED = 5000
MAX_DIFF = 256 * 1024
# What one call to file.change does at most.
MAX_OPERATIONS = 200
# A file as text in a JSON string, escapes included.
_MAX_WRITE_INPUT = 2 * MAX_FILE + 64 * 1024

_REVISION_RE = re.compile(r"[0-9a-f]{7,64}")
_DRAFT_DOC = ("A draft to read or write instead of main, by its session; an agent writes"
              " to the draft of its own session whatever it says, and reads it by default")
# A bundle, either way, as the base64 of at most an upload.
MAX_BUNDLE = MAX_UPLOAD_MB * 1024 * 1024
_MAX_BUNDLE_TEXT = MAX_BUNDLE * 4 // 3 + 4
# The directories of a tree are opened one from another, never through a link.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# Where pushed commits wait while they are looked at.
_INCOMING = "refs/fleet/incoming"
# A play among the top-level items of a list: this is what tells a playbook
# from a file of tasks or of variables.
# What keeps an empty directory in the history, where git keeps none: an
# empty file, put in and taken out by the backend, never listed.
KEEP = ".gitkeep"
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


# The file of a managed inventory that its text, written before inventories
# were trees, becomes; and what inventory.create and inventory.update write.
MAIN_FILES = {"yaml": "hosts.yml", "ini": "hosts"}


def is_inventory(container_id: str) -> bool:
    """Whether an id is an inventory's: ids of projects start with p, of inventories with i."""
    return container_id.startswith("i")


def _table(container_id: str) -> tuple[str, str]:
    return ("inventories", "inventory") if is_inventory(container_id) else ("projects", "project")


def _home(container_id: str) -> Path:
    if is_inventory(container_id):
        return paths.INVENTORIES_DIR / check_id(container_id, "inventory id")
    return paths.PROJECTS_DIR / check_id(container_id, "project id")


def _fetch(ctx: Context, container_id: str):
    table, what = _table(container_id)
    return store.fetch(ctx.conn, table, container_id, what)


@dataclass(frozen=True)
class Draft:
    """The draft of one session of an agent in a managed project or inventory."""

    container_id: str
    session: str

    @property
    def home(self) -> Path:
        return _home(self.container_id) / "drafts" / self.session

    @property
    def tree(self) -> Path:
        return self.home / "tree"

    @property
    def ref(self) -> str:
        return f"refs/heads/drafts/{self.session}"


def check_draft(value: str) -> str:
    """The name of a draft: the session of fleet-mcp that wrote it."""
    if not SESSION_RE.fullmatch(value):
        raise BadRequest("draft must be the identifier of a session")
    return value


def _run_git(project_id: str, args: tuple[str, ...], env: dict | None, draft: Draft | None,
             binary: bool):
    home = _home(project_id)
    tree = draft.tree if draft else home / "tree"
    extra = {"GIT_INDEX_FILE": str(draft.home / "index")} if draft else {}
    argv = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "gc.autoDetach=false",
            f"--git-dir={home / 'repo.git'}", f"--work-tree={tree}", *args]
    text = {} if binary else {"encoding": "utf-8", "errors": "replace"}
    try:
        return subprocess.run(
            argv, capture_output=True, timeout=120, check=False, stdin=subprocess.DEVNULL,
            cwd=tree if tree.is_dir() else home,
            env={**_GIT_ENV, "HOME": str(home), **extra, **(env or {})}, **text,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unavailable(f"cannot run git: {error}") from error


def git(project_id: str, *args: str, env: dict | None = None, check: bool = True,
         draft: Draft | None = None) -> str | None:
    """Output of one git command on a managed project, or on a draft of it;
    None if it fails and may."""
    result = _run_git(project_id, args, env, draft, binary=False)
    if result.returncode != 0:
        if check:
            raise Unavailable(f"git {args[0]} failed: {result.stderr.strip()[-500:]}")
        return None
    return result.stdout


def blob(project_id: str, oid: str) -> bytes:
    """The content of one file of the repository, by its object id."""
    result = _run_git(project_id, ("cat-file", "blob", oid), None, None, binary=True)
    if result.returncode != 0:
        raise Unavailable(f"git cat-file failed: {result.stderr.decode(errors='replace')[-500:]}")
    return result.stdout


def head(project_id: str) -> str | None:
    """The commit main is at; None while nothing was committed."""
    found = git(project_id, "rev-parse", "--verify", "--quiet", "refs/heads/main^{commit}",
                 check=False)
    return found.strip() if found else None


def draft_tip(draft: Draft) -> str | None:
    """The last commit of a draft; None if it has none, and so does not exist."""
    found = git(draft.container_id, "rev-parse", "--verify", "--quiet",
                 f"{draft.ref}^{{commit}}", check=False)
    return found.strip() if found else None


def draft_base(draft: Draft, tip: str | None = None) -> str | None:
    """The commit of main a draft started from: None for a draft begun when
    main had nothing."""
    tip = tip or draft_tip(draft)
    main = head(draft.container_id)
    if tip is None:
        return main
    if main is None:
        return None
    found = git(draft.container_id, "merge-base", main, tip, check=False)
    return found.strip() if found else None


def drafts_of(container_id: str) -> list[str]:
    """The sessions that have a draft in a managed project or inventory."""
    if not (_home(container_id) / "repo.git").is_dir():
        return []
    listing = git(container_id, "for-each-ref", "--format=%(refname)", "refs/heads/drafts/",
                   check=False) or ""
    prefix = "refs/heads/drafts/"
    return [line[len(prefix):] for line in listing.splitlines()
            if line.startswith(prefix) and SESSION_RE.fullmatch(line[len(prefix):])]


def _prepare(draft: Draft) -> None:
    """The work tree of a draft, checked out from its branch, or from main for
    a new one, made again each time until it has a commit: main may have
    moved since. Called with the lock held."""
    tip = draft_tip(draft)
    if tip and (draft.home / "ready").is_file() and draft.tree.is_dir():
        return
    shutil.rmtree(draft.home, ignore_errors=True)  # what a failed attempt left
    draft.tree.mkdir(mode=0o700, parents=True)
    source = tip or head(draft.container_id)
    if source:
        git(draft.container_id, "read-tree", source, draft=draft)
        git(draft.container_id, "checkout-index", "--all", "--force", draft=draft)
    else:
        git(draft.container_id, "read-tree", "--empty", draft=draft)
    (draft.home / "ready").touch(mode=0o600)


def drop(draft: Draft) -> None:
    """Remove a draft, its branch and its work tree. Called with the lock held."""
    git(draft.container_id, "update-ref", "-d", draft.ref, check=False)
    shutil.rmtree(draft.home, ignore_errors=True)


def init_repository(container_id: str) -> None:
    """The empty tree and the repository of a new managed project or inventory."""
    home = _home(container_id)
    (home / "tree").mkdir(mode=0o700, parents=True)
    git(container_id, "init", "--quiet", "--initial-branch=main")


@contextmanager
def locked(project_id: str):
    """One change at a time: a commit holds what one caller wrote."""
    with open(_home(project_id) / "lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _identity(author: str) -> dict:
    """AUTHOR as the author, the service as the committer."""
    host = socket.gethostname() or "localhost"
    user = re.sub(r"[<>\s]+", "-", author) or paths.SERVICE_USER
    return {
        "GIT_AUTHOR_NAME": user, "GIT_AUTHOR_EMAIL": f"{user}@{host}",
        "GIT_COMMITTER_NAME": paths.SERVICE_USER,
        "GIT_COMMITTER_EMAIL": f"{paths.SERVICE_USER}@{host}",
    }


def _commit(ctx: Context, project, message: str, by_service: bool = False,
            author: str | None = None) -> str:
    """Commit whatever differs in the tree; returns the revision of the
    project or inventory. The author is AUTHOR, or the caller; the service
    BY_SERVICE, for what no one wrote (the text of an inventory made its
    first file)."""
    project_id = project["id"]
    _keep_empty(_home(project_id) / "tree")
    # --force: a .gitignore in the tree must not keep a file runs see out of the history.
    git(project_id, "add", "--all", "--force")
    if not git(project_id, "status", "--porcelain"):
        return project["revision"]  # written again as it was
    git(project_id, "commit", "--quiet", f"--message={message}",
         env=_identity(paths.SERVICE_USER if by_service else author or ctx.caller.user))
    revision = git(project_id, "rev-parse", "HEAD").strip()
    table, what = _table(project_id)
    values = {"revision": revision}
    if table == "projects":
        values["synced_at"] = store.now()
    store.update(ctx.conn, table, project_id, values, what)
    return revision


def _commit_draft(ctx: Context, draft: Draft, message: str, author: str | None = None) -> str:
    """Commit whatever differs in the work tree of a draft on its branch;
    returns its last commit. main, its index and HEAD are not touched."""
    project_id = draft.container_id
    _keep_empty(draft.tree)
    git(project_id, "add", "--all", "--force", draft=draft)
    tree = git(project_id, "write-tree", draft=draft).strip()
    tip = draft_tip(draft)
    parent = tip or head(project_id)
    if parent and git(project_id, "rev-parse", f"{parent}^{{tree}}").strip() == tree:
        return parent  # written again as it was
    identity = _identity(author or ctx.caller.user)
    commit = git(project_id, "commit-tree", tree, *(["-p", parent] if parent else []),
                  "-m", message, env=identity).strip()
    git(project_id, "update-ref", draft.ref, commit, tip or "")
    return commit


def place(ctx: Context, row, given: str, writing: bool) -> Draft | None:
    """Where a call reads or writes the files of a managed project or
    inventory: main (None), or a draft. An agent writes to the draft of its
    session only, made when it first writes, and reads it while it exists; a
    person names a draft that exists to read it or to edit it."""
    name = check_draft(given) if given else ""
    if is_agent(ctx.via):
        if writing:
            if not ctx.session:
                raise Forbidden("an agent writes files only within a session of fleet-mcp")
            if name and name != ctx.session:
                raise Forbidden("an agent writes only to the draft of its own session")
            return Draft(row["id"], ctx.session)
        name = name or ctx.session
        if not name:
            return None
        draft = Draft(row["id"], name)
        if draft_tip(draft) is None:
            if given:
                raise NotFound(f"no such draft: {name}")
            return None
        return draft
    if not name:
        return None
    draft = Draft(row["id"], name)
    if draft_tip(draft) is None:
        raise NotFound(f"no such draft: {name}")
    return draft


def draft_tree(draft: Draft) -> Path:
    """The work tree of a draft that exists, checked out if it was not."""
    with locked(draft.container_id):
        _prepare(draft)
    return draft.tree.resolve()


def inventory_tree(ctx: Context, inventory) -> Path:
    """The tree of a managed inventory, made the first time it is needed:
    an inventory written before inventories were trees has its text made
    the first file of its repository, and the text dropped from the database."""
    inventory_id = inventory["id"]
    home = _home(inventory_id)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked(inventory_id):
        if not (home / "repo.git").is_dir():
            shutil.rmtree(home / "tree", ignore_errors=True)  # what a failed attempt left
            init_repository(inventory_id)
            row = store.fetch(ctx.conn, "inventories", inventory_id, "inventory")
            if row["content"]:
                _write_file(home / "tree" / MAIN_FILES[row["format"]], row["content"].encode())
                _commit(ctx, row, "Take the inventory written before as its first file",
                        by_service=True)
                store.update(ctx.conn, "inventories", inventory_id, {"content": ""}, "inventory")
    return (home / "tree").resolve()


def tree_files(ctx: Context, inventory) -> list[dict]:
    """Every file of a managed inventory, as inventory.create takes them:
    path, and content if it is text, data in base64 if not."""
    tree = inventory_tree(ctx, inventory)
    found = []
    for current, directories, names in os.walk(tree):
        directories.sort()
        for name in sorted(names):
            source = Path(current) / name
            if not stat.S_ISREG(os.lstat(source).st_mode):
                continue
            data = _read(source)
            entry: dict = {"path": source.relative_to(tree).as_posix()}
            try:
                entry["content"] = data.decode()
            except UnicodeDecodeError:
                entry["data"] = base64.b64encode(data).decode()
            found.append(entry)
    return found


def container_tree(ctx: Context, row) -> Path:
    """Where the files of a project, or of a managed inventory, are."""
    if is_inventory(row["id"]):
        return inventory_tree(ctx, row)
    return jobs.project_tree(row, ctx.config)


def main_text(ctx: Context, inventory) -> str:
    """The text of the main file of a managed inventory (hosts.yml, or hosts
    for INI), what inventory.get gives as its content; empty if there is none."""
    tree = inventory_tree(ctx, inventory)
    try:
        return _read(_locate(tree, PurePosixPath(MAIN_FILES[inventory["format"]]))).decode()
    except (NotFound, Conflict, UnicodeDecodeError):
        return ""


def _message(params: Values, default: str) -> str:
    text = params["message"].strip()
    return store.check_name(text, "message") if text else default


def _mode_on_way(tree: Path, path: PurePosixPath) -> int | None:
    """The mode of TREE/PATH as lstat gives it, None if it does not exist.

    Everything on the way must be a directory, not a link: a symbolic link
    may lead out of the project, and is refused rather than followed.
    """
    current = tree
    for index, part in enumerate(path.parts):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return None
        except OSError as error:
            raise BadRequest(f"invalid path: {error.strerror}") from error
        if index < len(path.parts) - 1 and not stat.S_ISDIR(mode):
            raise Conflict(f"{part} is not a directory")
    return mode


def _locate(tree: Path, path: PurePosixPath) -> Path:
    """TREE/PATH, which is a regular file or does not exist yet."""
    mode = _mode_on_way(tree, path)
    if mode is not None and not stat.S_ISREG(mode):
        raise Conflict(f"{path.name} is not a file")
    return tree / path


def _entry(tree: Path, path: PurePosixPath) -> bool:
    """Whether TREE/PATH, a file or a directory that exists, is a directory."""
    mode = _mode_on_way(tree, path)
    if mode is None:
        raise NotFound(f"no such file or directory: {path}")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise Conflict(f"{path.name} is neither a file nor a directory")
    return stat.S_ISDIR(mode)


def _read(target: Path) -> bytes:
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise NotFound(f"no such file: {target.name}") from None
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise Conflict(f"{target.name} is not a file")
        data = source.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise Conflict(f"{target.name} is larger than {MAX_FILE // 1024} kB")
    return data


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_marker(path: Path) -> bool:
    """Whether PATH is the empty file that keeps its directory."""
    try:
        status = os.lstat(path)
    except OSError:
        return False
    return path.name == KEEP and stat.S_ISREG(status.st_mode) and status.st_size == 0


def _keep_empty(tree: Path) -> None:
    """Give every empty directory of TREE the file that keeps it in the
    history, and take that file out of a directory that holds something else."""
    for current, directories, names in os.walk(tree, topdown=False):
        here = Path(current)
        if here == tree:
            continue
        if not directories and not names:
            (here / KEEP).touch(mode=0o600)
        elif KEEP in names and len(directories) + len(names) > 1 and _is_marker(here / KEEP):
            (here / KEEP).unlink()


def _project(ctx: Context, params: Values, managed: bool = False):
    """The project, or the managed inventory, that the id names."""
    container_id = params["id"]
    row = _fetch(ctx, container_id)
    if is_inventory(container_id) and row["kind"] != "managed":
        raise Conflict("the files of this inventory are those of its project")
    if managed and row["kind"] != "managed":
        raise Conflict("only a project written in the interface can be changed here")
    if is_inventory(container_id):
        inventory_tree(ctx, row)  # its repository, from its text the first time
        row = _fetch(ctx, container_id)
    return row


def managed(ctx: Context, container_id: str):
    """A managed project or inventory, by its id."""
    return _project(ctx, {"id": container_id}, managed=True)


def looks_like_playbook(path: str, content: str) -> bool:
    """A YAML file whose text is a list with a play in it."""
    if not path.endswith((".yml", ".yaml")):
        return False
    lines = (line for line in content.splitlines()
             if line.strip() and line.strip() != "---" and not line.lstrip().startswith("#"))
    return next(lines, "").startswith("- ") and bool(_PLAY_RE.search(content))


def _queue_check(ctx: Context, project, path: PurePosixPath, start: bool = True,
                 draft: Draft | None = None) -> str:
    """Queue a job that checks the syntax of one playbook of a project, or of
    a draft of it."""
    if draft is not None:
        tree, revision = draft_tree(draft), draft_tip(draft) or ""
    else:
        tree, revision = jobs.project_tree(project, ctx.config), project["revision"]
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
    options = {"path": str(path), **({"draft": draft.session} if draft else {})}
    title = f"{project['name']}: {path}" + (" (draft)" if draft else "")
    (jobs.enqueue if start else jobs.queue)(
        ctx, {"id": run_id, "kind": "syntax", "title": title,
              "project_id": project["id"], "revision": revision,
              "options": json.dumps(options)},
        {"kind": "syntax", "playbook": str(path)}, directory)
    return run_id


def _affects(inventory_path: str, is_directory: bool, touched: str) -> bool:
    """Whether a change of TOUCHED, a path of the project, may change what
    the inventory at INVENTORY_PATH reads: the inventory itself, what is in
    its directory (group_vars and host_vars beside a file), or a directory
    it is in."""
    if is_directory:
        scope = inventory_path
    else:
        scope = str(PurePosixPath(inventory_path).parent)
        if scope == ".":
            # A file at the top: its variables are the top's group_vars and host_vars.
            return touched == inventory_path or touched.split("/")[0] in ("group_vars",
                                                                         "host_vars")
    return (touched == scope or touched.startswith(scope + "/")
            or scope.startswith(touched + "/") or touched == inventory_path)


def reread_inventories(ctx: Context, project, touched: list[str] | None,
                       created_by: str | None = None, start: bool = True) -> list[str]:
    """Queue the reading of the inventories that are files of a project and
    that a change of the paths TOUCHED may change, every one if None.
    Returns the runs queued."""
    from .resources import queue_parse  # it builds on this module

    rows = ctx.conn.execute("SELECT * FROM inventories WHERE kind = 'file' AND project_id = ?",
                            (project["id"],)).fetchall()
    if not rows:
        return []
    tree = jobs.project_tree(project, ctx.config)
    queued = []
    for inventory in rows:
        try:
            is_directory = (tree / inventory["path"]).is_dir() \
                and not (tree / inventory["path"]).is_symlink()
        except OSError:
            is_directory = False
        if touched is None or any(_affects(inventory["path"], is_directory, path)
                                  for path in touched):
            queued.append(queue_parse(ctx, inventory, created_by, start=start))
    return queued


def _reread(ctx: Context, row, touched: list[str]) -> list[str]:
    """The readings of inventories a change of TOUCHED calls for: the
    inventory itself, or the inventories that are files of the project."""
    if is_inventory(row["id"]):
        from .resources import queue_parse  # it builds on this module
        return [queue_parse(ctx, row)]
    return reread_inventories(ctx, row, touched)


# --- changes ------------------------------------------------------------------------------

_OPERATION_KEYS = {
    "write": ({"path"}, {"content", "data", "expected"}),
    "move": ({"path", "to"}, set()),
    "copy": ({"path", "to"}, set()),
    "delete": ({"path"}, set()),
    "mkdir": ({"path"}, set()),
}


def check_operation(index: int, item: dict) -> dict:
    """One change asked for, checked: its kind, its paths and what it writes."""
    where = f"operations[{index}]"
    kind = item.get("op")
    if not isinstance(kind, str) or kind not in _OPERATION_KEYS:
        raise BadRequest(f"{where}.op must be one of: {', '.join(_OPERATION_KEYS)}")
    required, optional = _OPERATION_KEYS[kind]
    unknown = set(item) - required - optional - {"op"}
    if unknown:
        raise BadRequest(f"{where}: unknown parameter: {', '.join(sorted(unknown))}")
    missing = required - set(item)
    if missing:
        raise BadRequest(f"{where}: missing parameter: {', '.join(sorted(missing))}")
    for key in item:
        if key != "op" and not isinstance(item[key], str):
            raise BadRequest(f"{where}.{key} must be a string")
    operation = {"op": kind, "path": check_file_path(item["path"], f"{where}.path")}
    if "to" in required:
        operation["to"] = check_file_path(item["to"], f"{where}.to")
        source, target = str(operation["path"]), str(operation["to"])
        if target == source or target.startswith(source + "/"):
            raise BadRequest(f"{where}: cannot {kind} {source} into itself")
    if kind == "write":
        if ("content" in item) == ("data" in item):
            raise BadRequest(f"{where}: give either content (text) or data (base64)")
        if "content" in item:
            if len(item["content"]) > MAX_FILE:
                raise BadRequest(f"{where}: content is larger than {MAX_FILE // 1024} kB")
            try:
                data = item["content"].encode()
            except UnicodeEncodeError:
                raise BadRequest(f"{where}: content is not valid text") from None
        else:
            if len(item["data"]) > MAX_FILE * 4 // 3 + 4:
                raise BadRequest(f"{where}: data is larger than {MAX_FILE // 1024} kB")
            try:
                data = base64.b64decode(item["data"], validate=True)
            except (ValueError, binascii.Error):
                raise BadRequest(f"{where}: data is not valid base64") from None
        if len(data) > MAX_FILE:
            raise BadRequest(f"{where}: the file is larger than {MAX_FILE // 1024} kB")
        expected = item.get("expected", "")
        if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise BadRequest(f"{where}.expected must be a SHA-256 digest, or empty")
        operation.update(data=data, expected=expected)
    return operation


def _files_under(tree: Path, path: PurePosixPath) -> list[PurePosixPath]:
    """The regular files in the directory TREE/PATH, relative to it."""
    found = []
    base = tree / path
    for current, directories, names in os.walk(base):
        directories.sort()
        for name in sorted(names):
            source = Path(current) / name
            if stat.S_ISREG(os.lstat(source).st_mode):
                found.append(PurePosixPath(source.relative_to(base).as_posix()))
            elif not stat.S_ISDIR(os.lstat(source).st_mode):
                raise Conflict(f"{source.relative_to(tree)} is not a file")
    return found


def _write_file(target: Path, data: bytes) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.fleet-tmp")
    temporary.write_bytes(data)
    temporary.replace(target)


def _apply(tree: Path, operation: dict, changed: list[bool]) -> tuple[str, list[str]]:
    """Make one change in TREE; returns how a commit says it and the paths it
    touched. CHANGED is set once the tree is touched: what is refused before
    that leaves nothing to undo."""
    kind, path = operation["op"], operation["path"]
    if kind == "write":
        target = _locate(tree, path)
        exists = target.is_file()
        current = digest(_read(target)) if exists else ""
        expected = operation["expected"]
        if exists and not expected:
            raise Conflict(f"{path} already exists")
        if current != expected:
            raise Conflict(f"{path} was changed by someone else: reload it" if exists
                           else f"{path} was deleted by someone else")
        changed[0] = True
        _write_file(target, operation["data"])
        return f"{'Update' if exists else 'Add'} {path}", [str(path)]
    if kind == "mkdir":
        if _mode_on_way(tree, path) is not None:
            raise Conflict(f"{path} already exists")
        check_file_path(str(path / KEEP), "path")  # room for what keeps it
        changed[0] = True
        (tree / path).mkdir(mode=0o700, parents=True)
        return f"Add folder {path}", [str(path)]
    is_directory = _entry(tree, path)
    source = tree / path
    if kind == "delete":
        changed[0] = True
        if is_directory:
            shutil.rmtree(source)
        else:
            source.unlink()
        return f"Delete {path}", [str(path)]
    to = operation["to"]
    if _mode_on_way(tree, to) is not None:
        raise Conflict(f"{to} already exists")
    inside = _files_under(tree, path) if is_directory else [PurePosixPath()]
    for relative in inside:
        # Within the depth and the names the editor takes, once moved.
        check_file_path(str(to / relative) if relative.parts else str(to), "to")
    target = tree / to
    changed[0] = True
    if kind == "move":
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source.rename(target)
        return f"Rename {path} to {to}", [str(path), str(to)]
    for relative in inside:
        origin = source / relative if relative.parts else source
        _write_file(target / relative if relative.parts else target, _read(origin))
    return f"Copy {path} to {to}", [str(to)]


def _rollback(project_id: str, draft: Draft | None = None) -> None:
    """Put the tree back as the last commit has it: a list of changes that
    failed half way leaves nothing. A draft with no commit of its own is
    removed."""
    if draft is not None:
        if draft_tip(draft) is None:
            drop(draft)
            return
        git(project_id, "read-tree", "--reset", "-u", draft.ref, draft=draft)
        git(project_id, "clean", "-ffdxq", draft=draft)
        return
    if git(project_id, "rev-parse", "--verify", "--quiet", "HEAD", check=False) is not None:
        git(project_id, "reset", "--hard", "--quiet", "HEAD")
    git(project_id, "clean", "-ffdxq")


def change(ctx: Context, project, operations: list[dict], message: str,
           revision: str = "", reread: bool = True, author: str | None = None,
           start: bool = True, draft: Draft | None = None,
           tidy=None) -> dict:
    """Make OPERATIONS in a managed project or inventory as one commit, all
    or none, and what follows from them: for a project, syntax checks of the
    playbooks written, the collections a requirements file asks for, its
    inventories read again; for an inventory, reading it again (unless not
    REREAD: inventory.create and inventory.update leave that to the caller).
    The author is AUTHOR, or the caller; the jobs are only queued if not START
    (reconcile, which starts them after).

    In a DRAFT, the commit goes on its branch, and only the syntax checks
    follow, of what the draft holds: nothing else reads a draft. TIDY, if
    given, is called with the tree once the operations are made."""
    written, touched, said = [], [], []
    changed = [False]
    # Before the lock: an inventory's tree is made under it the first time.
    tree = None if draft is not None else container_tree(ctx, project)
    with locked(project["id"]):
        project = _fetch(ctx, project["id"])
        if draft is not None:
            _prepare(draft)
            tree = draft.tree.resolve()
            current = draft_tip(draft) or head(project["id"]) or ""
        else:
            current = project["revision"]
        if revision and current != revision:
            raise Conflict(f"the {_table(project['id'])[1]} was changed by someone else:"
                           " reload it")
        try:
            for operation in operations:
                line, paths_touched = _apply(tree, operation, changed)
                said.append(line)
                touched += paths_touched
                if operation["op"] == "write":
                    written.append(operation)
            if tidy is not None:
                changed[0] = True
                tidy(tree)
            if any(operation["op"] == "copy" for operation in operations):
                count = sum(len(names) for _current, _dirs, names in os.walk(tree))
                if count > MAX_LISTED:
                    raise Conflict(f"a tree holds at most {MAX_LISTED} files")
            default = said[0] if len(said) == 1 else f"{len(said)} changes: " + "; ".join(said)
            if draft is not None:
                new_revision = _commit_draft(ctx, draft, message or default[:200], author)
            else:
                new_revision = _commit(ctx, project, message or default[:200], author=author)
        except BaseException:
            if changed[0] or (draft is not None and draft_tip(draft) is None):
                _rollback(project["id"], draft)
            raise

    project = _fetch(ctx, project["id"])
    files = [{"path": str(op["path"]), "sha256": digest(op["data"])} for op in written]
    done = {"revision": new_revision, "files": files, "check_run_ids": [],
            "collections_run_id": None, "inventory_run_ids": [],
            "draft": draft.session if draft else None}
    if is_inventory(project["id"]):
        from .resources import queue_parse  # it builds on this module
        if draft is not None:
            done["check_run_ids"] = [queue_parse(ctx, project, start=start, syntax=True,
                                                 tree=tree, draft=draft.session,
                                                 revision=new_revision)]
        elif reread:
            done["check_run_ids"] = [queue_parse(ctx, project, start=start, syntax=True)]
            done["inventory_run_ids"] = [queue_parse(ctx, project, start=start)]
        return done
    # The last content written to each path, if the path is still there: a
    # file written, then moved or deleted by a later change, is not checked.
    last = {str(operation["path"]): operation for operation in written}
    for name, operation in last.items():
        try:
            content = operation["data"].decode()
        except UnicodeDecodeError:
            continue
        if looks_like_playbook(name, content) and (tree / name).is_file() \
                and not (tree / name).is_symlink():
            done["check_run_ids"].append(_queue_check(ctx, project, operation["path"], start,
                                                      draft))
    if draft is not None:
        return done
    if any(name == path or name.startswith(path + "/")
           for name in jobs.REQUIREMENTS for path in touched):
        done["collections_run_id"] = jobs.install_missing(ctx, project["id"], start=start)
    done["inventory_run_ids"] = reread_inventories(ctx, project, touched, start=start)
    return done


def _reading(ctx: Context, project, params: Values) -> Draft | None:
    """The draft a reading call names, or that an agent reads by default."""
    if project["kind"] != "managed":
        if params["draft"]:
            raise Conflict("only a project written in the interface has drafts")
        return None
    return place(ctx, project, params["draft"], writing=False)


def _done(done: dict) -> dict:
    return {"revision": done["revision"], "draft": done["draft"]}


@command("file.list", role="admin",
         params={
             "id": Id(),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_list(ctx: Context, params: Values) -> dict:
    """Every file of a project, with its size, and its directories."""
    project = _project(ctx, params)
    draft = _reading(ctx, project, params)
    tree = draft_tree(draft) if draft else container_tree(ctx, project)
    found: list[dict] = []
    folders: list[str] = []
    for current, directories, names in os.walk(tree):
        directories[:] = sorted(name for name in directories if name.casefold() != ".git")
        for name in directories:
            try:
                relative = str((Path(current) / name).relative_to(tree))
                relative.encode()
            except UnicodeEncodeError:
                continue
            folders.append(relative)
        for name in sorted(names):
            source = Path(current) / name
            try:
                status = os.lstat(source)
                relative = str(source.relative_to(tree))
                relative.encode()
            except (OSError, UnicodeEncodeError):
                continue
            if stat.S_ISREG(status.st_mode) and not (name == KEEP and status.st_size == 0):
                found.append({"path": relative, "size": status.st_size})
        if len(found) > MAX_LISTED:
            break
    return {"files": found[:MAX_LISTED], "directories": folders[:MAX_LISTED],
            "truncated": len(found) > MAX_LISTED, "editable": project["kind"] == "managed",
            "revision": draft_tip(draft) if draft else project["revision"],
            "draft": draft.session if draft else None}


@command("file.read", role="admin",
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_read(ctx: Context, params: Values) -> dict:
    """The content of one file of a project, with the digest a write must
    quote. A file that is not text is said to be binary, without its content."""
    project = _project(ctx, params)
    path = check_file_path(params["path"])
    draft = _reading(ctx, project, params)
    tree = draft_tree(draft) if draft else container_tree(ctx, project)
    data = _read(_locate(tree, path))
    try:
        content, binary = data.decode(), False
    except UnicodeDecodeError:
        content, binary = "", True
    # The digest is what a later write says it started from.
    return {"path": str(path), "content": content, "sha256": digest(data),
            "binary": binary, "size": len(data)}


@command("file.write", role="admin", mutating=True, agent="code", redact=("content",),
         max_input=_MAX_WRITE_INPUT,
         params={
             "id": Id(),
             "content": Str(required=True, max_length=MAX_FILE),
             "expected": Str(required=True, max_length=64),
             "path": Str(required=True, max_length=1024),
             "message": Str(default="", max_length=200),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_write(ctx: Context, params: Values) -> dict:
    """Create or replace one file of a managed project, as one commit.

    `expected` is the digest of the content the change started from, empty for
    a new file: a file that someone else changed meanwhile is not overwritten.
    An agent's change goes to the draft of its session, which a person
    reviews and merges; runs use main only.
    """
    project = _project(ctx, params, managed=True)
    operation = check_operation(0, {"op": "write", "path": params["path"],
                               "content": params["content"], "expected": params["expected"]})
    done = change(ctx, project, [operation], _message(params, ""),
                  draft=place(ctx, project, params["draft"], writing=True))
    return {"revision": done["revision"], "sha256": done["files"][0]["sha256"],
            "check_run_id": (done["check_run_ids"] or [None])[0],
            "collections_run_id": done["collections_run_id"], "draft": done["draft"]}


@command("file.delete", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
             "message": Str(default="", max_length=200),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_delete(ctx: Context, params: Values) -> dict:
    """Delete one file, or a directory with everything in it, of a project
    written in the interface, as a commit (an agent's, in its draft)."""
    project = _project(ctx, params, managed=True)
    operation = check_operation(0, {"op": "delete", "path": params["path"]})
    return _done(change(ctx, project, [operation], _message(params, ""),
                        draft=place(ctx, project, params["draft"], writing=True)))


@command("file.move", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
             "to": Str(required=True, max_length=1024),
             "message": Str(default="", max_length=200),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_move(ctx: Context, params: Values) -> dict:
    """Rename a file or a directory, or move it to another directory of the
    project (an agent, in its draft)."""
    project = _project(ctx, params, managed=True)
    operation = check_operation(0, {"op": "move", "path": params["path"], "to": params["to"]})
    return _done(change(ctx, project, [operation], _message(params, ""),
                        draft=place(ctx, project, params["draft"], writing=True)))


@command("file.change", role="admin", mutating=True, agent="code",
         redact=("operations[].content", "operations[].data"),
         max_input=_MAX_BUNDLE_TEXT + 64 * 1024,
         params={
             "id": Id(),
             "operations": Objs(
                 "The changes, made in this order: each has op (write, move, copy,"
                 " delete or mkdir) and path; to for move and copy; for write, content"
                 " (text) or data (base64) and expected, the SHA-256 of the file it"
                 " replaces, empty for a new one. Move, copy and delete take directories"
                 " too; a directory is kept when it is empty.",
                 max_items=MAX_OPERATIONS),
             "revision": Str("The revision of the project the changes were made on;"
                             " refused if it changed meanwhile", default="", max_length=64),
             "message": Str(default="", max_length=200),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_change(ctx: Context, params: Values) -> dict:
    """Change files and directories of a managed project as one commit: all
    the changes are made, or none of them. A directory left empty, or made
    with mkdir, is kept, with an empty .gitkeep in it that is not listed.
    Playbooks written are checked, as when written one at a time. An agent's
    changes go to the draft of its session, which a person reviews and merges."""
    project = _project(ctx, params, managed=True)
    operations = [check_operation(index, item) for index, item in enumerate(params["operations"])]
    if not operations:
        raise BadRequest("operations must list at least one change")
    total = sum(len(operation.get("data", b"")) for operation in operations)
    if total > MAX_BUNDLE:
        raise BadRequest(f"the changes are larger than {MAX_UPLOAD_MB} MB")
    revision = params["revision"]
    if revision and not _REVISION_RE.fullmatch(revision):
        raise BadRequest("revision must be a commit id")
    return change(ctx, project, operations, _message(params, ""), revision,
                  draft=place(ctx, project, params["draft"], writing=True))


@command("file.restore", role="admin", mutating=True, agent="code",
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
             "revision": Str("The commit to take the file or directory from",
                             required=True, max_length=64),
             "message": Str(default="", max_length=200),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_restore(ctx: Context, params: Values) -> dict:
    """Put a file or a directory of a managed project or inventory back as it was at an
    earlier revision, as a new commit: a directory gets exactly the files it
    had then. An agent restores in the draft of its session."""
    project = _project(ctx, params, managed=True)
    path = check_file_path(params["path"])
    revision = params["revision"]
    if not _REVISION_RE.fullmatch(revision):
        raise BadRequest("revision must be a commit id")
    message = _message(params, "")
    project_id = project["id"]
    draft = place(ctx, project, params["draft"], writing=True)
    # Before the lock: an inventory's tree is made under it the first time.
    tree, line = (None, "") if draft is not None else (container_tree(ctx, project), "HEAD")
    with locked(project_id):
        if draft is not None:
            _prepare(draft)
            tree, line = draft.tree.resolve(), draft.ref if draft_tip(draft) else "HEAD"
        # Only the history of the project: objects a refused push left are not in it.
        if (git(project_id, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}",
                 check=False) is None
                or git(project_id, "merge-base", "--is-ancestor", revision, line,
                        check=False) is None):
            if draft is not None and draft_tip(draft) is None:
                drop(draft)
            raise NotFound(f"no such revision: {revision}")
        listing = git(project_id, "ls-tree", "-r", "-z", revision, "--", str(path)) or ""
        entries = [entry for entry in listing.split("\0") if entry]
        try:
            if not entries:
                raise NotFound(f"{path} was not in the project at {revision[:10]}")
            for entry in entries:
                mode, kind = entry.split()[:2]
                if kind != "blob" or mode not in ("100644", "100755"):
                    raise Conflict(f"{entry.partition(chr(9))[2]}: not a regular file")
            project = _fetch(ctx, project_id)
            present = _mode_on_way(tree, path) is not None
            is_directory = present and _entry(tree, path)
            if is_directory:
                shutil.rmtree(tree / path)
            elif present:
                (tree / path).unlink()
            git(project_id, "checkout", revision, "--", str(path), draft=draft)
            message = message or f"Restore {path} as of {revision[:10]}"
            new_revision = _commit_draft(ctx, draft, message) if draft is not None \
                else _commit(ctx, project, message)
        except BaseException:
            _rollback(project_id, draft)
            raise
    if draft is not None:
        return {"revision": new_revision, "inventory_run_ids": [], "draft": draft.session}
    return {"revision": new_revision,
            "inventory_run_ids": _reread(ctx, _fetch(ctx, project_id), [str(path)]),
            "draft": None}


def _pack(directory_fd: int, prefix: str, archive: tarfile.TarFile, budget: list[int]) -> None:
    """Add what the open directory holds to ARCHIVE under PREFIX, following no
    link: links, devices and the like are left out."""
    for name in sorted(os.listdir(directory_fd)):
        if name.casefold() == ".git":
            continue
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            continue
        member = f"{prefix}/{name}" if prefix else name
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _DIR_FLAGS, dir_fd=directory_fd)
            try:
                entry = tarfile.TarInfo(member)
                entry.type, entry.mode, entry.mtime = tarfile.DIRTYPE, 0o755, int(info.st_mtime)
                archive.addfile(entry)
                _pack(child, member, archive, budget)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=directory_fd)
            with os.fdopen(descriptor, "rb") as source:
                status = os.fstat(source.fileno())
                if not stat.S_ISREG(status.st_mode):
                    continue
                budget[0] -= status.st_size
                if budget[0] < 0:
                    raise Conflict(f"more than {MAX_UPLOAD_MB} MB to download: choose a directory")
                entry = tarfile.TarInfo(member)
                entry.size, entry.mtime = status.st_size, int(status.st_mtime)
                entry.mode = 0o755 if status.st_mode & 0o100 else 0o644
                archive.addfile(entry, source)


@command("file.download", role="admin", mutating=True, agent="read", tool=False,
         params={
             "id": Id(),
             "path": Str("A file or a directory; empty for the whole project",
                         default="", max_length=1024),
         })
def file_download(ctx: Context, params: Values) -> dict:
    """A file of a project in base64, or a directory as a gzipped tar;
    recorded in the audit trail, as a copy of the files is."""
    project = _project(ctx, params)
    tree = container_tree(ctx, project)
    path = check_file_path(params["path"]) if params["path"] else None
    if path is not None and not _entry(tree, path):
        data = _read(tree / path)
        return {"name": path.name, "archive": False, "data": base64.b64encode(data).decode()}
    try:
        fd = jobs.open_tree(tree)
        for part in path.parts if path else ():
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
    except OSError as error:
        raise Conflict(f"cannot read the project: {error.strerror}") from None
    name = path.name if path else re.sub(r"[^\w.-]+", "-", project["name"]).strip("-") or "files"
    buffer = io.BytesIO()
    try:
        with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            _pack(fd, name, archive, [MAX_BUNDLE])
    except OSError as error:
        # A file of a local project that changed while it was read, say.
        raise Conflict(f"cannot read the project: {error.strerror or error}") from None
    finally:
        os.close(fd)
    data = buffer.getvalue()
    if len(data) > MAX_BUNDLE:
        raise Conflict(f"more than {MAX_UPLOAD_MB} MB to download: choose a directory")
    return {"name": f"{name}.tar.gz", "archive": True, "data": base64.b64encode(data).decode()}


@command("file.import", role="admin", mutating=True, agent="code", redact=("data",), tool=False,
         max_input=_MAX_BUNDLE_TEXT + 64 * 1024,
         params={
             "id": Id(),
             "filename": Str(default="", max_length=1024),
             "data": Str("The archive, in base64: tar (plain, gzip, bzip2, xz) or zip",
                         required=True, max_length=_MAX_BUNDLE_TEXT),
             "into": Str("The directory to unpack it into; empty for the top",
                         default="", max_length=1024),
             "message": Str(default="", max_length=200),
         })
def file_import(ctx: Context, params: Values) -> dict:
    """Unpack an archive into a managed project or inventory, as one commit
    of its files: a job unpacks it in the sandbox, as for an uploaded
    project, and its files are then taken as the editor would write them
    (regular files of at most 1 MB, no links), adding to what is there and
    replacing a file of the same path. A single directory that holds
    everything in the archive (project-1.0/) is not kept, as for an upload.
    Returns the run to follow."""
    row = _project(ctx, params, managed=True)
    into = str(check_file_path(params["into"], "into")) if params["into"] else ""
    message = _message(params, "")
    try:
        content = base64.b64decode(params["data"], validate=True)
    except ValueError as error:
        raise BadRequest("data is not valid base64") from error
    if not content:
        raise BadRequest("the archive is empty")
    if len(content) > ctx.config.max_upload_mb * 1024 * 1024:
        raise BadRequest(f"the archive is larger than {ctx.config.max_upload_mb} MB"
                         " (projects.max_upload_mb in config.toml)")
    shown = "".join(ch for ch in PurePosixPath(params["filename"].replace("\\", "/")).name
                    if ch.isprintable())[:200]
    options = {"filename": shown, "size": len(content), "sha256": digest(content),
               "import": {"into": into, "author": ctx.caller.user, "message": message}}
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    try:
        (directory / "archive").write_bytes(content)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    owner = {"inventory_id": row["id"]} if is_inventory(row["id"]) else {"project_id": row["id"]}
    jobs.enqueue(ctx, {"id": run_id, "kind": "unpack", "title": f"{row['name']}: {shown}",
                       "options": json.dumps(options), **owner},
                 {"kind": "unpack"}, directory)
    return {"run_id": run_id, "filename": shown, "size": len(content),
            "sha256": options["sha256"]}


def _gather(root: Path) -> list[tuple[str, bytes | None]]:
    """The files a job unpacked into ROOT, read following no link, as
    (relative path, content), and its empty directories, with None as
    content: only regular files and directories are taken."""
    found: list[tuple[str, bytes | None]] = []
    budget = [MAX_BUNDLE]

    def walk(directory_fd: int, prefix: str) -> None:
        names = sorted(os.listdir(directory_fd))
        if not names and prefix:
            found.append((prefix, None))
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, _DIR_FLAGS, dir_fd=directory_fd)
                try:
                    walk(child, relative)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise Conflict(f"{relative}: only files and directories are taken here, not links")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=directory_fd)
            with os.fdopen(descriptor, "rb") as source:
                data = source.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise Conflict(f"{relative} is larger than {MAX_FILE // 1024} kB")
            budget[0] -= len(data)
            if budget[0] < 0:
                raise Conflict(f"the archive holds more than {MAX_UPLOAD_MB} MB")
            found.append((relative, data))
            if len(found) > MAX_LISTED:
                raise Conflict(f"the archive holds more than {MAX_LISTED} files")

    fd = os.open(root, _DIR_FLAGS)
    try:
        walk(fd, "")
    finally:
        os.close(fd)
    return found


def take_import(ctx: Context, run, directory: Path) -> str:
    """Take what an unpack job of file.import left as one commit of the
    project or inventory, by whoever asked for it; returns its revision.
    What the job left is not trusted: it is read like an archive's members."""
    options = store.loads(run["options"], {})
    asked = options.get("import") or {}
    container_id = run["project_id"] or run["inventory_id"]
    if not container_id:
        raise Conflict("the project or inventory was deleted meanwhile")
    row = _fetch(ctx, container_id)
    if row["kind"] != "managed":
        raise Conflict("only what is written in the interface takes an import")
    if not stat.S_ISDIR(os.lstat(directory / "checkout").st_mode):
        raise Conflict("the job did not leave a directory")
    into = check_file_path(asked["into"], "into") if asked.get("into") else None
    tree = container_tree(ctx, row)
    operations = []
    for relative, data in _gather(directory / "checkout"):
        path = check_file_path(str(into / relative) if into else relative)
        if data is None:
            if _mode_on_way(tree, path) is None:
                operations.append({"op": "mkdir", "path": path})
            continue
        target = _locate(tree, path)
        expected = digest(_read(target)) if target.is_file() else ""
        operations.append({"op": "write", "path": path, "data": data, "expected": expected})
    if not operations:
        raise Conflict("the archive holds no file")
    name = options.get("filename") or "an archive"
    message = asked.get("message") or f"Import {name}" + (f" into {into}" if into else "")
    done = change(ctx, row, operations, message[:200], author=str(asked.get("author") or ""),
                  start=False)
    return done["revision"]


@command("file.history", role="admin",
         params={
             "id": Id(),
             "path": Str(default="", max_length=1024),
             "limit": Int(1, 500, default=50),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_history(ctx: Context, params: Values) -> dict:
    """The changes of a managed project, newest first; of one file if asked."""
    project = _project(ctx, params, managed=True)
    draft = _reading(ctx, project, params)
    path = params["path"]
    limit = params["limit"]
    selection = ["--", str(check_file_path(path))] if path else []
    # Nothing committed yet is not an error: git says so by failing.
    output = git(project["id"], "log", f"--max-count={limit}", "-z",
                  "--format=%H%x1f%an%x1f%at%x1f%s", draft.ref if draft else "HEAD",
                  *selection, check=False) or ""
    commits = []
    for record in output.split("\0"):
        fields = record.strip("\n").split("\x1f")
        if len(fields) == 4:
            commits.append({"revision": fields[0], "author": fields[1], "time": int(fields[2]),
                            "subject": fields[3]})
    return {"commits": commits, "draft": draft.session if draft else None}


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
    text = git(project["id"], "show", "--format=", "--no-color", "--no-ext-diff", "--no-textconv",
                f"{revision}^{{commit}}", check=False)
    if text is None:
        raise NotFound(f"no such revision: {revision}")
    return {"text": text[:MAX_DIFF], "cut": len(text) > MAX_DIFF}


@command("file.check", role="admin", mutating=True, agent="run",
         params={
             "id": Id(),
             "path": Str(required=True, max_length=1024),
             "draft": Str(_DRAFT_DOC, default="", max_length=16),
         })
def file_check(ctx: Context, params: Values) -> dict:
    """Queue a syntax check of one playbook, of any kind of project; in an
    inventory written in the interface, of the whole inventory, as it reads
    the file at path among its sources or their variables. In a draft, of
    what the draft holds."""
    project = _project(ctx, params)
    path = check_file_path(params["path"])
    draft = _reading(ctx, project, params)
    if is_inventory(project["id"]):
        from .resources import queue_parse  # it builds on this module
        tree = draft_tree(draft) if draft else container_tree(ctx, project)
        if not _locate(tree, path).is_file():
            raise NotFound(f"no such file: {path}")
        if draft is not None:
            return {"run_id": queue_parse(ctx, project, syntax=True, tree=tree,
                                          draft=draft.session, revision=draft_tip(draft))}
        return {"run_id": queue_parse(ctx, project, syntax=True)}
    return {"run_id": _queue_check(ctx, project, path, draft=draft)}


# --- at a terminal: clone and push --------------------------------------------------------

@command("file.bundle", role="admin", mutating=True, agent="read", tool=False,
         params={
             "id": Id(),
         })
def file_bundle(ctx: Context, params: Values) -> dict:
    """The repository of a managed project as a git bundle, in base64, for
    fleet project clone and pull; recorded in the audit trail, as a copy of
    every file of the project is."""
    project = _project(ctx, params, managed=True)
    with locked(project["id"]), tempfile.TemporaryDirectory(dir=_home(project["id"])) as work:
        target = Path(work) / "project.bundle"
        if git(project["id"], "rev-parse", "--verify", "--quiet", "HEAD", check=False) is None:
            raise Conflict("nothing was written in this project yet")
        git(project["id"], "bundle", "create", "--quiet", str(target), "main")
        data = target.read_bytes()
    if len(data) > MAX_BUNDLE:
        raise Conflict(f"the project is larger than {MAX_UPLOAD_MB} MB as a bundle")
    return {"revision": project["revision"], "bundle": base64.b64encode(data).decode("ascii")}


def _incoming_files(project_id: str, base: str) -> list[str]:
    """The paths the pushed commits change, after checking that every file of
    their last tree is one the editor could have written, and that they are a
    line of commits on top of base."""
    if git(project_id, "merge-base", "--is-ancestor", base, _INCOMING, check=False) is None:
        raise Conflict("the commits pushed do not follow the revision of the project")
    if (git(project_id, "rev-list", "--merges", f"{base}..{_INCOMING}") or "").strip():
        raise BadRequest("the commits pushed hold a merge: push a line of commits")
    listing = git(project_id, "ls-tree", "-r", "-l", "-z", "--full-tree", _INCOMING) or ""
    count = 0
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode, kind, _object, size = meta.split()
        count += 1
        if kind != "blob" or mode not in ("100644", "100755"):
            raise BadRequest(f"{path}: only regular files are taken, not links or submodules")
        check_file_path(path)
        if int(size) > MAX_FILE:
            raise BadRequest(f"{path} is larger than {MAX_FILE // 1024} kB")
    if count > MAX_LISTED:
        raise BadRequest(f"more than {MAX_LISTED} files")
    changed = git(project_id, "diff", "--name-only", "-z", "--no-renames", base, _INCOMING) or ""
    return [path for path in changed.split("\0") if path]


@command("file.push", role="admin", mutating=True, agent="code", redact=("bundle",), tool=False,
         max_input=_MAX_BUNDLE_TEXT + 64 * 1024,
         params={
             "id": Id(),
             "expected": Str("The revision the commits were made on top of", required=True,
                             max_length=64),
             "bundle": Str("A git bundle of the commits, in base64", required=True,
                           max_length=_MAX_BUNDLE_TEXT),
         })
def file_push(ctx: Context, params: Values) -> dict:
    """Take commits made in a clone of a managed project, for fleet project
    push: only on top of the revision the project still has, as a line of
    commits whose files the editor could have written. Playbooks they change
    are checked, as when written in the interface."""
    project = _project(ctx, params, managed=True)
    expected = params["expected"]
    if not _REVISION_RE.fullmatch(expected):
        raise BadRequest("expected must be a commit id")
    try:
        data = base64.b64decode(params["bundle"], validate=True)
    except ValueError as error:
        raise BadRequest("bundle is not valid base64") from error
    project_id = project["id"]
    with locked(project_id), tempfile.TemporaryDirectory(dir=_home(project_id)) as work:
        project = _fetch(ctx, project_id)
        if project["revision"] != expected:
            raise Conflict(f"the {_table(project_id)[1]} changed since your clone: pull, then push")
        bundle = Path(work) / "incoming.bundle"
        bundle.write_bytes(data)
        if git(project_id, "bundle", "verify", "--quiet", str(bundle), check=False) is None:
            raise BadRequest("this is not a bundle of commits on top of the project")
        # Every object checked, not only those of the tree that is looked at.
        git(project_id, "fetch", "--quiet", "--no-tags", str(bundle), f"+main:{_INCOMING}",
             env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "transfer.fsckObjects",
                  "GIT_CONFIG_VALUE_0": "true"})
        try:
            changed = _incoming_files(project_id, expected)
            git(project_id, "merge", "--quiet", "--ff-only", _INCOMING)
        finally:
            git(project_id, "update-ref", "-d", _INCOMING, check=False)
        revision = git(project_id, "rev-parse", "HEAD").strip()
        table, what = _table(project_id)
        store.update(ctx.conn, table, project_id,
                     {"revision": revision, **({"synced_at": store.now()}
                                               if table == "projects" else {})}, what)
    project = _fetch(ctx, project_id)
    if is_inventory(project_id):
        from .resources import queue_parse  # it builds on this module
        return {"revision": revision, "files": changed,
                "check_run_ids": [queue_parse(ctx, project, syntax=True)],
                "collections_run_id": None, "inventory_run_ids": _reread(ctx, project, changed)}
    tree = container_tree(ctx, project)
    checks = []
    for path in changed:
        target = tree / path
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if looks_like_playbook(path, content):
            checks.append(_queue_check(ctx, project, PurePosixPath(path)))
    collections = jobs.install_missing(ctx, project_id) \
        if any(path in jobs.REQUIREMENTS for path in changed) else None
    return {"revision": revision, "files": changed, "check_run_ids": checks,
            "collections_run_id": collections,
            "inventory_run_ids": reread_inventories(ctx, project, changed)}
