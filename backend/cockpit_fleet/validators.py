"""Validation of everything that ends up in a command line or a path.

Each check returns the accepted value or raises BadRequest naming what was
wrong, so callers can use the result directly.
"""

import re
import unicodedata
from pathlib import Path, PurePosixPath

from .errors import BadRequest

# Host patterns: names, groups, wildcards, unions, intersections, exclusions.
_LIMIT_RE = re.compile(r"[A-Za-z0-9_.*\[\]:,&!~-]{1,512}")
_USER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@\\-]{0,63}")
_TAG_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}")


_REF_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}")
# Only transports that cannot run a local program: no file paths, no ext::.
_GIT_URL_RE = re.compile(
    r"(https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/[A-Za-z0-9._~/%+-]+"
    r"|ssh://([A-Za-z0-9._-]+@)?[A-Za-z0-9.-]+(:[0-9]{1,5})?/[A-Za-z0-9._~/%+-]+"
    r"|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/%+-]+)"
)
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:]{2,45}")


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


def check_git_ref(value: object) -> str:
    ref = _match(_REF_RE, value, "git reference")
    if ".." in ref or ref.endswith(("/", ".lock")):
        raise BadRequest(f"invalid git reference: {value!r}")
    return ref


def check_git_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise BadRequest(f"invalid repository URL: {value!r}")
    return _match(_GIT_URL_RE, value, "repository URL (use https:// or ssh)")


def check_host(value: object) -> str:
    return _match(_HOST_RE, value, "host name or address")


_KNOWN_HOST_RE = re.compile(r"\[([^\]]+)\]:([0-9]{1,5})")
_KEY_TYPE_RE = re.compile(r"[a-z0-9][a-z0-9@.-]{0,63}")


def check_known_host(value: object) -> str:
    """A host as known_hosts names it: a name or an address, or either in
    brackets with a port. Nothing that known_hosts reads as a pattern, a
    list or a marker, which would trust a key for other hosts."""
    if isinstance(value, str):
        bracketed = _KNOWN_HOST_RE.fullmatch(value)
        if bracketed is None:
            return check_host(value)
        if 1 <= int(bracketed.group(2)) <= 65535:
            check_host(bracketed.group(1))
            return value
    raise BadRequest(f"invalid host name or address: {value!r}")


def check_key_type(value: object) -> str:
    return _match(_KEY_TYPE_RE, value, "key type")


# A group of an inventory, and a host as a line of a file given to --limit:
# nothing that reads as a pattern, but an IPv6 address.
_GROUP_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_INVENTORY_HOST_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}|[0-9A-Fa-f:]{2,45}")


def check_group(value: object) -> str:
    return _match(_GROUP_RE, value, "group name")


def check_inventory_host(value: object) -> str:
    return _match(_INVENTORY_HOST_RE, value, "host name")


_VAULT_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")


def check_vault_id(value: object) -> str:
    return _match(_VAULT_ID_RE, value, "vault identifier")


# A module by its full name: a short one could be answered by another collection.
MODULE_RE = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*){2,5}")


def check_module(value: object) -> str:
    return _match(MODULE_RE, value, "module name (use the full one, like ansible.builtin.ping)")


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


def check_file_path(value: object, what: str = "path") -> PurePosixPath:
    """The path of a file written from the interface: relative, of plain names.

    Nothing called .git, in any case: on a file system that ignores case it
    would be git's own directory.
    """
    path = check_relative_path(value, what)
    if not path.parts or len(path.parts) > 12:
        raise BadRequest(f"invalid {what}: {value!r}")
    for part in path.parts:
        if (any(unicodedata.category(ch)[0] == "C" for ch in part) or "\\" in part
                or part != part.strip() or len(part.encode()) > 255
                or part.casefold() == ".git"):
            raise BadRequest(f"invalid name in {what}: {part!r}")
    return path


def resolve_inside(base: Path, value: object, what: str = "path",
                   directory: bool = False) -> Path:
    """The existing file BASE/VALUE, refusing symlinks that lead outside BASE.

    With directory, a directory is accepted as well."""
    relative = check_relative_path(value, what)
    root = base.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise BadRequest(f"{what} leaves its directory: {value!r}")
    if not path.is_file() and not (directory and path.is_dir()):
        raise BadRequest(f"{what} is not a file: {value!r}")
    return path
