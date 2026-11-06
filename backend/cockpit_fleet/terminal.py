"""fleet: the backend for a person at a terminal.

It runs as the user who types it, with nothing of its own: every command is a
call to fleetctl through sudo, as the interface makes it, so roles and the
audit trail are the same, and "via" is "cli" in the audit trail. Secrets are
asked for at the terminal, or read from stdin, and handed to fleet-secret on
its stdin: never on a command line.

    fleet AREA VERB [--param value]...   any command of fleetctl, for example
                                         fleet run list --status failed
    fleet launch TEMPLATE [-a NAME=VALUE]...
    fleet log RUN [-f]
    fleet result RUN [--wait]
    fleet secret CREDENTIAL FIELD | --channel CHANNEL
    fleet assistant [--profile PROFILE] [AGENT]
    fleet project clone PROJECT [DIR] | pull [DIR] | push [DIR]
    fleet inventory clone INVENTORY [DIR] | pull [DIR] | push [DIR]
    fleet help [AREA [VERB]]

The commands are read from the registry of the installed package, so that
help needs no call; what a role may do is the backend's to say. Where a
command takes the id of something that has a name, the name may be given
instead, and completion offers the names, which it asks the backend for.
"""

import argparse
import base64
import getpass
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .cli import Command, Field, load_commands
from .mcp import Unreachable, fleetctl
from .paths import LIBEXEC_DIR

VIA = "cli"
ACTIVE = ("pending_approval", "queued", "running")

# Exit status: what a script after "fleet launch" wants to tell apart.
OK, NOT_SUCCESSFUL, USAGE, FAILED = 0, 1, 2, 3

SHORTCUTS = {
    "launch": "Launch a job template and follow its run",
    "log": "The output of a run, or follow it with -f",
    "result": "How a run went, host by host and task by task",
    "secret": "Set the secret of a credential or a notification channel (admin)",
    "assistant": "Talk to the agent of the chat here, with the tools of fleet-mcp only",
    "help": "What a command does and the parameters it takes",
}
# The columns a table shows first, when its rows have them.
_COLUMNS = ("id", "name", "status", "state", "title", "event", "channel", "kind", "user",
            "role", "command", "outcome", "via", "playbook", "path", "host", "check", "summary",
            "template", "calendar", "enabled", "next_at", "created_by", "created_at",
            "finished_at", "ts", "rc", "revision", "url", "username")
_MAX_COLUMNS = 6
_MAX_CELL = 40
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Commands made for other programs: they work, they are not listed.
_UNLISTED = ("chat.start", "mcp.tools", "run.reconcile", "scheduler.tick",
             "notification.deliver")


# What the id of an area names, for the areas whose things have a name: the
# command that lists them and the key of the list.
_NAMED = {
    "template": ("template.list", "templates"), "project": ("project.list", "projects"),
    "inventory": ("inventory.list", "inventories"),
    "credential": ("credential.list", "credentials"), "check": ("check.list", "checks"),
    "host": ("host.list", "hosts"), "schedule": ("schedule.list", "schedules"),
    "channel": ("channel.list", "channels"), "rule": ("rule.list", "rules"),
    "varset": ("varset.list", "variable_sets"),
}
# Parameters that hold the id of another area; "id" is of the command's own
# area, but for the files, which are of a project.
_ID_PARAMS = {
    "template_id": "template", "project_id": "project", "inventory_id": "inventory",
    "credential_id": "credential", "machine_credential_id": "credential",
    "vault_credential_id": "credential", "check_id": "check", "host_id": "host",
    "schedule_id": "schedule", "channel_id": "channel",
}
_ID_OF_AREA = {"file": "project"}


def id_area(command: Command, param: str) -> str | None:
    """The area whose id a parameter of a command holds, if it is known."""
    if param == "id":
        area = command.name.split(".")[0]
        return _ID_OF_AREA.get(area, area)
    return _ID_PARAMS.get(param)


