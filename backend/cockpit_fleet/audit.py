"""The audit trail: who did what, and what was refused."""

import json
import sqlite3
import time

from . import journal
from .auth import Caller

_MAX_PARAMS = 2000
_PRIORITY = {"ok": 6, "denied": 4, "error": 3}


def _params_text(params: dict, redact: tuple[str, ...]) -> str:
    shown = {key: ("***" if key in redact else value) for key, value in params.items()}
    text = json.dumps(shown, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= _MAX_PARAMS else text[:_MAX_PARAMS] + "…"


def record(
    conn: sqlite3.Connection | None,
    caller: Caller,
    command: str,
    params: dict,
    outcome: str,
    detail: str = "",
    redact: tuple[str, ...] = (),
) -> None:
    text = _params_text(params, redact)
    # The journal first: it still gets the record when the database is unusable.
    journal.send(
        f"{caller.user} {command}: {outcome}" + (f" ({detail})" if detail else ""),
        _PRIORITY[outcome],
        FLEET_USER=caller.user,
        FLEET_UID=str(caller.uid),
        FLEET_ROLE=caller.role or "",
        FLEET_COMMAND=command,
        FLEET_OUTCOME=outcome,
        FLEET_PARAMS=text,
    )
    if conn is not None:
        conn.execute(
            "INSERT INTO audit (ts, uid, user, role, command, params, outcome, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), caller.uid, caller.user, caller.role, command, text, outcome, detail),
        )


def entries(conn: sqlite3.Connection, limit: int, before: int | None) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ts, user, role, command, params, outcome, detail FROM audit"
        " WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
        (before, before, limit),
    )
    return [dict(row) for row in rows]
