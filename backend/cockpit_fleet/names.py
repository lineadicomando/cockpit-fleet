"""Identifier validation shared by every helper.

Identifiers end up in unit instance names, file names and argv, so the allowed
alphabet is deliberately narrow: no path separator, no dot, no leading dash.
"""

import re

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def check_id(value: object, what: str = "identifier") -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value
