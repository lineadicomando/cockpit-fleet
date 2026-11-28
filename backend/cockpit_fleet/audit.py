"""The audit trail: who did what, and what was refused."""

import json
import re
import sqlite3
import time

from . import journal
from .auth import Caller

_MAX_PARAMS = 2000
_PRIORITY = {"ok": 6, "denied": 4, "error": 3}


_MAX_VALUE = 200
# The ticket of a secret answer (survey.TICKET_RE) launches a run with it
# until a run takes it: never recorded, whatever the outcome of the call.
_TICKET_RE = re.compile(r"a-[0-9a-f]{32}")


def _short(value: object, depth: int = 0) -> object:
    """A value cut down, so that a long one does not push the others out."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_VALUE else value[:_MAX_VALUE] + "…"
    if depth > 3:
        return "…"
    if isinstance(value, list):
        return [_short(item, depth + 1) for item in value[:20]] + (["…"] if len(value) > 20 else [])
    if isinstance(value, dict):
        return {key: _short(item, depth + 1) for key, item in list(value.items())[:30]}
    return value


def _masked(value: object, secrets: tuple[str, ...], depth: int = 0) -> object:
    """VALUE with every one of SECRETS in its strings, and every ticket of a
    secret answer, replaced by ***. Deeper than _short() keeps, "…"."""
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "***")
        return _TICKET_RE.sub("a-***", value)
    if isinstance(value, (list, dict)) and depth > 4:
        return "…"
    if isinstance(value, list):
        return [_masked(item, secrets, depth + 1) for item in value]
    if isinstance(value, dict):
        return {key: _masked(item, secrets, depth + 1) for key, item in value.items()}
    return value


def _redacted_items(params: dict, redact: tuple[str, ...]) -> dict:
    """PARAMS with KEY of each object of the list LIST hidden, for every
    "LIST[].KEY" of REDACT: a list of changes keeps its paths, not their content."""
    params = dict(params)
    for entry in redact:
        name, found, key = entry.partition("[].")
        if found and isinstance(params.get(name), list):
            params[name] = [{**item, key: "***"} if isinstance(item, dict) and key in item
                            else item for item in params[name]]
    return params


def _params_text(params: dict, redact: tuple[str, ...], secrets: tuple[str, ...] = ()) -> str:
    params = _masked(params, secrets)
    params = _redacted_items(params, redact)
    # Identifiers and limits first and whole: they say what the call was about.
    order = sorted(params, key=lambda key: (not (key == "id" or key.endswith("_id")
                                                 or key == "limit"), key))
    shown = {key: ("***" if key in redact else
                   params[key] if key == "id" or key.endswith("_id") or key == "limit"
                   else _short(params[key])) for key in order}
    text = json.dumps(shown, ensure_ascii=False, default=str)
    return text if len(text) <= _MAX_PARAMS else text[:_MAX_PARAMS] + "…"


def record(
    conn: sqlite3.Connection | None,
    caller: Caller,
    command: str,
    params: dict,
    outcome: str,
    detail: str = "",
    redact: tuple[str, ...] = (),
    via: str = "",
    secrets: tuple[str, ...] = (),
    profile: str = "",
    session: str = "",
    mode: str = "",
) -> None:
    text = _params_text(params, redact, secrets)
    # The journal first: it still gets the record when the database is unusable.
    journal.send(
        f"{caller.user}{f' via {via}' if via else ''}"
        f"{f' ({profile}{f', {mode}' if mode else ''})' if profile else ''}"
        f" {command}: {outcome}"
        + (f" ({detail})" if detail else ""),
        _PRIORITY[outcome],
        FLEET_USER=caller.user,
        FLEET_UID=str(caller.uid),
        FLEET_ROLE=caller.role or "",
        FLEET_COMMAND=command,
        FLEET_OUTCOME=outcome,
        FLEET_PARAMS=text,
        **({"FLEET_VIA": via} if via else {}),
        **({"FLEET_PROFILE": profile} if profile else {}),
        **({"FLEET_SESSION": session} if session else {}),
        **({"FLEET_MODE": mode} if mode else {}),
    )
    if conn is not None:
        conn.execute(
            "INSERT INTO audit (ts, uid, user, role, command, params, outcome, detail, via,"
            " profile, session, mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), caller.uid, caller.user, caller.role, command, text, outcome, detail,
             via, profile, session, mode),
        )


def entries(conn: sqlite3.Connection, limit: int, before: int | None) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ts, user, role, command, params, outcome, detail, via, profile, session,"
        " mode"
        " FROM audit"
        " WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
        (before, before, limit),
    )
    return [dict(row) for row in rows]
