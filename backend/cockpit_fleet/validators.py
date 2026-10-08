"""Validation of everything that ends up in a command line or a path.

Each check returns the accepted value or raises BadRequest naming what was
wrong, so callers can use the result directly.
"""

import re
from pathlib import Path, PurePosixPath

from .errors import BadRequest

# Host patterns: names, groups, wildcards, unions, intersections, exclusions.
_LIMIT_RE = re.compile(r"[A-Za-z0-9_.*\[\]:,&!~-]{1,512}")
_USER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@\\-]{0,63}")
_TAG_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}")


def _match(pattern: re.Pattern, value: object, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value) or value.startswith("-"):
        raise BadRequest(f"invalid {what}: {value!r}")
    return value


def check_limit(value: object) -> str:
    return _match(_LIMIT_RE, value, "host pattern")


def check_user(value: object) -> str:
    return _match(_USER_RE, value, "user name")


def check_tag(value: object) -> str:
    return _match(_TAG_RE, value, "tag")


def check_int(value: object, what: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise BadRequest(f"{what} must be an integer between {low} and {high}")
    return value


def check_relative_path(value: object, what: str = "path") -> PurePosixPath:
    """A path that stays below its base: relative, no '..', no option look-alike."""
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 1024:
        raise BadRequest(f"invalid {what}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("-"):
        raise BadRequest(f"{what} must be a relative path without '..': {value!r}")
    return path


def resolve_inside(base: Path, value: object, what: str = "path") -> Path:
    """The existing file BASE/VALUE, refusing symlinks that lead outside BASE."""
    relative = check_relative_path(value, what)
    root = base.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise BadRequest(f"{what} leaves its directory: {value!r}")
    if not path.is_file():
        raise BadRequest(f"{what} is not a file: {value!r}")
    return path
