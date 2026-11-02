"""The audit trail: who did what, and what was refused."""

import json
import sqlite3
import time

from . import journal
from .auth import Caller

_MAX_PARAMS = 2000
_PRIORITY = {"ok": 6, "denied": 4, "error": 3}


_MAX_VALUE = 200


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


def _masked(value: object, secrets: tuple[str, ...]) -> object:
    """VALUE with every one of SECRETS in its strings replaced by ***."""
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "***")
        return value
    if isinstance(value, list):
        return [_masked(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _masked(item, secrets) for key, item in value.items()}
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
    if secrets:
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
) -> None:
    text = _params_text(params, redact, secrets)
    # The journal first: it still gets the record when the database is unusable.
    journal.send(
        f"{caller.user}{f' via {via}' if via else ''} {command}: {outcome}"
        + (f" ({detail})" if detail else ""),
        _PRIORITY[outcome],
        FLEET_USER=caller.user,
        FLEET_UID=str(caller.uid),
        FLEET_ROLE=caller.role or "",
        FLEET_COMMAND=command,
        FLEET_OUTCOME=outcome,
        FLEET_PARAMS=text,
        **({"FLEET_VIA": via} if via else {}),
    )
    if conn is not None:
        conn.execute(
            "INSERT INTO audit (ts, uid, user, role, command, params, outcome, detail, via)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), caller.uid, caller.user, caller.role, command, text, outcome, detail,
             via),
        )


def entries(conn: sqlite3.Connection, limit: int, before: int | None) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ts, user, role, command, params, outcome, detail, via FROM audit"
        " WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
        (before, before, limit),
    )
    return [dict(row) for row in rows]
