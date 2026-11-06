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
import re
import sqlite3
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import audit, autonomy, db, journal, paths, profiles
from .auth import ROLES, Caller, allowed, resolve_caller
from .config import Config
from .config import load as load_config
from .errors import BadRequest, FleetError, Forbidden
from .names import check_id

MAX_INPUT = 1024 * 1024
# What a command lets an agent do, from the least to the most: read; start,
# stop and follow work already defined; run any module on the nodes; change
# what is defined; change the code that runs, the secrets, or delete. The
# profiles of the assistant are made of these.
AGENT_CLASSES = ("read", "run", "adhoc", "config", "code")
# How a call that does not come from the interface names what it came through.
_VIA_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/ -]{0,79}")


@dataclass(frozen=True)
class Field:
    """One parameter of a command: its type, its bounds and what it is for.

    The declaration is what checks the input and what describes the command
    to whoever calls it without the interface (see schema()).
    """

    kind: str
    doc: str = ""
    required: bool = False
    default: object = None
    low: int = 0
    high: int = 0
    max_length: int = 4096
    max_items: int = 50
    choices: tuple[str, ...] = ()

    def read(self, params: "Params", name: str) -> object:
        if self.kind == "string":
            if not self.required and self.default is None and not params.has(name):
                return None
            return params.string(name, None if self.required else self.default, self.max_length)
        if self.kind == "integer":
            return params.integer(name, self.default, self.low, self.high)
        if self.kind == "boolean":
            return params.boolean(name, bool(self.default))
        if self.kind == "id":
            return params.ident(name) if self.required else params.optional_id(name)
        if self.kind == "strings":
            return params.strings(name, self.max_items)
        if self.kind == "objects":
            return params.objects(name, self.max_items)
        if self.kind == "object":
            return params.mapping(name)
        return params.choice(name, self.choices, self.default)

    def schema(self) -> dict:
        """This parameter as JSON Schema."""
        if self.kind == "string":
            out: dict = {"type": "string", "maxLength": self.max_length}
        elif self.kind == "integer":
            out = {"type": "integer", "minimum": self.low, "maximum": self.high}
        elif self.kind == "boolean":
            out = {"type": "boolean"}
        elif self.kind == "id":
            out = {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$"}
        elif self.kind == "strings":
            out = {"type": "array", "items": {"type": "string"}, "maxItems": self.max_items}
        elif self.kind == "objects":
            out = {"type": "array", "items": {"type": "object"}, "maxItems": self.max_items}
        elif self.kind == "object":
            out = {"type": "object"}
        else:
            out = {"type": "string", "enum": list(self.choices)}
        if self.doc:
            out["description"] = self.doc
        if self.default is not None and not self.required:
            out["default"] = self.default
        return out


def Str(doc: str = "", *, required: bool = False, default: str | None = None,  # noqa: N802
        max_length: int = 4096) -> Field:
    return Field("string", doc, required, default, max_length=max_length)


def Int(low: int, high: int, doc: str = "", *, default: int | None = None) -> Field:  # noqa: N802
    return Field("integer", doc, default=default, low=low, high=high)


def Bool(doc: str = "", *, default: bool = False) -> Field:  # noqa: N802
    return Field("boolean", doc, default=default)


def Id(doc: str = "", *, required: bool = True) -> Field:  # noqa: N802
    return Field("id", doc, required)


def Strs(doc: str = "", *, max_items: int = 50) -> Field:  # noqa: N802
    return Field("strings", doc, max_items=max_items)


def Objs(doc: str = "", *, max_items: int = 50) -> Field:  # noqa: N802
    return Field("objects", doc, max_items=max_items)


def Obj(doc: str = "") -> Field:  # noqa: N802
    return Field("object", doc)


def Choice(choices: tuple[str, ...], doc: str = "", *, default: str | None = None) -> Field:  # noqa: N802
    return Field("choice", doc, default is None, default, choices=choices)


class Values(dict):
    """The parameters of a call, checked: every declared name is there, with
    the value given, or its default, or None."""

    def __init__(self, values: dict, given: frozenset[str]):
        super().__init__(values)
        self.given = given

    def unused(self, *names: str) -> None:
        """Refuse parameters that mean nothing in this form of the command."""
        extra = sorted(self.given.intersection(names))
        if extra:
            raise BadRequest(f"unknown parameter: {', '.join(extra)}")

    def need(self, name: str) -> object:
        """A parameter that this form of the command cannot do without."""
        if self[name] is None:
            raise BadRequest(f"{name} is required")
        return self[name]


@dataclass(frozen=True)
class Command:
    name: str
    role: str
    handler: Callable
    mutating: bool
    redact: tuple[str, ...]
    max_input: int
    params: Mapping[str, Field]
    # Whether an agent is offered this command as a tool.
    tool: bool = True
    # A right that lets a caller use the command whatever their role.
    right: str = ""
    # Which of AGENT_CLASSES the command is in.
    agent: str = "read"

    def read(self, data: dict) -> Values:
        """Check the parameters of a call against the declaration."""
        params = Params(data)
        values = {name: field.read(params, name) for name, field in self.params.items()}
        params.done()
        return Values(values, frozenset(data).intersection(self.params))

    def call(self, ctx: "Context", data: dict) -> dict:
        return self.handler(ctx, self.read(data))

    def schema(self) -> dict:
        """The parameters as JSON Schema: what a tool built on this command takes."""
        return {
            "type": "object",
            "properties": {name: field.schema() for name, field in self.params.items()},
            "required": [name for name, field in self.params.items() if field.required],
            "additionalProperties": False,
        }


COMMANDS: dict[str, Command] = {}


def command(name: str, role: str, mutating: bool = False, redact: tuple[str, ...] = (),
            max_input: int = MAX_INPUT, params: Mapping[str, Field] | None = None,
            tool: bool = True, right: str = "", agent: str = ""):
    """Register a handler. Every command states the least role that may call
    it and the parameters it takes; the handler gets them checked, as Values.
    A command that names a right may also be called by whoever has it.

    agent is its class (AGENT_CLASSES). By default a command that changes
    nothing reads, one that changes something is run for an operator and
    config for an administrator; the others say theirs.

    max_input is for the commands that take a file: the others never need
    more than the default.
    """

    kind = agent or ("read" if not mutating else "config" if role == "admin" else "run")
    if kind not in AGENT_CLASSES:
        raise ValueError(f"{name}: unknown class {kind}")

    def register(handler: Callable) -> Callable:
        COMMANDS[name] = Command(name, role, handler, mutating, redact, max_input, params or {},
                                 tool, right, kind)
        return handler

    return register


@dataclass
class Context:
    caller: Caller
    database: Path = paths.DATABASE
    config_file: Path = paths.CONFIG_FILE
    # Set when the call says it comes from an agent: see dispatch().
    via: str = ""
    # The profile of the assistant an agent's call is held to; "" for a person.
    profile: str = ""
    # The fleet-mcp an agent's call came through, when it says.
    session: str = ""
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

    def close(self) -> None:
        """Close the connection, if one was opened; conn opens another."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class Params:
    """Typed access to the parameters; done() rejects anything not consumed."""

    def __init__(self, data: dict):
        self._data = dict(data)

    def _take(self, name: str, default: object) -> object:
        return self._data.pop(name, default)

    def has(self, name: str) -> bool:
        return name in self._data

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

    def objects(self, name: str, max_items: int = 50) -> list[dict]:
        value = self._take(name, [])
        if (not isinstance(value, list) or len(value) > max_items
                or not all(isinstance(item, dict) for item in value)):
            raise BadRequest(f"{name} must be a list of at most {max_items} objects")
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


def _parse_input(raw: bytes, limit: int = MAX_INPUT) -> dict:
    if len(raw) > limit:
        raise BadRequest("input too large")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise BadRequest(f"input is not valid JSON: {error}") from error
    except RecursionError:
        raise BadRequest("input is nested too deeply") from None
    if not isinstance(data, dict):
        raise BadRequest("input must be a JSON object")
    return data


def is_agent(via: str) -> bool:
    """Whether a call says it comes from an agent, through fleet-mcp."""
    return via == "mcp" or via.startswith("mcp:")


def _recorded(error: FleetError) -> FleetError:
    """An error whose refusal is in the audit trail already."""
    error.audited = True
    return error


def _record(ctx: Context, name: str, params: dict, outcome: str, detail: str = "",
            redact: tuple[str, ...] = (), secrets: tuple[str, ...] = (), conn=None) -> None:
    """One record of the audit trail, with what the call came through."""
    audit.record(conn if conn is not None else ctx.conn_or_none(), ctx.caller, name, params,
                 outcome, detail, redact, ctx.via, secrets, ctx.profile, ctx.session)


def _through(ctx: Context, params: dict, name: str, redact: tuple[str, ...]) -> None:
    """Take "via" out of the parameters of a call that does not come from the
    interface, and hold a call made for an agent to what config.toml lets
    agents do.

    fleet-mcp adds "mcp:CLIENT" to everything it sends, the command fleet
    adds "cli". For an agent saying it can only restrict: the caller's role
    is lowered to the ceiling, never raised, the call is held to the profile
    of the assistant fleet-mcp adds beside it ("profile", or the default),
    and nothing goes through while agents are not enabled. Any other value
    is only recorded. Not saying it, or saying another, is not a way around
    anything the caller could not already do by hand. An agent has no right
    beyond its role: a person approves a run, not a program acting for them.
    """
    via = params.pop("via", None)
    if via is None:
        return
    if not isinstance(via, str) or not _VIA_RE.fullmatch(via):
        raise BadRequest("via must name what the call comes through")
    ctx.via = via
    if not is_agent(via):
        return
    profile = params.pop("profile", None)
    session = params.pop("session", None)
    if session is not None:
        if not isinstance(session, str) or not autonomy.SESSION_RE.fullmatch(session):
            raise BadRequest("session must be what fleet-mcp drew")
        ctx.session = session
    config = ctx.config

    def refuse(reason: str, message: str) -> FleetError:
        _record(ctx, name, params, "denied", reason, redact)
        return _recorded(Forbidden(message))

    if not config.mcp_enabled:
        raise refuse("agents are not enabled",
                     "agents are not enabled on this controller ([mcp] in config.toml)")
    if profile is None:
        profile = config.chat_default_profile
    if not isinstance(profile, str) or profile not in config.chat_profiles:
        asked = profile[:40] if isinstance(profile, str) else "?"
        raise refuse(f"profile {asked!r} not enabled", "this profile of the assistant is not"
                     " enabled on this controller ([chat] profiles in config.toml)")
    ctx.profile = profile
    spec = COMMANDS.get(name)
    if spec is not None and not spec.tool and name != "mcp.tools":
        # fleet-mcp offers no such tool; a call that names one anyway is refused.
        raise refuse("not a tool for agents", f"{name} is not for agents")
    ceiling = config.mcp_max_role
    if ctx.caller.role is not None and ROLES.index(ctx.caller.role) > ROLES.index(ceiling):
        ctx.caller = replace(ctx.caller, role=ceiling)
    ctx.caller = replace(ctx.caller, rights=frozenset())
    if spec is not None:
        kind = profiles.call_class(name, spec.agent, params, config.mcp_adhoc_modules)
        if not profiles.allows(profile, kind):
            raise refuse(f"{kind} is outside the profile {profile}",
                         f"{name} ({kind}) is not allowed to an agent in the profile {profile}")


def dispatch(name: str, raw: bytes, ctx: Context) -> dict:
    """Run one command and return the answer. Never raises."""
    params: dict = {}
    spec = COMMANDS.get(name)
    try:
        if spec is None:
            raise BadRequest(f"unknown command: {name}")
        params = _parse_input(raw, spec.max_input)
        _through(ctx, params, name, spec.redact)
        if not allowed(ctx.caller, spec.role, spec.right):
            _record(ctx, name, params, "denied", f"requires {spec.role}", spec.redact)
            raise _recorded(Forbidden(f"{name} requires the {spec.role} role"))
        if ctx.profile in profiles.AUTONOMOUS:
            autonomy.hold(ctx, name, params)
        data = spec.call(ctx, params)
        if spec.mutating:
            _record(ctx, name, params, "ok", redact=spec.redact, conn=ctx.conn)
        return {"ok": True, "data": data}
    except FleetError as error:
        if spec is not None and getattr(error, "audited", False):
            pass
        elif spec is not None and isinstance(error, Forbidden):
            # Refused by the command itself: approving one's own launch, say.
            _record(ctx, name, params, "denied", str(error), spec.redact, error.secrets)
        elif spec is not None and spec.mutating:
            _record(ctx, name, params, "error", str(error), spec.redact, error.secrets)
        return {"ok": False, "error": {"code": error.code, "message": str(error)}}
    except Exception:
        # The details go to the journal, not to the browser.
        journal.send(f"{name} failed", 3, FLEET_COMMAND=name,
                     FLEET_TRACEBACK=traceback.format_exc())
        message = "internal error, see the journal"
        return {"ok": False, "error": {"code": "internal", "message": message}}
    finally:
        ctx.close()


def load_commands() -> dict[str, Command]:
    """Every command, registered by importing the modules that declare them."""
    from . import (  # noqa: F401  (register the commands)
        agents,
        approvals,
        chat,
        checks,
        cmdb,
        files,
        health,
        metrics,
        moduledocs,
        notify,
        resources,
        runs,
        schedules,
        system,
        transfer,
        variables,
        workflows,
    )

    return COMMANDS


def main(argv: list[str], env: Mapping[str, str] = os.environ) -> int:
    load_commands()
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
    limit = COMMANDS[argv[1]].max_input if argv[1] in COMMANDS else MAX_INPUT
    answer = dispatch(argv[1], sys.stdin.buffer.read(limit + 1), ctx)
    json.dump(answer, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0
