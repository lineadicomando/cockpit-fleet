"""The ciphertext of a run, moved out of the service account's reach before
PID 1 reads it. Used by fleet-unit, as root.

PID 1 reads LoadCredentialEncrypted= paths as root and, with systemd 257,
follows symbolic links: a run directory or a creds/ that the account turned
into a link would have it decrypt root's credentials of other services into
the run. So fleet-unit opens each part of the path without following links,
takes only regular files of the account, and copies them into a directory
that only root can reach, which the unit reads instead.
"""

import os
import re
import shutil
import stat
from pathlib import Path

MAX_FILES = 64
# A secret is 64 KiB at most; encrypted, a little more.
MAX_SIZE = 256 * 1024
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_owned_dir(name: str, dir_fd: int, uid: int) -> int:
    fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    if os.fstat(fd).st_uid != uid:
        os.close(fd)
        raise ValueError(f"{name} is not owned by the service account")
    return fd


def _read_file(name: str, dir_fd: int, uid: int) -> bytes:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"unexpected file in creds: {name!r}")
    # O_NONBLOCK: a FIFO in its place must not hold root here.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_size > MAX_SIZE):
            raise ValueError(f"creds/{name} is not a credential of the service account")
        data = os.read(fd, MAX_SIZE + 1)
        if len(data) > MAX_SIZE:
            raise ValueError(f"creds/{name} is too large")
        return data
    finally:
        os.close(fd)


def _fresh_dir(staging: Path, run_id: str) -> Path:
    staging.mkdir(mode=0o700, exist_ok=True)
    info = os.lstat(staging)
    # Root's, as fleet-unit runs; the caller's in the tests.
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError(f"{staging} must be a directory of root's, mode 0700")
    target = staging / run_id
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(mode=0o700)
    return target


def stage(state_dir: Path, run_id: str, staging: Path, uid: int) -> Path:
    """Copy STATE_DIR/runs/RUN_ID/creds into STAGING/RUN_ID and return it.
    RUN_ID must have been validated. Raises ValueError or OSError, having
    copied nothing, when anything is not as the backend leaves it."""
    fds = [os.open(state_dir.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)]
    try:
        for name in (state_dir.name, "runs", run_id):
            fds.append(_open_owned_dir(name, fds[-1], uid))
        try:
            fds.append(_open_owned_dir("creds", fds[-1], uid))
            names = sorted(os.listdir(fds[-1]))
        except FileNotFoundError:
            names = []
        if len(names) > MAX_FILES:
            raise ValueError("too many files in creds")
        files = {name: _read_file(name, fds[-1], uid) for name in names}
    finally:
        for fd in fds:
            os.close(fd)

    target = _fresh_dir(staging, run_id)
    try:
        for name, data in files.items():
            fd = os.open(target / name,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(data)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def discard(staging: Path, run_id: str) -> None:
    shutil.rmtree(staging / run_id, ignore_errors=True)
