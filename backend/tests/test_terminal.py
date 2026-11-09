"""fleet, the command for a terminal: parameters from options, output for a
person, the shortcuts, and that it reaches the backend as the interface does."""

import io
import json
import os
import tempfile
import unittest

from test_phase2 import Base

from cockpit_fleet import cli, terminal
from cockpit_fleet.mcp import Unreachable
from cockpit_fleet.terminal import Failure, Terminal, Usage

COMMANDS = cli.load_commands()
TICKET = "a-" + "0" * 32


def parse(name, *args, stdin=""):
    return terminal.parse_params(COMMANDS[name], list(args), io.StringIO(stdin))


class ParseTest(unittest.TestCase):
    def test_options_take_the_type_the_command_declares(self):
        self.assertEqual(parse("run.list", "--limit", "3", "--status=failed"),
                         {"limit": 3, "status": "failed"})
        self.assertEqual(parse("run.start", "--template-id", "t1", "--check", "--no-diff",
                               "--answers", '{"a": 1}'),
                         {"template_id": "t1", "check": True, "diff": False, "answers": {"a": 1}})
        self.assertEqual(parse("run.start", "--check=no"), {"check": False})
        self.assertEqual(parse("run.adhoc", "--vault-credential-ids", "c1",
                               "--vault-credential-ids", "c2"),
                         {"vault_credential_ids": ["c1", "c2"]})

    def test_a_lone_word_is_the_id(self):
        self.assertEqual(parse("run.get", "r1"), {"id": "r1"})
        with self.assertRaises(Usage):
            parse("run.list", "r1")
        with self.assertRaises(Usage):
            parse("run.get", "r1", "r2")

    def test_a_value_may_come_from_a_file_or_stdin(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml") as source:
            source.write("- hosts: all\n")
            source.flush()
            self.assertEqual(parse("file.write", "--content", f"@{source.name}")["content"],
                             "- hosts: all\n")
        self.assertEqual(parse("file.write", "--content", "@-", stdin="x")["content"], "x")
        self.assertEqual(parse("file.write", "--content", "@@home")["content"], "@home")
        with self.assertRaises(Usage):
            parse("file.write", "--content", "@/nonexistent/file")

    def test_mistakes_are_said(self):
        for args in (["--nope", "1"], ["--limit"], ["--limit", "many"], ["--no-status"]):
            with self.assertRaises(Usage, msg=args):
                parse("run.list", *args)
        with self.assertRaises(Usage):
            parse("run.start", "--answers", "{not json")

    def test_the_first_words_name_the_command(self):
        self.assertEqual(terminal.find_command(COMMANDS, ["run", "list", "--x"])[0].name,
                         "run.list")
        self.assertEqual(terminal.find_command(COMMANDS, ["whoami"])[0].name, "whoami")
        self.assertIsNone(terminal.find_command(COMMANDS, ["run.list"]))
        self.assertIsNone(terminal.find_command(COMMANDS, ["nothing"]))


class CompleteTest(unittest.TestCase):
    def test_commands_verbs_and_options(self):
        complete = terminal.complete
        self.assertEqual(complete(COMMANDS, ["r"]), ["result", "rule", "run"])
        self.assertIn("start", complete(COMMANDS, ["run", ""]))
        self.assertNotIn("reconcile", complete(COMMANDS, ["run", ""]))
        self.assertEqual(complete(COMMANDS, ["run", "start", "--t"]), ["--template-id"])
        self.assertIn("--json", complete(COMMANDS, ["run", "list", "--"]))
        self.assertIn("--detach", complete(COMMANDS, ["launch", "x", "--"]))
        self.assertEqual(complete(COMMANDS, ["help", "temp"]), ["template"])

    def test_names_and_choices_come_from_the_backend(self):
        listed = {"template.list": {"templates": [{"id": "t1", "name": "Say hello"},
                                                 {"id": "t2", "name": "Patch"}]},
                  "run.list": {"runs": [{"id": "r9"}, {"id": "r8"}]},
                  "credential.list": {"credentials": [{"id": "c1", "name": "nodes",
                                                       "secret_names": {"private_key": "c-x",
                                                                        "password": "c-y"}}]}}
        asked = []

        def lookup(name, params):
            asked.append(name)
            return listed[name]
        complete = terminal.complete
        self.assertEqual(complete(COMMANDS, ["launch", "S"], lookup), ["Say hello"])
        self.assertEqual(complete(COMMANDS, ["launch", "'Say h"], lookup), ["Say hello"])
        self.assertEqual(complete(COMMANDS, ["launch", "Say\\ h"], lookup), ["Say hello"])
        self.assertEqual(complete(COMMANDS, ["log", ""], lookup), ["r9", "r8"])
        self.assertEqual(complete(COMMANDS, ["secret", "nodes", ""], lookup),
                         ["password", "private_key"])
        self.assertEqual(complete(COMMANDS, ["template", "update", ""], lookup), ["Patch", "Say hello"])
        self.assertEqual(complete(COMMANDS, ["run", "start", "--template-id", "P"], lookup), ["Patch"])
        self.assertEqual(complete(COMMANDS, ["check", "create", "--kind", "comp"], lookup),
                         ["compliance"])
        self.assertEqual(complete(COMMANDS, ["run", "start", "--limit", ""], lookup), [])
        self.assertIn("--archive", complete(COMMANDS, ["project", "upload", "x", "--a"], lookup))
        # Without the backend, or when it refuses, there is nothing to offer.
        self.assertEqual(complete(COMMANDS, ["launch", ""]), [])

        def refusing(name, params):
            raise Failure("forbidden")
        self.assertEqual(complete(COMMANDS, ["launch", ""], refusing), [])


class RenderTest(unittest.TestCase):
    def test_records_become_a_table_with_the_known_columns_first(self):
        text = terminal.render({"runs": [
            {"title": "Say hello", "id": "r1", "status": "successful", "options": {"a": 1},
             "created_at": 0, "rc": 0},
            {"title": "x" * 60, "id": "r2", "status": "failed", "options": {},
             "created_at": None, "rc": None},
        ], "more": False})
        lines = text.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "STATUS", "TITLE", "CREATED", "AT", "RC"])
        self.assertIn("…", lines[2])
        self.assertNotIn("options", text)
        self.assertNotIn("more", text)
        more = terminal.render({"runs": [{"id": "r1", "cursor": 7}], "more": True})
        self.assertTrue(more.startswith("more: yes, --before 7 for the next page\n"), more)

    def test_notifications_show_their_state_and_channel(self):
        text = terminal.table([{"id": 3, "channel_id": "n1", "event": "failed", "run_id": "r1",
                                "state": "pending", "attempts": 2, "next_at": 0, "error": "HTTP 401",
                                "created_at": 0, "sent_at": None, "channel": "ops",
                                "title": "deploy"}])
        self.assertEqual(text.splitlines()[0].split(),
                         ["ID", "STATE", "TITLE", "EVENT", "CHANNEL", "NEXT", "AT"])

    def test_a_table_fits_the_width(self):
        text = terminal.table([{"id": "r1", "title": "a long title"}], width=8)
        self.assertTrue(all(len(line) <= 8 for line in text.splitlines()))
        self.assertEqual(terminal.table([]), "(none)\n")

    def test_other_answers_are_key_and_value(self):
        self.assertEqual(terminal.render({"user": "anna", "commands": ["a", "b"], "x": None,
                                          "text": "one\ntwo"}),
                         "user: anna\ncommands: a, b\nx: -\ntext:\n  one\n  two\n")


