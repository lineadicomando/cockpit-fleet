"""The store: files on the controller that playbooks copy to nodes.

A tree of plain files in /var/lib/cockpit-fleet/store, one for the
controller, read by every run: installers, images, what runs fetched from
nodes. Not for secrets. Its files arrive from the browser (store.put, which
takes the bytes after its parameters, of any size), from a run, or by hand;
they leave through store.read, which writes them out, and to nodes through
playbooks.

A run of a playbook or an ad-hoc command gets a farm: the tree again in
its own directory, made of hard links to the files of the store, which it
knows as fleet_store. A link costs nothing, whatever the size of the file;
the store itself stays out of the sandbox. What the run writes there is
taken into the store when it ends:

- a file it added, or replaced (Ansible writes a file to a temporary name
  and renames it over the old one, which leaves a new inode), is moved in;
- a file it changed in place, through the link, has changed the store
  already: its digest is taken again, and the change is put down to the run;
- a file it deleted from its farm stays in the store.

The farm is made when the run starts, not when it is queued: a run that
waits a day for an approval would otherwise keep alive files deleted
meanwhile. What each link was is kept beside the store, where the run
cannot reach it (STORE_FARMS), so that a file replaced in the store during
the run is not taken for one the run wrote. Hard links need the store and
the runs on one file system.

The database keeps what the disk cannot say of a file: its digest, where it
came from, who put it there. sync() brings it up to date with the tree, so
that a file put there by hand appears too; the digest of a file nobody has
taken yet is taken by the tick of the scheduler (digest_pending), never by
a call of the interface, which must answer at once.
"""

import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
import tarfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath

from . import audit, copier, fetch, journal, paths, store
from .auth import Caller
from .cli import Bool, Context, Int, Objs, Str, Strs, Values, command
from .errors import BadRequest, Conflict, NotFound
from .names import check_id
from .validators import check_file_path

# The directories of the store are opened one from another, never through a link.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CHUNK = 1024 * 1024
# Entries of the tree that store.list gives at most.
MAX_LISTED = 20_000
# What a file system must keep free after a file is put in the store.
MIN_FREE = 1024 ** 3
# Kinds of run that get a farm.
FARM_KINDS = ("playbook", "adhoc")
# Paths a run's output names, at most, among those it left that were not taken.
_SHOWN = 20
# Uploads not resumed for this long are dropped by the tick.
_UPLOAD_AGE = 7 * 86400
# What the tick spends on digests, at most: it finishes the file it started.
_DIGEST_BUDGET = 40.0
_LARGEST = 2 ** 50


# --- the tree ---------------------------------------------------------------

def _mkdirs() -> None:
    for directory in (paths.STORE_DIR, paths.STORE_UPLOADS, paths.STORE_FARMS):
        directory.mkdir(mode=0o750 if directory == paths.STORE_DIR else 0o700, exist_ok=True)


