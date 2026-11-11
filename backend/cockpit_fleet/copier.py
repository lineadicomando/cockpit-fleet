"""Copies within the store: the copy itself, and the job of kind "copy".

A copy is made with copy_file_range: on XFS and Btrfs the kernel shares the
blocks of the file (a reflink), at once whatever its size; elsewhere (ext4)
it copies inside the kernel, without the bytes passing through the process.

Copies of up to COPY_AT_ONCE in all are made by store.copy itself. Larger
ones are a job, in the lane of downloads: store.copy gives the run hard
links to the files to copy (copy/in/N, one entry per copy asked), the job
copies them to copy/out/N and writes progress.json as a download does, and
the backend moves what it made into the store when the job has ended
(storage.take_copy). The job reads nothing but its own run directory.
"""

import errno
import json
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

IN = Path("copy") / "in"
OUT = Path("copy") / "out"
PROGRESS = "progress.json"
# Copies of up to this much in all are made at once; larger ones by a job.
COPY_AT_ONCE = 1024 ** 3
# What one copy_file_range is asked for: the job looks at a cancel between two.
_STEP = 64 * 1024 * 1024
_PROGRESS_EVERY = 0.5


class Stopped(Exception):
    """The run was canceled."""


def copy_fd(source: int, target: int, size: int,
            step: Callable[[int], None] = lambda done: None) -> None:
    """SIZE bytes from SOURCE to TARGET, both at their start. STEP is told
    the bytes copied so far, and may raise to stop."""
    done = 0
    while done < size:
        try:
            copied = os.copy_file_range(source, target, min(_STEP, size - done))
        except OSError as error:
            if error.errno not in (errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL):
                raise
            copied = _read_write(source, target, min(_STEP, size - done))
        if copied == 0:
            raise OSError(errno.EIO, "the file is shorter than it was")
        done += copied
        step(done)


def _read_write(source: int, target: int, length: int) -> int:
    """copy_file_range by hand, for a file system that cannot do it."""
    done = 0
    while done < length:
        chunk = os.read(source, min(1024 * 1024, length - done))
        if not chunk:
            break
        os.write(target, chunk)
        done += len(chunk)
    return done


def _progress(run_dir: Path, received: int, total: int) -> None:
    temporary = run_dir / f"{PROGRESS}.tmp"
    temporary.write_text(json.dumps({"received": received, "total": total,
                                     "updated_at": time.time()}))
    temporary.replace(run_dir / PROGRESS)


def _tree(source: Path) -> list[tuple[str, os.stat_result | None]]:
    """The entries under SOURCE, outermost first: (relative path, status),
    None for a directory. Nothing but plain files and directories."""
    entries: list[tuple[str, os.stat_result | None]] = []
    for root, directories, files in os.walk(source, followlinks=False):
        relative = Path(root).relative_to(source)
        directories.sort()
        for name in directories:
            if not stat.S_ISDIR(os.lstat(Path(root) / name).st_mode):
                raise ValueError(f"{relative / name} is not a plain directory")
            entries.append((str(relative / name), None))
        for name in sorted(files):
            status = os.lstat(Path(root) / name)
            if not stat.S_ISREG(status.st_mode):
                raise ValueError(f"{relative / name} is not a plain file")
            entries.append((str(relative / name), status))
    return entries


def copy(spec: dict, run_dir: Path, say: Callable[[str], None],
         stopped: Callable[[], bool] = lambda: False) -> dict:
    """The job: each copy/in/N to copy/out/N, a file or a whole directory."""
    items = spec.get("items")
    total = spec.get("total")
    if not isinstance(items, list) or not isinstance(total, int):
        raise ValueError("the spec of a copy needs items and total")
    copied, last, files = 0, 0.0, 0
    (run_dir / OUT).mkdir(mode=0o700, parents=True, exist_ok=True)

    def step(base: int) -> Callable[[int], None]:
        def tell(done: int) -> None:
            nonlocal last
            if stopped():
                raise Stopped
            if time.monotonic() - last > _PROGRESS_EVERY:
                _progress(run_dir, base + done, total)
                last = time.monotonic()
        return tell

    def one(source: Path, target: Path) -> None:
        nonlocal copied, files
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            size = os.fstat(fd).st_size
            out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                          | os.O_CLOEXEC, 0o640)
            try:
                copy_fd(fd, out, size, step(copied))
                os.fsync(out)
            finally:
                os.close(out)
        finally:
            os.close(fd)
        copied += size
        files += 1

    try:
        for index in range(len(items)):
            source = run_dir / IN / str(index)
            target = run_dir / OUT / str(index)
            status = os.lstat(source)
            if stat.S_ISREG(status.st_mode):
                one(source, target)
            elif stat.S_ISDIR(status.st_mode):
                target.mkdir(mode=0o750)
                for relative, entry in _tree(source):
                    if entry is None:
                        (target / relative).mkdir(mode=0o750)
                    else:
                        one(source / relative, target / relative)
            else:
                raise ValueError(f"copy {index}: not a file or a directory")
            say(f"{items[index].get('path', index)}: copied")
    except Stopped:
        raise ValueError("canceled") from None
    _progress(run_dir, copied, total)
    say(f"{files} files, {copied} bytes")
    return {"rc": 0, "files": files, "bytes": copied}
