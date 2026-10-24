"""Unpack an uploaded archive into the tree of a project.

This runs inside a run unit, never in fleetctl: the archive is whatever the
browser sent, and reading it means running the decompressors on it.

Nothing is delegated to tarfile.extract or ZipFile.extract. Every member is
checked and written here: regular files and directories only, under names
that stay inside the destination, up to a number and a size. Symbolic links
are made last, when no file is left to be written through one, and only if
they stay inside the tree. Anything else stops the whole extraction: an
archive is taken as it is or not at all.
"""

import lzma
import os
import shutil
import stat
import tarfile
import unicodedata
import zipfile
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NamedTuple

from .errors import BadRequest

# What a run can copy (jobs.snapshot): a larger tree would be of no use.
MAX_FILES = 20_000
MAX_BYTES = 500 * 1024 * 1024
# Members read, including those left out: an archive of a work tree brings
# its .git along.
MAX_MEMBERS = 200_000
MAX_DEPTH = 32
MAX_LINK = 1024

_CHUNK = 1024 * 1024
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_DAMAGED = (tarfile.TarError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error,
            lzma.LZMAError, EOFError, NotImplementedError, RuntimeError, OSError)


class Member(NamedTuple):
    name: str
    kind: str  # "file", "directory", "link" or what it is instead, for the error
    executable: bool = False
    target: str = ""
    read: Callable[[], BinaryIO | None] | None = None
    # What it says it holds: a tar read as a stream goes through all of it,
    # even for a member that is left out.
    size: int = 0


def member_path(name: str) -> PurePosixPath | None:
    """Where a member goes, relative to the tree; None for one that is left out.

    Left out: the archive's own root (".") and anything of git.
    """
    if not name or "\x00" in name or "\\" in name:
        raise BadRequest(f"invalid name in the archive: {name!r}")
    try:
        name.encode()
    except UnicodeEncodeError:
        raise BadRequest(f"a name in the archive is not UTF-8: {name!r}") from None
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise BadRequest(f"the archive has a path that leaves it: {name!r}")
    if len(path.parts) > MAX_DEPTH:
        raise BadRequest(f"the archive has a path that is too deep: {name!r}")
    for part in path.parts:
        if any(unicodedata.category(ch)[0] == "C" for ch in part) or len(part.encode()) > 255:
            raise BadRequest(f"invalid name in the archive: {name!r}")
    if not path.parts or any(part.casefold() == ".git" for part in path.parts):
        return None
    return path


def _tar_members(source: BinaryIO) -> Iterator[Member]:
    # A stream: members are read in order, once, whatever the compression.
    with tarfile.open(fileobj=source, mode="r|*") as archive:
        for info in archive:
            if info.isreg():
                yield Member(info.name, "file", bool(info.mode & 0o111),
                             read=lambda info=info: archive.extractfile(info), size=info.size)
            elif info.isdir():
                yield Member(info.name, "directory")
            elif info.issym():
                yield Member(info.name, "link", target=info.linkname)
            elif info.islnk():
                yield Member(info.name, "hard link")
            else:
                yield Member(info.name, "device or special file")


def _zip_members(source: BinaryIO) -> Iterator[Member]:
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.flag_bits & 0x1:
                raise BadRequest("the archive is encrypted")
            # Only an archive made on Unix says what kind of file a member is.
            mode = info.external_attr >> 16 if info.create_system == 3 else 0
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                yield Member(info.filename, "directory")
            elif kind == stat.S_IFLNK:
                with archive.open(info) as link:
                    target = link.read(MAX_LINK + 1)
                yield Member(info.filename, "link", target=target.decode(errors="surrogateescape"))
            elif kind in (0, stat.S_IFREG):
                yield Member(info.filename, "file", bool(mode & 0o111),
                             read=lambda info=info: archive.open(info))
            else:
                yield Member(info.filename, "device or special file")


def _make_directories(root: Path, path: PurePosixPath) -> None:
    current = root
    for part in path.parts:
        current = current / part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                raise BadRequest(f"the archive has both a file and a directory called "
                                 f"{current.relative_to(root)}") from None


