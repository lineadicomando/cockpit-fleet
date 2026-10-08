"""fleetctl: the one interface between the web console and the backend.

    fleetctl COMMAND < params.json

COMMAND is a name from the registry; parameters are a JSON object on stdin.
The answer is one JSON object on stdout: {"ok": true, "data": ...} or
{"ok": false, "error": {"code": ..., "message": ...}}. The exit status is 0
whenever an answer was written, so the caller always gets to read it.
"""

import json
import os
import pwd
import sqlite3
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import audit, db, journal, paths
from .auth import Caller, resolve_caller, role_at_least
from .config import Config
from .config import load as load_config
from .errors import BadRequest, FleetError, Forbidden
from .names import check_id

MAX_INPUT = 1024 * 1024


@dataclass(frozen=True)
class Command:
    name: str
    role: str
    handler: Callable
    mutating: bool
    redact: tuple[str, ...]


COMMANDS: dict[str, Command] = {}


def command(name: str, role: str, mutating: bool = False, redact: tuple[str, ...] = ()):
    """Register a handler. Every command states the least role that may call it."""

    def register(handler: Callable) -> Callable:
        COMMANDS[name] = Command(name, role, handler, mutating, redact)
        return handler

    return register


@dataclass
class Context:
    caller: Caller
    database: Path = paths.DATABASE
    config_file: Path = paths.CONFIG_FILE
    _conn: sqlite3.Connection | None = field(default=None, repr=False)
    _config: Config | None = field(default=None, repr=False)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = db.connect(self.database)
        return self._conn

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = load_config(self.config_file)
        return self._config

    def conn_or_none(self) -> sqlite3.Connection | None:
        try:
            return self.conn
        except FleetError:
            return None


class Params:
    """Typed access to the parameters; done() rejects anything not consumed."""

    def __init__(self, data: dict):
        self._data = dict(data)

    def _take(self, name: str, default: object) -> object:
        return self._data.pop(name, default)

    def integer(self, name: str, default: int | None, low: int, high: int) -> int | None:
        value = self._take(name, default)
        if value is None and default is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise BadRequest(f"{name} must be an integer between {low} and {high}")
        return value

    def string(self, name: str, default: str | None = None, max_length: int = 4096) -> str:
        value = self._take(name, default)
        if not isinstance(value, str) or len(value) > max_length or "\x00" in value:
            raise BadRequest(f"{name} must be a string of at most {max_length} characters")
        return value

    def boolean(self, name: str, default: bool = False) -> bool:
        value = self._take(name, default)
        if not isinstance(value, bool):
            raise BadRequest(f"{name} must be true or false")
        return value

    def optional_id(self, name: str) -> str | None:
        value = self._take(name, None)
        if value is None or value == "":
            return None
        try:
            return check_id(value, name)
        except ValueError as error:
            raise BadRequest(str(error)) from error

    def ident(self, name: str) -> str:
        value = self.optional_id(name)
        if value is None:
            raise BadRequest(f"{name} is required")
        return value

    def strings(self, name: str, max_items: int = 50) -> list[str]:
        value = self._take(name, [])
        if (not isinstance(value, list) or len(value) > max_items
                or not all(isinstance(item, str) for item in value)):
            raise BadRequest(f"{name} must be a list of at most {max_items} strings")
        return value

    def mapping(self, name: str) -> dict:
        value = self._take(name, {})
        if not isinstance(value, dict):
            raise BadRequest(f"{name} must be an object")
        return value

    def choice(self, name: str, choices: tuple[str, ...], default: str | None = None) -> str:
        value = self._take(name, default)
        if value not in choices:
            raise BadRequest(f"{name} must be one of: {', '.join(choices)}")
        return value

    def done(self) -> None:
        if self._data:
            raise BadRequest(f"unknown parameter: {', '.join(sorted(self._data))}")


def _parse_input(raw: bytes) -> dict:
    if len(raw) > MAX_INPUT:
        raise BadRequest("input too large")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise BadRequest(f"input is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise BadRequest("input must be a JSON object")
    return data


def dispatch(name: str, raw: bytes, ctx: Context) -> dict:
    """Run one command and return the answer. Never raises."""
    params: dict = {}
    spec = COMMANDS.get(name)
    try:
        if spec is None:
            raise BadRequest(f"unknown command: {name}")
        params = _parse_input(raw)
        if not role_at_least(ctx.caller.role, spec.role):
            audit.record(ctx.conn_or_none(), ctx.caller, name, params, "denied",
                         f"requires {spec.role}", spec.redact)
            raise Forbidden(f"{name} requires the {spec.role} role")
        data = spec.handler(ctx, Params(params))
        if spec.mutating:
            audit.record(ctx.conn, ctx.caller, name, params, "ok", redact=spec.redact)
        return {"ok": True, "data": data}
    except FleetError as error:
        if spec is not None and spec.mutating and not isinstance(error, Forbidden):
            audit.record(ctx.conn_or_none(), ctx.caller, name, params, "error",
                         str(error), spec.redact)
        return {"ok": False, "error": {"code": error.code, "message": str(error)}}
    except Exception:
        # The details go to the journal, not to the browser.
        journal.send(f"{name} failed", 3, FLEET_COMMAND=name,
                     FLEET_TRACEBACK=traceback.format_exc())
        message = "internal error, see the journal"
        return {"ok": False, "error": {"code": "internal", "message": message}}


def main(argv: list[str], env: Mapping[str, str] = os.environ) -> int:
    from . import resources, runs, system  # noqa: F401  (register the commands)

    if len(argv) != 2:
        print("usage: fleetctl COMMAND < params.json", file=sys.stderr)
        return 2
    try:
        service_uid = pwd.getpwnam(paths.SERVICE_USER).pw_uid
    except KeyError:
        service_uid = -1
    if os.geteuid() != service_uid:
        print(f"fleetctl: must run as {paths.SERVICE_USER}, through sudo", file=sys.stderr)
        return 2

    os.umask(0o077)
    ctx = Context(resolve_caller(env, service_uid))
    answer = dispatch(argv[1], sys.stdin.buffer.read(MAX_INPUT + 1), ctx)
    json.dump(answer, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0
