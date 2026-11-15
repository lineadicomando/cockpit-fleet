"""The store over SFTP: fleetctl store.sftp speaks version 3 of the protocol
(draft-ietf-secsh-filexfer-02, as OpenSSH does) on its input and output.

A person points an SFTP client at their own SSH login on the controller and
names "fleet store sftp" as the server to start there (WinSCP's "SFTP
server", sftp -s, sshfs -o sftp_server): fleet starts fleetctl through sudo,
as every command, so the role is the caller's, from their Unix groups, and
there is no daemon, no root and nothing of sshd's to configure. The root of
the session is the root of the store.

Viewers read; operators and administrators also write. What is written goes
to a file among the uploads, not into the store, and is put in the store
whole when it is closed, as store.put does with what arrives from the
browser: a run never gets half a file. A file whose name ends in .filepart
(what WinSCP writes before renaming it) stays there when closed, listed to
its writer only, until it is renamed; a session that ends before a close
leaves it there too, to be resumed. Uploads not touched for a week go with
the tick. A file of the store is replaced whole, never written in place.

Each change is a record of the audit trail under the command that makes it
from the page (store.put, store.mkdir, store.move, store.delete), and so is
each file opened for reading (store.read), with "sftp" as what it came
through; the session itself is one more, store.sftp, when it ends.
"""

import errno
import json
import os
import posixpath
import shutil
import stat
import struct
import time
import traceback
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import BinaryIO

from . import journal, paths, storage
from .auth import allowed
from .cli import Context, Values, _record, command
from .errors import BadRequest, Conflict, FleetError, Forbidden, NotFound
from .validators import check_file_path

VERSION = 3
# Requests.
INIT, OPEN, CLOSE, READ, WRITE, LSTAT, FSTAT, SETSTAT, FSETSTAT = 1, 3, 4, 5, 6, 7, 8, 9, 10
OPENDIR, READDIR, REMOVE, MKDIR, RMDIR, REALPATH, STAT, RENAME = 11, 12, 13, 14, 15, 16, 17, 18
READLINK, SYMLINK, EXTENDED = 19, 20, 200
# Replies.
VERSION_REPLY, STATUS, HANDLE, DATA, NAME, ATTRS = 2, 101, 102, 103, 104, 105
EXTENDED_REPLY = 201
# Status codes.
OK, EOF, NO_SUCH_FILE, PERMISSION_DENIED, FAILURE, BAD_MESSAGE = 0, 1, 2, 3, 4, 5
OP_UNSUPPORTED = 8
# Open flags.
FXF_READ, FXF_WRITE, FXF_APPEND, FXF_CREAT, FXF_TRUNC, FXF_EXCL = 1, 2, 4, 8, 0x10, 0x20
# Attributes.
ATTR_SIZE, ATTR_UIDGID, ATTR_PERMISSIONS, ATTR_ACMODTIME = 1, 2, 4, 8
ATTR_EXTENDED = 0x80000000

POSIX_RENAME = "posix-rename@openssh.com"
STATVFS = "statvfs@openssh.com"
# The largest request taken: a write of 256 KiB, OpenSSH's own limit, and its header.
MAX_PACKET = 256 * 1024 + 1024
# What one read gives at most.
MAX_READ = 256 * 1024
# Names one READDIR gives at most.
_BATCH = 100
# Handles one session holds at most.
MAX_HANDLES = 256
# How often, in bytes written, the free space of the disk is looked at.
_ROOM_EVERY = 256 * 1024 * 1024
# What WinSCP writes before renaming it to its name.
PARTIAL = ".filepart"


