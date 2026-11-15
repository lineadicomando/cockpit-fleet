"""Starters: ready-made content that a new managed project, a new managed
inventory or a new playbook starts from.

They come from the package (/usr/share/cockpit-fleet/starters) and from the
administrator (/etc/cockpit-fleet/starters), laid out the same way:
projects/NAME/ and inventories/NAME/ are trees, playbooks/NAME.yaml one file
(or NAME.yml, which an administrator may have written).
Their title and description are in a starter.toml inside a tree, or in
NAME.toml beside a playbook, which is not copied. When both sources have a
starter with the same name, both are offered: one never replaces the other.

A starter is copied as it is, in the first commit of what is made from it,
and nothing links the two afterwards. It is read under the rules of the
editor: plain names, no links, no special files, each file at most
files.MAX_FILE, and a cap on the whole tree; one that breaks them is listed
with what is wrong, and cannot be used.

A third source, saved, is the managed projects and inventories kept as
starters (project.starter, inventory.starter): their files as main has
them, within the limits of a tree rather than of a starter on disk, since
the editor wrote them.
"""

import os
import re
import stat
import tomllib
from pathlib import Path, PurePosixPath

from . import files, paths
from .cli import Choice, Context, Str, Values, command
from .errors import BadRequest, FleetError, NotFound
from .files import KEEP, MAIN_FILES, MAX_FILE, OLD_MAIN
from .validators import check_file_path

KINDS = {"project": "projects", "inventory": "inventories", "playbook": "playbooks"}
SOURCES = ("package", "local")
SAVED = "saved"
_TABLES = {"project": "projects", "inventory": "inventories"}
META = "starter.toml"
# What one source offers of a kind, and what one starter holds.
MAX_STARTERS = 100
MAX_FILES = 500
MAX_TOTAL = 16 * 1024 * 1024
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_REF_RE = re.compile(r"(package|local|saved)/([A-Za-z0-9][A-Za-z0-9_-]{0,63})")
# A playbook starter's file; .yaml first, the name of what is made from it.
PLAYBOOK_SUFFIXES = (".yaml", ".yml")
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_STARTER_DOC = ("A starter to begin with, as SOURCE/NAME (package, local or saved, see"
                " starter.list): its files are the first commit")


def _root(source: str) -> Path:
    return paths.STARTERS_DIR if source == "package" else paths.LOCAL_STARTERS_DIR


def _read(path: Path) -> bytes:
    """A regular file, not through a link, of at most MAX_FILE."""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError as error:
        raise BadRequest(f"{path.name}: cannot be read ({error.strerror})") from None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise BadRequest(f"{path.name}: not a regular file")
        data = handle.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise BadRequest(f"{path.name}: larger than {MAX_FILE // 1024} kB")
    return data


