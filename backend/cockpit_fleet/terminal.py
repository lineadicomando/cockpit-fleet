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
    fleet secret CREDENTIAL FIELD
    fleet help [AREA [VERB]]

The commands are read from the registry of the installed package, so that
help and completion need no call; what a role may do is the backend's to say.
"""

import argparse
import getpass
import json
import re
import shutil
import subprocess
import sys
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
ACTIVE = ("queued", "running")

# Exit status: what a script after "fleet launch" wants to tell apart.
OK, NOT_SUCCESSFUL, USAGE, FAILED = 0, 1, 2, 3

SHORTCUTS = {
    "launch": "Launch a job template and follow its run",
    "log": "The output of a run, or follow it with -f",
    "result": "How a run went, host by host and task by task",
    "secret": "Set the secret of a credential (admin)",
    "help": "What a command does and the parameters it takes",
}
# The columns a table shows first, when its rows have them.
_COLUMNS = ("id", "name", "status", "title", "kind", "user", "role", "command", "outcome",
            "via", "playbook", "path", "host", "template", "calendar", "enabled", "next_at",
            "created_by", "created_at", "finished_at", "ts", "rc", "revision", "url", "username")
_MAX_COLUMNS = 6
_MAX_CELL = 40
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Commands made for other programs: they work, they are not listed.
_UNLISTED = ("chat.start", "mcp.tools", "run.reconcile", "scheduler.tick")


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
                          " cockpit-fleet-viewer, -operator and -admin") from error
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


def _cell(key: str, value: object) -> str:
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


def complete(commands: dict[str, Command], words: list[str]) -> list[str]:
    """Candidates for the last of the words after "fleet", for the shell."""
    current = words[-1] if words else ""
    before = words[:-1]
    commands = {name: spec for name, spec in commands.items() if name not in _UNLISTED}
    areas = sorted({name.split(".")[0] for name in commands})
    if not before:
        candidates = [*SHORTCUTS, *areas]
    elif before[0] in SHORTCUTS:
        candidates = {"launch": ["--answer", "--limit", "--check", "--diff", "--detach"],
                      "log": ["--follow"], "result": ["--wait"],
                      "help": areas if len(before) == 1 else []}.get(before[0], [])
        if before[0] == "help" and len(before) == 2:
            candidates = sorted(name.split(".")[1] for name in commands
                                if name.startswith(f"{before[1]}."))
    elif len(before) == 1 and any(name.startswith(f"{before[0]}.") for name in commands):
        candidates = sorted(name.split(".", 1)[1] for name in commands
                            if name.startswith(f"{before[0]}."))
    else:
        found = find_command(commands, before)
        candidates = [] if found is None else [
            f"--{name.replace('_', '-')}" for name in found[0].params]
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
    parser.add_argument("--detach", action="store_true", help="do not follow the run")
    options = parser.parse_args(args)
    template = _one(term("template.list")["templates"], options.template, "template",
                    "fleet template list")
    answers = _answers(term, template, options.answer)
    run = term("run.start", {"template_id": template["id"], "limit": options.limit,
                             "check": options.check, "diff": options.diff, "answers": answers})
    if options.detach:
        term.show(run)
        return OK
    if not term.as_json:
        term.err.write(f"run {run['id']}: {run['title']}, {run['status']}\n")
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


def _drain(term: Terminal, run_id: str, offset: int = 0) -> None:
    more = True
    while more:
        chunk = term("run.log", {"id": run_id, "offset": offset})
        offset, more = chunk["offset"], chunk["more"]
        if not term.as_json:
            term.out.write(chunk["text"] if term.color else _ANSI_RE.sub("", chunk["text"]))
            term.out.flush()


def _log(term: Terminal, args: list[str]) -> int:
    parser = _parser("log", "The output of a run, as Ansible wrote it.")
    parser.add_argument("run", help="the id of the run")
    parser.add_argument("-f", "--follow", action="store_true",
                        help="go on while the run runs, then say how it went")
    options = parser.parse_args(args)
    if options.follow:
        return _follow(term, options.run)
    _drain(term, options.run)
    return OK


def _result(term: Terminal, args: list[str]) -> int:
    parser = _parser("result", "How a run went: per host, and task by task.")
    parser.add_argument("run", help="the id of the run")
    parser.add_argument("--wait", action="store_true", help="wait for the run to end")
    options = parser.parse_args(args)
    result = term("run.result", {"id": options.run, "wait": 50 if options.wait else 0})
    while options.wait and not result["done"]:
        result = term("run.result", {"id": options.run, "wait": 50})
    if term.as_json:
        term.show(result)
        return _status_code(result["status"])
    rc = "" if result["rc"] is None else f", rc {result['rc']}"
    term.say(f"\n{result['id']}  {result['title']}: {result['status']}{rc}")
    if result["error"]:
        term.say(f"error: {result['error']}")
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
    parser = _parser("secret", "Set the secret of a credential: asked for at the terminal,"
                     " or read from stdin. It cannot be read back.")
    parser.add_argument("credential", help="its name or its id")
    parser.add_argument("field", help="which secret: private_key, password, vault_password…")
    options = parser.parse_args(args)
    credential = _one(term("credential.list")["credentials"], options.credential, "credential",
                      "fleet credential list")
    names = credential["secret_names"]
    if options.field not in names:
        raise Usage(f"{credential['name']!r} has no secret {options.field!r}"
                    f" (it has: {', '.join(names)})")
    if term.interactive:
        value = term.ask(f"{options.field} of {credential['name']}", True)
        if "\n" not in value and value != term.ask("Again, to confirm", True):
            raise Usage("the two do not match")
    else:
        value = term.stdin.read()
        if value.count("\n") == 1 and value.endswith("\n"):
            value = value[:-1]  # one line, as echo writes it; a key keeps its last newline
    if not value:
        raise Usage("empty value")
    term.secret(["set", names[options.field]], value)
    if not term.as_json:
        term.say(f"{options.field} of {credential['name']} is set")
    return OK


def _help(term: Terminal, commands: dict[str, Command], args: list[str]) -> int:
    if args and args[0] in SHORTCUTS and args[0] != "help":
        return run(term, commands, [args[0], "--help"])
    found = find_command(commands, args)
    if found is not None:
        term.out.write(describe(found[0]))
        return OK
    if len(args) == 1 and any(name.startswith(f"{args[0]}.") for name in commands):
        names = sorted(name for name in commands
                       if name.startswith(f"{args[0]}.") and name not in _UNLISTED)
        for name in names:
            term.say(f"fleet {name.replace('.', ' '):<24}{summary(commands[name])}")
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
    shortcut = {"launch": _launch, "log": _log, "result": _result, "secret": _secret}
    if words[0] in shortcut:
        try:
            return shortcut[words[0]](term, words[1:])
        except SystemExit as done:  # --help of a shortcut
            return int(done.code or 0)
    found = find_command(commands, words)
    if found is None:
        raise Usage(f"no command {' '.join(words[:2])}: fleet help lists them")
    command, rest = found
    if "-h" in rest or "--help" in rest:
        term.out.write(describe(command))
        return OK
    term.show(term(command.name, parse_params(command, rest, term.stdin)))
    return OK


def main(argv: list[str]) -> int:
    commands = load_commands()
    words = argv[1:]
    if words[:1] == ["--complete"]:
        print("\n".join(complete(commands, words[1:])))
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