@contextmanager
def _locked() -> Iterator[None]:
    """One change of the tree at a time: a farm is made or taken in whole."""
    _mkdirs()
    fd = os.open(paths.STORE_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _open_dir(parts: tuple[str, ...], create: bool = False, root: int | None = None) -> int:
    """The directory of the store at PARTS, opened; with create, made on the way."""
    if root is None:
        _mkdirs()
        fd = os.open(paths.STORE_DIR, _DIR_FLAGS)
    else:
        fd = os.dup(root)
    try:
        for part in parts:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise NotFound(f"no such directory: {part}") from None
                os.mkdir(part, 0o750, dir_fd=fd)
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as error:
                if error.errno in (errno.ENOTDIR, errno.ELOOP):
                    raise Conflict(f"{part} is not a directory") from None
                raise
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _lstat(fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _walk(fd: int, prefix: str, files: dict, directories: list, depth: int = 0) -> bool:
    """The regular files and directories under the open directory FD, by
    path; links and the rest are not part of the tree. False when cut."""
    for name in sorted(os.listdir(fd)):
        if len(files) + len(directories) >= MAX_LISTED:
            return False
        status = _lstat(fd, name)
        if status is None:
            continue
        path = f"{prefix}/{name}" if prefix else name
        if stat.S_ISREG(status.st_mode):
            files[path] = status
        elif stat.S_ISDIR(status.st_mode) and depth < 11:
            directories.append(path)
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError:
                continue
            try:
                if not _walk(child, path, files, directories, depth + 1):
                    return False
            finally:
                os.close(child)
    return True


def scan() -> tuple[dict[str, os.stat_result], list[str], bool]:
    """The files and directories of the store, and whether all of them."""
    files: dict[str, os.stat_result] = {}
    directories: list[str] = []
    fd = _open_dir(())
    try:
        whole = _walk(fd, "", files, directories)
    finally:
        os.close(fd)
    return files, directories, whole


def _seen(status: os.stat_result) -> tuple[int, int, int]:
    return status.st_ino, status.st_size, status.st_mtime_ns


def _record(conn, path: str, status: os.stat_result, origin: str, source: str,
            user: str | None, sha256: str | None = None) -> None:
    conn.execute(
        "INSERT INTO store_files (path, size, mtime_ns, inode, sha256, origin, source,"
        " updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (path) DO UPDATE SET size = excluded.size, mtime_ns = excluded.mtime_ns,"
        " inode = excluded.inode, sha256 = excluded.sha256, origin = excluded.origin,"
        " source = excluded.source, updated_by = excluded.updated_by,"
        " updated_at = excluded.updated_at",
        (path, status.st_size, status.st_mtime_ns, status.st_ino, sha256, origin, source[:1024],
         user, time.time()))


def sync(conn) -> tuple[dict[str, os.stat_result], list[str], bool]:
    """Bring the rows up to date with the tree: a file that appeared was put
    there by hand, one that changed was changed outside the backend; either
    waits for its digest. A row whose file is gone goes, unless the listing
    was cut."""
    files, directories, whole = scan()
    rows = {row["path"]: row for row in conn.execute(
        "SELECT path, inode, size, mtime_ns FROM store_files")}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for path, status in files.items():
            row = rows.get(path)
            if row is None or (row["inode"], row["size"], row["mtime_ns"]) != _seen(status):
                _record(conn, path, status, "found", "", None)
        if whole:
            for path in rows.keys() - files.keys():
                conn.execute("DELETE FROM store_files WHERE path = ?", (path,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return files, directories, whole


def _usage(conn) -> int:
    return conn.execute("SELECT coalesce(sum(size), 0) FROM store_files").fetchone()[0]


def _room(ctx: Context, adding: int, replacing: int = 0) -> None:
    """Refuse a file that would take the store beyond its limit, or leave
    the disk with less than MIN_FREE."""
    limit = ctx.config.store_max_gb * 1024 ** 3
    used = _usage(ctx.conn)
    if limit and used - replacing + adding > limit:
        raise Conflict(f"the store would exceed its limit of {ctx.config.store_max_gb} GB"
                       " ([store] max_gb)")
    _mkdirs()
    disk = shutil.disk_usage(paths.STORE_DIR)
    if disk.free - adding < MIN_FREE:
        raise Conflict("not enough free space on the disk of the store")


def _row(row) -> dict:
    return {"path": row["path"], "size": row["size"], "sha256": row["sha256"],
            "origin": row["origin"], "source": row["source"],
            "updated_by": row["updated_by"], "updated_at": row["updated_at"]}


def _digest_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


# --- commands ---------------------------------------------------------------

@command("store.list", role="viewer", params={
    "since": Str("The token of an earlier listing: when nothing has changed since,"
                 " the answer is only unchanged, the token and the free space",
                 default="", max_length=64),
})
def store_list(ctx: Context, params: Values) -> dict:
    """The files and directories of the store, with what is known of each
    file: its size, its digest (null until the tick has taken it), where it
    came from (upload, run, url, copy, found when put there by hand), who
    put it there and when; how much of the disk the store takes and has
    left; and a token that changes with any of it but the free space."""
    files, directories, whole = sync(ctx.conn)
    rows = ctx.conn.execute("SELECT * FROM store_files ORDER BY path").fetchall()
    own = os.geteuid()
    listed = []
    for row in rows:
        status = files.get(row["path"])
        if status is None:
            continue
        # A file a run cannot be given: hard links are only made to the
        # account's own files (fs.protected_hardlinks).
        listed.append({**_row(row), "usable": status.st_uid == own})
    disk = shutil.disk_usage(paths.STORE_DIR)
    limit = ctx.config.store_max_gb * 1024 ** 3
    # The walk of the tree is done either way: what is saved is the answer,
    # and the page drawing it again.
    token = hashlib.sha256(json.dumps([listed, directories, whole, limit],
                                      sort_keys=True).encode()).hexdigest()[:32]
    if params["since"] == token:
        return {"unchanged": True, "token": token, "free": disk.free}
    return {"files": listed, "directories": directories, "complete": whole,
            "used": sum(file["size"] for file in listed), "free": disk.free,
            "limit": limit, "token": token}


@command("store.get", role="viewer", params={
    "path": Str("The path of a file in the store", required=True),
})
def store_get(ctx: Context, params: Values) -> dict:
    """One file of the store: what store.list says of it. The interface asks
    before a download, which cannot tell an error once it has begun."""
    path = check_file_path(params["path"])
    fd = _open_dir(path.parts[:-1])
    try:
        status = _lstat(fd, path.name)
    finally:
        os.close(fd)
    if status is None or not stat.S_ISREG(status.st_mode):
        raise NotFound(f"no such file in the store: {path}")
    row = ctx.conn.execute("SELECT * FROM store_files WHERE path = ?", (str(path),)).fetchone()
    if row is None or (row["inode"], row["size"], row["mtime_ns"]) != _seen(status):
        _record(ctx.conn, str(path), status, "found", "", None)
        row = ctx.conn.execute("SELECT * FROM store_files WHERE path = ?",
                               (str(path),)).fetchone()
    return {**_row(row), "usable": status.st_uid == os.geteuid()}


@command("store.read", role="viewer", mutating=True, tool=False, stream="out", params={
    "path": Str("The path of a file in the store", required=True),
})
def store_read(ctx: Context, params: Values) -> dict:
    """The bytes of a file of the store, written out as they are, not in an
    answer; in the audit trail, as a copy of the file."""
    path = check_file_path(params["path"])
    directory = _open_dir(path.parts[:-1])
    try:
        fd = os.open(path.name, _FILE_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        raise NotFound(f"no such file in the store: {path}") from None
    except OSError as error:
        raise Conflict(f"cannot read {path}: {error.strerror}") from None
    finally:
        os.close(directory)
    status = os.fstat(fd)
    if not stat.S_ISREG(status.st_mode):
        os.close(fd)
        raise Conflict(f"{path} is not a file")
    os.set_blocking(fd, True)
    ctx.output = os.fdopen(fd, "rb")
    return {"path": str(path), "size": status.st_size}


class _Archive:
    """A tar of entries of the store, made as it is read: a thread writes it
    into a pipe, whose end main() copies out. Nothing of it is held whole."""

    def __init__(self, entries: list[tuple[str, int | None, os.stat_result]]):
        reader, writer = os.pipe()
        self._reader = os.fdopen(reader, "rb")
        self._writer = os.fdopen(writer, "wb")
        self._entries = entries
        # The files not yet handed to the tar, which closes those it has read.
        self._open = {fd for _name, fd, _status in entries if fd is not None}
        self._thread = threading.Thread(target=self._write, daemon=True)
        self._thread.start()

    def _write(self) -> None:
        try:
            with tarfile.open(fileobj=self._writer, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for name, fd, status in self._entries:
                    info = tarfile.TarInfo(name)
                    info.mtime = int(status.st_mtime)
                    if fd is None:
                        info.type, info.mode = tarfile.DIRTYPE, 0o755
                        tar.addfile(info)
                        continue
                    info.size, info.mode = status.st_size, 0o644
                    self._open.discard(fd)
                    with os.fdopen(fd, "rb") as source:
                        tar.addfile(info, source)
        except (OSError, tarfile.TarError):
            pass  # the reader sees the tar end short: the browser's file is broken
        finally:
            for fd in self._open:
                os.close(fd)
            self._open.clear()
            try:
                self._writer.close()
            except OSError:
                pass

    def read(self, size: int = -1) -> bytes:
        return self._reader.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self._reader.close()


@command("store.archive", role="viewer", mutating=True, tool=False, stream="out", params={
    "paths": Strs("Files and directories of the store, each with what it holds",
                  max_items=1000),
})
def store_archive(ctx: Context, params: Values) -> dict:
    """Files and directories of the store as one tar, not compressed, written
    out as it is made; in the audit trail, as a copy of them. Each is named
    in it from its own name down: a folder win/office is office/ there."""
    given = [check_file_path(path) for path in params["paths"]]
    if not given:
        raise BadRequest("nothing to archive: give paths")
    chosen = [path for path in dict.fromkeys(given)
              if not any(path != other and path.is_relative_to(other) for other in given)]
    names = [path.name for path in chosen]
    if len(set(names)) != len(names):
        raise BadRequest("two of them have the same name: archive their folder instead")
    # Every file opened now: what fails, fails before the first byte.
    entries: list[tuple[str, int | None, os.stat_result]] = []
    try:
        for path in chosen:
            status = _kind(path)
            if stat.S_ISREG(status.st_mode):
                entries.append((path.name, _open_file(path), status))
                continue
            fd = _open_dir(path.parts)
            files: dict[str, os.stat_result] = {}
            directories: list[str] = []
            try:
                if not _walk(fd, "", files, directories):
                    raise BadRequest(f"{path} holds too many entries to archive")
            finally:
                os.close(fd)
            entries.append((path.name, None, status))
            inside = sorted([(name, None) for name in directories]
                            + [(name, files[name]) for name in files])
            for name, file_status in inside:
                if file_status is None:
                    entries.append((f"{path.name}/{name}", None, status))
                else:
                    entries.append((f"{path.name}/{name}", _open_file(path / name),
                                    file_status))
    except BaseException:
        for _name, fd, _status in entries:
            if fd is not None:
                os.close(fd)
        raise
    # What was opened is what is sent: its size as it was opened.
    entries = [(name, fd, os.fstat(fd) if fd is not None else status)
               for name, fd, status in entries]
    ctx.output = _Archive(entries)
    return {"paths": [str(path) for path in chosen],
            "size": sum(status.st_size for _name, fd, status in entries if fd is not None)}


def _open_file(path: PurePosixPath) -> int:
    """A file of the store, opened for reading, never through a link."""
    directory = _open_dir(path.parts[:-1])
    try:
        fd = os.open(path.name, _FILE_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        raise NotFound(f"no such file in the store: {path}") from None
    except OSError as error:
        raise Conflict(f"cannot read {path}: {error.strerror}") from None
    finally:
        os.close(directory)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Conflict(f"{path} is not a file")
    os.set_blocking(fd, True)
    return fd


def _upload_key(user: str, path: str) -> str:
    return hashlib.sha256(f"{user}\0{path}".encode()).hexdigest()[:32]


def _uploads(user: str) -> list[dict]:
    found = []
    _mkdirs()
    for entry in os.scandir(paths.STORE_UPLOADS):
        if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
            continue
        try:
            meta = json.loads(paths.STORE_UPLOADS.joinpath(entry.name).read_bytes())
            part = os.stat(entry.path[:-5] + ".part", follow_symlinks=False)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("user") == user:
            found.append({"path": meta.get("path"), "size": meta.get("size"),
                          "received": part.st_size, "started_at": meta.get("started_at")})
    return found


def _drop_upload(key: str) -> None:
    for suffix in (".part", ".json"):
        (paths.STORE_UPLOADS / f"{key}{suffix}").unlink(missing_ok=True)


@command("store.uploads", role="operator")
def store_uploads(ctx: Context, params: Values) -> dict:
    """The caller's uploads that stopped before their end: each may be
    resumed by store.put from what it received, or dropped by store.discard."""
    return {"uploads": _uploads(ctx.caller.user)}


@command("store.discard", role="operator", mutating=True, agent="code", tool=False, params={
    "path": Str("The path the upload was for", required=True),
})
def store_discard(ctx: Context, params: Values) -> dict:
    """Drop what an upload of the caller received, to start it again."""
    _drop_upload(_upload_key(ctx.caller.user, str(check_file_path(params["path"]))))
    return {}


def _place(source_dir: int, source_name: str, path: PurePosixPath, replace: bool) -> int:
    """Move a file into the store at PATH; the directory it is now in, open."""
    target = _open_dir(path.parts[:-1], create=True)
    try:
        existing = _lstat(target, path.name)
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise Conflict(f"{path} is a directory")
        if existing is not None and not replace:
            raise Conflict(f"{path} is already in the store")
        try:
            os.rename(source_name, path.name, src_dir_fd=source_dir, dst_dir_fd=target)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            # The store on a file system of its own: a copy, then the rename.
            temporary = f".{path.name}.{os.getpid()}"
            with os.fdopen(os.open(source_name, _FILE_FLAGS, dir_fd=source_dir), "rb") as src, \
                    os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                      | os.O_NOFOLLOW, 0o640, dir_fd=target), "wb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK)
            os.rename(temporary, path.name, src_dir_fd=target, dst_dir_fd=target)
            os.unlink(source_name, dir_fd=source_dir)
    except BaseException:
        os.close(target)
        raise
    return target


@command("store.put", role="operator", mutating=True, agent="code", tool=False, stream="in",
         params={
             "path": Str("Where the file goes in the store", required=True),
             "size": Int(0, _LARGEST, "Its size in bytes", default=0),
             "offset": Int(0, _LARGEST, "Bytes of an earlier upload of the same file that"
                           " are not sent again: what store.uploads says it received",
                           default=0),
             "sha256": Str("Its digest, checked when it has arrived", default="",
                           max_length=64),
             "replace": Bool("Replace a file already there"),
         })
def store_put(ctx: Context, params: Values) -> dict:
    """Put a file in the store. The parameters are the first line of the
    input, the bytes of the file follow: SIZE of them, less OFFSET when an
    upload that stopped is resumed. One that stops again is kept, to be
    resumed; it is complete only when every byte has arrived."""
    if ctx.stdin is None:
        raise BadRequest("store.put takes the file after its parameters, on its input")
    path = check_file_path(params["path"])
    size, offset, expected = params["size"], params["offset"], params["sha256"].lower()
    if offset > size:
        raise BadRequest("offset cannot exceed size")
    if expected and not (len(expected) == 64 and all(c in "0123456789abcdef" for c in expected)):
        raise BadRequest("sha256 must be 64 hexadecimal digits")
    row = ctx.conn.execute("SELECT size FROM store_files WHERE path = ?",
                           (str(path),)).fetchone()
    if row is not None and not params["replace"]:
        raise Conflict(f"{path} is already in the store")
    _room(ctx, size, row["size"] if row is not None else 0)

    _mkdirs()
    key = _upload_key(ctx.caller.user, str(path))
    part = paths.STORE_UPLOADS / f"{key}.part"
    meta = paths.STORE_UPLOADS / f"{key}.json"
    if offset == 0:
        fd = os.open(part, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600)
        meta.write_text(json.dumps({"path": str(path), "size": size, "user": ctx.caller.user,
                                    "started_at": time.time()}))
    else:
        try:
            fd = os.open(part, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            raise Conflict("there is no upload of this file to resume") from None
    try:
        received = os.fstat(fd).st_size
        if received != offset:
            raise Conflict(f"the upload to resume has {received} bytes, not {offset}")
        digest = hashlib.sha256()
        os.lseek(fd, 0, os.SEEK_SET)
        while chunk := os.read(fd, _CHUNK):
            digest.update(chunk)
        remaining = size - offset
        while remaining:
            chunk = ctx.stdin.read(min(_CHUNK, remaining))
            if not chunk:
                break
            os.write(fd, chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if remaining:
            raise Conflict(f"the upload stopped after {size - remaining} of {size} bytes;"
                           " it can be resumed")
        if ctx.stdin.read(1):
            _drop_upload(key)
            raise BadRequest(f"more than {size} bytes were sent")
        os.fsync(fd)
        found = digest.hexdigest()
        if expected and found != expected:
            _drop_upload(key)
            raise BadRequest(f"the file that arrived has the digest {found}, not {expected}")
    finally:
        os.close(fd)

    with _locked():
        uploads = os.open(paths.STORE_UPLOADS, _DIR_FLAGS)
        try:
            target = _place(uploads, part.name, path, params["replace"])
        finally:
            os.close(uploads)
        try:
            status = os.stat(path.name, dir_fd=target, follow_symlinks=False)
        finally:
            os.close(target)
        _record(ctx.conn, str(path), status, "upload", path.name, ctx.caller.user, found)
    meta.unlink(missing_ok=True)
    return {"path": str(path), "size": size, "sha256": found}


@command("store.mkdir", role="operator", mutating=True, agent="code", tool=False, params={
    "path": Str("The directory to make, with those on its way", required=True),
})
def store_mkdir(ctx: Context, params: Values) -> dict:
    """Make a directory in the store."""
    path = check_file_path(params["path"])
    with _locked():
        os.close(_open_dir(path.parts, create=True))
    return {"path": str(path)}


def _pairs(items: list[dict]) -> list[tuple[PurePosixPath, PurePosixPath]]:
    """The (path, to) pairs of objects with both, checked against each other."""
    pairs = []
    for item in items:
        path = check_file_path(item.get("path"))
        to = check_file_path(item.get("to"), "to")
        if to == path or to.is_relative_to(path):
            raise BadRequest(f"{path} cannot go into itself")
        pairs.append((path, to))
    targets = [to for _path, to in pairs]
    for index, to in enumerate(targets):
        if any(to == other or to.is_relative_to(other) or other.is_relative_to(to)
               for other in targets[:index]):
            raise BadRequest(f"two of them would go to {to}")
    return pairs


def _kind(path: PurePosixPath) -> os.stat_result:
    """The status of a file or a directory of the store, or NotFound."""
    parent = _open_dir(path.parts[:-1])
    try:
        status = _lstat(parent, path.name)
    finally:
        os.close(parent)
    if status is None or not (stat.S_ISREG(status.st_mode) or stat.S_ISDIR(status.st_mode)):
        raise NotFound(f"no such file or directory in the store: {path}")
    return status


def _absent(path: PurePosixPath) -> None:
    """Conflict when PATH is in the store already."""
    try:
        parent = _open_dir(path.parts[:-1])
    except NotFound:
        return
    try:
        if _lstat(parent, path.name) is not None:
            raise Conflict(f"{path} is already in the store")
    finally:
        os.close(parent)


def _rename(path: PurePosixPath, to: PurePosixPath) -> None:
    source = _open_dir(path.parts[:-1])
    try:
        target = _open_dir(to.parts[:-1], create=True)
        try:
            os.rename(path.name, to.name, src_dir_fd=source, dst_dir_fd=target)
        finally:
            os.close(target)
    finally:
        os.close(source)


@command("store.move", role="operator", mutating=True, agent="code", tool=False, params={
    "path": Str("A file or a directory of the store", default=""),
    "to": Str("Its new path, which must not exist", default=""),
    "moves": Objs("Many at once instead: objects with path and to. All are checked"
                  " before any is made", max_items=1000),
})
def store_move(ctx: Context, params: Values) -> dict:
    """Rename files or directories of the store, or move them elsewhere in
    it: one, or many at once with one record in the audit trail. Nothing is
    moved when one of them cannot be: a path that is not there, a new path
    that is taken, a directory moved into itself."""
    items = params["moves"] or ([{"path": params["path"], "to": params["to"]}]
                                if params["path"] or params["to"] else [])
    if not items:
        raise BadRequest("nothing to move: give path and to, or moves")
    pairs = _pairs(items)
    sources = [path for path, _to in pairs]
    for index, path in enumerate(sources):
        if any(path == other or path.is_relative_to(other) or other.is_relative_to(path)
               for other in sources[:index]):
            raise BadRequest(f"{path} is given twice, or with a directory that holds it")
    for _path, to in pairs:
        if any(to.is_relative_to(source) for source in sources):
            raise BadRequest(f"{to} would be inside something that is moved")
    with _locked():
        for path, to in pairs:
            _kind(path)
            _absent(to)
        done: list[tuple[PurePosixPath, PurePosixPath]] = []
        try:
            for path, to in pairs:
                _rename(path, to)
                done.append((path, to))
        except BaseException:
            # What was moved goes back, so that the call did nothing.
            for path, to in reversed(done):
                try:
                    _rename(to, path)
                except OSError:
                    journal.send(f"store: {to} could not be moved back to {path}", 3)
            raise
        conn = ctx.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            for row in conn.execute("SELECT path FROM store_files").fetchall():
                old = PurePosixPath(row["path"])
                for path, to in pairs:
                    if old == path or old.is_relative_to(path):
                        new = to / old.relative_to(path) if old != path else to
                        conn.execute("UPDATE store_files SET path = ? WHERE path = ?",
                                     (str(new), row["path"]))
                        break
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return {"path": str(pairs[0][1]), "moved": [{"path": str(path), "to": str(to)}
                                                for path, to in pairs]}


@command("store.delete", role="operator", mutating=True, agent="code", tool=False, params={
    "path": Str("A file, or a directory with all it holds", default=""),
    "paths": Strs("Many at once instead. All must be there for any to go", max_items=1000),
})
def store_delete(ctx: Context, params: Values) -> dict:
    """Delete files or directories of the store: one, or many at once with
    one record in the audit trail. Runs under way keep what they were given
    until they end."""
    given = params["paths"] or ([params["path"]] if params["path"] else [])
    if not given:
        raise BadRequest("nothing to delete: give path or paths")
    checked = [check_file_path(path) for path in given]
    # A directory takes what is in it along.
    gone = [path for path in dict.fromkeys(checked)
            if not any(path != other and path.is_relative_to(other) for other in checked)]
    with _locked():
        kinds = [_kind(path) for path in gone]
        for path, status in zip(gone, kinds, strict=True):
            parent = _open_dir(path.parts[:-1])
            try:
                if stat.S_ISDIR(status.st_mode):
                    shutil.rmtree(path.name, dir_fd=parent)
                else:
                    os.unlink(path.name, dir_fd=parent)
            finally:
                os.close(parent)
        for row in ctx.conn.execute("SELECT path FROM store_files").fetchall():
            old = PurePosixPath(row["path"])
            if any(old == path or old.is_relative_to(path) for path in gone):
                ctx.conn.execute("DELETE FROM store_files WHERE path = ?", (row["path"],))
    return {"deleted": [str(path) for path in gone]}


# --- copies -------------------------------------------------------------------

class _Copy:
    """One copy asked for: a file, or a directory with its tree."""

    def __init__(self, path: PurePosixPath, to: PurePosixPath):
        self.path, self.to = path, to
        status = _kind(path)
        self.directory = stat.S_ISDIR(status.st_mode)
        self.files: dict[str, os.stat_result] = {}
        self.directories: list[str] = []
        if self.directory:
            fd = _open_dir(path.parts)
            try:
                if not _walk(fd, "", self.files, self.directories):
                    raise BadRequest(f"{path} holds too many entries to copy")
            finally:
                os.close(fd)
        else:
            self.files[""] = status

    @property
    def size(self) -> int:
        return sum(status.st_size for status in self.files.values())


def _copy_now(ctx: Context, copies: list[_Copy]) -> None:
    """Each file copied beside the uploads, then put in its place under the
    lock, as an upload is. One that fails undoes what this call made."""
    _mkdirs()
    uploads = os.open(paths.STORE_UPLOADS, _DIR_FLAGS)
    placed: list[PurePosixPath] = []
    made: list[PurePosixPath] = []
    temporary = f"copy-{os.getpid()}"
    try:
        for copy in copies:
            if copy.directory:
                with _locked():
                    _absent(copy.to)
                    for directory in [copy.to, *(copy.to / name for name in copy.directories)]:
                        os.close(_open_dir(directory.parts, create=True))
                        made.append(directory)
            for relative, given in copy.files.items():
                source = copy.path / relative if relative else copy.path
                target = copy.to / relative if relative else copy.to
                parent = _open_dir(source.parts[:-1])
                try:
                    fd = os.open(source.name, _FILE_FLAGS, dir_fd=parent)
                except OSError as error:
                    raise Conflict(f"cannot read {source}: {error.strerror}") from None
                finally:
                    os.close(parent)
                try:
                    os.set_blocking(fd, True)
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_ino != given.st_ino:
                        raise Conflict(f"{source} changed while it was being copied")
                    out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                  | os.O_CLOEXEC, 0o640, dir_fd=uploads)
                    try:
                        copier.copy_fd(fd, out, before.st_size)
                        os.fsync(out)
                    finally:
                        os.close(out)
                    after = os.fstat(fd)
                finally:
                    os.close(fd)
                # The digest of the source, when it is known and the file did not move.
                row = ctx.conn.execute("SELECT * FROM store_files WHERE path = ?",
                                       (str(source),)).fetchone()
                digest = row["sha256"] if row is not None and _seen(before) == _seen(after) \
                    and (row["inode"], row["size"], row["mtime_ns"]) == _seen(before) else None
                with _locked():
                    directory = _place(uploads, temporary, target, False)
                    try:
                        status = os.stat(target.name, dir_fd=directory, follow_symlinks=False)
                    finally:
                        os.close(directory)
                    _record(ctx.conn, str(target), status, "copy", str(source),
                            ctx.caller.user, digest)
                placed.append(target)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=uploads)
        except FileNotFoundError:
            pass
        with _locked():
            for target in placed:
                parent = _open_dir(target.parts[:-1])
                try:
                    os.unlink(target.name, dir_fd=parent)
                except FileNotFoundError:
                    pass
                finally:
                    os.close(parent)
                ctx.conn.execute("DELETE FROM store_files WHERE path = ?", (str(target),))
            for directory in reversed(made):
                parent = _open_dir(directory.parts[:-1])
                try:
                    os.rmdir(directory.name, dir_fd=parent)
                except OSError:
                    pass
                finally:
                    os.close(parent)
        raise
    finally:
        os.close(uploads)


def _copy_later(ctx: Context, copies: list[_Copy], total: int) -> str:
    """A job for copies too large to make in a call: it is given the files
    as hard links in its run directory, copy/in/N."""
    from . import jobs  # it builds on this module
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    inbox = directory / copier.IN
    try:
        inbox.mkdir(mode=0o700, parents=True)
        target = os.open(inbox, _DIR_FLAGS)
        try:
            with _locked():
                for index, copy in enumerate(copies):
                    name = str(index)
                    if copy.directory:
                        source = _open_dir(copy.path.parts)
                        try:
                            os.mkdir(name, 0o700, dir_fd=target)
                            made = os.open(name, _DIR_FLAGS, dir_fd=target)
                            refused: list[str] = []
                            try:
                                _link_tree(source, made, "", {}, refused)
                            finally:
                                os.close(made)
                        finally:
                            os.close(source)
                        if refused:
                            raise Conflict(f"{copy.path}/{refused[0]} cannot be copied: it is not"
                                           " the service account's own")
                    else:
                        parent = _open_dir(copy.path.parts[:-1])
                        try:
                            os.link(copy.path.name, name, src_dir_fd=parent, dst_dir_fd=target,
                                    follow_symlinks=False)
                        except OSError as error:
                            if error.errno == errno.EXDEV:
                                raise
                            raise Conflict(f"{copy.path} cannot be copied: it is not the service"
                                           " account's own") from None
                        finally:
                            os.close(parent)
        finally:
            os.close(target)
    except OSError as error:
        shutil.rmtree(directory, ignore_errors=True)
        if error.errno == errno.EXDEV:
            raise Conflict("the store is not on the file system of the runs: copies larger than"
                           f" {copier.COPY_AT_ONCE // 1024 ** 3} GiB cannot be made") from None
        raise
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    asked = [{"path": str(copy.path), "to": str(copy.to)} for copy in copies]
    title = asked[0]["to"] if len(asked) == 1 else f"{asked[0]['to']} and {len(asked) - 1} more"
    run = {"id": run_id, "kind": "copy", "title": title,
           "options": json.dumps({"copies": asked, "total": total})}
    spec = {"kind": "copy", "items": [{"path": item["path"]} for item in asked], "total": total}
    jobs.enqueue(ctx, run, spec, directory)
    return run_id


@command("store.copy", role="operator", mutating=True, agent="code", tool=False, params={
    "copies": Objs("Objects with path (a file or a directory of the store) and to (its"
                   " copy, which must not exist)", max_items=1000),
})
def store_copy(ctx: Context, params: Values) -> dict:
    """Copy files or directories within the store. Up to 1 GiB in all the
    copies are made at once, and nothing is made when one fails; larger ones
    are a job beside the downloads (store.downloads follows it, run.cancel
    stops it), which copies the files as they were when it was asked. On
    XFS and Btrfs a copy shares the blocks of its file and takes no time."""
    if not params["copies"]:
        raise BadRequest("nothing to copy: give copies")
    pairs = _pairs(params["copies"])
    with _locked():
        copies = [_Copy(path, to) for path, to in pairs]
        for copy in copies:
            _absent(copy.to)
    total = sum(copy.size for copy in copies)
    _room(ctx, total)
    if total <= copier.COPY_AT_ONCE:
        _copy_now(ctx, copies)
        return {"copied": [str(copy.to) for copy in copies], "size": total}
    return {"run_id": _copy_later(ctx, copies, total), "size": total}


def take_copy(ctx: Context, run, directory) -> None:
    """When a copy job has ended well: what it made into the store, each
    where it was asked to go, if that is still free."""
    options = store.loads(run["options"], {})
    asked = options.get("copies") or []
    out = os.open(directory / copier.OUT, _DIR_FLAGS)
    try:
        with _locked():
            pairs = [(check_file_path(item.get("path")), check_file_path(item.get("to"), "to"))
                     for item in asked]
            for _path, to in pairs:
                _absent(to)
            for index, (path, to) in enumerate(pairs):
                status = _lstat(out, str(index))
                if status is None:
                    raise Conflict(f"the copy of {path} left nothing")
                if stat.S_ISREG(status.st_mode):
                    target = _place(out, str(index), to, False)
                    try:
                        placed = {str(to): os.stat(to.name, dir_fd=target, follow_symlinks=False)}
                    finally:
                        os.close(target)
                else:
                    parent = _open_dir(to.parts[:-1], create=True)
                    try:
                        os.rename(str(index), to.name, src_dir_fd=out, dst_dir_fd=parent)
                    finally:
                        os.close(parent)
                    fd = _open_dir(to.parts)
                    placed = {}
                    try:
                        _walk(fd, str(to), placed, [])
                    finally:
                        os.close(fd)
                for where, entry in placed.items():
                    source = path / PurePosixPath(where).relative_to(to) if where != str(to) \
                        else path
                    _record(ctx.conn, where, entry, "copy", str(source), run["created_by"])
    finally:
        os.close(out)
    options["size"] = options.get("total")
    store.update(ctx.conn, "runs", run["id"], {"options": json.dumps(options)}, "run")


# --- downloads from URLs -------------------------------------------------------

# Downloads store.downloads lists: those under way, and the last ones done.
_DOWNLOADS_LISTED = 50


def _room_for(ctx: Context, replacing: int) -> int:
    """Bytes a download may bring, at most: what the limit and the disk leave."""
    _mkdirs()
    room = shutil.disk_usage(paths.STORE_DIR).free - MIN_FREE
    limit = ctx.config.store_max_gb * 1024 ** 3
    if limit:
        room = min(room, limit - _usage(ctx.conn) + replacing)
    return max(room, 0)


@command("store.fetch", role="operator", mutating=True, agent="code", tool=False, params={
    "url": Str("An http or https URL, with no user or password in it", required=True),
    "path": Str("Where the file goes in the store", required=True),
    "sha256": Str("Its digest: the download fails when the file has another",
                  default="", max_length=64),
    "replace": Bool("Replace a file already there"),
})
def store_fetch(ctx: Context, params: Values) -> dict:
    """Queue a download from a URL into the store. A job does it, in the
    sandbox of a run, beside the playbooks rather than in their queue
    ([store] max_downloads at once); store.downloads follows it, run.cancel
    stops it. The controller's own addresses and link-local ones are refused."""
    url = fetch.check_url(params["url"])
    path = check_file_path(params["path"])
    expected = params["sha256"].lower()
    if expected and not (len(expected) == 64 and all(c in "0123456789abcdef" for c in expected)):
        raise BadRequest("sha256 must be 64 hexadecimal digits")
    row = ctx.conn.execute("SELECT size FROM store_files WHERE path = ?",
                           (str(path),)).fetchone()
    if row is not None and not params["replace"]:
        raise Conflict(f"{path} is already in the store")
    room = _room_for(ctx, row["size"] if row is not None else 0)
    if room <= 0:
        raise Conflict("the store has no room left")
    from . import jobs  # it builds on this module
    run_id = store.new_id("r")
    directory = jobs.create_dir(run_id)
    run = {"id": run_id, "kind": "download", "title": str(path),
           "options": json.dumps({"url": url, "path": str(path), "sha256": expected,
                                  "replace": params["replace"]})}
    spec = {"kind": "download", "url": url, "sha256": expected, "proxy": ctx.config.proxy,
            "max_bytes": room}
    jobs.enqueue(ctx, run, spec, directory)
    return {"run_id": run_id}


def _download(row) -> dict:
    options = store.loads(row["options"], {})
    copies = options.get("copies") if row["kind"] == "copy" else None
    progress: dict = {}
    if row["status"] == "running":
        from . import jobs  # it builds on this module
        try:
            progress = json.loads(jobs.read_run_file(jobs.run_dir(row["id"]), fetch.PROGRESS,
                                                     4096))
        except (OSError, ValueError):
            progress = {}
    received = progress.get("received") if isinstance(progress, dict) else None
    total = progress.get("total") if isinstance(progress, dict) else None
    if copies:
        received = received if isinstance(received, int) else None
        return {"run_id": row["id"], "kind": "copy", "status": row["status"], "url": "",
                "path": row["title"], "source": copies[0].get("path", ""),
                "count": len(copies), "sha256": "", "replace": False, "received": received,
                "total": options.get("total"), "size": options.get("size"),
                "error": row["error"], "created_by": row["created_by"],
                "created_at": row["created_at"], "finished_at": row["finished_at"]}
    return {"run_id": row["id"], "kind": "download", "status": row["status"],
            "url": options.get("url", ""),
            "path": options.get("path", ""), "sha256": options.get("sha256", ""),
            "replace": bool(options.get("replace")),
            "received": received if isinstance(received, int) else None,
            "total": total if isinstance(total, int) else None,
            "size": options.get("size"), "error": row["error"],
            "created_by": row["created_by"], "created_at": row["created_at"],
            "finished_at": row["finished_at"]}


@command("store.downloads", role="operator")
def store_downloads(ctx: Context, params: Values) -> dict:
    """Downloads into the store, and copies made by a job: those queued or
    running, with what has arrived of each, and the last ones that ended."""
    from . import jobs  # it builds on this module
    jobs.reconcile(ctx)
    rows = ctx.conn.execute(
        "SELECT * FROM runs WHERE kind IN ('download', 'copy') ORDER BY"
        " status IN ('queued', 'running') DESC, created_at DESC LIMIT ?",
        (_DOWNLOADS_LISTED,)).fetchall()
    return {"downloads": [_download(row) for row in rows]}


def take_download(ctx: Context, run, directory, result: dict) -> None:
    """When a download has ended well: its file into the store. The digest
    is the job's, which took it as the bytes came (and checked the one it
    was given); the tick takes it again only if the file changes."""
    options = store.loads(run["options"], {})
    path = check_file_path(options.get("path"))
    parent = os.open(directory / fetch.TARGET.parent, _DIR_FLAGS)
    try:
        status = os.stat(fetch.TARGET.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise Conflict("the download left no file")
        row = ctx.conn.execute("SELECT size FROM store_files WHERE path = ?",
                               (str(path),)).fetchone()
        _room(ctx, status.st_size, row["size"] if row is not None else 0)
        fd = os.open(fetch.TARGET.name, _FILE_FLAGS, dir_fd=parent)
        try:
            os.fchmod(fd, 0o640)
        finally:
            os.close(fd)
        with _locked():
            target = _place(parent, fetch.TARGET.name, path, bool(options.get("replace")))
            try:
                placed = os.stat(path.name, dir_fd=target, follow_symlinks=False)
            finally:
                os.close(target)
            digest = result.get("sha256")
            valid = isinstance(digest, str) and len(digest) == 64 \
                and placed.st_size == status.st_size
            _record(ctx.conn, str(path), placed, "url", str(options.get("url", "")),
                    run["created_by"], digest if valid else None)
    finally:
        os.close(parent)
    options["size"] = status.st_size
    store.update(ctx.conn, "runs", run["id"], {"options": json.dumps(options)}, "run")


# --- the farm of a run ------------------------------------------------------

def _manifest(run_id: str):
    return paths.STORE_FARMS / f"{check_id(run_id, 'run id')}.json"


def _link_tree(source: int, target: int, prefix: str, manifest: dict, refused: list) -> None:
    for name in sorted(os.listdir(source)):
        status = _lstat(source, name)
        if status is None:
            continue
        path = f"{prefix}/{name}" if prefix else name
        if stat.S_ISDIR(status.st_mode):
            os.mkdir(name, 0o700, dir_fd=target)
            child = os.open(name, _DIR_FLAGS, dir_fd=source)
            made = os.open(name, _DIR_FLAGS, dir_fd=target)
            try:
                _link_tree(child, made, path, manifest, refused)
            finally:
                os.close(child)
                os.close(made)
        elif stat.S_ISREG(status.st_mode):
            try:
                os.link(name, name, src_dir_fd=source, dst_dir_fd=target, follow_symlinks=False)
            except OSError as error:
                if error.errno == errno.EXDEV:
                    raise
                refused.append(path)
                continue
            manifest[path] = list(_seen(status))


def make_farm(directory, run_id: str) -> None:
    """Give a run that is starting the tree of the store, as hard links in
    DIRECTORY/store, and note what each was. What cannot be given is said at
    the head of its output."""
    farm = directory / "store"
    manifest: dict = {}
    refused: list[str] = []
    note = ""
    with _locked():
        source = _open_dir(())
        try:
            os.mkdir(farm, 0o700)
            target = os.open(farm, _DIR_FLAGS)
            try:
                _link_tree(source, target, "", manifest, refused)
            finally:
                os.close(target)
        except OSError as error:
            shutil.rmtree(farm, ignore_errors=True)
            manifest = {}
            if error.errno == errno.EXDEV:
                note = ("fleet: the store is not on the file system of the runs, so this run"
                        " was not given it (fleet_store is not defined)\n")
            else:
                note = f"fleet: the store could not be given to this run: {error.strerror}\n"
        finally:
            os.close(source)
        if not note:
            _manifest(run_id).write_text(json.dumps(manifest))
    if refused:
        note += (f"fleet: {len(refused)} files of the store were left out of this run: they"
                 " are not the service account's own\n")
        note += "".join(f"  {path}\n" for path in refused[:_SHOWN])
    if note:
        fd = os.open(directory / "stdout.log",
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w", errors="replace") as log:
            log.write(note)


def drop_farm(run_id: str) -> None:
    """Forget the farm of a run that will not be taken in."""
    _manifest(run_id).unlink(missing_ok=True)


def _take(ctx: Context, run, farm: int, store: int, prefix: str, manifest: dict,
          taken: dict, depth: int = 0) -> None:
    """Take in what the run left in the open directory FARM of its farm,
    which is PREFIX in the store, open as STORE (or None, not there yet)."""
    user = run["created_by"]
    for name in sorted(os.listdir(farm)):
        status = _lstat(farm, name)
        if status is None:
            continue
        path = f"{prefix}/{name}" if prefix else name
        given = manifest.get(path)
        if given is not None and status.st_ino == given[0] and stat.S_ISREG(status.st_mode):
            current = _lstat(store, name)
            if list(_seen(status)) != given and current is not None \
                    and current.st_ino == status.st_ino:
                # Written in place through the link: the store has it already.
                _record(ctx.conn, path, current, "run", run["id"], user)
                taken["changed"].append(path)
            continue
        try:
            check_file_path(path)
        except BadRequest:
            taken["skipped"].append(f"{path}: not a name the store takes")
            continue
        if stat.S_ISDIR(status.st_mode) and depth < 11:
            inner = os.open(name, _DIR_FLAGS, dir_fd=farm)
            try:
                kept = _open_dir((name,), create=True, root=store)
            except (Conflict, OSError):
                os.close(inner)
                taken["skipped"].append(f"{path}: a file of the store has this name")
                continue
            try:
                _take(ctx, run, inner, kept, path, manifest, taken, depth + 1)
            finally:
                os.close(inner)
                os.close(kept)
            continue
        if not stat.S_ISREG(status.st_mode):
            continue
        if status.st_nlink != 1:
            taken["skipped"].append(f"{path}: it has other links")
            continue
        existing = _lstat(store, name)
        if existing is not None and stat.S_ISDIR(existing.st_mode):
            taken["skipped"].append(f"{path}: a directory of the store has this name")
            continue
        try:
            _room(ctx, status.st_size, existing.st_size if existing is not None else 0)
        except Conflict as error:
            taken["skipped"].append(f"{path}: {error}")
            continue
        fd = os.open(name, _FILE_FLAGS, dir_fd=farm)
        try:
            os.fchmod(fd, 0o640)
        finally:
            os.close(fd)
        os.rename(name, name, src_dir_fd=farm, dst_dir_fd=store)
        _record(ctx.conn, path, os.stat(name, dir_fd=store, follow_symlinks=False), "run",
                run["id"], user)
        taken["replaced" if existing is not None else "added"].append(path)


def take_farm(ctx: Context, run, directory) -> dict | None:
    """When a run ends: take into the store what it added to its farm, or
    replaced there, and put down to it what it changed in place. None for a
    run that had no farm."""
    run_id = run["id"]
    try:
        manifest = json.loads(_manifest(run_id).read_bytes())
    except (OSError, ValueError):
        return None
    taken: dict = {"added": [], "replaced": [], "changed": [], "skipped": []}
    with _locked():
        try:
            farm = os.open(directory / "store", _DIR_FLAGS)
        except OSError:
            farm = None
        if farm is not None:
            store = _open_dir(())
            try:
                _take(ctx, run, farm, store, "", manifest, taken)
            finally:
                os.close(farm)
                os.close(store)
    drop_farm(run_id)
    shutil.rmtree(directory / "store", ignore_errors=True)
    moved = taken["added"] + taken["replaced"] + taken["changed"]
    if moved or taken["skipped"]:
        caller = Caller(-1, run["created_by"] or "", None)
        audit.record(ctx.conn, caller, "store.intake",
                     {"run_id": run_id, "added": len(taken["added"]),
                      "replaced": len(taken["replaced"]), "changed": len(taken["changed"]),
                      "skipped": len(taken["skipped"])},
                     "ok", "; ".join(moved[:_SHOWN]))
    if taken["skipped"]:
        journal.send(f"run {run_id}: {len(taken['skipped'])} files not taken into the store", 4,
                     FLEET_RUN=run_id)
    return {name: items[:_SHOWN] for name, items in taken.items()} | {
        "counts": {name: len(items) for name, items in taken.items()}}


# --- the tick ---------------------------------------------------------------

def digest_pending(ctx: Context, now: float, budget: float = _DIGEST_BUDGET) -> int:
    """At each tick: the tree read again, and the digests of the files that
    have none taken, for at most BUDGET seconds; uploads abandoned for a
    week and farms of runs long gone dropped. How many digests were taken."""
    sync(ctx.conn)
    start = time.monotonic()
    done = 0
    for row in ctx.conn.execute("SELECT path, inode, size, mtime_ns FROM store_files"
                                " WHERE sha256 IS NULL ORDER BY size").fetchall():
        if time.monotonic() - start > budget:
            break
        path = PurePosixPath(row["path"])
        try:
            directory = _open_dir(path.parts[:-1])
            try:
                fd = os.open(path.name, _FILE_FLAGS, dir_fd=directory)
            finally:
                os.close(directory)
        except (FileNotFoundError, NotFound, Conflict):
            continue
        except OSError as error:
            journal.send(f"store: cannot read {path}: {error.strerror}", 4)
            continue
        try:
            status = os.fstat(fd)
            if not stat.S_ISREG(status.st_mode):
                continue
            os.set_blocking(fd, True)
            digest = _digest_fd(fd)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if _seen(status) != _seen(after) or \
                (row["inode"], row["size"], row["mtime_ns"]) != _seen(status):
            continue  # it changed meanwhile: the next tick
        ctx.conn.execute("UPDATE store_files SET sha256 = ? WHERE path = ? AND inode = ?"
                         " AND size = ? AND mtime_ns = ?",
                         (digest, row["path"], *_seen(status)))
        done += 1
    for directory, suffix, age in ((paths.STORE_UPLOADS, "", _UPLOAD_AGE),
                                   (paths.STORE_FARMS, ".json", 86400)):
        for entry in os.scandir(directory):
            if not entry.name.endswith(suffix) or not entry.is_file(follow_symlinks=False):
                continue
            if now - entry.stat(follow_symlinks=False).st_mtime < age:
                continue
            if directory == paths.STORE_FARMS:
                run = ctx.conn.execute("SELECT status FROM runs WHERE id = ?",
                                       (entry.name[:-5],)).fetchone()
                if run is not None and run["status"] == "running":
                    continue
            os.unlink(entry.path)
    return done

