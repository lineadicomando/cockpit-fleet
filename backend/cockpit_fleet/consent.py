"""What a person allowed an agent of theirs in the chat page, before the
agent made the call.

An ad-hoc command an agent asks for waits until the person it acts for
confirms it (runs.run_adhoc): the question of the page is no control, since
an agent at a terminal, or fleet-mcp started by hand, asks nobody. When the
page asks that question, and the person allows the call once, the page
records it here (chat.consent) with the parameters it showed; the command
the agent then makes with the same parameters, in the same session of
fleet-mcp, for the same user and within CONSENT_SECONDS, starts at once,
recorded as confirmed by that person. One consent is one call. The command
that records it is no tool: an agent cannot consent for its person.
"""

import hashlib
import json

from . import store
from .cli import COMMANDS

# Commands whose call of an agent waits for its person, and may be allowed
# beforehand.
CONSENTED = ("run.adhoc",)
# How long a consent waits for the call it allows.
CONSENT_SECONDS = 600
# What fleet-mcp adds to the arguments of an agent: not theirs, not compared.
_THROUGH = ("via", "session", "profile")


def digest(values: dict) -> str:
    """The digest of the checked parameters of a call."""
    text = json.dumps(dict(values), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def of_input(command: str, data: dict) -> str:
    """The digest of a call of COMMAND as the agent wrote it, checked as the
    command checks it: the defaults filled in, a wrong one refused."""
    data = {key: value for key, value in data.items() if key not in _THROUGH}
    return digest(COMMANDS[command].read(data))


def give(conn, session: str, user: str, command: str, value: str) -> None:
    conn.execute("INSERT INTO chat_consents (session, user, command, digest, given_at)"
                 " VALUES (?, ?, ?, ?, ?)", (session, user, command, value, store.now()))


def take(conn, session: str | None, user: str, command: str, value: str) -> bool:
    """Use a consent given for this call; False when there is none."""
    if not session:
        return False
    now = store.now()
    return conn.execute(
        "UPDATE chat_consents SET used_at = ? WHERE rowid = ("
        " SELECT rowid FROM chat_consents WHERE session = ? AND user = ? AND command = ?"
        " AND digest = ? AND used_at IS NULL AND given_at > ? ORDER BY given_at LIMIT 1)",
        (now, session, user, command, value, now - CONSENT_SECONDS)).rowcount == 1
