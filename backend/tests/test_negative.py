"""Negative tests over the whole registry: role escalation, injection,
traversal and secrets.

The other test modules show what each command does when it is used as
meant. These ones take every command in turn and give it what an attacker
would: a role below its own, values made to become options, paths, shell or
template text, the wrong types, and they look at what came out: the answer,
the audit trail, the journal, the command lines that were spawned, and a
directory that nothing may write to.
"""

import base64
import dataclasses
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from test_checks import CheckBase
from test_notify import WEBHOOK

from cockpit_fleet import cli, journal, paths, runner, schedules
from cockpit_fleet.auth import ROLES, Caller
from cockpit_fleet.errors import BadRequest

cli.load_commands()

# Every command with the least role that may call it, and whether it is
# audited as a change. A command added, or one whose role or kind changes,
# fails here until this table says so: lowering a role is a decision.
ROLE_TABLE = """
approval.approve admin M      approval.list viewer -        approval.reject admin M
audit.list admin -            channel.create admin M        channel.delete admin M
channel.list viewer -         channel.test admin M          channel.update admin M
chat.agents viewer -          chat.start viewer M           check.catalog viewer -
check.create admin M          check.delete admin M          check.list viewer -
check.run operator M          check.update admin M          cmdb.get viewer -
cmdb.list viewer -            cmdb.packages viewer -        config.export admin -
config.import admin M         config.show admin -           credential.create admin M
credential.delete admin M     credential.list viewer -      credential.update admin M
drift.get operator -          file.bundle admin M           file.check admin M
file.delete admin M           file.diff admin -             file.history admin -
file.list admin -             file.move admin M             file.push admin M
file.read admin -             file.write admin M            host.get viewer -
file.change admin M           file.download admin M         file.restore admin M
file.import admin M
host.list viewer -            hostkey.add admin M           hostkey.list viewer -
hostkey.remove admin M        hostkey.scan admin -          inventory.create admin M
inventory.delete admin M      inventory.get viewer -        inventory.list viewer -
inventory.parse operator M    inventory.update admin M      maintenance.create operator M
maintenance.delete operator M maintenance.list viewer -     mcp.tools viewer -
metric.query viewer -         module.doc admin -            module.help admin -
module.list admin -           notification.deliver admin -  notification.list viewer -
notification.retry admin M    project.collections admin M   project.create admin M
project.delete admin M        project.files admin -         project.list viewer -
project.sync admin M          project.update admin M        project.upload admin M
rule.create admin M           rule.delete admin M           rule.list viewer -
rule.update admin M           run.adhoc admin M             run.cancel operator M
run.events operator -         run.get viewer -              run.list viewer -
run.log operator -            run.reconcile admin -         run.result operator -
run.start operator M          schedule.create admin M       schedule.delete admin M
schedule.disable operator M   schedule.enable operator M    schedule.list viewer -
schedule.preview viewer -     schedule.run operator M       schedule.update admin M
scheduler.tick admin -        selfcheck viewer -            state.ack operator M
state.list viewer -           state.unack operator M        system.health viewer -
template.create admin M       template.delete admin M       template.list viewer -
template.update admin M       whoami viewer -               workflow.create admin M
workflow.delete admin M       workflow.launch operator M    workflow.list viewer -
workflow.progress viewer -    workflow.resume operator M    workflow.update admin M
"""
EXPECTED = {}
_words = ROLE_TABLE.split()
for _index in range(0, len(_words), 3):
    EXPECTED[_words[_index]] = (_words[_index + 1], _words[_index + 2] == "M")

# The commands a right opens, whatever the role.
RIGHTS = {"approval.approve": "approve", "approval.list": "approve",
          "approval.reject": "approve", "whoami": "approve"}

# Commands that may write to the database without being a change of their
# own: they keep it in step with what happened meanwhile, for whoever looks.
HOUSEKEEPING = {"run.reconcile", "scheduler.tick", "notification.deliver", "run.list",
                "run.get", "run.result", "workflow.progress", "approval.list", "state.list",
                "selfcheck", "system.health", "hostkey.scan", "module.list", "module.doc",
                "module.help", "check.list", "host.list", "host.get", "run.log", "run.events"}

TARBALL = base64.b64encode(b"\x1f\x8b" + bytes(30)).decode()
PUBLIC_KEY = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519" + bytes(36)).decode()


def canary_values(canary: Path) -> list[str]:
    """Strings made to do harm if they reach a shell, a command line, a path
    or a template, each of them aimed at CANARY."""
    climb = "../" * 24 + str(canary).lstrip("/")
    touch = f"touch {canary}"
    return [
        climb, str(canary), f"a/{climb}", f"{climb}/x.yml",
        "-PWNED", "--PWNED=1", "-oProxyCommand=" + touch, "--upload-pack=" + touch,
        f"$({touch})", f"`{touch}`", f"x; {touch}", f"x\n{touch}", f"x | {touch}",
        "x\r\nX-PWNED: 1", "x\nPWNED ssh-ed25519 " + PUBLIC_KEY,
        "{{ lookup('pipe', '" + touch + "') }}", "{% for x in range(9) %}PWNED{% endfor %}",
        "ext::sh -c " + touch.replace(" ", "% "), "file://" + str(canary),
        "@" + str(canary), "[a]:22,*", "a,*", "*", "all:!PWNED", ".git", ".git/config",
        "./.git/hooks/post-checkout", "a\\..\\..\\PWNED", "%2e%2e%2fPWNED",
        "\x1b]0;PWNED\x07", "\u202ePWNED", "\ufeffPWNED", "", " ", "'\"<>&",
    ]


# Values of the wrong type, by the kind of the parameter.
WRONG_TYPES = {
    "string": [1, True, [], {}, None],
    "integer": [-1, 2**64, -(2**64), 1.5, "1", True, None, [], {}],
    "boolean": ["true", 1, 0, None, [], {}],
    "id": ["../x", "X", "-x", "x" * 65, "x.y", "x\n", "x/y", "", 1, [], None],
    "strings": ["x", [1], [None], [[]], [{}], {}, None],
    "objects": ["x", [1], ["x"], [None], {}, None],
    "object": ["x", [], 1, None],
    "choice": ["PWNED", "", 1, None, []],
}


def leaves(value, path=()):
    """Every place inside a JSON value, leaves and what holds them."""
    yield path
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


def nested(depth: int):
    value: object = "PWNED"
    for index in range(depth):
        value = {"a": value} if index % 2 else [value]
    return value


def replaced(value, path, leaf):
    """VALUE, a copy, with LEAF at PATH."""
    if not path:
        return leaf
    copy = json.loads(json.dumps(value))
    target = copy
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = leaf
    return copy