class Refused(Exception):
    """A request that fails: its status code and why."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Malformed(Exception):
    """A packet that cannot be read: the session ends."""


class _Reader:
    """The fields of one packet, in order."""

    def __init__(self, data: bytes):
        self.data, self.at = data, 0

    def take(self, size: int) -> bytes:
        if self.at + size > len(self.data):
            raise Malformed("a packet ended short")
        chunk = self.data[self.at:self.at + size]
        self.at += size
        return chunk

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.take(8))[0]

    def bytes(self) -> bytes:
        return self.take(self.u32())

    def text(self) -> str:
        try:
            return self.bytes().decode()
        except UnicodeDecodeError:
            raise Refused(NO_SUCH_FILE, "names are UTF-8") from None

    def attrs(self) -> dict:
        flags = self.u32()
        found: dict = {}
        if flags & ATTR_SIZE:
            found["size"] = self.u64()
        if flags & ATTR_UIDGID:
            self.u32()
            self.u32()
        if flags & ATTR_PERMISSIONS:
            found["permissions"] = self.u32()
        if flags & ATTR_ACMODTIME:
            found["atime"], found["mtime"] = self.u32(), self.u32()
        if flags & ATTR_EXTENDED:
            for _ in range(self.u32()):
                self.bytes()
                self.bytes()
        return found


def _string(value: bytes | str) -> bytes:
    data = value.encode() if isinstance(value, str) else value
    return struct.pack(">I", len(data)) + data


def _attrs(status: os.stat_result | None, size: int | None = None, mtime: float | None = None,
           writable: bool = True, directory: bool = False) -> bytes:
    """Size, permissions and times; permissions say what the session may do."""
    if directory:
        mode = stat.S_IFDIR | (0o755 if writable else 0o555)
    else:
        mode = stat.S_IFREG | (0o644 if writable else 0o444)
    size = size if size is not None else status.st_size if status is not None else 0
    mtime = mtime if mtime is not None else status.st_mtime if status is not None else 0
    atime = status.st_atime if status is not None else mtime
    return struct.pack(">IQIII", ATTR_SIZE | ATTR_PERMISSIONS | ATTR_ACMODTIME, size, mode,
                       int(atime) & 0xFFFFFFFF, int(mtime) & 0xFFFFFFFF)


def _longname(name: str, size: int, mtime: float, writable: bool, directory: bool) -> str:
    """The line ls -l would give, which some clients show."""
    mode = ("d" if directory else "-") + ("rwxr-xr-x" if directory else "rw-r--r--")
    if not writable:
        mode = mode.replace("w", "-")
    when = time.localtime(mtime)
    recent = abs(time.time() - mtime) < 180 * 86400
    stamp = time.strftime("%b %d %H:%M" if recent else "%b %d  %Y", when)
    owner = paths.SERVICE_USER
    return f"{mode}    1 {owner:<8} {owner:<8} {size:>12} {stamp} {name}"


@dataclass
class _Upload:
    """A file being written: where it waits, and what for."""
    path: PurePosixPath
    key: str
    fd: int
    mtime: float | None = None
    written: int = 0


@dataclass
class _Reading:
    path: PurePosixPath
    fd: int


@dataclass
class _Listing:
    entries: list[bytes] = field(default_factory=list)


def _part(key: str):
    return paths.STORE_UPLOADS / f"{key}.part"


def _meta(key: str):
    return paths.STORE_UPLOADS / f"{key}.json"


class Session:
    """One SFTP session: requests read from SOURCE, replies written to SINK."""

    def __init__(self, ctx: Context, source: BinaryIO, sink: BinaryIO):
        self.ctx, self.source, self.sink = ctx, source, sink
        self.handles: dict[bytes, _Upload | _Reading | _Listing] = {}
        self.next_handle = 0
        self.user = ctx.caller.user
        self.writer = allowed(ctx.caller, "operator")

    # --- the loop -------------------------------------------------------------

    def _packet(self) -> bytes | None:
        head = self.source.read(4)
        if not head:
            return None
        if len(head) < 4:
            raise Malformed("the input ended inside a packet")
        size = struct.unpack(">I", head)[0]
        if not 1 <= size <= MAX_PACKET:
            raise Malformed(f"a packet of {size} bytes")
        data = self.source.read(size)
        if len(data) < size:
            raise Malformed("the input ended inside a packet")
        return data

    def _send(self, kind: int, payload: bytes) -> None:
        self.sink.write(struct.pack(">IB", len(payload) + 1, kind) + payload)
        self.sink.flush()

    def _status(self, request: int, code: int, message: str = "") -> None:
        self._send(STATUS, struct.pack(">II", request, code) + _string(message) + _string("en"))

    def serve(self) -> None:
        """Answer requests until the input ends; what is open is closed then,
        and uploads not closed are left to be resumed."""
        try:
            first = self._packet()
            if first is None:
                return
            if first[0] != INIT:
                raise Malformed("the session did not begin with INIT")
            self._send(VERSION_REPLY, struct.pack(">I", VERSION)
                       + _string(POSIX_RENAME) + _string("1")
                       + _string(STATVFS) + _string("2"))
            while (data := self._packet()) is not None:
                self._one(data)
        finally:
            for handle in self.handles.values():
                if isinstance(handle, _Upload | _Reading):
                    os.close(handle.fd)
            self.handles.clear()

    def _one(self, data: bytes) -> None:
        reader = _Reader(data[1:])
        kind = data[0]
        request = reader.u32()
        try:
            handler = self._HANDLERS.get(kind)
            if handler is None:
                raise Refused(OP_UNSUPPORTED, "not supported")
            handler(self, request, reader)
        except Refused as error:
            self._status(request, error.code, str(error))
        except Malformed:
            raise
        except NotFound as error:
            self._status(request, NO_SUCH_FILE, str(error))
        except Forbidden as error:
            self._status(request, PERMISSION_DENIED, str(error))
        except FleetError as error:
            self._status(request, FAILURE, str(error))
        except OSError as error:
            code = NO_SUCH_FILE if error.errno == errno.ENOENT else \
                PERMISSION_DENIED if error.errno in (errno.EACCES, errno.EPERM) else FAILURE
            self._status(request, code, error.strerror or "failed")
        except Exception:
            journal.send("store.sftp: a request failed", 3,
                         FLEET_TRACEBACK=traceback.format_exc())
            self._status(request, FAILURE, "internal error, see the journal")

    # --- paths ----------------------------------------------------------------

    @staticmethod
    def _path(text: str) -> PurePosixPath | None:
        """A path of the store, or None for its root; what a client gives may
        be absolute or relative to the root, with . and .. in it."""
        normal = posixpath.normpath("/" + text).lstrip("/")
        if not normal or normal == ".":
            return None
        try:
            return check_file_path(normal)
        except BadRequest as error:
            raise Refused(NO_SUCH_FILE, str(error)) from None

    def _need(self, text: str) -> PurePosixPath:
        path = self._path(text)
        if path is None:
            raise Refused(PERMISSION_DENIED, "the root of the store")
        return path

    def _write_access(self, name: str, path: PurePosixPath) -> None:
        if not self.writer:
            _record(self.ctx, name, {"path": str(path)}, "denied", "requires operator")
            raise Refused(PERMISSION_DENIED, f"{name} requires the operator role")

    def _audit(self, name: str, params: dict) -> None:
        _record(self.ctx, name, params, "ok", conn=self.ctx.conn)

    def _staged(self) -> dict[str, tuple[str, os.stat_result, dict]]:
        """The uploads of this user over SFTP that are not in the store yet,
        by path: key, the status of what arrived, what is kept of it."""
        found = {}
        try:
            entries = list(os.scandir(paths.STORE_UPLOADS))
        except FileNotFoundError:
            return {}
        for entry in entries:
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            try:
                meta = json.loads(_meta(entry.name[:-5]).read_bytes())
                status = os.stat(_part(entry.name[:-5]), follow_symlinks=False)
            except (OSError, ValueError):
                continue
            if isinstance(meta, dict) and meta.get("via") == "sftp" \
                    and meta.get("user") == self.user and isinstance(meta.get("path"), str):
                found[meta["path"]] = (entry.name[:-5], status, meta)
        return found

    def _key(self, path: PurePosixPath) -> str:
        return storage._upload_key(self.user, f"sftp\0{path}")

    def _in_store(self, path: PurePosixPath) -> os.stat_result | None:
        """What the store has at PATH, a file or a directory; None if nothing.
        A directory on the way that is not there is NotFound."""
        parent = storage._open_dir(path.parts[:-1])
        try:
            status = storage._lstat(parent, path.name)
        finally:
            os.close(parent)
        if status is None or not (stat.S_ISREG(status.st_mode) or stat.S_ISDIR(status.st_mode)):
            return None
        return status

    def _new_handle(self, value) -> bytes:
        if len(self.handles) >= MAX_HANDLES:
            raise Refused(FAILURE, f"at most {MAX_HANDLES} files open at once")
        self.next_handle += 1
        handle = str(self.next_handle).encode()
        self.handles[handle] = value
        return handle

    def _handle(self, reader: _Reader, *kinds):
        found = self.handles.get(reader.bytes())
        if found is None or not isinstance(found, kinds):
            raise Refused(FAILURE, "no such handle")
        return found

    def _attrs_of(self, path: PurePosixPath | None) -> bytes:
        if path is None:
            storage._mkdirs()
            return _attrs(os.stat(paths.STORE_DIR), writable=self.writer, directory=True)
        status = self._in_store(path)
        if status is not None:
            return _attrs(status, writable=self.writer, directory=stat.S_ISDIR(status.st_mode))
        staged = self._staged().get(str(path))
        if staged is None:
            raise Refused(NO_SUCH_FILE, f"no such file or directory: {path}")
        _key, status, meta = staged
        return _attrs(status, mtime=meta.get("mtime"))

    # --- requests -------------------------------------------------------------

    def _realpath(self, request: int, reader: _Reader) -> None:
        path = self._path(reader.text())
        name = f"/{path}" if path is not None else "/"
        self._send(NAME, struct.pack(">II", request, 1) + _string(name) + _string(name)
                   + struct.pack(">I", 0))

    def _stat(self, request: int, reader: _Reader) -> None:
        self._send(ATTRS, struct.pack(">I", request) + self._attrs_of(self._path(reader.text())))

    def _fstat(self, request: int, reader: _Reader) -> None:
        handle = self._handle(reader, _Upload, _Reading)
        status = os.fstat(handle.fd)
        mtime = handle.mtime if isinstance(handle, _Upload) else None
        self._send(ATTRS, struct.pack(">I", request)
                   + _attrs(status, mtime=mtime, writable=self.writer))

    def _opendir(self, request: int, reader: _Reader) -> None:
        path = self._path(reader.text())
        parts = path.parts if path is not None else ()
        fd = storage._open_dir(parts)
        listing = _Listing()
        names = set()
        try:
            for name in sorted(os.listdir(fd)):
                if len(listing.entries) >= storage.MAX_LISTED:
                    break
                status = storage._lstat(fd, name)
                if status is None:
                    continue
                directory = stat.S_ISDIR(status.st_mode)
                if not directory and not stat.S_ISREG(status.st_mode):
                    continue
                names.add(name)
                listing.entries.append(self._entry(name, status, None, directory))
            current = os.fstat(fd)
        finally:
            os.close(fd)
        for staged, (_key, status, meta) in sorted(self._staged().items()):
            where = PurePosixPath(staged)
            if (where.parent.parts == parts) and where.name not in names:
                listing.entries.append(self._entry(where.name, status, meta.get("mtime"), False))
        listing.entries[:0] = [self._entry(dot, current, None, True) for dot in (".", "..")]
        self._send(HANDLE, struct.pack(">I", request) + _string(self._new_handle(listing)))

    def _entry(self, name: str, status: os.stat_result, mtime: float | None,
               directory: bool) -> bytes:
        when = mtime if mtime is not None else status.st_mtime
        return (_string(name)
                + _string(_longname(name, status.st_size, when, self.writer, directory))
                + _attrs(status, mtime=mtime, writable=self.writer, directory=directory))

    def _readdir(self, request: int, reader: _Reader) -> None:
        listing = self._handle(reader, _Listing)
        if not listing.entries:
            raise Refused(EOF, "")
        batch, listing.entries = listing.entries[:_BATCH], listing.entries[_BATCH:]
        self._send(NAME, struct.pack(">II", request, len(batch)) + b"".join(batch))

    def _open(self, request: int, reader: _Reader) -> None:
        path = self._need(reader.text())
        flags = reader.u32()
        attrs = reader.attrs()
        if flags & FXF_WRITE:
            handle = self._open_write(path, flags, attrs)
        else:
            handle = self._open_read(path)
        self._send(HANDLE, struct.pack(">I", request) + _string(self._new_handle(handle)))

    def _open_read(self, path: PurePosixPath) -> _Reading:
        status = self._in_store(path)
        if status is None:
            staged = self._staged().get(str(path))
            if staged is None:
                raise Refused(NO_SUCH_FILE, f"no such file: {path}")
            fd = os.open(_part(staged[0]), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            return _Reading(path, fd)
        if stat.S_ISDIR(status.st_mode):
            raise Refused(FAILURE, f"{path} is a directory")
        fd = storage._open_file(path)
        self._audit("store.read", {"path": str(path)})
        return _Reading(path, fd)

    def _open_write(self, path: PurePosixPath, flags: int, attrs: dict) -> _Upload:
        self._write_access("store.put", path)
        status = self._in_store(path)  # NotFound: a directory on the way is not there
        if status is not None and stat.S_ISDIR(status.st_mode):
            raise Refused(FAILURE, f"{path} is a directory")
        key = self._key(path)
        staged = os.path.exists(_part(key)) and os.path.exists(_meta(key))
        if any(isinstance(h, _Upload) and h.key == key for h in self.handles.values()):
            raise Refused(FAILURE, f"{path} is open for writing already")
        if flags & FXF_EXCL and (status is not None or staged):
            raise Refused(FAILURE, f"{path} exists")
        storage._mkdirs()
        base = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
        if flags & FXF_TRUNC or (status is None and not staged):
            if status is None and not staged and not flags & FXF_CREAT:
                raise Refused(NO_SUCH_FILE, f"no such file: {path}")
            fd = os.open(_part(key), base | os.O_CREAT | os.O_TRUNC, 0o600)
            meta = {"path": str(path), "user": self.user, "via": "sftp",
                    "started_at": time.time()}
        elif staged:
            # Resumed: what arrived before stays, the client writes on from its end.
            fd = os.open(_part(key), base)
            try:
                meta = json.loads(_meta(key).read_bytes())
            except (OSError, ValueError):
                meta = {"path": str(path), "user": self.user, "via": "sftp",
                        "started_at": time.time()}
        else:
            raise Refused(OP_UNSUPPORTED, f"{path} is in the store: a file of the store is"
                          " replaced whole, opened to be truncated, not written in place")
        if "mtime" in attrs:
            meta["mtime"] = attrs["mtime"]
        _meta(key).write_text(json.dumps(meta))
        return _Upload(path, key, fd, meta.get("mtime"))

    def _read(self, request: int, reader: _Reader) -> None:
        handle = self._handle(reader, _Reading, _Upload)
        offset, length = reader.u64(), reader.u32()
        data = os.pread(handle.fd, min(length, MAX_READ), offset)
        if not data:
            raise Refused(EOF, "")
        self._send(DATA, struct.pack(">I", request) + _string(data))

    def _write(self, request: int, reader: _Reader) -> None:
        handle = self._handle(reader, _Upload, _Reading)
        offset, data = reader.u64(), reader.bytes()
        if isinstance(handle, _Reading):
            raise Refused(PERMISSION_DENIED, "the file was opened for reading")
        if offset + len(data) > storage._LARGEST:
            raise Refused(FAILURE, "too large")
        handle.written += len(data)
        if handle.written >= _ROOM_EVERY:
            handle.written = 0
            if shutil.disk_usage(paths.STORE_UPLOADS).free - len(data) < storage.MIN_FREE:
                raise Refused(FAILURE, "not enough free space on the disk of the store")
        os.pwrite(handle.fd, data, offset)
        self._status(request, OK)

    def _close(self, request: int, reader: _Reader) -> None:
        name = reader.bytes()
        handle = self.handles.pop(name, None)
        if handle is None:
            raise Refused(FAILURE, "no such handle")
        if isinstance(handle, _Upload | _Reading):
            os.close(handle.fd)
        if isinstance(handle, _Upload):
            if handle.path.name.endswith(PARTIAL):
                os.utime(_meta(handle.key))  # kept: a week from now
            else:
                self._place(handle.key, handle.path, replace=True)
        self._status(request, OK)

    def _place(self, key: str, path: PurePosixPath, replace: bool) -> None:
        """Put an upload in the store at PATH, as store.put does."""
        part = _part(key)
        size = os.stat(part, follow_symlinks=False).st_size
        try:
            meta = json.loads(_meta(key).read_bytes())
        except (OSError, ValueError):
            meta = {}
        row = self.ctx.conn.execute("SELECT size FROM store_files WHERE path = ?",
                                    (str(path),)).fetchone()
        try:
            storage._room(self.ctx, size, row["size"] if row is not None and replace else 0)
        except Conflict:
            storage._drop_upload(key)
            raise
        with storage._locked():
            uploads = os.open(paths.STORE_UPLOADS, storage._DIR_FLAGS)
            try:
                target = storage._place(uploads, part.name, path, replace)
            finally:
                os.close(uploads)
            try:
                if isinstance(meta.get("mtime"), int):
                    os.utime(path.name, (meta["mtime"], meta["mtime"]), dir_fd=target,
                             follow_symlinks=False)
                status = os.stat(path.name, dir_fd=target, follow_symlinks=False)
            finally:
                os.close(target)
            # The digest is taken by the tick, as for a file put there by hand.
            storage._record(self.ctx.conn, str(path), status, "upload", path.name, self.user)
        _meta(key).unlink(missing_ok=True)
        self._audit("store.put", {"path": str(path), "size": size})

    def _setstat(self, request: int, reader: _Reader) -> None:
        path = self._need(reader.text())
        self._set(path, reader.attrs(), None)
        self._status(request, OK)

    def _fsetstat(self, request: int, reader: _Reader) -> None:
        handle = self._handle(reader, _Upload, _Reading)
        attrs = reader.attrs()
        if isinstance(handle, _Upload):
            if "size" in attrs:
                os.ftruncate(handle.fd, attrs["size"])
            if "mtime" in attrs:
                handle.mtime = attrs["mtime"]
                self._keep_mtime(handle.key, attrs["mtime"])
        else:
            self._set(handle.path, attrs, handle)
        self._status(request, OK)

    def _keep_mtime(self, key: str, mtime: int) -> None:
        try:
            meta = json.loads(_meta(key).read_bytes())
        except (OSError, ValueError):
            return
        meta["mtime"] = mtime
        _meta(key).write_text(json.dumps(meta))

    def _set(self, path: PurePosixPath, attrs: dict, _handle) -> None:
        """Times are kept; permissions and owners are the store's own, so
        a change of them is taken and does nothing; a size is refused."""
        if not attrs:
            return
        self._write_access("store.put", path)
        status = self._in_store(path)
        if status is None:
            staged = self._staged().get(str(path))
            if staged is None:
                raise Refused(NO_SUCH_FILE, f"no such file: {path}")
            if "size" in attrs:
                os.truncate(_part(staged[0]), attrs["size"])
            if "mtime" in attrs:
                self._keep_mtime(staged[0], attrs["mtime"])
            return
        if "size" in attrs and (stat.S_ISDIR(status.st_mode) or attrs["size"] != status.st_size):
            raise Refused(OP_UNSUPPORTED, "a file of the store is replaced whole, not cut")
        if "mtime" not in attrs or stat.S_ISDIR(status.st_mode):
            return
        with storage._locked():
            parent = storage._open_dir(path.parts[:-1])
            try:
                before = storage._lstat(parent, path.name)
                if before is None or not stat.S_ISREG(before.st_mode):
                    raise Refused(NO_SUCH_FILE, f"no such file: {path}")
                os.utime(path.name, (attrs.get("atime", attrs["mtime"]), attrs["mtime"]),
                         dir_fd=parent, follow_symlinks=False)
                after = storage._lstat(parent, path.name)
            finally:
                os.close(parent)
            # Only the time changed: the row keeps its digest and origin.
            self.ctx.conn.execute(
                "UPDATE store_files SET mtime_ns = ? WHERE path = ? AND inode = ? AND size = ?"
                " AND mtime_ns = ?", (after.st_mtime_ns, str(path), *storage._seen(before)))

    def _mkdir(self, request: int, reader: _Reader) -> None:
        path = self._need(reader.text())
        reader.attrs()
        self._write_access("store.mkdir", path)
        with storage._locked():
            parent = storage._open_dir(path.parts[:-1])
            try:
                if storage._lstat(parent, path.name) is not None:
                    raise Refused(FAILURE, f"{path} exists")
                os.mkdir(path.name, 0o750, dir_fd=parent)
            finally:
                os.close(parent)
        self._audit("store.mkdir", {"path": str(path)})
        self._status(request, OK)

    def _rmdir(self, request: int, reader: _Reader) -> None:
        path = self._need(reader.text())
        self._write_access("store.delete", path)
        with storage._locked():
            status = self._in_store(path)
            if status is None or not stat.S_ISDIR(status.st_mode):
                raise Refused(NO_SUCH_FILE, f"no such directory: {path}")
            parent = storage._open_dir(path.parts[:-1])
            try:
                os.rmdir(path.name, dir_fd=parent)
            except OSError as error:
                if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    raise Refused(FAILURE, f"{path} is not empty") from None
                raise
            finally:
                os.close(parent)
        self._audit("store.delete", {"path": str(path)})
        self._status(request, OK)

    def _remove(self, request: int, reader: _Reader) -> None:
        path = self._need(reader.text())
        self._write_access("store.delete", path)
        status = self._in_store(path)
        if status is None:
            staged = self._staged().get(str(path))
            if staged is None:
                raise Refused(NO_SUCH_FILE, f"no such file: {path}")
            storage._drop_upload(staged[0])
            self._status(request, OK)
            return
        if stat.S_ISDIR(status.st_mode):
            raise Refused(FAILURE, f"{path} is a directory")
        with storage._locked():
            parent = storage._open_dir(path.parts[:-1])
            try:
                now = storage._lstat(parent, path.name)
                if now is None or not stat.S_ISREG(now.st_mode):
                    raise Refused(NO_SUCH_FILE, f"no such file: {path}")
                os.unlink(path.name, dir_fd=parent)
            finally:
                os.close(parent)
            storage.forget(self.ctx.conn, [path])
        self._audit("store.delete", {"path": str(path)})
        self._status(request, OK)

    def _rename(self, request: int, reader: _Reader, replace: bool = False) -> None:
        path, to = self._need(reader.text()), self._need(reader.text())
        self._write_access("store.move", path)
        if to == path:
            self._status(request, OK)
            return
        if to.is_relative_to(path):
            raise Refused(FAILURE, f"{path} cannot go into itself")
        target = self._in_store(to)  # NotFound: the directory it goes to is not there
        if target is not None and not (replace and stat.S_ISREG(target.st_mode)):
            raise Refused(FAILURE, f"{to} exists")
        status = self._in_store(path)
        if status is None:
            staged = self._staged().get(str(path))
            if staged is None:
                raise Refused(NO_SUCH_FILE, f"no such file: {path}")
            if any(isinstance(h, _Upload) and h.key == staged[0] for h in self.handles.values()):
                raise Refused(FAILURE, f"{path} is still open")
            if to.name.endswith(PARTIAL):
                # From one waiting name to another: still waiting.
                key = self._key(to)
                if os.path.exists(_part(key)):
                    raise Refused(FAILURE, f"{to} exists")
                os.rename(_part(staged[0]), _part(key))
                meta = {**staged[2], "path": str(to)}
                _meta(key).write_text(json.dumps(meta))
                _meta(staged[0]).unlink(missing_ok=True)
            else:
                self._place(staged[0], to, replace)
            self._status(request, OK)
            return
        if target is not None and stat.S_ISDIR(status.st_mode):
            raise Refused(FAILURE, f"{to} exists")
        with storage._locked():
            source = storage._open_dir(path.parts[:-1])
            try:
                destination = storage._open_dir(to.parts[:-1])
                try:
                    if not replace and storage._lstat(destination, to.name) is not None:
                        raise Refused(FAILURE, f"{to} exists")
                    os.rename(path.name, to.name, src_dir_fd=source, dst_dir_fd=destination)
                finally:
                    os.close(destination)
            finally:
                os.close(source)
            storage.moved_rows(self.ctx.conn, [(path, to)],
                               (to,) if target is not None else ())
        self._audit("store.move", {"path": str(path), "to": str(to)})
        self._status(request, OK)

    def _extended(self, request: int, reader: _Reader) -> None:
        name = reader.text()
        if name == POSIX_RENAME:
            self._rename(request, reader, replace=True)
        elif name == STATVFS:
            self._statvfs(request, reader)
        else:
            raise Refused(OP_UNSUPPORTED, f"{name} is not supported")

    def _statvfs(self, request: int, reader: _Reader) -> None:
        """The disk of the store, its room as store.put counts it: less what
        must stay free, and within [store] max_gb."""
        reader.text()
        storage._mkdirs()
        disk = os.statvfs(paths.STORE_DIR)
        unit = disk.f_frsize or 4096
        room = max(0, disk.f_bavail * unit - storage.MIN_FREE)
        limit = self.ctx.config.store_max_gb * 1024 ** 3
        if limit:
            room = min(room, max(0, limit - storage._usage(self.ctx.conn)))
        free = room // unit
        flags = 1 if not self.writer else 0  # ST_RDONLY
        self._send(EXTENDED_REPLY, struct.pack(
            ">IQQQQQQQQQQQ", request, disk.f_bsize, unit, disk.f_blocks, free, free,
            disk.f_files, disk.f_ffree, disk.f_favail, 0, flags, 255))

    def _unsupported(self, request: int, reader: _Reader) -> None:
        raise Refused(OP_UNSUPPORTED, "links are not part of the store")

    _HANDLERS = {
        REALPATH: _realpath, STAT: _stat, LSTAT: _stat, FSTAT: _fstat,
        OPENDIR: _opendir, READDIR: _readdir, OPEN: _open, READ: _read, WRITE: _write,
        CLOSE: _close, SETSTAT: _setstat, FSETSTAT: _fsetstat, MKDIR: _mkdir, RMDIR: _rmdir,
        REMOVE: _remove, RENAME: _rename, EXTENDED: _extended,
        READLINK: _unsupported, SYMLINK: _unsupported,
    }


@command("store.sftp", role="viewer", mutating=True, agent="code", tool=False, stream="session")
def store_sftp(ctx: Context, params: Values) -> dict:
    """The store over SFTP, version 3, on the input and output: what an SFTP
    client starts at the other end of an SSH login, through fleet store
    sftp. Viewers read; operators and administrators also write, each
    change in the audit trail. What is written goes into the store when it
    is closed, whole."""
    if ctx.stdin is None or ctx.replies is None:
        raise BadRequest("store.sftp speaks SFTP on its input and output: start it from an"
                         " SFTP client, as fleet store sftp")
    ctx.via = ctx.via or "sftp"
    session = Session(ctx, ctx.stdin, ctx.replies)
    try:
        session.serve()
    except Malformed as error:
        raise BadRequest(f"not SFTP: {error}") from None
    except (BrokenPipeError, ConnectionResetError):
        pass  # the client went away
    return {}