def _run_git(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """git as the user, in a clone of theirs."""
    try:
        done = subprocess.run(["/usr/bin/git", *args], cwd=cwd, capture_output=True, text=True,
                              check=False, stdin=subprocess.DEVNULL)
    except OSError as error:
        return 127, "", f"cannot run git: {error}"
    return done.returncode, done.stdout, done.stderr


class Usage(Exception):
    """The command line is wrong: said with how to do it right."""


class Failure(Exception):
    """The backend refused or could not be reached."""


def _fleet_secret(args: list[str], value: str) -> str:
    """fleet-secret, through sudo, with the value on its stdin."""
    argv = ["/usr/bin/sudo", "-n", str(LIBEXEC_DIR / "fleet-secret"), *args]
    try:
        result = subprocess.run(argv, input=value, capture_output=True, text=True, timeout=60,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Failure(f"cannot run fleet-secret: {error}") from error
    if result.returncode != 0:
        said = result.stderr.strip()[-300:]
        if "password is required" in said or "not allowed" in said:
            said = "your role cannot do this (sudo refused fleet-secret)"
        raise Failure(said or "fleet-secret failed")
    return result.stdout


def _ask(text: str, secret: bool) -> str:
    if secret:
        return getpass.getpass(f"{text}: ")
    return input(f"{text}: ")


@dataclass
class Terminal:
    """One invocation of fleet, with what it talks to: replaced in tests."""

    call: Callable[[str, dict], dict] = fleetctl
    secret: Callable[[list[str], str], str] = _fleet_secret
    ask: Callable[[str, bool], str] = _ask
    git: Callable[[list[str], str | None], tuple[int, str, str]] = _run_git
    # Runs a program on this terminal and gives its exit status.
    execute: Callable[[list[str]], int] = lambda argv: subprocess.run(argv, check=False).returncode
    sleep: Callable[[float], None] = time.sleep
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    interactive: bool = False
    color: bool = False
    width: int = 0
    as_json: bool = False

    def __call__(self, name: str, params: dict | None = None) -> dict:
        try:
            answer = self.call(name, {**(params or {}), "via": VIA})
        except Unreachable as error:
            raise Failure(f"{error}\nfleet works for the members of the groups"
                          " cockpit-fleet-viewer, -operator, -admin and -approver") from error
        if not answer["ok"]:
            raise Failure(f"{answer['error']['code']}: {answer['error']['message']}")
        return answer["data"]

    def say(self, text: str = "") -> None:
        self.out.write(text + "\n")

    def show(self, data: object) -> None:
        if self.as_json:
            self.say(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            self.out.write(render(data, self.width))


# --- output ---------------------------------------------------------------------------

def _when(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    return _cell("", value)


# Control characters, which a host name or what a node said may hold: in a
# table they would move the cursor, or with ESC set the title or the clipboard.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _cell(key: str, value: object) -> str:
    return _CONTROL_RE.sub("?", _text(key, value))


def _text(key: str, value: object) -> str:
    if key.endswith("_at") or key == "ts":
        return _when(value)
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value):
        return ", ".join(str(item) for item in value) or "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False) if value else "-"
    return str(value)


def _scalar(value: object) -> bool:
    return not isinstance(value, (dict, list))


def table(rows: list[dict], columns: list[str] | None = None, width: int = 0) -> str:
    """Rows as aligned columns: the known ones first, at most a few."""
    if not rows:
        return "(none)\n"
    if columns is None:
        first = rows[0]
        columns = [key for key in _COLUMNS if key in first and _scalar(first[key])]
        for key, value in first.items():
            if len(columns) >= _MAX_COLUMNS:
                break
            if key not in columns and _scalar(value):
                columns.append(key)
        columns = columns[:_MAX_COLUMNS]
    cells = [[_cell(key, row.get(key)) for key in columns] for row in rows]
    cells = [[text if len(text) <= _MAX_CELL else text[:_MAX_CELL - 1] + "…" for text in row]
             for row in cells]
    head = [key.upper().replace("_", " ") for key in columns]
    sizes = [max(len(row[index]) for row in [head, *cells]) for index in range(len(columns))]
    lines = []
    for row in [head, *cells]:
        line = "  ".join(text.ljust(size) for text, size in zip(row, sizes, strict=True)).rstrip()
        lines.append(line[:width] if width else line)
    return "\n".join(lines) + "\n"


def render(data: object, width: int = 0) -> str:
    """An answer of the backend for a person: its lists of records as tables,
    everything else as "key: value"."""
    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            return table(data, width=width)
        return "".join(f"{_cell('', item)}\n" for item in data)
    if not isinstance(data, dict):
        return f"{_cell('', data)}\n"
    lines, tables = [], []
    for key, value in data.items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            tables.append((key, value))
        elif key == "more":
            if value:
                cursor = next((row["cursor"] for row in reversed(data.get("runs", []))
                               if isinstance(row, dict) and "cursor" in row), None)
                lines.append("more: yes" + (f", --before {cursor} for the next page"
                                             if cursor else "") + "\n")
        elif isinstance(value, str) and "\n" in value:
            lines.append(f"{key}:\n" + "".join(f"  {line}\n" for line in value.splitlines()))
        else:
            lines.append(f"{key}: {_cell(key, value)}\n")
    parts = ["".join(lines)] if lines else []
    for key, rows in tables:
        title = f"{key}:\n" if lines or len(tables) > 1 else ""
        parts.append(title + table(rows, width=width))
    return "\n".join(parts)


# --- commands of the registry ---------------------------------------------------------

def find_command(commands: dict[str, Command],
                 words: list[str]) -> tuple[Command, list[str]] | None:
    """The command the first words name ("run list" or "whoami"), and the rest."""
    if len(words) >= 2 and f"{words[0]}.{words[1]}" in commands:
        return commands[f"{words[0]}.{words[1]}"], words[2:]
    if words and "." not in words[0] and words[0] in commands:
        return commands[words[0]], words[1:]
    return None


def _boolean(text: str, what: str) -> bool:
    lowered = text.lower()
    if lowered in ("true", "yes", "on", "1"):
        return True
    if lowered in ("false", "no", "off", "0"):
        return False
    raise Usage(f"{what} must be true or false")


def _content(text: str, stdin: TextIO) -> str:
    """A value, or the content of a file for "@path" ("@-" is stdin, "@@" a plain @)."""
    if text.startswith("@@"):
        return text[1:]
    if text == "@-":
        return stdin.read()
    if text.startswith("@"):
        try:
            return Path(text[1:]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise Usage(f"cannot read {text[1:]}: {error}") from error
    return text


def _value(spec: Field, option: str, text: str, before: object, stdin: TextIO) -> object:
    if spec.kind == "integer":
        try:
            return int(text)
        except ValueError:
            raise Usage(f"{option} takes an integer") from None
    if spec.kind == "strings":
        return [*(before or []), text]
    if spec.kind in ("object", "objects"):
        try:
            return json.loads(_content(text, stdin))
        except ValueError as error:
            raise Usage(f"{option} takes JSON: {error}") from None
    if spec.kind == "string":
        return _content(text, stdin)
    return text


def parse_params(command: Command, args: list[str], stdin: TextIO) -> dict:
    """The parameters of a command from "--name value" options; a lone word is
    the id, for the commands that take one."""
    params: dict = {}
    words: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if not arg.startswith("--") or arg == "--":
            words.append(arg)
            continue
        option, given, text = arg.partition("=")
        key = option[2:].replace("-", "_")
        negated = False
        spec = command.params.get(key)
        if spec is None and key.startswith("no_"):
            spec = command.params.get(key[3:])
            if spec is not None and spec.kind == "boolean":
                key, negated = key[3:], True
            else:
                spec = None
        if spec is None:
            raise Usage(f"{command.name} has no parameter {option}")
        if spec.kind == "boolean":
            params[key] = _boolean(text, option) if given else not negated
            continue
        if not given:
            if index >= len(args):
                raise Usage(f"{option} needs a value")
            text = args[index]
            index += 1
        params[key] = _value(spec, option, text, params.get(key), stdin)
    if words:
        if len(words) == 1 and "id" in command.params and "id" not in params:
            params["id"] = words[0]
        else:
            raise Usage(f"unexpected {' '.join(words)}: parameters are given as --name value")
    return params


def resolve_names(term: "Terminal", command: Command, params: dict) -> dict:
    """The parameters with the names given for ids replaced by those ids."""
    out = dict(params)
    for key, value in params.items():
        spec = command.params.get(key)
        area = id_area(command, key) if spec is not None and spec.kind == "id" else None
        if area == "draft" and isinstance(value, str):
            # A draft is of a project or an inventory written in the interface.
            rows = [*term("project.list")["projects"], *term("inventory.list")["inventories"]]
            out[key] = _one([row for row in rows if row.get("kind") == "managed"], value,
                            "project or inventory", "fleet project list")["id"]
            continue
        if area not in _NAMED or not isinstance(value, str):
            continue
        lister, listed = _NAMED[area]
        found = _one(term(lister)[listed], value, area, f"fleet {lister.replace('.', ' ')}")
        out[key] = found["id"]
    return out


def archive_params(args: list[str], stdin: TextIO) -> tuple[list[str], dict]:
    """--archive PATH of project upload: the file, or - for stdin, as the
    base64 the backend takes, and its name; the other arguments as they are."""
    rest: list[str] = []
    found: dict = {}
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg != "--archive" and not arg.startswith("--archive="):
            rest.append(arg)
            continue
        if "=" in arg:
            path = arg.partition("=")[2]
        elif index < len(args):
            path, index = args[index], index + 1
        else:
            raise Usage("--archive needs a file, or - for stdin")
        try:
            content = (getattr(stdin, "buffer", stdin).read() if path == "-"
                       else Path(path).read_bytes())
        except OSError as error:
            raise Usage(f"cannot read {path}: {error}") from error
        if isinstance(content, str):
            content = content.encode()
        found = {"data": base64.b64encode(content).decode("ascii")}
        if path != "-":
            found["filename"] = Path(path).name
    return rest, found


def _kind(spec: Field) -> str:
    if spec.kind == "choice":
        return "|".join(spec.choices)
    if spec.kind == "integer":
        return f"{spec.low}..{spec.high}"
    return {"string": "text", "boolean": "", "id": "id", "strings": "text, repeated",
            "objects": "JSON list", "object": "JSON"}[spec.kind]


def describe(command: Command) -> str:
    """The help of one command: what it does, who may and what it takes."""
    words = command.name.replace(".", " ")
    lines = [f"fleet {words} [--param value]...", ""]
    lines += [line.strip() for line in (command.handler.__doc__ or "").strip().splitlines()]
    lines += ["", f"Role: {command.role}" + (", recorded in the audit trail" if command.mutating
                                               else ", reads only")]
    if command.params:
        lines += ["", "Parameters:"]
        for name, spec in command.params.items():
            kind = _kind(spec)
            option = f"--{name.replace('_', '-')}" + (f" {kind}" if kind else "")
            notes = [spec.doc] if spec.doc else []
            if spec.required:
                notes.append("required")
            elif spec.default not in (None, "", False):
                notes.append(f"default {spec.default}")
            lines.append(f"  {option}" + (f"  {'; '.join(notes)}" if notes else ""))
        if "id" in command.params:
            lines += ["", "The id may also be given alone, without --id."]
        if any(spec.kind == "id" and id_area(command, name) in _NAMED
               for name, spec in command.params.items()):
            lines += ["An id may be given by the name of what it names."]
        if command.name == "project.upload":
            lines += ["--archive PATH reads the archive from a file (- for stdin) in place of"
                      " --data."]
    lines += ["", "A text or JSON value may be read from a file: @path, or @- for stdin.",
              "--json prints the answer as the backend gives it."]
    return "\n".join(lines) + "\n"


def summary(command: Command) -> str:
    """The first sentence of what a command does."""
    text = " ".join((command.handler.__doc__ or "").split())
    end = text.find(". ")
    return text[:end + 1] if end >= 0 else text


def overview(commands: dict[str, Command], allowed: list[str] | None) -> str:
    """What fleet can do, for the role of the caller when it is known."""
    lines = ["usage: fleet COMMAND [options]   (fleet help COMMAND for its options)", "",
             "Shortcuts:"]
    lines += [f"  {name:<10}{text}" for name, text in SHORTCUTS.items()]
    lines += ["", "Commands of the backend" + ("" if allowed is None else ", for your role") + ":"]
    names = sorted(name for name in commands
                   if name not in _UNLISTED and (allowed is None or name in allowed))
    for name in names:
        lines.append(f"  {name.replace('.', ' '):<22}{summary(commands[name])}")
    lines += ["", "Every command takes --json, to print the answer as the backend gives it."]
    return "\n".join(lines) + "\n"


def _names(lookup: Callable[[str, dict], dict] | None, area: str | None) -> list[str]:
    """What completion offers for an id of an area: names, or recent runs."""
    if lookup is None or area is None:
        return []
    try:
        if area == "run":
            return [run["id"] for run in lookup("run.list", {"limit": 30})["runs"]]
        if area in _NAMED:
            lister, listed = _NAMED[area]
            return sorted({row["name"] for row in lookup(lister, {})[listed]})
    except (Failure, KeyError, TypeError):
        pass
    return []


def _value_candidates(command: Command, option: str,
                      lookup: Callable[[str, dict], dict] | None) -> list[str] | None:
    """What may follow an option of a command; None if it takes no value."""
    spec = command.params.get(option[2:].replace("-", "_"))
    if spec is None or spec.kind == "boolean":
        return None
    if spec.kind == "choice":
        return list(spec.choices)
    if spec.kind == "id":
        return _names(lookup, id_area(command, option[2:].replace("-", "_")))
    return []


def _shortcut_candidates(before: list[str], current: str, areas: list[str],
                         commands: dict[str, Command],
                         lookup: Callable[[str, dict], dict] | None) -> list[str]:
    name, given = before[0], before[1:]
    positional = [word for word in given if not word.startswith("-")]
    if name == "help":
        if not given:
            return areas
        if len(given) == 1:
            return sorted(name.split(".")[1] for name in commands
                          if name.startswith(f"{given[0]}."))
        return []
    if current.startswith("-"):
        return {"launch": ["--answer", "--limit", "--check", "--diff", "--detach"],
                "log": ["--follow"], "result": ["--wait"], "secret": ["--channel"],
                "assistant": ["--profile"]}.get(name, [])
    if name == "assistant" and given and given[-1] == "--profile":
        try:
            return list(lookup("chat.agents", {})["profiles"]) if lookup is not None else []
        except (Failure, KeyError, TypeError):
            return []
    if given and given[-1] in ("--answer", "-a", "--limit"):
        return []
    if name == "launch" and not positional:
        return _names(lookup, "template")
    if name in ("log", "result") and not positional:
        return _names(lookup, "run")
    agent_named = [word for index, word in enumerate(given) if not word.startswith("-")
                   and not (index and given[index - 1] == "--profile")]
    if name == "assistant" and not agent_named and lookup is not None:
        try:
            return [agent["name"] for agent in lookup("chat.agents", {})["agents"]
                    if agent["terminal"]]
        except (Failure, KeyError, TypeError):
            return []
    if name == "secret":
        if "--channel" in given:
            return _names(lookup, "channel") if not positional else []
        if not positional:
            return _names(lookup, "credential")
        if len(positional) == 1 and lookup is not None:
            try:
                rows = lookup("credential.list", {})["credentials"]
                return sorted({field for row in rows if positional[0] in (row["id"], row["name"])
                               for field in row["secret_names"]})
            except (Failure, KeyError, TypeError):
                return []
    return []


def complete(commands: dict[str, Command], words: list[str],
             lookup: Callable[[str, dict], dict] | None = None) -> list[str]:
    """Candidates for the last of the words after "fleet", for the shell. With
    lookup, the names of what the ids name, asked for to the backend."""
    # What is typed of a name is still quoted or escaped as the shell takes it.
    current = re.sub(r"\\(.)", r"\1", (words[-1] if words else "").lstrip("'\""))
    before = words[:-1]
    commands = {name: spec for name, spec in commands.items() if name not in _UNLISTED}
    areas = sorted({name.split(".")[0] for name in commands})
    if not before:
        candidates = [*SHORTCUTS, *areas]
    elif before[0] in SHORTCUTS:
        candidates = _shortcut_candidates(before, current, areas, commands, lookup)
    elif len(before) == 1 and any(name.startswith(f"{before[0]}.") for name in commands):
        candidates = sorted(name.split(".", 1)[1] for name in commands
                            if name.startswith(f"{before[0]}."))
        if before[0] in LOCAL_AREAS:
            candidates = sorted([*candidates, *LOCAL_VERBS])
    elif before[0] in LOCAL_AREAS and before[1] in LOCAL_VERBS:
        candidates = _names(lookup, before[0]) if before[1] == "clone" and len(before) == 2 \
            and not current.startswith("-") else []
    else:
        found = find_command(commands, before)
        candidates = []
        if found is not None:
            command, given = found
            values = _value_candidates(command, given[-1], lookup) \
                if given and given[-1].startswith("--") and "=" not in given[-1] else None
            if values is not None:
                candidates = values
            elif current.startswith("-") or "id" not in command.params or any(
                    not word.startswith("-") for word in given):
                candidates = [f"--{name.replace('_', '-')}" for name in command.params]
                if command.name == "project.upload":
                    candidates.append("--archive")
            else:
                candidates = _names(lookup, id_area(command, "id"))
    if before:
        candidates = [*candidates, "--json"] if current.startswith("-") else candidates
    return [candidate for candidate in candidates if candidate.startswith(current)]


# --- shortcuts ------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise Usage(f"{message}\n{self.format_usage().strip()}")


def _parser(name: str, description: str) -> _Parser:
    return _Parser(prog=f"fleet {name}", description=description)


def _status_code(status: str) -> int:
    return OK if status in ("successful", *ACTIVE) else NOT_SUCCESSFUL


def _typed(question: dict, text: str) -> object:
    kind, name = question["type"], question["name"]
    if kind == "number":
        try:
            return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
        except ValueError:
            raise Usage(f"{name} takes a number") from None
    if kind == "boolean":
        return _boolean(text, name)
    return text


def _one(rows: list[dict], ref: str, what: str, lister: str) -> dict:
    found = [row for row in rows if row["id"] == ref]
    found = found or [row for row in rows if row["name"].casefold() == ref.casefold()]
    if not found:
        raise Failure(f"no {what} {ref!r}: {lister} lists them")
    if len(found) > 1:
        raise Failure(f"more than one {what} is called {ref!r}: give its id"
                      f" ({', '.join(row['id'] for row in found)})")
    return found[0]


def _answers(term: Terminal, template: dict, given: list[str]) -> dict:
    """The answers to the survey: from -a, and asked for at the terminal when
    one is required, or is a secret, which is never taken from the command line."""
    questions = {question["name"]: question for question in template["survey"]}
    answers: dict = {}
    for item in given:
        name, has, text = item.partition("=")
        if not has:
            raise Usage(f"-a takes NAME=VALUE, not {item!r}")
        question = questions.get(name)
        if question is None:
            asked = ", ".join(questions) or "none"
            raise Usage(f"{name} is not a question of {template['name']!r} (questions: {asked})")
        if question["type"] == "secret":
            raise Usage(f"{name} is secret: leave it out, and it is asked for at the terminal")
        answers[name] = _typed(question, text)
    for name, question in questions.items():
        if name in answers:
            continue
        secret = question["type"] == "secret"
        if not (question.get("required") and "default" not in question) and not secret:
            continue
        if not term.interactive:
            if question.get("required"):
                raise Usage(f"{name} needs an answer: give it with -a, or run fleet at a terminal"
                            + (" to be asked for it" if secret else ""))
            continue
        label = question.get("label") or name
        if question["type"] == "choice":
            label += f" ({', '.join(str(choice) for choice in question['choices'])})"
        if not question.get("required"):
            label += " (empty to leave it out)"
        text = term.ask(label, secret)
        if not text:
            if question.get("required"):
                raise Usage(f"{name} needs an answer")
            continue
        answers[name] = term.secret(["answer"], text).strip() if secret else _typed(question, text)
    return answers


def _launch(term: Terminal, args: list[str]) -> int:
    parser = _parser("launch", "Launch a job template, then follow its output and say how it"
                     " went. Secret questions are asked for at the terminal.")
    parser.add_argument("template", help="its name or its id")
    parser.add_argument("-a", "--answer", action="append", default=[], metavar="NAME=VALUE",
                        help="the answer to a question of the survey")
    parser.add_argument("--limit", default="", help="a host pattern that narrows the hosts")
    parser.add_argument("--check", action="store_true", help="dry run: change nothing")
    parser.add_argument("--diff", action="store_true", help="show the differences")
    parser.add_argument("--serial", action="store_true",
                        help="wait while another run is running on its inventory, or one"
                        " launched before it is queued there")
    parser.add_argument("--detach", action="store_true", help="do not follow the run")
    options = parser.parse_args(args)
    template = _one(term("template.list")["templates"], options.template, "template",
                    "fleet template list")
    answers = _answers(term, template, options.answer)
    run = term("run.start", {"template_id": template["id"], "limit": options.limit,
                             "check": options.check, "diff": options.diff, "answers": answers,
                             "serial": options.serial})
    if options.detach:
        term.show(run)
        return OK
    if not term.as_json:
        term.err.write(f"run {run['id']}: {run['title']}, {run['status']}\n")
        if run["status"] == "pending_approval":
            approval = term("run.get", {"id": run["id"]}).get("approval") or {}
            if approval.get("mode") == "confirm" and approval.get("can_decide"):
                term.err.write("It waits for your confirmation (fleet approval approve --id"
                               f" {run['id']}); Ctrl-C stops following it.\n")
            else:
                term.err.write("It starts once another user approves it (fleet approval"
                               f" approve --id {run['id']}); Ctrl-C stops following it.\n")
        elif run["status"] == "queued" and options.serial:
            ahead = term("run.get", {"id": run["id"]}).get("waiting_for")
            if ahead:
                term.err.write(f"It waits for run {ahead['id']} ({ahead['title']}) on the same"
                               " inventory; Ctrl-C stops following it.\n")
    return _follow(term, run["id"])


def _follow(term: Terminal, run_id: str) -> int:
    """The output of a run as it is written, then how it went."""
    offset = 0
    try:
        while True:
            chunk = term("run.log", {"id": run_id, "offset": offset})
            offset = chunk["offset"]
            if not term.as_json:
                term.out.write(chunk["text"] if term.color else _ANSI_RE.sub("", chunk["text"]))
                term.out.flush()
            if chunk["more"]:
                continue
            if chunk["text"]:
                continue  # the end of the output comes before the end of the run
            if term("run.get", {"id": run_id})["status"] not in ACTIVE:
                _drain(term, run_id, offset)  # what was written after the last read
                break
            term.sleep(1)
    except KeyboardInterrupt:
        term.err.write(f"\nThe run goes on: fleet log -f {run_id} follows it again,"
                       f" fleet run cancel {run_id} cancels it.\n")
        return 130
    return _result(term, [run_id])


def _drain(term: Terminal, run_id: str, offset: int = 0) -> int:
    more = True
    while more:
        chunk = term("run.log", {"id": run_id, "offset": offset})
        offset, more = chunk["offset"], chunk["more"]
        if not term.as_json:
            term.out.write(chunk["text"] if term.color else _ANSI_RE.sub("", chunk["text"]))
            term.out.flush()
    return offset


def _pruned(run: dict) -> str:
    return f"its output was deleted on {_when(run['pruned_at'])}: only the outcome is kept"


def _log(term: Terminal, args: list[str]) -> int:
    parser = _parser("log", "The output of a run, as Ansible wrote it.")
    parser.add_argument("run", help="the id of the run")
    parser.add_argument("-f", "--follow", action="store_true",
                        help="go on while the run runs, then say how it went")
    options = parser.parse_args(args)
    if options.follow:
        return _follow(term, options.run)
    if not _drain(term, options.run):
        run = term("run.get", {"id": options.run})
        if run.get("pruned_at"):
            term.err.write(f"{run['id']}: {_pruned(run)}\n")
    return OK


def _result(term: Terminal, args: list[str]) -> int:
    parser = _parser("result", "How a run went: per host, and task by task.")
    parser.add_argument("run", help="the id of the run")
    parser.add_argument("--wait", action="store_true", help="wait for the run to end")
    options = parser.parse_args(args)
    result = term("run.result", {"id": options.run, "wait": 50 if options.wait else 0})
    while options.wait and not result["done"]:
        if result["status"] == "pending_approval":
            # run.result does not hold a call while a person has to decide.
            term.sleep(5)
        result = term("run.result", {"id": options.run, "wait": 50})
    if term.as_json:
        term.show(result)
        return _status_code(result["status"])
    rc = "" if result["rc"] is None else f", rc {result['rc']}"
    term.say(f"\n{result['id']}  {result['title']}: {result['status']}{rc}")
    if result["error"]:
        term.say(f"error: {result['error']}")
    if result.get("pruned_at"):
        term.say(_pruned(result))
    stats = result["stats"]
    if stats:
        columns = ["host", "ok", "changed", "failures", "unreachable", "skipped"]
        rows = [{"host": host, **counts} for host, counts in stats.items()]
        term.say()
        term.out.write(table(rows, columns, term.width))
    if result["tasks"]:
        columns = ["play", "task", "ok", "changed", "failed", "unreachable", "skipped"]
        term.say()
        term.out.write(table(result["tasks"], columns, term.width))
        for task in result["tasks"]:
            for host, said in task["problems"].items():
                term.say(f"  {task['task']} on {host}: {said}")
    return _status_code(result["status"])


def _secret(term: Terminal, args: list[str]) -> int:
    parser = _parser("secret", "Set the secret of a credential, or of a notification channel:"
                     " asked for at the terminal, or read from stdin. It cannot be read back.")
    parser.add_argument("credential", help="its name or its id")
    parser.add_argument("field", nargs="?",
                        help="which secret: private_key, password, vault_password…")
    parser.add_argument("--channel", action="store_true",
                        help="a notification channel, which has one secret, not a credential")
    options = parser.parse_args(args)
    if options.channel:
        if options.field:
            raise Usage("a channel has one secret: give no field")
        channel = _one(term("channel.list")["channels"], options.credential, "channel",
                       "fleet channel list")
        name, label = channel["secret_name"], f"secret of {channel['name']}"
    else:
        if not options.field:
            raise Usage("which secret of the credential? (or --channel for a channel)")
        credential = _one(term("credential.list")["credentials"], options.credential,
                          "credential", "fleet credential list")
        names = credential["secret_names"]
        if options.field not in names:
            raise Usage(f"{credential['name']!r} has no secret {options.field!r}"
                        f" (it has: {', '.join(names)})")
        name, label = names[options.field], f"{options.field} of {credential['name']}"
    if term.interactive:
        value = term.ask(label, True)
        if "\n" not in value and value != term.ask("Again, to confirm", True):
            raise Usage("the two do not match")
    else:
        value = term.stdin.read()
        if value.count("\n") == 1 and value.endswith("\n"):
            value = value[:-1]  # one line, as echo writes it; a key keeps its last newline
    if not value:
        raise Usage("empty value")
    term.secret(["set", name], value)
    if not term.as_json:
        term.say(f"{label} is set")
    return OK


# What an agent may do in each profile, and the risks a person accepts once
# before a session in a profile beyond assisted: as the chat page says them.
PROFILE_TEXT = {
    "observer": "It reads what you may read, and launches and changes nothing.",
    "assisted": "It reads, launches the job templates, workflows, schedules and checks that"
                " are defined, and cancels runs, asking you before each of these.",
    "diagnostic": "As assisted, and it runs ad-hoc commands of modules that only read, without"
                  " become, without asking you.",
    "autonomous": "It reads and launches what is defined without asking you; Fleet holds it to"
                  " a few runs in a session, nothing on an inventory in maintenance, and it"
                  " never silences a problem.",
    "administrator": "Whatever your role allows: ad-hoc commands, changes to templates,"
                     " inventories and the rest, files; it asks you before every change, and"
                     " files it writes go to a draft for you to review (fleet draft).",
}
RISKS = {
    "diagnostic": ["Ad-hoc commands run on the managed nodes without a question; what they read"
                   " goes to the provider of the model."],
    "autonomous": ["Nobody confirms its launches: a playbook it launches runs on the nodes as"
                   " soon as it asks for it.",
                   "Only the limits of Fleet hold it, not your judgement."],
    "administrator": ["It may change templates, inventories, schedules and the rest, and run"
                      " any ad-hoc command your role allows, once you confirm; a change you"
                      " confirm is made in your name."],
}
STEERING = ("The output of runs and commands is written by the managed nodes: whoever controls"
            " a node can write there text meant to steer the assistant.")


def _accept(term: Terminal, profile: str) -> None:
    """Tell the risks of PROFILE and record that they are accepted, or stop."""
    term.say(f"The profile {profile}: {PROFILE_TEXT.get(profile, '')}")
    term.say("Before the first session in it:")
    for risk in [*RISKS.get(profile, []), STEERING]:
        term.say(f"  - {risk}")
    term.say("This is asked once, and recorded in the audit trail.")
    if term.ask("Type yes to accept", False).strip().lower() != "yes":
        raise Failure(f"the risks of the profile {profile} were not accepted")
    term("chat.accept", {"profile": profile})


def _assistant(term: Terminal, args: list[str]) -> int:
    parser = _parser("assistant", "Talk to the agent of the chat in this terminal. Its only"
                     " tools are those of fleet-mcp, with your role under the ceiling set for"
                     " agents and what its profile allows; what you write goes to the"
                     " provider of its model.")
    parser.add_argument("agent", nargs="?", help="which agent, when there is more than one")
    parser.add_argument("--profile", help="the profile of the assistant; by default the one"
                        " config.toml names")
    options = parser.parse_args(args)
    if not term.interactive:
        raise Usage("the assistant needs a terminal")
    found = term("chat.agents")
    if found["unavailable"]:
        raise Failure(found["unavailable"])
    profile = options.profile or found.get("default_profile", "")
    enabled = found.get("profiles", [])
    if profile and enabled and profile not in enabled:
        raise Usage(f"the profile {profile} is not enabled here: " + ", ".join(enabled))
    agents = [agent for agent in found["agents"] if agent["terminal"]]
    if options.agent:
        agents = [agent for agent in agents if agent["name"] == options.agent]
    if not agents:
        raise Failure("no agent has a program for a terminal (terminal in [chat.agents] of"
                      " config.toml)" if not options.agent else
                      f"no agent {options.agent!r} with a program for a terminal")
    if len(agents) > 1:
        raise Usage("which agent? " + ", ".join(agent["name"] for agent in agents))
    agent = agents[0]
    if not agent["installed"]:
        raise Failure(f"the agent {agent['name']} is not installed"
                      + (f": {agent['setup']}" if agent["setup"] else ""))
    if profile in found.get("to_accept", []):
        _accept(term, profile)
    code = term.execute([str(LIBEXEC_DIR / "fleet-chat"), "--terminal",
                         *(["--profile", profile] if profile else []), agent["name"]])
    if code not in (OK, 130) and agent["login"]:
        term.err.write(f"If the agent is not logged in to its model: {agent['login']}\n")
    return code


# --- projects and inventories written in the interface, at a terminal -----------------------

# Areas whose things written in the interface are cloned, pulled and pushed.
LOCAL_AREAS = ("project", "inventory")
# Verbs of those areas that fleet does itself, with the bundles of the backend.
LOCAL_VERBS = {
    "clone": "Clone a {area} written in the interface, to work on it with git",
    "pull": "Bring into a clone what was written in the interface since",
    "push": "Send the commits of a clone to its {area}, as if written in the interface",
}
# The last revision of the project a clone knows: what a push builds on.
_BASE = "refs/remotes/fleet/main"


def _git(term: Terminal, args: list[str], cwd: str | None = None) -> str:
    code, out, err = term.git(args, cwd)
    if code != 0:
        raise Failure(f"git {args[0]}: {err.strip()[-300:] or 'failed'}")
    return out


def _clone_of(term: Terminal, directory: str, area: str) -> str:
    """The id of the project or inventory a clone was made of, which must
    be of AREA."""
    code, out, _err = term.git(["config", "--get", f"fleet.{area}"], directory)
    if code != 0 or not out.strip():
        raise Usage(f"{directory} is not a clone made by fleet {area} clone")
    return out.strip()


def _fetch(term: Terminal, project_id: str, directory: str) -> str:
    """The project's repository fetched into the clone; its revision."""
    data = term("file.bundle", {"id": project_id})
    with tempfile.TemporaryDirectory(prefix="fleet-bundle-") as work:
        bundle = Path(work) / "project.bundle"
        bundle.write_bytes(base64.b64decode(data["bundle"]))
        _git(term, ["fetch", "--quiet", "--no-tags", str(bundle), f"+main:{_BASE}"], directory)
    return data["revision"]


def clone_directory(project: dict) -> str:
    """Where a clone goes by default. The name is another administrator's
    choice: as a directory, only a plain one, in the current one."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", project["name"]).lstrip("-.") or project["id"]


def _local_clone(term: Terminal, area: str, args: list[str]) -> int:
    parser = _parser(f"{area} clone", f"Clone a {area} written in the interface, to work on"
                     f" it with git and push the commits back with fleet {area} push.")
    parser.add_argument(area, help="its name or its id")
    parser.add_argument("directory", nargs="?", help=f"where; the name of the {area} by default")
    options = parser.parse_args(args)
    plural = "inventories" if area == "inventory" else f"{area}s"
    thing = _one(term(f"{area}.list")[plural], getattr(options, area), area,
                 f"fleet {area} list")
    if thing["kind"] != "managed":
        raise Usage(f"{thing['name']} is not written in the interface: only such a {area}"
                    " is cloned here" + (" (a git project is cloned from its URL)"
                                         if area == "project" else ""))
    directory = options.directory or clone_directory(thing)
    if Path(directory).exists():
        raise Usage(f"{directory} exists already")
    _git(term, ["init", "--quiet", "--initial-branch=main", "--", directory])
    _git(term, ["config", f"fleet.{area}", thing["id"]], directory)
    revision = _fetch(term, thing["id"], directory)
    _git(term, ["reset", "--quiet", "--hard", _BASE], directory)
    if not term.as_json:
        term.say(f"{thing['name']} is in {directory}, at {revision[:12]}: commit there, then"
                 f" fleet {area} push")
    else:
        term.show({area: thing["id"], "directory": directory, "revision": revision})
    return OK


def _local_pull(term: Terminal, area: str, args: list[str]) -> int:
    parser = _parser(f"{area} pull", "Bring into a clone what was written in the interface"
                     " since it was cloned or last pulled.")
    parser.add_argument("directory", nargs="?", default=".", help="the clone; here by default")
    options = parser.parse_args(args)
    thing_id = _clone_of(term, options.directory, area)
    revision = _fetch(term, thing_id, options.directory)
    code, _out, _err = term.git(["merge", "--quiet", "--ff-only", _BASE], options.directory)
    if code != 0:
        raise Failure(f"the {area} changed and so did your commits: git rebase fleet/main,"
                      f" then fleet {area} push")
    if not term.as_json:
        term.say(f"up to date with the {area}, at {revision[:12]}")
    return OK


def _local_push(term: Terminal, area: str, args: list[str]) -> int:
    parser = _parser(f"{area} push", "Send the commits of the branch main of a clone to its"
                     f" {area}, as if written in the interface. Taken only on top of what the"
                     f" {area} has: when it changed meanwhile, pull first.")
    parser.add_argument("directory", nargs="?", default=".", help="the clone; here by default")
    options = parser.parse_args(args)
    directory = options.directory
    project_id = _clone_of(term, directory, area)
    base = _git(term, ["rev-parse", "--verify", f"{_BASE}^{{commit}}"], directory).strip()
    head = _git(term, ["rev-parse", "--verify", "main^{commit}"], directory).strip()
    if head == base:
        term.say(f"nothing to push: main is where the {area} is")
        return OK
    if _git(term, ["status", "--porcelain"], directory).strip():
        term.err.write("fleet: what is not committed stays here\n")
    with tempfile.TemporaryDirectory(prefix="fleet-bundle-") as work:
        bundle = Path(work) / "push.bundle"
        _git(term, ["bundle", "create", "--quiet", str(bundle), f"{base}..main"], directory)
        data = base64.b64encode(bundle.read_bytes()).decode("ascii")
    answer = term("file.push", {"id": project_id, "expected": base, "bundle": data})
    _git(term, ["update-ref", _BASE, answer["revision"]], directory)
    if term.as_json:
        term.show(answer)
        return OK
    term.say(f"pushed: the {area} is at {answer['revision'][:12]},"
             f" {len(answer['files'])} file{'s' if len(answer['files']) != 1 else ''} changed")
    for run_id in answer["check_run_ids"]:
        term.say(f"syntax check: fleet result {run_id}")
    for run_id in answer.get("inventory_run_ids", []):
        term.say(f"inventory read again: fleet result {run_id}")
    return OK


def _help(term: Terminal, commands: dict[str, Command], args: list[str]) -> int:
    if args and args[0] in SHORTCUTS and args[0] != "help":
        return run(term, commands, [args[0], "--help"])
    found = find_command(commands, args)
    if found is not None:
        term.out.write(describe(found[0]))
        return OK
    if args[:1] and args[0] in LOCAL_AREAS and len(args) == 2 and args[1] in LOCAL_VERBS:
        return run(term, commands, [args[0], args[1], "--help"])
    if len(args) == 1 and any(name.startswith(f"{args[0]}.") for name in commands):
        names = sorted(name for name in commands
                       if name.startswith(f"{args[0]}.") and name not in _UNLISTED)
        for name in names:
            term.say(f"fleet {name.replace('.', ' '):<24}{summary(commands[name])}")
        if args[0] in LOCAL_AREAS:
            for verb, text in LOCAL_VERBS.items():
                term.say(f"fleet {args[0] + ' ' + verb:<24}{text.format(area=args[0])}")
        return OK
    if args:
        raise Usage(f"no command {' '.join(args)}: fleet help lists them")
    try:
        allowed = term("whoami")["commands"]
    except Failure:
        allowed = None
    term.out.write(overview(commands, allowed))
    return OK


def run(term: Terminal, commands: dict[str, Command], words: list[str]) -> int:
    """One command line, without "fleet"."""
    if not words or words[0] in ("help", "-h", "--help"):
        return _help(term, commands, words[1:])
    shortcut = {"launch": _launch, "log": _log, "result": _result, "secret": _secret,
                "assistant": _assistant}
    if words[0] in shortcut:
        try:
            return shortcut[words[0]](term, words[1:])
        except SystemExit as done:  # --help of a shortcut
            return int(done.code or 0)
    if words[:1] and words[0] in LOCAL_AREAS and len(words) >= 2 and words[1] in LOCAL_VERBS:
        verb = {"clone": _local_clone, "pull": _local_pull, "push": _local_push}[words[1]]
        try:
            return verb(term, words[0], words[2:])
        except SystemExit as done:  # --help
            return int(done.code or 0)
    found = find_command(commands, words)
    if found is None:
        raise Usage(f"no command {' '.join(words[:2])}: fleet help lists them")
    command, rest = found
    if "-h" in rest or "--help" in rest:
        term.out.write(describe(command))
        return OK
    extra: dict = {}
    if command.name == "project.upload":
        rest, extra = archive_params(rest, term.stdin)
    params = {**extra, **parse_params(command, rest, term.stdin)}
    params = resolve_names(term, command, params)
    data = term(command.name, params)
    if command.name == "draft.diff" and not term.as_json:
        term.out.write(draft_text(params["id"], data))
    else:
        term.show(data)
    return OK


def draft_text(container: str, data: dict) -> str:
    """What a draft changes, for a person at a terminal: the files, the syntax
    checks, the diff, and how to merge exactly what was shown."""
    lines = [f"Draft {data['draft']} by {data['author']}, last on {_when(data['time'])}."
             " No run uses it until it is merged.", ""]
    for change in data["changes"]:
        conflict = "  (changed in main too: cannot be merged)" if change["conflict"] else ""
        path = change["path"] + ("/" if change["directory"] else "")
        lines.append(f"  {change['status']:<8} {_cell('', path)}{conflict}")
    if not data["changes"]:
        lines.append("  main already has everything in this draft")
    if data["checks"]:
        lines += ["", "Syntax checks of the draft:"]
        for check in data["checks"]:
            note = "" if check["current"] else "  (of an earlier version)"
            lines.append(f"  {check['status']:<10} {_cell('', check['path'])}  run"
                         f" {check['run_id']}{note}")
    text = _CONTROL_RE.sub(lambda found: found.group() if found.group() in "\n\t" else "?",
                           data["text"])
    lines += ["", text.rstrip("\n")] if text else []
    if data["cut"]:
        lines.append("(only the beginning of the change)")
    if data["changes"]:
        lines += ["", f"fleet draft merge --id {container} --draft {data['draft']}"
                      f" --revision {data['revision']}   (--paths to take some files only)",
                  f"fleet draft discard --id {container} --draft {data['draft']}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    commands = load_commands()
    words = argv[1:]
    if words[:1] == ["--complete"]:
        def lookup(name: str, params: dict) -> dict:
            try:
                answer = fleetctl(name, {**params, "via": VIA})
            except Unreachable as error:
                raise Failure(str(error)) from error
            if not answer["ok"]:
                raise Failure(answer["error"]["message"])
            return answer["data"]
        print("\n".join(complete(commands, words[1:], lookup)))
        return OK
    as_json = "--json" in words
    words = [word for word in words if word != "--json"]
    term = Terminal(interactive=sys.stdin.isatty(), color=sys.stdout.isatty(),
                    width=shutil.get_terminal_size((0, 0)).columns if sys.stdout.isatty() else 0,
                    as_json=as_json)
    try:
        return run(term, commands, words)
    except Usage as error:
        print(f"fleet: {error}", file=sys.stderr)
        return USAGE
    except Failure as error:
        print(f"fleet: {error}", file=sys.stderr)
        return FAILED
    except KeyboardInterrupt:
        return 130