def _meta(path: Path) -> dict:
    """The title and description of a starter, from its TOML file if it has one."""
    if not os.path.lexists(path):
        return {}
    try:
        data = tomllib.loads(_read(path).decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise BadRequest(f"{path.name}: {error}") from None
    meta = {}
    for key in ("title", "description"):
        if key in data:
            if not isinstance(data[key], str) or len(data[key]) > 500:
                raise BadRequest(f"{path.name}: {key} must be a string of at most 500 characters")
            meta[key] = data[key].strip()
    return meta


def _tree(base: Path) -> dict[str, bytes]:
    """The files of the tree BASE by relative path, its metadata left out."""
    found: dict[str, bytes] = {}
    total = 0
    for current, directories, names in os.walk(base):
        here = Path(current)
        for name in sorted(directories + names):
            path = here / name
            relative = PurePosixPath(path.relative_to(base).as_posix())
            if str(relative) == META or name == KEEP:
                continue
            check_file_path(str(relative))
            mode = os.lstat(path).st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                # A link included: os.walk does not follow one to a directory either.
                raise BadRequest(f"{relative}: not a regular file or a directory")
            data = _read(path)
            total += len(data)
            if len(found) >= MAX_FILES or total > MAX_TOTAL:
                raise BadRequest(f"more than {MAX_FILES} files or {MAX_TOTAL // 1024 // 1024} MB")
            found[str(relative)] = data
        directories.sort()
    return found


def _paths(kind: str, source: str, name: str) -> tuple[Path, Path]:
    """Where the content of a starter is, and where its metadata."""
    home = _root(source) / KINDS[kind]
    if kind == "playbook":
        found = [home / f"{name}{suffix}" for suffix in PLAYBOOK_SUFFIXES
                 if os.path.lexists(home / f"{name}{suffix}")]
        return (found or [home / f"{name}.yaml"])[0], home / f"{name}.toml"
    return home / name, home / name / META


def _content(kind: str, source: str, name: str) -> dict[str, bytes]:
    where, _meta_path = _paths(kind, source, name)
    if kind == "playbook":
        return {where.name: _read(where)}
    mode = os.lstat(where).st_mode
    if not stat.S_ISDIR(mode):
        raise BadRequest("not a directory")
    return _tree(where)


def _names(kind: str, source: str) -> list[str]:
    """The names of the starters of KIND a source offers."""
    home = _root(source) / KINDS[kind]
    try:
        entries = sorted(os.listdir(home))
    except (FileNotFoundError, NotADirectoryError):
        return []
    names = []
    for entry in entries:
        if kind == "playbook":
            if not entry.endswith(PLAYBOOK_SUFFIXES):
                continue
            entry = entry.rsplit(".", 1)[0]
        if _NAME_RE.fullmatch(entry) and entry not in names:
            names.append(entry)
    return names[:MAX_STARTERS]


def _format(content: dict[str, bytes]) -> str:
    """The format of an inventory a starter makes: that of its main file."""
    if MAIN_FILES["yaml"] in content or OLD_MAIN in content:
        return "yaml"
    return "ini" if MAIN_FILES["ini"] in content else ""


def describe(kind: str, source: str, name: str) -> dict:
    """One starter as starter.list shows it; error says why it cannot be used."""
    entry = {"kind": kind, "source": source, "name": name, "ref": f"{source}/{name}",
             "title": name, "description": "", "files": [], "error": ""}
    try:
        entry.update(_meta(_paths(kind, source, name)[1]))
        content = _content(kind, source, name)
        if not content:
            raise BadRequest("it holds no file")
        entry["files"] = sorted(content)
        if kind == "inventory":
            entry["format"] = _format(content)
        elif kind == "playbook":
            try:
                entry["content"] = next(iter(content.values())).decode()
            except UnicodeDecodeError:
                raise BadRequest("not text") from None
    except (BadRequest, OSError) as error:
        entry["error"] = str(error) if isinstance(error, BadRequest) else \
            f"cannot be read ({error.strerror})"
        entry["files"] = []
        entry.pop("content", None)
    return entry


def _saved_rows(ctx: Context, kind: str) -> list:
    """The managed projects or inventories kept as starters."""
    if kind not in _TABLES:
        return []
    return ctx.conn.execute(
        f"SELECT * FROM {_TABLES[kind]} WHERE kind = 'managed' AND starter"  # noqa: S608
        " ORDER BY name COLLATE NOCASE").fetchall()


def describe_saved(ctx: Context, kind: str, row) -> dict:
    """A project or an inventory kept as a starter, as starter.list shows it."""
    entry = {"kind": kind, "source": SAVED, "name": row["id"], "ref": f"{SAVED}/{row['id']}",
             "title": row["name"], "description": "", "files": [], "error": ""}
    if kind == "inventory":
        entry["format"] = row["format"]
        entry["path"] = row["path"]
    try:
        tree = files.container_tree(ctx, row)
        for current, directories, names in os.walk(tree):
            directories.sort()
            for name in sorted(names):
                path = Path(current) / name
                status = os.lstat(path)
                if stat.S_ISREG(status.st_mode) and not (name == KEEP and not status.st_size):
                    entry["files"].append(path.relative_to(tree).as_posix())
        if not entry["files"]:
            entry["error"] = "it holds no file"
    except (FleetError, OSError) as error:
        entry["error"] = str(error)
        entry["files"] = []
    return entry


def load(ctx: Context, kind: str, ref: str) -> tuple[dict, list[dict]]:
    """The starter REF of KIND, as its description and the operations of
    files.change that write its files."""
    match = _REF_RE.fullmatch(ref or "")
    if not match:
        raise BadRequest(f"invalid starter: {ref!r}: give SOURCE/NAME")
    source, name = match.groups()
    if source == SAVED:
        row = next((one for one in _saved_rows(ctx, kind) if one["id"] == name), None)
        if row is None:
            raise NotFound(f"no such {kind} starter: {ref}")
        entry = describe_saved(ctx, kind, row)
        if entry["error"]:
            raise BadRequest(f"the starter {ref} cannot be used: {entry['error']}")
        return entry, files.copy_operations(ctx, row)
    if name not in _names(kind, source):
        raise NotFound(f"no such {kind} starter: {ref}")
    entry = describe(kind, source, name)
    if entry["error"]:
        raise BadRequest(f"the starter {ref} cannot be used: {entry['error']}")
    content = _content(kind, source, name)
    operations = [{"op": "write", "path": PurePosixPath(path), "data": data, "expected": ""}
                  for path, data in sorted(content.items())]
    return entry, operations


@command("starter.list", role="admin",
         params={"kind": Choice(("all", *KINDS), "Only the starters of this kind",
                                default="all")})
def starter_list(ctx: Context, params: Values) -> dict:
    """The starters a new managed project (kind project), managed inventory
    (inventory) or playbook (playbook) can begin with, the package's, this
    controller's files and its projects and inventories kept as starters
    (saved), with their files; a playbook's with its text. Give
    one as SOURCE/NAME (ref) to project.create, inventory.create, or write
    a playbook's text with file.write."""
    kinds = list(KINDS) if params["kind"] == "all" else [params["kind"]]
    found = []
    for kind in kinds:
        found += [describe(kind, source, name) for source in SOURCES
                  for name in _names(kind, source)]
        found += [describe_saved(ctx, kind, row) for row in _saved_rows(ctx, kind)]
    return {"starters": found}


STARTER_PARAM = Str(_STARTER_DOC, max_length=100)