class Recorder:
    """subprocess.run as the backend sees it: git really runs, since files
    are committed with it; anything else is recorded and answered with
    nothing, as a program that did no harm would."""

    REAL = staticmethod(subprocess.run)

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, *args, **kwargs):
        if kwargs.get("shell") or not isinstance(argv, list):
            raise AssertionError(f"spawned through a shell: {argv!r}")
        if not all(isinstance(item, str) for item in argv):
            raise AssertionError(f"argv with something that is not a string: {argv!r}")
        self.calls.append(argv)
        if argv[0] == "/usr/bin/git":
            return self.REAL(argv, *args, **kwargs)
        text = (kwargs.get("text") or kwargs.get("universal_newlines")
                or kwargs.get("encoding"))
        empty = "" if text else b""
        return subprocess.CompletedProcess(argv, 1, empty, empty)


def options_from(argv: list[str], hostile: list[str]) -> list[str]:
    """The hostile values that a command line reads as options: those that
    start an argument of their own, before any "--"."""
    starts = tuple(value for value in hostile if value.startswith("-"))
    found = []
    for item in argv[1:]:
        if item == "--":
            break
        if item.startswith(starts):
            found.append(item)
    return found


class Fixture(CheckBase):
    """A controller with one of everything, so that a command given an id
    reaches past the lookup of what it names."""

    def setUp(self):
        super().setUp()
        self.canary_dir = Path(tempfile.mkdtemp(prefix="fleet-neg-"))
        self.addCleanup(shutil.rmtree, self.canary_dir, True)
        self.canary = self.canary_dir / "PWNED"
        self.journal: list[tuple] = []
        self.recorder = Recorder()

        def analyze(calendar, timezone="", count=1):
            if calendar != "hourly":
                raise BadRequest(f"not a valid OnCalendar expression: {calendar}")
            return calendar, [self.clock + 3600 * (index + 1) for index in range(count)]

        patches = [
            mock.patch.object(journal, "send", lambda *a, **k: self.journal.append((a, k))),
            mock.patch.object(subprocess, "run", self.recorder),
            mock.patch.object(schedules, "analyze", analyze),
            mock.patch.object(paths, "ANSWERS_DIR", self.root / "answers"),
            mock.patch.object(paths, "CACHE_DIR", self.root / "cache"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        (self.root / "answers").mkdir()
        self.fx = self.build()

    def build(self) -> dict:
        fx = {}
        project = self.local_project()
        fx["project"], fx["project_path"] = project["id"], project["path"]
        if os.path.exists("/usr/bin/git"):
            managed = self.ok("project.create", {"name": "edited", "kind": "managed"})
            fx["managed"] = managed["id"]
            fx["revision"] = self.ok("file.write", {
                "id": managed["id"], "path": "site.yml",
                "content": "- hosts: all\n  tasks: []\n", "expected": ""})["revision"]
        fx["git"] = self.ok("project.create", {"name": "remote", "kind": "git",
                                               "url": "https://example.org/a.git"})["id"]
        fx["archive"] = self.ok("project.create", {"name": "packed", "kind": "archive"})["id"]
        inventory = self.inventory()
        fx["inventory"] = inventory["id"]
        machine = self.ok("credential.create", {"name": "ssh", "kind": "machine",
                                                "username": "maint"})
        vault = self.ok("credential.create", {"name": "vault", "kind": "vault"})
        fx["credential"], fx["vault"] = machine["id"], vault["id"]
        template = self.ok("template.create", {
            "name": "deploy", "project_id": project["id"], "playbook": "site.yml",
            "inventory_id": inventory["id"], "machine_credential_id": machine["id"],
            "survey": [{"name": "colour", "type": "text"},
                       {"name": "token", "type": "secret"}]})
        fx["template"] = template["id"]
        gated = self.ok("template.create", {
            "name": "gated", "project_id": project["id"], "playbook": "site.yml",
            "inventory_id": inventory["id"], "needs_approval": True})
        check = self.check(inventory)
        fx["check"] = check["id"]
        hosts = self.ok("host.list", {"inventory_id": inventory["id"]})["hosts"]
        fx["host"] = hosts[0]["id"]
        channel = self.channel(base=WEBHOOK, secret="")
        fx["channel"] = channel["id"]
        fx["rule"] = self.rule(channel)["id"]
        fx["schedule"] = self.ok("schedule.create", {"name": "hourly",
                                                     "template_id": template["id"],
                                                     "calendar": "hourly"})["id"]
        fx["workflow"] = self.ok("workflow.create", {"name": "release", "nodes": [
            {"id": "a", "template_id": template["id"]}], "edges": []})["id"]
        fx["maintenance"] = self.ok("maintenance.create", {
            "inventory_id": inventory["id"], "group": "all",
            "ends_at": int(self.clock) + 3600})["id"]
        self.ok("hostkey.add", {"host": "web01", "key_type": "ssh-ed25519", "key": PUBLIC_KEY})
        fx["run"] = self.ok("run.start", {"template_id": template["id"]}, role="operator")["id"]
        fx["pending"] = self.ok("run.start", {"template_id": gated["id"]},
                                role="operator", )["id"]
        fx["flow_run"] = self.ok("workflow.launch", {"id": fx["workflow"]},
                                 role="operator")["id"]
        fx["export"] = self.ok("config.export")
        return fx

    # The id each command takes as "id", by what it names.
    ID_OF = {"approval": "pending", "channel": "channel", "check": "check",
             "credential": "credential", "file": "managed", "inventory": "inventory",
             "maintenance": "maintenance", "project": "project", "rule": "rule",
             "run": "run", "schedule": "schedule", "template": "template",
             "workflow": "workflow", "host": "host"}
    FOREIGN = {"template_id": "template", "project_id": "project", "inventory_id": "inventory",
               "credential_id": "credential", "machine_credential_id": "credential",
               "vault_credential_id": "vault", "channel_id": "channel", "check_id": "check",
               "host_id": "host", "scope_id": "template"}

    def valid(self, name: str) -> dict:
        """Parameters with which the command gets as far as it can."""
        fx = self.fx
        spec = cli.COMMANDS[name]
        area = name.split(".")[0]
        params: dict = {}
        for key, field in spec.params.items():
            if key == "id" and field.kind == "id":
                params[key] = fx.get(self.ID_OF.get(area, ""), "x")
            elif key in self.FOREIGN and field.kind == "id" and field.required:
                params[key] = fx[self.FOREIGN[key]]
            elif field.kind == "choice" and field.required:
                params[key] = field.choices[0]
            elif field.required and field.kind == "string":
                params[key] = "x"
        params.update(self.OVERRIDES.get(name, lambda fx: {})(fx))
        return params

    OVERRIDES = {
        "workflow.progress": lambda fx: {"id": fx["flow_run"]},
        "workflow.resume": lambda fx: {"id": fx["flow_run"]},
        "file.bundle": lambda fx: {"id": fx.get("managed", "x")},
        "file.read": lambda fx: {"path": "site.yml"},
        "file.check": lambda fx: {"path": "site.yml"},
        "file.history": lambda fx: {"path": "site.yml"},
        "file.delete": lambda fx: {"path": "site.yml"},
        "file.move": lambda fx: {"path": "site.yml", "to": "moved.yml"},
        "file.write": lambda fx: {"path": "new.yml", "content": "- hosts: all\n",
                                  "expected": ""},
        "file.diff": lambda fx: {"revision": fx.get("revision", "x")},
        "file.change": lambda fx: {"revision": fx.get("revision", ""), "operations": [
            {"op": "write", "path": "new.yml", "content": "- hosts: all\n", "expected": ""},
            {"op": "copy", "path": "site.yml", "to": "copy/site.yml"},
            {"op": "move", "path": "copy", "to": "moved"},
            {"op": "write", "path": "files/blob.bin", "data": "AAEC"},
            {"op": "delete", "path": "moved"}]},
        "file.restore": lambda fx: {"path": "site.yml", "revision": fx.get("revision", "x")},
        "file.download": lambda fx: {"path": "site.yml"},
        "file.import": lambda fx: {"filename": "lab.tar.gz", "data": TARBALL, "into": "labs"},
        "file.push": lambda fx: {"expected": fx.get("revision", "x"), "bundle": ""},
        "project.create": lambda fx: {"name": "new", "kind": "git",
                                      "url": "https://example.org/a.git"},
        "project.update": lambda fx: {"name": "site", "path": fx["project_path"]},
        "project.upload": lambda fx: {"id": fx["archive"], "filename": "site.tar.gz",
                                      "data": TARBALL},
        "project.sync": lambda fx: {"id": fx["git"]},
        "inventory.create": lambda fx: {"name": "new", "kind": "managed",
                                        "content": "all:\n  hosts:\n    a:\n"},
        "inventory.update": lambda fx: {"content": "all:\n"},
        "credential.create": lambda fx: {"name": "new", "kind": "machine", "username": "u"},
        "template.create": lambda fx: {
            "name": "new", "playbook": "site.yml", "limit_hosts": "web", "tags": ["a"],
            "skip_tags": ["b"], "vault_credential_ids": [fx["vault"]],
            "machine_credential_id": fx["credential"], "extra_vars": {"a": {"b": [1, "c"]}},
            "survey": [{"name": "size", "type": "choice", "choices": ["s", "m"],
                        "default": "s", "required": True},
                       {"name": "count", "type": "number", "min": 1, "max": 9}]},
        "template.update": lambda fx: {"playbook": "site.yml", "tags": ["a"],
                                       "extra_vars": {"a": 1}},
        "check.create": lambda fx: {
            "name": "new", "kind": "disk", "group": "web",
            "params": {"mounts": ["/"], "exclude_types": ["tmpfs"]},
            "thresholds": {"used_pct": {"warning": 80, "critical": 90}}},
        "check.update": lambda fx: {"params": {"mounts": ["/srv"]},
                                    "thresholds": {"used_pct": {"warning": 70, "critical": 80}}},
        "check.run": lambda fx: {"host_id": fx["host"]},
        "channel.create": lambda fx: {"name": "new", "kind": "webhook", "settings": {
            "url": "https://hooks.example.org/x", "method": "POST", "header": "X-Token",
            "template": '{"text": "x"}'}},
        "rule.create": lambda fx: {"name": "new", "events": ["failed"], "scope": "all"},
        "schedule.create": lambda fx: {"name": "new", "calendar": "hourly", "limit": "web",
                                       "answers": {"colour": "red"}, "timezone": "UTC"},
        "schedule.preview": lambda fx: {"calendar": "hourly"},
        "workflow.create": lambda fx: {"name": "new", "nodes": [
            {"id": "a", "template_id": fx["template"], "limit": "web01",
             "answers": {"colour": "blue"}}], "edges": []},
        "workflow.update": lambda fx: {"nodes": [
            {"id": "a", "template_id": fx["template"]},
            {"id": "b", "template_id": fx["template"]}],
            "edges": [{"from": "a", "to": "b", "condition": "success"}]},
        "maintenance.create": lambda fx: {"ends_at": 1_800_003_600, "group": "web"},
        "hostkey.scan": lambda fx: {"inventory_id": fx["inventory"]},
        "hostkey.add": lambda fx: {"host": "web02", "key_type": "ssh-ed25519",
                                   "key": PUBLIC_KEY},
        "hostkey.remove": lambda fx: {"id": 1},
        "run.start": lambda fx: {"limit": "web01", "answers": {"colour": "blue"}},
        "run.adhoc": lambda fx: {"module": "ansible.builtin.ping", "limit": "all",
                                 "args": {"data": "x"}, "vault_credential_ids": [fx["vault"]],
                                 "machine_credential_id": fx["credential"]},
        "metric.query": lambda fx: {"name": "used_pct"},
        "module.doc": lambda fx: {"name": "ansible.builtin.ping"},
        "module.help": lambda fx: {"name": "ansible.builtin.ping"},
        "cmdb.packages": lambda fx: {"name": "openssl"},
        "chat.start": lambda fx: {"agent": "claude"},
        "notification.retry": lambda fx: {"id": 1},
        "config.import": lambda fx: {"document": fx["export"]},
        "channel.update": lambda fx: {"settings": WEBHOOK["settings"]},
        "rule.update": lambda fx: {"events": ["failed", "problem"]},
        "schedule.update": lambda fx: {"calendar": "hourly"},
        "approval.approve": lambda fx: {"comment": "fine"},
        "state.ack": lambda fx: {"comment": "seen"},
    }

    def call_as(self, name: str, params: dict, role="admin", raw: bytes | None = None,
                rights=frozenset()):
        ctx = cli.Context(Caller(1001, "mallory", role, rights), self.root / "fleet.db",
                          self.root / "config.toml")
        ctx._config = self.config
        data = raw if raw is not None else json.dumps(params).encode()
        return cli.dispatch(name, data, ctx)


class FuzzTest(Fixture):
    """Every parameter of every command, given what it must refuse or bear."""

    TREES = ("projects", "runs", "src")

    def snapshot(self) -> Path:
        for name in self.TREES:
            shutil.copytree(self.root / name, self.root / f"{name}.image", symlinks=True)
        image = self.root / "image.db"
        with (closing(sqlite3.connect(self.root / "fleet.db")) as source,
              closing(sqlite3.connect(image)) as copy):
            source.backup(copy)
        return image

    def restore(self, image: Path, trees: bool = True) -> None:
        if trees:
            for name in self.TREES:
                shutil.rmtree(self.root / name)
                shutil.copytree(self.root / f"{name}.image", self.root / name, symlinks=True)
        source, target = sqlite3.connect(image), sqlite3.connect(self.root / "fleet.db")
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()

    def variants(self, name: str, base: dict):
        """(label, params) for each hostile change to one parameter."""
        spec = cli.COMMANDS[name]
        strings = canary_values(self.canary)
        for key, field in spec.params.items():
            kind = field.kind
            for value in WRONG_TYPES[kind]:
                yield f"{key}={value!r}", {**base, key: value}
            if kind == "string":
                lengths = ([] if field.max_length > 65536
                           else ["x" * field.max_length, "x" * (field.max_length + 1)])
                for value in strings + lengths:
                    yield f"{key}={value[:40]!r}", {**base, key: value}
            elif kind == "integer":
                for value in (field.low - 1, field.high + 1):
                    yield f"{key}={value}", {**base, key: value}
            elif kind == "strings":
                for value in strings:
                    yield f"{key}=[{value[:40]!r}]", {**base, key: [value]}
                yield f"{key} too many", {**base, key: ["x"] * (field.max_items + 1)}
            elif kind in ("object", "objects"):
                current = base.get(key, {} if kind == "object" else [])
                for value in strings[:12] + [None, 1.5, True, -1, 2**70, [], {}, [None],
                                             {"": None}, nested(400)]:
                    yield f"{key} new key", {**base, key: self.with_key(current, value)}
                    for path in list(leaves(current)):
                        if path:
                            yield (f"{key}{list(path)}={value!r:.40}",
                                   {**base, key: replaced(current, path, value)})
                if kind == "objects":
                    yield f"{key} too many", {**base, key: [{}] * (field.max_items + 1)}
        yield "unknown parameter", {**base, "PWNED": 1}

    @staticmethod
    def with_key(current, value):
        if isinstance(current, dict):
            return {**current, "-PWNED": value, "../PWNED": value, "{{PWNED}}": value}
        if current and isinstance(current[0], dict):
            return [{**current[0], "-PWNED": value}] + current[1:]
        return [{"-PWNED": value}]

    def look(self, name, label, answer, params) -> list[str]:
        """What went wrong with one call, in words."""
        where = f"{name} {label}"
        problems = []
        if not answer["ok"] and answer["error"]["code"] == "internal":
            trace = [k.get("FLEET_TRACEBACK", "") for _a, k in self.journal]
            last = (trace[-1].strip().splitlines() or ["?"]) if trace else ["?"]
            problems.append(f"{where}: internal error: {last[-1]} at {last[-3].strip()}"
                            if len(last) > 2 else f"{where}: internal error")
        if list(self.canary_dir.iterdir()):
            problems.append(f"{where}: wrote outside")
            for item in self.canary_dir.iterdir():
                item.unlink()
        hostile = canary_values(self.canary)
        for argv in self.recorder.calls:
            if options_from(argv, hostile):
                problems.append(f"{where}: an option in {argv}")
        self.recorder.calls.clear()
        spec = cli.COMMANDS[name]
        hidden = [params[key] for key in spec.redact
                  if isinstance(params.get(key), str) and "PWNED" in params[key]]
        if hidden:
            entries = self.ctx().conn.execute("SELECT params FROM audit").fetchall()
            text = json.dumps([row[0] for row in entries]) + repr(self.journal)
            problems += [f"{where}: recorded {value[:30]!r}" for value in hidden
                         if json.dumps(value)[1:-1] in text]
        return problems

    # Other forms of a command, fuzzed besides the one valid() gives.
    FORMS = {
        "channel.create": lambda fx: [
            {"name": "mail", "kind": "smtp", "settings": {
                "host": "mail.example.org", "security": "starttls", "username": "u",
                "sender": "fleet@example.org", "recipients": ["ops@example.org"]}},
            {"name": "chat", "kind": "matrix", "settings": {
                "homeserver": "https://matrix.example.org", "room_id": "!abc:example.org"}},
            {"name": "tg", "kind": "telegram", "settings": {"chat_id": "-100123"}}],
        "project.create": lambda fx: [
            {"name": "here", "kind": "local", "path": fx["project_path"]},
            {"name": "typed", "kind": "managed"}, {"name": "packed2", "kind": "archive"}],
        "inventory.create": lambda fx: [
            {"name": "file", "kind": "file", "project_id": fx["project"],
             "path": "inventories/hosts.yml"}],
        "credential.create": lambda fx: [
            {"name": "v2", "kind": "vault", "vault_id": "prod"},
            {"name": "s2", "kind": "source", "username": "git"}],
        "check.create": lambda fx: [
            {"name": "own", "kind": "playbook", "project_id": fx["project"],
             "playbook": "site.yml", "inventory_id": fx["inventory"]}],
        "rule.create": lambda fx: [
            {"name": "t", "channel_id": fx["channel"], "events": ["failed"],
             "scope": "template", "scope_id": fx["template"]},
            {"name": "g", "channel_id": fx["channel"], "events": ["problem"],
             "scope": "group", "scope_id": fx["inventory"], "group": "web"}],
    }

    def test_no_value_breaks_a_command_or_gets_past_its_checks(self):
        image = self.snapshot()
        tried, problems = 0, []
        forms = [(name, self.valid(name)) for name in sorted(cli.COMMANDS)]
        for name, more in self.FORMS.items():
            forms += [(name, {**self.valid(name), **form}) for form in more(self.fx)]
        for name, base in forms:
            # Only these change files that the next call would miss.
            spec = cli.COMMANDS[name]
            trees = spec.mutating and name.startswith(("file.", "project."))
            writes = spec.mutating or name in HOUSEKEEPING
            self.restore(image, trees=True)
            for label, params in self.variants(name, base):
                if writes:
                    self.restore(image, trees=trees)
                self.journal.clear()
                answer = self.call_as(name, params)
                problems += self.look(name, label, answer, params)
                tried += 1
        self.assertEqual(problems, [], f"{len(problems)} of {tried}:\n" + "\n".join(problems))
        self.assertGreater(tried, 5000)

    def test_input_that_is_not_a_json_object_is_refused_by_every_command(self):
        deep = b"[" * 100_000 + b"]" * 100_000
        nested = b'{"a":' * 50_000 + b"1" + b"}" * 50_000
        for name in sorted(cli.COMMANDS):
            for raw in (b"[]", b"null", b"1", b'"x"', b"{", b"\xff", b"{}" * 2, deep, nested,
                        b'{"a": NaN}', b'{"a": Infinity}', b'{"\\u0000": 1}',
                        b"\x00{}", b'{"via": "mcp\\n"}', b'{"via": 1}'):
                answer = self.call_as(name, {}, raw=raw)
                self.assertFalse(answer["ok"], (name, raw[:20]))
                self.assertNotEqual(answer["error"]["code"], "internal", (name, raw[:20]))


class RoleTest(Fixture):
    """Nobody gets past the role of a command, by any way in."""

    def test_the_table_says_the_role_and_kind_of_every_command(self):
        actual = {name: (spec.role, spec.mutating) for name, spec in cli.COMMANDS.items()}
        self.assertEqual(actual, EXPECTED)
        rights = {name: spec.right for name, spec in cli.COMMANDS.items() if spec.right}
        self.assertEqual(rights, RIGHTS)

    def reached(self, name, role, rights=frozenset(), via=None, config=None):
        """Whether a call got to the handler of the command."""
        called = []
        spec = cli.COMMANDS[name]
        stand_in = dataclasses.replace(spec, handler=lambda ctx, params: called.append(1) or {})
        params = self.valid(name)
        if via is not None:
            params["via"] = via
        ctx = cli.Context(Caller(1001, "mallory", role, rights), self.root / "fleet.db",
                          self.root / "config.toml")
        ctx._config = config or self.config
        with mock.patch.dict(cli.COMMANDS, {name: stand_in}):
            answer = cli.dispatch(name, json.dumps(params).encode(), ctx)
        if not called:
            self.assertEqual(answer["error"]["code"], "forbidden", (name, role, answer))
        return bool(called)

    def denials(self, name):
        return self.ctx().conn.execute(
            "SELECT count(*) FROM audit WHERE command = ? AND outcome = 'denied'"
            " AND user = 'mallory'", (name,)).fetchone()[0]

    def test_every_command_is_refused_below_its_role_and_the_refusal_audited(self):
        for name, spec in sorted(cli.COMMANDS.items()):
            least = ROLES.index(spec.role)
            for role in (None, *ROLES):
                below = role is None or ROLES.index(role) < least
                before = self.denials(name)
                self.assertEqual(self.reached(name, role), not below, (name, role))
                self.assertEqual(self.denials(name) - before, int(below), (name, role))

    def test_a_right_opens_its_own_commands_and_nothing_else(self):
        for name, spec in sorted(cli.COMMANDS.items()):
            for role in (None, "viewer", "operator"):
                expected = (name in RIGHTS or (role is not None
                                               and ROLES.index(role) >= ROLES.index(spec.role)))
                self.assertEqual(self.reached(name, role, frozenset({"approve"})), expected,
                                 (name, role))
            # A right nobody has heard of opens nothing.
            if spec.role != "viewer":
                self.assertFalse(self.reached(name, "viewer", frozenset({"admin", "approve2"})),
                                 name)

    def test_an_agent_is_held_to_the_ceiling_and_has_no_right(self):
        enabled = dataclasses.replace(self.config, mcp_enabled=True, mcp_max_role="operator")
        for name, spec in sorted(cli.COMMANDS.items()):
            for via in ("mcp", "mcp:client", "mcp:x/y 1.0"):
                expected = spec.role != "admin" and (spec.tool or name == "mcp.tools")
                self.assertEqual(self.reached(name, "admin", frozenset({"approve"}), via,
                                              enabled), expected, (name, via))
            # While agents are not enabled, nothing goes through for them at all.
            self.assertFalse(self.reached(name, "admin", via="mcp:client"), name)

    def test_saying_where_a_call_comes_from_never_raises_a_role(self):
        enabled = dataclasses.replace(self.config, mcp_enabled=True, mcp_max_role="admin")
        for name, spec in sorted(cli.COMMANDS.items()):
            if spec.role == "viewer":
                continue
            for via in ("cli", "admin", "system", "mcp:admin", "root"):
                self.assertFalse(self.reached(name, "viewer", via=via, config=enabled),
                                 (name, via))

    def test_only_sudo_says_who_calls_and_only_in_digits(self):
        from cockpit_fleet.auth import resolve_caller

        accounts = {0: "root", 1000: "anna"}

        def getpwuid(uid):
            if uid not in accounts:
                raise KeyError(uid)
            return mock.Mock(pw_name=accounts[uid], pw_gid=uid)

        def groups(user, gid):
            return ["cockpit-fleet-viewer"] if user == "anna" else []

        def caller(value):
            return resolve_caller({"SUDO_UID": value}, 990, getpwuid, groups)

        self.assertEqual(caller("1000").role, "viewer")
        self.assertEqual(caller("0").role, "admin")
        # What int() would read as an account, but sudo never writes.
        for value in (" 0", "0 ", "0\n", "+0", "-0", "0_0", "\u0660", "\uff10", "00",
                      "1000.0", "0x0", "", "-1", "4294967296", "1e3", "1000 ", "+1000"):
            found = caller(value)
            self.assertIsNone(found.role, repr(value))
            self.assertEqual(found.uid, -1, repr(value))


class SecretTest(Fixture):
    """A secret enters through fleet-secret and reaches a run as a file;
    no command reads it, answers with it or records it."""

    MARK = "S3CRET-MARK"

    def plant(self) -> list[Path]:
        """Every place a secret lives, holding a value that is easy to find."""
        places = []
        credentials = self.ok("credential.list")["credentials"]
        for credential in credentials:
            for name in credential.get("secret_names", {}).values():
                places.append(paths.SECRETS_DIR / f"{name}.cred")
        for channel in self.ok("channel.list")["channels"]:
            if channel.get("secret_name"):
                places.append(paths.NOTIFY_SECRETS_DIR / f"{channel['secret_name']}.cred")
        places.append(paths.ANSWERS_DIR / ("a-" + "0" * 32 + ".cred"))
        for run in self.ok("run.list")["runs"]:
            places.append(paths.RUNS_DIR / run["id"] / "creds" / "fleet_private_key")
        places.append(self.creds / "notify_n-x.cred")
        for place in places:
            place.parent.mkdir(parents=True, exist_ok=True)
            place.write_text(self.MARK + "\n")
        return places

    def test_no_command_answers_with_a_secret_or_records_one(self):
        self.plant()
        enabled = dataclasses.replace(self.config, mcp_enabled=True)
        self.config = enabled
        answers = []
        for name in sorted(cli.COMMANDS):
            for role in ROLES:
                if ROLES.index(role) >= ROLES.index(cli.COMMANDS[name].role):
                    answers.append((name, role, self.call_as(name, self.valid(name), role)))
        for name, role, answer in answers:
            self.assertNotIn(self.MARK, json.dumps(answer), (name, role))
        self.assertNotIn(self.MARK, repr(self.journal))
        self.ctx().conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for item in self.root.glob("fleet.db*"):
            self.assertNotIn(self.MARK.encode(), item.read_bytes(), item)

    def test_a_secret_answer_given_in_clear_is_refused_and_kept_nowhere(self):
        value = "plain-" + self.MARK
        attempts = [
            ("run.start", {"template_id": self.fx["template"], "answers": {"token": value}}),
            ("schedule.create", {"name": "s2", "template_id": self.fx["template"],
                                 "calendar": "hourly", "answers": {"token": value}}),
            ("schedule.update", {**self.valid("schedule.update"), "name": "hourly",
                                 "template_id": self.fx["template"],
                                 "answers": {"token": value}}),
            ("workflow.create", {"name": "w2", "edges": [], "nodes": [
                {"id": "a", "template_id": self.fx["template"], "answers": {"token": value}}]}),
            ("template.update", {**self.valid("template.update"), "name": "deploy",
                                 "project_id": self.fx["project"], "survey": [
                                     {"name": "token", "type": "secret", "default": value}]}),
            ("workflow.update", {**self.valid("workflow.update"), "name": "release",
                                 "edges": [], "nodes": [
                {"id": "a", "template_id": self.fx["template"], "answers": {"token": value}}]}),
            ("run.start", {"template_id": self.fx["template"],
                           "answers": {"token": [value], "colour": 1}}),
        ]
        for name, params in attempts:
            answer = self.call_as(name, params)
            self.assertEqual(answer.get("error", {}).get("code"), "bad_request", (name, answer))
            self.assertNotIn(value, json.dumps(answer), name)
        self.assertNotIn(value, repr(self.journal))
        rows = self.ctx().conn.execute("SELECT params FROM audit").fetchall()
        self.assertNotIn(value, json.dumps([row[0] for row in rows]))

    def test_what_names_a_secret_is_never_audited_in_clear(self):
        secretive = ("password", "passphrase", "token", "secret", "private")
        for name, spec in sorted(cli.COMMANDS.items()):
            for key, field in spec.params.items():
                if field.kind == "string" and any(word in key for word in secretive):
                    self.assertIn(key, spec.redact, f"{name}: {key}")
            # Large values are left out of the audit too: a file, an archive.
            for key, field in spec.params.items():
                if field.kind == "string" and field.max_length > 64 * 1024:
                    self.assertIn(key, spec.redact, f"{name}: {key}")


class HostKeyTest(Fixture):
    """A host key is one line of known_hosts for one host: nothing in it can
    add a line, or a pattern that trusts the key for other hosts."""

    def test_a_host_is_a_name_or_an_address_with_a_port_and_nothing_more(self):
        for host in ("web03", "10.0.0.9", "[web03]:2222", "[fe80::1]:22", "fe80::1"):
            self.assertTrue(self.call_as("hostkey.add", {
                "host": host, "key_type": "ssh-ed25519", "key": PUBLIC_KEY})["ok"], host)
        for host in ("[a]:22,*", "[a]:22\n* ssh-ed25519 " + PUBLIC_KEY, "[a]:22 x", "[a]:x",
                     "[a]:99999", "[a]:0", "[a]", "[a]:", "[*]:22", "[a]:22]", "[[a]]:22",
                     "a,b", "*", "!a", "|1|abc", "@cert-authority", "[a]:22\r", "-a"):
            answer = self.call_as("hostkey.add", {"host": host, "key_type": "ssh-ed25519",
                                                  "key": PUBLIC_KEY})
            self.assertEqual(answer.get("error", {}).get("code"), "bad_request", repr(host))

    def test_a_key_type_is_a_plain_name(self):
        for key_type in ("ssh-ed25519\n", "ssh ed25519", "ssh-éd25519", "", "-x", "a" * 65):
            answer = self.call_as("hostkey.add", {"host": "web03", "key_type": key_type,
                                                  "key": PUBLIC_KEY})
            self.assertEqual(answer.get("error", {}).get("code"), "bad_request", repr(key_type))

    def test_an_import_takes_host_keys_through_the_same_checks(self):
        document = {**self.fx["export"], "host_keys": [
            {"host": "[a]:22,*", "key_type": "ssh-ed25519", "key": PUBLIC_KEY},
            {"host": "web04\n* ssh-ed25519 " + PUBLIC_KEY, "key_type": "ssh-ed25519",
             "key": PUBLIC_KEY}]}
        self.call_as("config.import", {"document": document})
        hosts = [key["host"] for key in self.ok("hostkey.list")["keys"]]
        self.assertEqual(hosts, ["web01"])

    def test_known_hosts_has_one_line_per_key(self):
        from cockpit_fleet import jobs
        self.call_as("hostkey.add", {"host": "[web03]:2222", "key_type": "ssh-rsa",
                                     "key": PUBLIC_KEY})
        jobs.write_known_hosts(self.ctx().conn, self.root)
        lines = (self.root / "known_hosts").read_text().splitlines()
        self.assertEqual([line.split(" ")[0] for line in lines], ["[web03]:2222", "web01"])
        self.assertTrue(all(len(line.split(" ")) == 3 for line in lines))

    def test_a_key_stored_before_these_checks_is_left_out_of_known_hosts(self):
        from cockpit_fleet import jobs
        conn = self.ctx().conn
        for host in ("[a]:22,*", "web05\n* ssh-rsa AAAA"):
            conn.execute("INSERT INTO host_keys (host, key_type, key, added_by, added_at)"
                         " VALUES (?, 'ssh-ed25519', ?, 'old', 0)", (host, PUBLIC_KEY))
        conn.execute("INSERT INTO host_keys (host, key_type, key, added_by, added_at)"
                     " VALUES ('web06', 'ssh-ed25519', 'AAAA\n* ssh-rsa AAAA', 'old', 0)")
        jobs.write_known_hosts(conn, self.root)
        lines = (self.root / "known_hosts").read_text().splitlines()
        self.assertEqual([line.split(" ")[0] for line in lines], ["web01"])


class SpecTest(unittest.TestCase):
    """fleet-run builds every command line from spec.json, which only
    fleetctl writes. A spec changed behind its back still makes no option,
    no path outside the run and no shell."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.canary_dir = root / "canary"
        self.canary_dir.mkdir()
        self.canary = self.canary_dir / "PWNED"
        self.run = root / "run"
        self.runtime = root / "runtime"
        self.creds = root / "creds"
        for directory in (self.run / "project" / "inventories", self.run / "inventory",
                          self.runtime, self.creds):
            directory.mkdir(parents=True)
        for name in ("site.yml", "requirements.yml", "inventories/hosts.yml"):
            (self.run / "project" / name).write_text("")
        (self.run / "inventory" / "hosts.yml").write_text("")
        for name in ("vault_password_0", "survey_0", "private_key"):
            (self.creds / f"fleet_{name}").write_text("x")

    COMMON = {"inventory": "inventories/hosts.yml", "inventory_source": "project", "forks": 5,
              "user": "maint", "become": True, "verbosity": 1,
              "vaults": [{"file": "vault_password_0", "id": "prod"}]}
    SPECS = {
        "playbook": {**COMMON, "playbook": "site.yml", "limit": "web", "tags": ["a"],
                     "skip_tags": ["b"], "check": True, "extra_vars": {"a": {"b": [1]}},
                     "survey_vars": {"c": "d"}, "survey_secrets": {"t": "survey_0"},
                     "workflow_vars": {"e": ["f"]}},
        "limits": {**COMMON, "limits": ["web", "all:!db"]},
        "adhoc": {**COMMON, "module": "ansible.builtin.command", "free_form": "id",
                  "args": {"chdir": "/"}, "limit": "all"},
        "check": {**COMMON, "plan": {"web01": [{"id": "c1", "check": "disk", "params": {
            "mounts": ["/"]}}]}, "timeout": 60, "compliance": False},
        "own_check": {**COMMON, "playbook": "site.yml", "compliance": True, "plan": {
            "web01": [{"id": "c1", "check": "playbook", "params": {}}]}},
        "inventory": {"inventory": "hosts.yml", "inventory_source": "managed"},
        "syntax": {"playbook": "site.yml"},
        "sync": {"url": "https://example.org/a.git", "ref": "main", "user": "git"},
        "galaxy": {"requirements": "requirements.yml"},
    }

    def build(self, kind: str, spec: dict) -> list[list[str]]:
        if kind == "playbook":
            return [runner.build_argv(spec, self.run, self.runtime, self.creds)]
        if kind == "limits":
            return [argv for argv, _output in runner.limit_plan(spec, self.run, self.runtime,
                                                                self.creds)]
        if kind == "adhoc":
            return [runner.build_adhoc_argv(spec, self.run, self.runtime, self.creds)]
        if kind in ("check", "own_check"):
            return [runner.build_check_argv(spec, self.run, self.runtime, self.creds)]
        if kind == "inventory":
            return [runner.build_inventory_argv(spec, self.run, self.creds)]
        if kind == "syntax":
            return [runner.build_syntax_argv(spec, self.run), runner.build_lint_argv(spec, self.run)]
        if kind == "sync":
            commands, env = runner.build_sync(spec, self.run, self.runtime, self.creds)
            return [*commands, [env["GIT_SSH_COMMAND"]]]
        argv, env = runner.build_galaxy(spec, self.run, self.runtime)
        return [argv]

    def problems_of(self, kind, label, spec) -> list[str]:
        where = f"{kind} {label}"
        try:
            commands = self.build(kind, spec)
        except BadRequest:
            return []
        except Exception as error:  # noqa: BLE001  (what fleet-run would die of)
            return [f"{where}: {type(error).__name__}: {error}"]
        problems = []
        hostile = canary_values(self.canary)
        allowed = (str(self.run), str(self.runtime), str(self.creds), "/usr/", "/dev/null")
        for argv in commands:
            if options_from(argv, hostile):
                problems.append(f"{where}: an option in {argv}")
            for item in argv:
                bare = item.split("@", 1)[-1] if "@/" in item else item
                if bare.startswith("/") and not bare.startswith(allowed):
                    problems.append(f"{where}: a path outside the run: {item}")
        if list(self.canary_dir.iterdir()):
            problems.append(f"{where}: wrote outside")
        return problems

    def test_every_value_of_every_spec_is_checked_again(self):
        hostile = canary_values(self.canary) + [None, 1, -1, 2**70, 1.5, True, [], {}, [None],
                                                nested(400)]
        problems, tried = [], 0
        for kind, base in self.SPECS.items():
            self.assertEqual(self.problems_of(kind, "as it is", base), [])
            self.build(kind, base)
            for path in list(leaves(base))[1:]:
                for value in hostile:
                    label = f"{list(path)}={value!r:.40}"
                    problems += self.problems_of(kind, label, replaced(base, path, value))
                    tried += 1
            for key in ("playbook", "limit", "user", "tags", "module", "url", "ref",
                        "requirements", "inventory", "limits", "vaults", "proxy"):
                if key not in base:
                    for value in hostile:
                        problems += self.problems_of(kind, f"+{key}={value!r:.40}",
                                                     {**base, key: value})
                        tried += 1
        self.assertEqual(problems, [], f"{len(problems)} of {tried}:\n" + "\n".join(problems))


def load_helper(name: str):
    """A root helper from backend/libexec, as a module."""
    path = Path(__file__).resolve().parents[1] / "libexec" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    # No bytecode beside it: the package installs backend/libexec/* as it is.
    saved, writes = list(sys.path), sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader.exec_module(module)
    finally:
        sys.path[:] = saved
        sys.dont_write_bytecode = writes
    return module


class FleetUnitTest(unittest.TestCase):
    """fleet-unit, run as root by the service account through sudo: the one
    way to systemd, for an instance of the run template and nothing else."""

    def setUp(self):
        self.unit = load_helper("fleet-unit")
        self.spawned: list[list[str]] = []
        self.staged: list[str] = []

        def run(argv, **kwargs):
            self.spawned.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        def execv(program, argv):
            self.spawned.append([program, *argv[1:]])
            raise SystemExit(0)

        patches = [
            mock.patch.object(self.unit.os, "geteuid", lambda: 0),
            mock.patch.object(self.unit.subprocess, "run", run),
            mock.patch.object(self.unit.os, "execv", execv),
            mock.patch.object(self.unit.pwd, "getpwnam", lambda name: mock.Mock(pw_uid=990)),
            mock.patch.object(self.unit.credstage, "stage",
                              lambda state, run_id, staging, uid: self.staged.append(run_id)),
            mock.patch.object(self.unit.credstage, "discard", lambda staging, run_id: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def main(self, *argv):
        with self.assertRaises(SystemExit) as stop:
            self.unit.main(["fleet-unit", *argv])
        return stop.exception.code

    def test_start_and_stop_reach_one_instance_of_the_template(self):
        self.assertEqual(self.main("start", "r1"), 0)
        self.assertEqual(self.main("stop", "r1"), 0)
        self.assertEqual(self.spawned, [
            ["/usr/bin/systemctl", "start", "--", "cockpit-fleet-run@r1.service"],
            ["/usr/bin/systemctl", "stop", "--", "cockpit-fleet-run@r1.service"]])
        self.assertEqual(self.staged, ["r1"])

    def test_anything_else_reaches_nothing(self):
        for argv in ([], ["start"], ["start", "r1", "r2"], ["restart", "r1"], ["START", "r1"],
                     ["start", "--version"], ["start", "-r1"], ["start", "x.service"],
                     ["start", "../../etc"], ["start", "r1/../x"], ["start", "R1"],
                     ["start", "r1\n"], ["start", "r1 x"], ["start", ""], ["start", "r" * 65],
                     ["start", "r1@x"], ["stop", "*"], ["stop", "r1;id"], ["--", "start", "r1"],
                     ["start", "--", "r1"]):
            self.assertNotEqual(self.main(*argv), 0, argv)
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.staged, [])

    def test_only_root(self):
        with mock.patch.object(self.unit.os, "geteuid", lambda: 990):
            self.assertNotEqual(self.main("start", "r1"), 0)
        self.assertEqual(self.spawned, [])


class FleetSecretTest(unittest.TestCase):
    """fleet-secret, run as root by administrators (and by operators for an
    answer): it writes ciphertext under a validated name, in two directories,
    and nowhere else."""

    def setUp(self):
        self.helper = load_helper("fleet-secret")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.secrets, self.answers = root / "secrets", root / "answers"
        self.notify = self.secrets / "notify"
        for directory in (self.notify, self.answers):
            directory.mkdir(parents=True)
        self.encrypted: list[bytes] = []
        self.logged: list[str] = []

        def run(argv, input=None, **kwargs):  # noqa: A002
            self.assertEqual(argv, ["/usr/bin/systemd-creds", "encrypt", "--name=", "-", "-"])
            self.encrypted.append(input)
            return subprocess.CompletedProcess(argv, 0, b"CIPHERTEXT", b"")

        patches = [
            mock.patch.object(self.helper.os, "geteuid", lambda: 0),
            mock.patch.object(self.helper.os, "fchown", lambda fd, uid, gid: None),
            mock.patch.object(self.helper.subprocess, "run", run),
            mock.patch.object(self.helper, "SECRETS_DIR", self.secrets),
            mock.patch.object(self.helper, "NOTIFY_SECRETS_DIR", self.notify),
            mock.patch.object(self.helper, "ANSWERS_DIR", self.answers),
            mock.patch.object(self.helper.journal, "send",
                              lambda message, *a, **k: self.logged.append(message)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def main(self, *argv, stdin=b"value"):
        out = io.StringIO()
        fake = mock.Mock(buffer=io.BytesIO(stdin))
        with mock.patch.object(self.helper.sys, "stdin", fake), \
                mock.patch.object(self.helper.sys, "stdout", out), \
                mock.patch.object(self.helper.sys, "stderr", io.StringIO()):
            try:
                self.helper.main(["fleet-secret", *argv])
                code = 0
            except SystemExit as stop:
                code = stop.code
            except OSError as error:
                code = repr(error)
        return code, out.getvalue()

    def files(self):
        root = self.secrets.parent
        return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())

    def test_names_are_identifiers_and_go_where_their_prefix_says(self):
        self.assertEqual(self.main("set", "c-ssh")[0], 0)
        self.assertEqual(self.main("set", "n-chan")[0], 0)
        self.assertEqual(self.files(), ["secrets/c-ssh.cred", "secrets/notify/n-chan.cred"])
        self.assertEqual(self.encrypted, [b"value", b"value"])
        self.assertEqual(oct((self.secrets / "c-ssh.cred").stat().st_mode & 0o777), "0o640")
        self.assertFalse(any("value" in line for line in self.logged))

    def test_a_name_that_is_not_an_identifier_writes_nothing(self):
        for name in ("../x", "x/../../y", "/etc/shadow", "-x", "X", "x.cred", "x\n", "",
                     ".x", "n-../x", "x" * 65, "x y", "*"):
            for verb in ("set", "delete"):
                self.assertNotEqual(self.main(verb, name)[0], 0, (verb, name))
        self.assertEqual(self.files(), [])
        self.assertEqual(self.encrypted, [])

    def test_other_forms_are_refused(self):
        for argv in ([], ["set"], ["set", "a", "b"], ["get", "a"], ["read", "a"], ["list", "x"],
                     ["answer", "a-x"], ["answer", "--", "x"], ["delete"], ["SET", "a"],
                     ["--", "set", "a"], ["set", "--", "a"]):
            self.assertNotEqual(self.main(*argv)[0], 0, argv)
        self.assertEqual(self.files(), [])

    def test_an_empty_or_large_value_is_refused(self):
        self.assertNotEqual(self.main("set", "c-x", stdin=b"")[0], 0)
        self.assertNotEqual(self.main("set", "c-x", stdin=b"x" * (64 * 1024 + 1))[0], 0)
        self.assertNotEqual(self.main("answer", stdin=b"x" * 4097)[0], 0)
        self.assertEqual(self.files(), [])

    def test_an_answer_gets_a_name_chosen_here_and_they_are_limited(self):
        code, name = self.main("answer")
        self.assertEqual(code, 0)
        self.assertRegex(name, r"^a-[0-9a-f]{32}\n$")
        self.assertEqual(self.files(), [f"answers/{name.strip()}.cred"])
        for index in range(self.helper.MAX_ANSWERS - 1):
            (self.answers / f"a-{index:032x}.cred").write_bytes(b"x")
        self.assertNotEqual(self.main("answer")[0], 0)

    def test_nothing_is_written_through_a_link(self):
        outside = Path(self.tmp.name) / "outside"
        outside.write_text("kept")
        os.symlink(outside, self.secrets / f".c-x.{os.getpid()}.tmp")
        self.assertNotEqual(self.main("set", "c-x")[0], 0)
        self.assertEqual(outside.read_text(), "kept")

    def test_only_root(self):
        with mock.patch.object(self.helper.os, "geteuid", lambda: 1000):
            self.assertNotEqual(self.main("set", "c-x")[0], 0)
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main()