class Fake:
    """A backend that answers from a script, and records what it was asked."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.secrets = []

    def call(self, name, params):
        self.calls.append((name, params))
        answer = self.answers[name]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if "ok" in answer else {"ok": True, "data": answer}

    def secret(self, args, value):
        self.secrets.append((args, value))
        return TICKET + "\n"


def term(fake, asked=(), stdin="", **extra):
    replies = list(asked)
    out = io.StringIO()
    return Terminal(call=fake.call, secret=fake.secret, ask=lambda text, secret: replies.pop(0),
                    sleep=lambda seconds: None, out=out, err=io.StringIO(),
                    stdin=io.StringIO(stdin), **extra)


SURVEY = [{"name": "greeting", "label": "Greeting", "type": "text", "required": True},
          {"name": "count", "type": "number", "required": False},
          {"name": "token", "label": "Token", "type": "secret", "required": True}]
TEMPLATE = {"id": "t1", "name": "Say hello", "survey": SURVEY}
RUN = {"id": "r1", "title": "Say hello", "status": "queued"}
RESULT = {"id": "r1", "title": "Say hello", "status": "failed", "rc": 2, "error": "", "done": True,
          "stats": {"a": {"ok": 1, "changed": 0, "failures": 1, "unreachable": 0, "skipped": 0}},
          "tasks": [{"play": "p", "task": "Greet", "ok": 0, "changed": 0, "failed": 1,
                     "unreachable": 0, "skipped": 0, "problems": {"a": "boom"}}]}


class LaunchTest(unittest.TestCase):
    def test_secret_answers_are_asked_for_and_go_to_fleet_secret_only(self):
        fake = Fake({"template.list": {"templates": [TEMPLATE]}, "run.start": RUN})
        t = term(fake, asked=["hush"], interactive=True)
        code = terminal.run(t, COMMANDS, ["launch", "say HELLO", "-a", "greeting=ciao",
                                          "-a", "count=3", "--limit", "a", "--detach"])
        self.assertEqual(code, terminal.OK)
        self.assertEqual(fake.secrets, [(["answer"], "hush")])
        name, params = fake.calls[-1]
        self.assertEqual(name, "run.start")
        self.assertEqual(params, {"template_id": "t1", "limit": "a", "check": False, "diff": False,
                                  "answers": {"greeting": "ciao", "count": 3, "token": TICKET},
                                  "serial": False, "via": "cli"})
        self.assertNotIn("hush", json.dumps(fake.calls))

    def test_what_is_missing_is_asked_at_a_terminal_and_refused_elsewhere(self):
        fake = Fake({"template.list": {"templates": [TEMPLATE]}, "run.start": RUN})
        terminal.run(term(fake, asked=["hi", "hush"], interactive=True), COMMANDS,
                     ["launch", "t1", "--detach"])
        self.assertEqual(fake.calls[-1][1]["answers"], {"greeting": "hi", "token": TICKET})
        with self.assertRaises(Usage):
            terminal.run(term(fake), COMMANDS, ["launch", "t1", "-a", "greeting=x"])
        with self.assertRaises(Usage):
            terminal.run(term(fake), COMMANDS, ["launch", "t1", "-a", "token=x"])
        with self.assertRaises(Usage):
            terminal.run(term(fake), COMMANDS, ["launch", "t1", "-a", "nope=x"])
        with self.assertRaises(Usage):
            terminal.run(term(fake), COMMANDS, ["launch", "t1", "-a", "count=many"])

    def test_a_template_is_named_once(self):
        twins = [{**TEMPLATE, "id": "t1"}, {**TEMPLATE, "id": "t2"}]
        fake = Fake({"template.list": {"templates": twins}})
        with self.assertRaises(Failure) as caught:
            terminal.run(term(fake), COMMANDS, ["launch", "say hello"])
        self.assertIn("t1, t2", str(caught.exception))
        with self.assertRaises(Failure):
            terminal.run(term(fake), COMMANDS, ["launch", "other"])

    def test_a_followed_run_prints_its_output_then_how_it_went(self):
        fake = Fake({
            "template.list": {"templates": [{**TEMPLATE, "survey": []}]},
            "run.start": RUN,
            "run.log": [{"text": "PLAY \x1b[0;32mok\x1b[0m\n", "offset": 10, "more": False},
                        {"text": "", "offset": 10, "more": False},
                        {"text": "", "offset": 10, "more": False},
                        {"text": "RECAP\n", "offset": 16, "more": False}],
            "run.get": [{"status": "running"}, {"status": "failed"}],
            "run.result": RESULT,
        })
        t = term(fake)
        code = terminal.run(t, COMMANDS, ["launch", "t1"])
        self.assertEqual(code, terminal.NOT_SUCCESSFUL)
        out = t.out.getvalue()
        self.assertTrue(out.startswith("PLAY ok\nRECAP\n"), out)
        self.assertIn("r1  Say hello: failed, rc 2", out)
        self.assertIn("Greet on a: boom", out)
        offsets = [params["offset"] for name, params in fake.calls if name == "run.log"]
        self.assertEqual(offsets, [0, 10, 10, 10])

    def test_a_launch_in_series_says_what_it_waits_for(self):
        fake = Fake({
            "template.list": {"templates": [{**TEMPLATE, "survey": []}]},
            "run.start": RUN,
            "run.log": {"text": "", "offset": 0, "more": False},
            "run.get": [{"status": "queued", "waiting_for": {"id": "r0", "title": "backup"}},
                        {"status": "successful"}],
            "run.result": {**RESULT, "status": "successful"},
        })
        t = term(fake)
        terminal.run(t, COMMANDS, ["launch", "t1", "--serial"])
        self.assertTrue(fake.calls[1][1]["serial"])
        self.assertIn("It waits for run r0 (backup) on the same inventory", t.err.getvalue())

    def test_a_launch_that_waits_says_who_decides(self):
        for approval, said in (({"mode": "confirm", "can_decide": True},
                                "It waits for your confirmation"),
                               ({"mode": "other", "can_decide": False},
                                "It starts once another user approves it")):
            fake = Fake({
                "template.list": {"templates": [{**TEMPLATE, "survey": []}]},
                "run.start": {**RUN, "status": "pending_approval"},
                "run.get": {"status": "pending_approval", "approval": approval},
            })
            t = term(fake)
            terminal.run(t, COMMANDS, ["launch", "t1", "--detach"])
            self.assertEqual(t.err.getvalue(), "")
            fake.answers.update({"run.log": {"text": "", "offset": 0, "more": False},
                                 "run.get": [{"status": "pending_approval",
                                              "approval": approval},
                                             {"status": "successful"}],
                                 "run.result": {**RESULT, "status": "successful"}})
            t = term(fake)
            terminal.run(t, COMMANDS, ["launch", "t1"])
            self.assertIn(said, t.err.getvalue())

    def test_result_waits_while_a_person_decides(self):
        fake = Fake({"run.result": [{**RESULT, "status": "pending_approval", "done": False},
                                    {**RESULT, "status": "running", "done": False},
                                    {**RESULT, "status": "successful"}]})
        slept = []
        t = term(fake)
        t.sleep = slept.append
        self.assertEqual(terminal.run(t, COMMANDS, ["result", "r1", "--wait"]), terminal.OK)
        self.assertEqual((len(fake.calls), slept), (3, [5]))

    def test_result_waits_until_the_end(self):
        fake = Fake({"run.result": [{**RESULT, "status": "running", "done": False},
                                    {**RESULT, "status": "successful"}]})
        self.assertEqual(terminal.run(term(fake), COMMANDS, ["result", "r1", "--wait"]),
                         terminal.OK)
        self.assertEqual([params["wait"] for _, params in fake.calls], [50, 50])


    def test_a_run_whose_output_was_deleted_says_so(self):
        pruned = {**RESULT, "pruned_at": 1_800_000_000, "tasks": []}
        fake = Fake({"run.log": {"text": "", "offset": 0, "more": False}, "run.get": pruned,
                     "run.result": pruned})
        t = term(fake)
        self.assertEqual(terminal.run(t, COMMANDS, ["log", "r1"]), terminal.OK)
        self.assertIn("r1: its output was deleted on ", t.err.getvalue())
        t = term(fake)
        terminal.run(t, COMMANDS, ["result", "r1"])
        self.assertIn("only the outcome is kept", t.out.getvalue())


class SecretTest(unittest.TestCase):
    CREDENTIALS = {"credentials": [{"id": "c1", "name": "git",
                                    "secret_names": {"token": "c-c1-token"}}]}

    def test_asked_twice_at_a_terminal(self):
        fake = Fake({"credential.list": self.CREDENTIALS})
        terminal.run(term(fake, asked=["s3cret", "s3cret"], interactive=True), COMMANDS,
                     ["secret", "git", "token"])
        self.assertEqual(fake.secrets, [(["set", "c-c1-token"], "s3cret")])
        with self.assertRaises(Usage):
            terminal.run(term(fake, asked=["a", "b"], interactive=True), COMMANDS,
                         ["secret", "git", "token"])
        with self.assertRaises(Usage):
            terminal.run(term(fake, asked=[]), COMMANDS, ["secret", "git", "password"])

    def test_from_stdin_one_line_loses_its_newline_a_key_keeps_it(self):
        fake = Fake({"credential.list": self.CREDENTIALS})
        terminal.run(term(fake, stdin="tok\n"), COMMANDS, ["secret", "c1", "token"])
        key = "-----BEGIN KEY-----\nabc\n-----END KEY-----\n"
        terminal.run(term(fake, stdin=key), COMMANDS, ["secret", "c1", "token"])
        self.assertEqual([value for _, value in fake.secrets], ["tok", key])


class ErrorsTest(unittest.TestCase):
    def test_a_refusal_and_an_unreachable_backend_are_failures(self):
        fake = Fake({"whoami": [{"ok": False, "error": {"code": "forbidden", "message": "no"}},
                                Unreachable("sudo: a password is required")]})
        with self.assertRaises(Failure) as caught:
            terminal.run(term(fake), COMMANDS, ["whoami"])
        self.assertEqual(str(caught.exception), "forbidden: no")
        with self.assertRaises(Failure) as caught:
            terminal.run(term(fake), COMMANDS, ["whoami"])
        self.assertIn("cockpit-fleet-viewer", str(caught.exception))

    def test_unknown_commands_and_help(self):
        t = term(Fake({"whoami": {"commands": ["run.list", "run.reconcile"]}}))
        self.assertEqual(terminal.run(t, COMMANDS, []), terminal.OK)
        self.assertIn("  run list", t.out.getvalue())
        self.assertNotIn("run start", t.out.getvalue())
        self.assertNotIn("reconcile", t.out.getvalue())
        with self.assertRaises(Usage):
            terminal.run(t, COMMANDS, ["run", "fly"])
        t = term(Fake({}))
        terminal.run(t, COMMANDS, ["run", "start", "--help"])
        self.assertIn("--template-id id  required", t.out.getvalue())


class AssistantTest(unittest.TestCase):
    AGENTS = {"agents": [{"name": "claude", "label": "Claude", "login": "claude /login",
                          "installed": True, "terminal": True, "setup": ""},
                         {"name": "acp-only", "label": "", "login": "", "installed": True,
                          "terminal": False, "setup": ""}],
              "unavailable": "", "max_role": "operator", "max_sessions": 2, "open_sessions": 0,
              "profiles": ["observer", "assisted", "administrator"], "default_profile": "assisted",
              "to_accept": ["administrator"]}

    def test_the_agent_with_a_terminal_program_runs_through_fleet_chat(self):
        ran = []
        t = term(Fake({"chat.agents": self.AGENTS}), interactive=True)
        t.execute = lambda argv: ran.append(argv) or 0
        self.assertEqual(terminal.run(t, COMMANDS, ["assistant"]), terminal.OK)
        self.assertEqual(ran, [["/usr/libexec/cockpit-fleet/fleet-chat", "--terminal",
                                "--profile", "assisted", "claude"]])
        t.execute = lambda argv: 1
        self.assertEqual(terminal.run(t, COMMANDS, ["assistant", "claude"]), 1)
        self.assertIn("claude /login", t.err.getvalue())

    def test_what_stops_it_is_said(self):
        t = term(Fake({"chat.agents": self.AGENTS}), interactive=False)
        with self.assertRaises(Usage):
            terminal.run(t, COMMANDS, ["assistant"])
        t = term(Fake({"chat.agents": self.AGENTS}), interactive=True)
        with self.assertRaisesRegex(Failure, "acp-only"):
            terminal.run(t, COMMANDS, ["assistant", "acp-only"])
        off = {**self.AGENTS, "unavailable": "no agent is named in [chat] of config.toml"}
        with self.assertRaisesRegex(Failure, "no agent is named"):
            terminal.run(term(Fake({"chat.agents": off}), interactive=True), COMMANDS,
                         ["assistant"])
        self.assertEqual(terminal.complete(COMMANDS, ["assistant", ""],
                                           lambda name, params: self.AGENTS), ["claude"])
        self.assertEqual(terminal.complete(COMMANDS, ["assistant", "--profile", ""],
                                           lambda name, params: self.AGENTS),
                         ["observer", "assisted", "administrator"])
        self.assertEqual(terminal.complete(COMMANDS, ["assistant", "--profile", "observer", ""],
                                           lambda name, params: self.AGENTS), ["claude"])
        with self.assertRaisesRegex(Usage, "not enabled here: observer, assisted"):
            terminal.run(term(Fake({"chat.agents": self.AGENTS}), interactive=True), COMMANDS,
                         ["assistant", "--profile", "autonomous"])

    def test_a_profile_beyond_assisted_is_accepted_first_once(self):
        ran = []
        fake = Fake({"chat.agents": self.AGENTS, "chat.accept": {"profile": "administrator"}})
        t = term(fake, asked=["no"], interactive=True)
        t.execute = lambda argv: ran.append(argv) or 0
        with self.assertRaisesRegex(Failure, "not accepted"):
            terminal.run(t, COMMANDS, ["assistant", "--profile", "administrator"])
        self.assertIn("steer the assistant", t.out.getvalue())
        self.assertEqual((ran, [name for name, _ in fake.calls]), ([], ["chat.agents"]))
        t = term(fake, asked=["YES"], interactive=True)
        t.execute = lambda argv: ran.append(argv) or 0
        self.assertEqual(terminal.run(t, COMMANDS, ["assistant", "--profile", "administrator"]),
                         terminal.OK)
        self.assertEqual(fake.calls[-1], ("chat.accept", {"profile": "administrator",
                                                          "via": "cli"}))
        self.assertEqual(ran[-1][2:4], ["--profile", "administrator"])
        # Once accepted, it is not asked again.
        accepted = {**self.AGENTS, "to_accept": []}
        t = term(Fake({"chat.agents": accepted}), interactive=True)
        t.execute = lambda argv: 0
        self.assertEqual(terminal.run(t, COMMANDS, ["assistant", "--profile", "administrator"]),
                         terminal.OK)


class DraftTest(unittest.TestCase):
    DIFF = {"draft": "a0574ca423e012e2", "revision": "3fc5" * 10, "author": "anna", "time": 0,
            "changes": [{"path": "roles/web/tasks/main.yml", "status": "added",
                         "directory": False, "conflict": False},
                        {"path": "site.yml", "status": "changed", "directory": False,
                         "conflict": True}],
            "text": "--- /dev/null\n+++ b/roles/web/tasks/main.yml\n+- name: \x1b]0;x\x07nginx\n",
            "cut": False,
            "checks": [{"run_id": "r1", "path": "site.yml", "status": "failed",
                        "revision": "x", "current": False, "lint": None}]}

    def test_a_draft_is_shown_with_how_to_merge_what_was_shown(self):
        fake = Fake({"project.list": {"projects": [{"id": "p1", "name": "demo", "kind": "managed"},
                                                   {"id": "p2", "name": "git", "kind": "git"}]},
                     "inventory.list": {"inventories": []}, "draft.diff": self.DIFF})
        t = term(fake)
        self.assertEqual(terminal.run(t, COMMANDS, ["draft", "diff", "--id", "demo", "--draft",
                                                    "a0574ca423e012e2"]), terminal.OK)
        self.assertEqual(fake.calls[-1], ("draft.diff", {"id": "p1", "draft": "a0574ca423e012e2",
                                                         "via": "cli"}))
        out = t.out.getvalue()
        self.assertIn("added    roles/web/tasks/main.yml", out)
        self.assertIn("site.yml  (changed in main too: cannot be merged)", out)
        self.assertIn("failed     site.yml  run r1  (of an earlier version)", out)
        self.assertIn("+- name: ?]0;x?nginx", out)
        self.assertNotIn("\x1b", out)
        self.assertIn(f"fleet draft merge --id p1 --draft a0574ca423e012e2 --revision {'3fc5' * 10}",
                      out)
        with self.assertRaisesRegex(Failure, "no project or inventory 'git'"):
            terminal.run(term(fake), COMMANDS, ["draft", "diff", "--id", "git", "--draft", "x"])


class BackendTest(Base):
    """Through the real dispatch, as the caller would reach it through sudo."""

    def terminal(self, role):
        def call(name, params):
            return cli.dispatch(name, json.dumps(params).encode(), self.ctx(role))
        return Terminal(call=call, out=io.StringIO(), err=io.StringIO(), stdin=io.StringIO())

    def test_a_launch_is_the_callers_own_with_cli_in_the_audit_trail(self):
        template = self.template(survey=[{"name": "greeting", "type": "text", "required": True}])
        t = self.terminal("operator")
        code = terminal.run(t, COMMANDS, ["launch", "deploy", "-a", "greeting=ciao", "--detach"])
        self.assertEqual(code, terminal.OK)
        row = self.ctx().conn.execute(
            "SELECT user, role, command, outcome, via FROM audit ORDER BY id DESC").fetchone()
        self.assertEqual(tuple(row), ("anna", "operator", "run.start", "ok", "cli"))
        self.assertIn(f"template_id: {template['id']}", t.out.getvalue())

    def test_a_role_is_held_as_in_the_interface(self):
        with self.assertRaises(Failure) as caught:
            terminal.run(self.terminal("viewer"), COMMANDS,
                         ["credential", "create", "--name", "x", "--kind", "vault"])
        self.assertTrue(str(caught.exception).startswith("forbidden:"))
        t = self.terminal("admin")
        terminal.run(t, COMMANDS, ["credential", "create", "--name", "x", "--kind", "vault"])
        self.assertIn("name: x", t.out.getvalue())

    def test_a_name_stands_for_an_id(self):
        template = self.template()
        t = self.terminal("admin")
        terminal.run(t, COMMANDS, ["template", "update", "deploy", "--name", "deploy",
                                   "--project-id", "site", "--playbook", "site.yml",
                                   "--inventory-id", template["inventory_id"]])
        row = self.ctx().conn.execute("SELECT params FROM audit WHERE command = 'template.update'"
                                      " ORDER BY id DESC").fetchone()
        self.assertEqual(json.loads(row["params"])["id"], template["id"])
        with self.assertRaises(Failure) as caught:
            terminal.run(t, COMMANDS, ["template", "delete", "nothing"])
        self.assertIn("no template 'nothing'", str(caught.exception))

    def streaming(self, role, sent=None):
        """A terminal whose fleetctl moves files as main() does."""
        def stream(name, params, source, sink):
            ctx = self.ctx(role)
            if source is not None:
                data = source.read()
                ctx.stdin = io.BytesIO(data if sent is None else sent(data))
            answer = cli.dispatch(name, json.dumps(params).encode(), ctx)
            if sink is not None and answer["ok"]:
                with ctx.output as output:
                    sink.write(output.read())
                return {"ok": True, "data": {}}
            if sink is not None:
                return {"ok": False, "error": {"code": "failed",
                                               "message": answer["error"]["message"]}}
            return answer
        t = self.terminal(role)
        t.stream = stream
        return t

    def test_a_file_goes_into_the_store_and_comes_back(self):
        from cockpit_fleet import paths
        with tempfile.TemporaryDirectory() as here:
            local = f"{here}/setup.msi"
            with open(local, "wb") as source:
                source.write(b"installer " * 1000)
            t = self.streaming("operator", sent=lambda data: data[:4000])
            with self.assertRaises(Failure) as caught:
                terminal.run(t, COMMANDS, ["store", "put", "--file", local, "--path",
                                           "apps/setup.msi"])
            self.assertIn("can be resumed", str(caught.exception))
            t = self.streaming("operator")
            terminal.run(t, COMMANDS, ["store", "put", "--file", local, "--path",
                                       "apps/setup.msi", "--resume"])
            self.assertEqual((paths.STORE_DIR / "apps/setup.msi").read_bytes(),
                             b"installer " * 1000)
            row = self.ctx().conn.execute("SELECT via FROM audit WHERE command = 'store.put'"
                                          " ORDER BY id DESC").fetchone()
            self.assertEqual(row["via"], "cli")
            with self.assertRaises(Usage):
                terminal.run(t, COMMANDS, ["store", "put", "--path", "x"])

            back = f"{here}/back.msi"
            t = self.streaming("viewer")
            terminal.run(t, COMMANDS, ["store", "read", "--path", "apps/setup.msi",
                                       "--output", back])
            with open(back, "rb") as copy:
                self.assertEqual(copy.read(), b"installer " * 1000)
            with self.assertRaises(Failure):
                terminal.run(t, COMMANDS, ["store", "read", "--path", "apps/setup.msi",
                                           "--output", back])
            with self.assertRaises(Failure):
                terminal.run(t, COMMANDS, ["store", "read", "--path", "none",
                                           "--output", f"{here}/none"])
            self.assertFalse(os.path.exists(f"{here}/none"))

    def test_an_archive_is_read_from_a_file(self):
        with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
            archive.write(b"\x1f\x8b not really")
            archive.flush()
            rest, params = terminal.archive_params(["p1", "--archive", archive.name],
                                                   io.StringIO())
        self.assertEqual(rest, ["p1"])
        self.assertEqual(params["data"], "H4sgbm90IHJlYWxseQ==")
        self.assertTrue(params["filename"].endswith(".tar.gz"))
        rest, params = terminal.archive_params(["--archive=-"], io.StringIO("abc"))
        self.assertEqual((rest, params), ([], {"data": "YWJj"}))
        with self.assertRaises(Usage):
            terminal.archive_params(["--archive", "/nonexistent/x.tgz"], io.StringIO())

    def test_json_is_the_answer_as_given(self):
        t = self.terminal("viewer")
        t.as_json = True
        terminal.run(t, COMMANDS, ["whoami"])
        self.assertEqual(json.loads(t.out.getvalue())["role"], "viewer")


if __name__ == "__main__":
    unittest.main()


class CloneDirectoryTest(unittest.TestCase):
    def test_a_name_becomes_a_plain_directory_here(self):
        from cockpit_fleet.terminal import clone_directory
        for name, expected in (("site", "site"), ("--upload-pack=x", "upload-pack-x"),
                               ("../../etc", "etc"), ("my site/one", "my-site-one"),
                               ("..", "p1")):
            self.assertEqual(clone_directory({"name": name, "id": "p1"}), expected, name)


class CellTest(unittest.TestCase):
    def test_a_cell_holds_no_control_character(self):
        from cockpit_fleet.terminal import _cell
        self.assertEqual(_cell("name", "web\x1b]52;c;ZXZpbA==\x07\n01"), "web?]52;c;ZXZpbA==??01")
        self.assertEqual(_cell("name", ["a\x9bb"]), "a?b")