def _write_file(root: Path, path: PurePosixPath, member: Member, room: int) -> int:
    """Write one file, of at most ROOM bytes; returns its size."""
    _make_directories(root, path.parent)
    try:
        descriptor = os.open(root.joinpath(*path.parts),
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o700 if member.executable else 0o600)
    except FileExistsError:
        raise BadRequest(f"the archive has {path} twice") from None
    size = 0
    with os.fdopen(descriptor, "wb") as target:
        content = member.read() if member.read else None
        if content is None:
            return 0
        with content:
            # Counted as it is written: the size a member declares is only a claim.
            while chunk := content.read(_CHUNK):
                size += len(chunk)
                if size > room:
                    raise BadRequest(f"the archive unpacks to more than "
                                     f"{MAX_BYTES // (1024 * 1024)} MB")
                target.write(chunk)
    return size


def _check_link(path: PurePosixPath, target: str) -> None:
    """A link is kept if following it, as written, does not leave the tree."""
    if not target or len(target) > MAX_LINK or "\x00" in target or target.startswith("/"):
        raise BadRequest(f"the link {path} points outside the archive")
    try:
        target.encode()
    except UnicodeEncodeError:
        raise BadRequest(f"the link {path} has a target that is not UTF-8") from None
    parts = list(path.parts[:-1])
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part != "..":
            parts.append(part)
        elif parts:
            parts.pop()
        else:
            raise BadRequest(f"the link {path} points outside the archive")


def _make_links(root: Path, links: dict[PurePosixPath, str]) -> None:
    for path, target in links.items():
        _check_link(path, target)
        _make_directories(root, path.parent)
        try:
            os.symlink(target, root.joinpath(*path.parts))
        except FileExistsError:
            raise BadRequest(f"the archive has {path} twice") from None
    # Again, as the kernel follows them: a link that goes through another one
    # can leave the tree with a target that, read alone, stays inside.
    real_root = os.path.realpath(root)
    for path in links:
        real = os.path.realpath(root.joinpath(*path.parts))
        if real != real_root and not real.startswith(real_root + os.sep):
            raise BadRequest(f"the link {path} points outside the archive")


def extract(archive: Path, destination: Path) -> dict:
    """Unpack ARCHIVE (tar, compressed or not, or zip) into DESTINATION.

    DESTINATION must not exist; it does not exist afterwards either, if the
    archive is refused. A single directory holding everything else is not
    kept: its content becomes the tree. Returns what was done, in numbers.
    """
    if os.path.lexists(destination):
        raise FileExistsError(f"{destination} exists")
    staging = destination.with_name(destination.name + ".unpack")
    os.mkdir(staging, 0o700)
    try:
        summary, root = _unpack(archive, staging)
        os.rename(root, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return summary


def _unpack(archive: Path, staging: Path) -> tuple[dict, Path]:
    files = size = skipped = passed = 0
    links: dict[PurePosixPath, str] = {}
    top: set[str] = set()
    try:
        with open(archive, "rb") as source:
            magic = source.read(4)
            source.seek(0)
            members = _zip_members(source) if magic in _ZIP_MAGIC else _tar_members(source)
            for count, member in enumerate(members, 1):
                if count > MAX_MEMBERS:
                    raise BadRequest(f"the archive has more than {MAX_MEMBERS} members")
                path = member_path(member.name)
                if path is None:
                    skipped += 1
                    passed += member.size
                    if passed > MAX_BYTES:
                        raise BadRequest("the archive holds more than"
                                         f" {MAX_BYTES // (1024 * 1024)} MB in what is left out")
                    continue
                if member.kind not in ("file", "directory", "link"):
                    raise BadRequest(f"the archive has a {member.kind}: {path}")
                top.add(path.parts[0])
                if member.kind == "directory":
                    _make_directories(staging, path)
                    continue
                files += 1
                if files > MAX_FILES:
                    raise BadRequest(f"the archive has more than {MAX_FILES} files")
                if member.kind == "link":
                    if path in links:
                        raise BadRequest(f"the archive has {path} twice")
                    links[path] = member.target
                else:
                    size += _write_file(staging, path, member, MAX_BYTES - size)
    except tarfile.ReadError as error:
        raise BadRequest(f"not a tar or zip archive, or a damaged one: {error}") from error
    except _DAMAGED as error:
        raise BadRequest(f"the archive cannot be read: {error}") from error
    if not files:
        raise BadRequest("the archive holds no files")

    # project.tar.gz usually holds project/: that level says nothing.
    root, stripped = staging, ""
    if len(top) == 1 and PurePosixPath(*top) not in links and (staging / next(iter(top))).is_dir():
        stripped = next(iter(top))
        root = staging / stripped
        links = {PurePosixPath(*path.parts[1:]): target for path, target in links.items()}
    _make_links(root, links)
    return {"files": files - len(links), "links": len(links), "bytes": size,
            "skipped": skipped, "stripped": stripped}, root
