"""Agents: declared parameters as tool schemas, the ceiling on what a call
made for an agent may do, templates as tools, and the MCP server itself."""

import io
import json
import time
import unittest
from dataclasses import replace
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import agents, cli, mcp, paths, profiles, schedules

# What each tool lets an agent do, beyond reading. A new tool, or one moved to
# another class, fails here until this table says so: the profiles of the
# assistant are made of these classes.
CLASS_TABLE = {
    "run": """check.run file.check inventory.parse maintenance.create maintenance.delete
        notification.retry run.cancel run.start schedule.disable schedule.enable schedule.run
        state.ack state.unack workflow.launch workflow.resume""",
    "adhoc": "run.adhoc",
    "config": """channel.create channel.test channel.update check.create check.update
        inventory.create inventory.update rule.create rule.update schedule.create
        schedule.update template.create template.update varset.create varset.update
        workflow.create workflow.update""",
    "code": """channel.delete check.delete config.import credential.create credential.delete
        credential.update file.change file.delete file.move file.restore file.write hostkey.add
        hostkey.remove inventory.delete project.collections project.create project.delete
        project.sync project.update rule.delete schedule.delete template.delete varset.delete
        workflow.delete""",
}


class ClassTest(unittest.TestCase):
    def setUp(self):
        cli.load_commands()

    def test_every_tool_has_its_class(self):
        expected = {name: kind for kind, names in CLASS_TABLE.items() for name in names.split()}
        for name, spec in cli.COMMANDS.items():
            self.assertIn(spec.agent, cli.AGENT_CLASSES, name)
            if spec.tool:
                self.assertEqual(spec.agent, expected.pop(name, "read"), name)
        self.assertEqual(expected, {}, "not tools")

    def test_a_command_that_changes_nothing_reads(self):
        for name, spec in cli.COMMANDS.items():
            if spec.agent == "read" and spec.tool:
                self.assertFalse(spec.mutating, name)

    def test_the_default_comes_from_role_and_mutating(self):
        register = cli.command
        try:
            for role, mutating, agent, kind in (("viewer", False, "", "read"),
                                                ("operator", True, "", "run"),
                                                ("admin", True, "", "config"),
                                                ("admin", True, "code", "code")):
                register("test.class", role, mutating, agent=agent)(lambda ctx, params: {})
                self.assertEqual(cli.COMMANDS["test.class"].agent, kind)
            with self.assertRaises(ValueError):
                register("test.class", "admin", True, agent="everything")
        finally:
            cli.COMMANDS.pop("test.class", None)


class SchemaTest(unittest.TestCase):
    def test_every_command_describes_itself(self):
        for name, spec in cli.COMMANDS.items():
            schema = spec.schema()
            json.dumps(schema)
            self.assertLessEqual(set(schema["required"]), set(schema["properties"]), name)
            self.assertTrue((spec.handler.__doc__ or "").strip(), f"{name} has no description")

    def test_types_bounds_and_defaults(self):
        schema = cli.COMMANDS["run.list"].schema()
        self.assertEqual(schema["properties"]["limit"],
                         {"type": "integer", "minimum": 1, "maximum": 200, "default": 50})
        self.assertEqual(schema["required"], [])
        self.assertFalse(schema["additionalProperties"])
        start = cli.COMMANDS["run.start"].schema()
        self.assertEqual(start["required"], ["template_id"])
        self.assertEqual(start["properties"]["answers"], {"type": "object"})
        kind = cli.COMMANDS["project.create"].schema()["properties"]["kind"]
        self.assertEqual(kind["enum"], ["local", "git", "managed", "archive"])

    def test_parameters_are_checked_before_the_handler(self):
        read = cli.COMMANDS["run.list"].read
        self.assertEqual(read({})["limit"], 50)
        self.assertEqual(read({"limit": 3}).given, frozenset({"limit"}))
        for bad in ({"limit": 0}, {"limit": "3"}, {"nope": 1}, {"status": 3}):
            with self.assertRaises(cli.BadRequest, msg=bad):
                read(bad)


class FormsTest(Base):
    def test_a_parameter_of_another_form_of_the_command_is_refused(self):
        tree = str(self.root / "src")
        base = {"name": "p", "kind": "local", "path": tree}
        self.assertEqual(self.code("project.create", {**base, "url": "https://example.org/a.git"}),
                         "bad_request")
        self.assertEqual(self.code("project.create", {"name": "p", "kind": "local"}), "bad_request")
        self.assertEqual(self.code("project.create", {"name": "m", "kind": "managed",
                                                      "path": tree}), "bad_request")
        self.assertEqual(self.code("inventory.create", {"name": "i", "kind": "managed",
                                                        "content": "", "project_id": "p1"}),
                         "bad_request")
        self.assertEqual(self.code("inventory.create", {"name": "f", "kind": "file",
                                                        "files": [{"path": "a", "content": ""}],
                                                        "project_id": "p1", "path": "a"}),
                         "bad_request")
        self.assertEqual(self.code("hostkey.scan", {}), "bad_request")


class CeilingTest(Base):
    def via(self, name, params=None, role="admin", **config):
        self.config = replace(self.config, **config)
        return self.call(name, {**(params or {}), "via": "mcp:test-client"}, role)

    def audit(self):
        return [(row["command"], row["outcome"], row["role"], row["via"], row["detail"])
                for row in self.ctx().conn.execute("SELECT * FROM audit ORDER BY id")]

    def test_nothing_goes_through_while_agents_are_not_enabled(self):
        answer = self.via("whoami")
        self.assertEqual(answer["error"]["code"], "forbidden")
        self.assertEqual(self.audit(), [("whoami", "denied", "admin", "mcp:test-client",
                                         "agents are not enabled")])

    def test_the_role_is_lowered_to_the_ceiling_never_raised(self):
        who = self.via("whoami", mcp_enabled=True)["data"]
        self.assertEqual(who["role"], "operator")
        self.assertNotIn("run.adhoc", who["commands"])
        self.assertEqual(self.via("whoami", role="viewer", mcp_enabled=True)["data"]["role"],
                         "viewer")
        self.assertEqual(self.via("whoami", mcp_enabled=True, mcp_max_role="admin")["data"]["role"],
                         "admin")
        self.assertEqual(self.via("whoami", role=None, mcp_enabled=True)["error"]["code"],
                         "forbidden")

    def test_a_command_above_the_ceiling_is_refused_and_recorded(self):
        answer = self.via("credential.create", {"name": "x", "kind": "vault"}, mcp_enabled=True,
                          chat_profiles=("administrator",),
                          chat_default_profile="administrator")
        self.assertEqual(answer["error"]["code"], "forbidden")
        self.assertEqual(self.audit(), [("credential.create", "denied", "operator",
                                         "mcp:test-client", "requires admin")])
        self.assertNotIn("via", json.loads(
            self.ctx().conn.execute("SELECT params FROM audit").fetchone()[0]))

    def test_what_an_agent_does_is_recorded_with_where_it_came_from(self):
        template = self.template()
        run = self.via("run.start", {"template_id": template["id"]}, mcp_enabled=True)
        self.assertTrue(run["ok"], run)
        self.assertEqual(self.audit()[-1][:4], ("run.start", "ok", "operator", "mcp:test-client"))
        entry = self.ok("audit.list")["entries"][0]
        self.assertEqual((entry["command"], entry["via"]), ("run.start", "mcp:test-client"))
        # Without it the same person is not an agent: nothing is recorded as one.
        self.ok("run.cancel", {"id": run["data"]["id"]})
        self.assertEqual(self.audit()[-1][3], "")

    def test_a_call_from_the_terminal_is_recorded_and_not_held_as_an_agent(self):
        answer = self.call("credential.create", {"name": "x", "kind": "vault", "via": "cli"})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.audit()[-1][:4], ("credential.create", "ok", "admin", "cli"))
        self.assertEqual(self.via("whoami", mcp_enabled=False)["error"]["code"], "forbidden")

    def test_via_must_be_a_plain_name(self):
        self.config = replace(self.config, mcp_enabled=True)
        for bad in ("", "x" * 100, "a\nb", 3, ["mcp"], "-x"):
            self.assertEqual(self.call("whoami", {"via": bad})["error"]["code"], "bad_request", bad)


class ProfileTest(Base):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, mcp_enabled=True, mcp_max_role="admin",
                              chat_profiles=tuple(profiles.PROFILES))

    def via(self, name, params=None, profile=None, role="admin"):
        extra = {} if profile is None else {"profile": profile}
        return self.call(name, {**(params or {}), "via": "mcp:test", **extra}, role)

    def audit(self):
        return [(row["command"], row["outcome"], row["profile"], row["detail"])
                for row in self.ctx().conn.execute("SELECT * FROM audit ORDER BY id")]

    def adhoc(self, module="ansible.builtin.ping", **extra):
        if not hasattr(self, "inventory"):
            self.inventory = self.ok("inventory.create", {
                "name": "inv", "kind": "managed", "content": "all:\n  hosts:\n    a:\n"})
        return {"inventory_id": self.inventory["id"], "module": module, "limit": "all", **extra}

    def test_an_agent_that_names_none_gets_the_default_and_it_is_recorded(self):
        template = self.template()
        self.assertTrue(self.via("run.start", {"template_id": template["id"]})["ok"])
        self.assertEqual(self.audit()[-1][:3], ("run.start", "ok", "assisted"))
        self.assertEqual(self.ok("audit.list")["entries"][0]["profile"], "assisted")
        self.ok("run.list")
        self.ok("credential.create", {"name": "x", "kind": "vault"})
        self.assertEqual(self.audit()[-1][:3], ("credential.create", "ok", ""))

    def test_a_class_outside_the_profile_is_refused_and_recorded(self):
        template = self.template()
        answer = self.via("run.start", {"template_id": template["id"]}, "observer")
        self.assertEqual(answer["error"]["code"], "forbidden")
        self.assertIn("observer", answer["error"]["message"])
        self.assertEqual(self.audit()[-1], ("run.start", "denied", "observer",
                                            "run is outside the profile observer"))
        self.assertTrue(self.via("run.list", {}, "observer")["ok"])
        for profile in ("assisted", "diagnostic", "autonomous"):
            answer = self.via("template.delete", {"id": template["id"]}, profile)
            self.assertEqual(answer["error"]["code"], "forbidden", profile)
            answer = self.via("channel.create", {"name": "x"}, profile)
            self.assertEqual(answer["error"]["code"], "forbidden", profile)
            answer = self.via("credential.create", {"name": "x", "kind": "vault"}, profile)
            self.assertEqual(answer["error"]["code"], "forbidden", profile)
        self.assertTrue(self.via("credential.create", {"name": "x", "kind": "vault"},
                                 "administrator")["ok"])

    def test_a_profile_not_enabled_is_refused_and_one_made_up_too(self):
        self.config = replace(self.config, chat_profiles=("observer", "assisted"))
        for profile in ("administrator", "root", 3, ["assisted"]):
            answer = self.via("whoami", {}, profile)
            self.assertEqual(answer["error"]["code"], "forbidden", profile)
        self.assertEqual(self.audit()[0][3], "profile 'administrator' not enabled")

    def test_the_profile_of_a_person_is_no_parameter(self):
        self.assertEqual(self.code("whoami", {"profile": "observer"}), "bad_request")
        self.assertEqual(self.code("whoami", {"profile": "observer", "via": "cli"}),
                         "bad_request")

    def test_a_diagnostic_profile_runs_only_modules_that_change_nothing(self):
        refused = [self.adhoc("ansible.builtin.shell", free_form="reboot"),
                   self.adhoc(become=True)]
        for params in refused:
            answer = self.via("run.adhoc", params, "diagnostic")
            self.assertEqual(answer["error"]["code"], "forbidden", params)
        self.assertEqual(self.audit()[-1][3], "adhoc is outside the profile diagnostic")
        self.assertTrue(self.via("run.adhoc", self.adhoc(), "diagnostic")["ok"])
        self.assertEqual(self.audit()[-1][:3], ("run.adhoc", "ok", "diagnostic"))
        self.assertEqual(self.via("run.adhoc", self.adhoc(), "assisted")["error"]["code"],
                         "forbidden")
        self.config = replace(self.config, mcp_adhoc_modules=())
        self.assertEqual(self.via("run.adhoc", self.adhoc(), "diagnostic")["error"]["code"],
                         "forbidden")
        self.assertTrue(self.via("run.adhoc", self.adhoc("ansible.builtin.shell",
                                                         free_form="uptime"),
                                 "administrator")["ok"])

    def test_the_role_still_bounds_a_profile(self):
        self.config = replace(self.config, mcp_max_role="operator")
        answer = self.via("credential.create", {"name": "x", "kind": "vault"}, "administrator")
        self.assertEqual(answer["error"]["code"], "forbidden")
        self.assertEqual(self.audit()[-1][3], "requires admin")

    def test_tools_are_those_of_the_profile(self):
        self.template()

        def tools(profile):
            answer = self.via("mcp.tools", {}, profile)
            self.assertEqual(answer["data"]["profile"], profile or "assisted")
            return {tool["name"]: tool for tool in answer["data"]["tools"]}

        observer = tools("observer")
        self.assertEqual({tool["class"] for tool in observer.values()}, {"read"})
        self.assertIn("run_list", observer)
        assisted = tools(None)
        self.assertEqual({tool["class"] for tool in assisted.values()}, {"read", "run"})
        self.assertTrue(any(name.startswith("launch_") for name in assisted))
        self.assertNotIn("run_adhoc", assisted)
        adhoc = tools("diagnostic")["run_adhoc"]
        self.assertEqual(adhoc["class"], "adhoc-diagnostic")
        self.assertEqual(adhoc["inputSchema"]["properties"]["module"]["enum"],
                         list(self.config.mcp_adhoc_modules))
        self.assertNotIn("become", adhoc["inputSchema"]["properties"])
        self.assertIn("become", cli.COMMANDS["run.adhoc"].schema()["properties"])
        administrator = tools("administrator")
        self.assertEqual(administrator["run_adhoc"]["class"], "adhoc")
        self.assertIn("file_write", administrator)
        self.config = replace(self.config, mcp_adhoc_modules=())
        self.assertNotIn("run_adhoc", tools("diagnostic"))

    def test_the_page_asks_the_tools_of_a_profile(self):
        names = {tool["name"] for tool in self.ok("mcp.tools", {"profile": "observer"})["tools"]}
        self.assertNotIn("run_start", names)
        self.assertIn("run_start", {tool["name"] for tool in self.ok("mcp.tools")["tools"]})
        self.config = replace(self.config, chat_profiles=("assisted",))
        self.assertEqual(self.code("mcp.tools", {"profile": "observer"}), "bad_request")

    def test_no_tool_takes_what_fleet_mcp_adds(self):
        for name, spec in cli.COMMANDS.items():
            if spec.tool:
                self.assertFalse({"via", "profile"} & set(spec.params), name)


class AutonomyTest(Base):
    SESSION = "0123456789abcdef"

    def setUp(self):
        super().setUp()
        cli.load_commands()
        self.config = replace(self.config, mcp_enabled=True, mcp_max_role="admin",
                              chat_profiles=("assisted", "autonomous"),
                              chat_default_profile="autonomous", chat_autonomous_runs=2)
        analyze = mock.patch.object(schedules, "analyze", lambda calendar, timezone="", count=1:
                                    (calendar, [time.time() + 3600]))
        analyze.start()
        self.addCleanup(analyze.stop)

    def via(self, name, params=None, session=SESSION, profile="autonomous", role="admin"):
        extra = {"session": session} if session else {}
        return self.call(name, {**(params or {}), "via": "mcp:test", "profile": profile,
                                **extra}, role)

    def refused(self, answer, words):
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["error"]["code"], "forbidden")
        self.assertIn(words, answer["error"]["message"])

    def window(self, inventory_id, starts=-60, ends=3600):
        now = int(time.time())
        return self.ok("maintenance.create", {"inventory_id": inventory_id, "group": "all",
                                              "starts_at": now + starts, "ends_at": now + ends})

    def test_runs_are_counted_by_session_from_the_audit(self):
        template = self.template()
        launch = {"template_id": template["id"]}
        self.assertTrue(self.via("run.start", launch)["ok"])
        self.assertEqual(self.via("run.start", {"template_id": "nope"})["error"]["code"],
                         "not_found")
        self.assertTrue(self.via("run.start", launch)["ok"])
        self.refused(self.via("run.start", launch), "as many runs as")
        row = self.ctx().conn.execute("SELECT * FROM audit ORDER BY id DESC").fetchone()
        self.assertEqual((row["outcome"], row["session"], row["profile"]),
                         ("denied", self.SESSION, "autonomous"))
        self.assertEqual(self.ok("audit.list")["entries"][0]["session"], self.SESSION)
        # Another session counts on its own; a person and a profile that asks are not held.
        self.assertTrue(self.via("run.start", launch, session="fedcba9876543210")["ok"])
        self.assertTrue(self.via("run.start", launch, profile="assisted")["ok"])
        self.ok("run.start", launch)
        self.assertTrue(self.via("run.list")["ok"])

    def test_no_launch_without_a_session_and_no_session_made_up(self):
        template = self.template()
        self.refused(self.via("run.start", {"template_id": template["id"]}, session=""),
                     "within a session")
        for bad in ("x", "0123456789ABCDEF", 3, "0" * 17):
            self.assertEqual(self.via("run.list", {}, session=bad)["error"]["code"],
                             "bad_request", bad)

    def test_nothing_on_an_inventory_in_maintenance(self):
        template = self.template()
        launch = {"template_id": template["id"]}
        window = self.window(template["inventory_id"])
        self.refused(self.via("run.start", launch), "in maintenance")
        workflow = self.ok("workflow.create", {"name": "w", "nodes": [
            {"id": "a", "template_id": template["id"]}], "edges": []})
        self.refused(self.via("workflow.launch", {"id": workflow["id"]}), "in maintenance")
        schedule = self.ok("schedule.create", {"name": "s", "template_id": template["id"],
                                               "calendar": "hourly"})
        self.refused(self.via("schedule.run", {"id": schedule["id"]}), "in maintenance")
        check = self.ok("check.create", {"name": "disk", "kind": "disk",
                                         "inventory_id": template["inventory_id"]})
        self.refused(self.via("check.run", {"id": check["id"]}), "in maintenance")
        # A person, and an agent that asks, are not held by it.
        self.ok("run.start", launch)
        self.assertTrue(self.via("run.start", launch, profile="assisted")["ok"])
        self.ok("maintenance.delete", {"id": window["id"]})
        self.window(template["inventory_id"], starts=600)
        self.assertTrue(self.via("run.start", launch)["ok"])

    def test_it_never_silences_a_problem(self):
        template = self.template()
        for name, params in (("maintenance.create", {"inventory_id": template["inventory_id"],
                                                     "ends_at": int(time.time()) + 60}),
                             ("maintenance.delete", {"id": "m-x"}),
                             ("state.ack", {"host_id": "h-x", "check_id": "c-x"}),
                             ("state.unack", {"host_id": "h-x", "check_id": "c-x"})):
            self.refused(self.via(name, params), "does not silence")
            self.assertNotEqual(self.via(name, params, profile="assisted")["error"]["code"],
                                "forbidden", name)
        self.assertEqual(self.ok("maintenance.list")["maintenances"], [])

    def test_dry_runs_only(self):
        self.config = replace(self.config, chat_autonomous_dry_run=True, chat_autonomous_runs=9)
        template = self.template()
        run = self.via("run.start", {"template_id": template["id"], "check": False})["data"]
        self.assertTrue(self.ok("run.get", {"id": run["id"]})["options"]["check"])
        workflow = self.ok("workflow.create", {"name": "w", "nodes": [
            {"id": "a", "template_id": template["id"]}], "edges": []})
        self.refused(self.via("workflow.launch", {"id": workflow["id"]}), "dry runs only")
        schedule = self.ok("schedule.create", {"name": "s", "template_id": template["id"],
                                               "calendar": "hourly"})
        self.refused(self.via("schedule.run", {"id": schedule["id"]}), "dry runs only")
        check = self.ok("check.create", {"name": "disk", "kind": "disk",
                                         "inventory_id": template["inventory_id"]})
        self.assertNotEqual(self.via("check.run", {"id": check["id"]}).get("error", {}).get(
            "code"), "forbidden")
        run = self.via("run.start", {"template_id": template["id"]}, profile="assisted")["data"]
        self.assertFalse(self.ok("run.get", {"id": run["id"]})["options"]["check"])

    def test_a_template_that_needs_approval_still_waits_for_a_person(self):
        template = self.template(needs_approval=True)
        run = self.via("run.start", {"template_id": template["id"]})["data"]
        self.assertEqual(run["status"], "pending_approval")
        self.assertEqual(self.via("approval.approve", {"run_id": run["id"]})["error"]["code"],
                         "forbidden")
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "pending_approval")


class ToolsTest(Base):
    def test_commands_become_tools_by_role(self):
        names = {role: {tool["name"] for tool in self.ok("mcp.tools", role=role)["tools"]}
                 for role in ("viewer", "operator", "admin")}
        self.assertIn("run_list", names["viewer"])
        self.assertNotIn("run_start", names["viewer"])
        self.assertIn("run_result", names["operator"])
        self.assertNotIn("run_adhoc", names["operator"])
        self.assertIn("run_adhoc", names["admin"])
        for hidden in ("project_upload", "run_reconcile", "module_list", "mcp_tools", "run_events"):
            self.assertNotIn(hidden, names["admin"])
        tool = next(tool for tool in self.ok("mcp.tools")["tools"] if tool["name"] == "run_cancel")
        self.assertEqual((tool["command"], tool["fixed"], tool["readOnly"], tool["class"]),
                         ("run.cancel", {}, False, "run"))
        classes = {tool["name"]: tool["class"] for tool in self.ok("mcp.tools")["tools"]}
        self.assertEqual((classes["run_list"], classes["run_adhoc"], classes["file_write"]),
                         ("read", "adhoc", "code"))
        self.assertEqual(tool["inputSchema"]["required"], ["id"])
        self.assertEqual(tool["description"], "Cancel a run that is queued, running or waiting for approval.")

    def test_a_template_is_a_tool_whose_parameters_are_its_questions(self):
        template = self.template(limit_hosts="web*", survey=[
            {"name": "version", "label": "Version", "help": "To deploy.", "type": "text",
             "required": True, "max_length": 20},
            {"name": "count", "type": "number", "min": 1, "max": 9, "default": 2},
            {"name": "env", "type": "choice", "choices": ["dev", "prod"]},
            {"name": "token", "type": "secret"},
        ])
        tools = self.ok("mcp.tools", role="operator")["tools"]
        tool = next(tool for tool in tools if tool["name"].startswith("launch_"))
        self.assertEqual(tool["name"], f"launch_deploy_{template['id'][-6:]}")
        self.assertEqual((tool["command"], tool["fixed"], tool["class"]),
                         ("run.start", {"template_id": template["id"]}, "run"))
        answers = tool["inputSchema"]["properties"]["answers"]
        self.assertEqual(answers["required"], ["version"])
        self.assertEqual(sorted(answers["properties"]), ["count", "env", "version"])
        self.assertEqual(answers["properties"]["version"],
                         {"type": "string", "minLength": 0, "maxLength": 20,
                          "description": "Version To deploy."})
        self.assertEqual(answers["properties"]["count"],
                         {"type": "number", "minimum": 1, "maximum": 9, "default": 2})
        self.assertEqual(answers["properties"]["env"]["enum"], ["dev", "prod"])
        self.assertIn("web*", tool["inputSchema"]["properties"]["limit"]["description"])
        self.assertEqual(tool["inputSchema"]["required"], ["answers"])
        self.assertEqual([t for t in self.ok("mcp.tools", role="viewer")["tools"]
                          if t["name"].startswith("launch_")], [])

    def test_a_template_that_needs_a_secret_is_not_a_tool(self):
        self.template(survey=[{"name": "token", "type": "secret", "required": True}])
        self.assertEqual([tool for tool in self.ok("mcp.tools")["tools"]
                          if tool["name"].startswith("launch_")], [])

    def test_result_of_a_run_by_host_and_by_task(self):
        run = self.ok("run.start", {"template_id": self.template()["id"]})
        directory = paths.RUNS_DIR / run["id"]
        events = [
            {"event": "play_start", "play": "Deploy"},
            {"event": "task_start", "task": "Copy", "task_uuid": "t1"},
            {"event": "host_ok", "task_uuid": "t1", "host": "a", "changed": True},
            {"event": "host_ok", "task_uuid": "t1", "host": "b", "changed": False},
            {"event": "task_start", "task": "Restart", "task_uuid": "t2"},
            {"event": "host_failed", "task_uuid": "t2", "host": "a",
             "result": {"msg": "no such service " + "x" * 600}},
            {"event": "host_unreachable", "task_uuid": "t2", "host": "b", "result": {"msg": ["a"]}},
            {"event": "host_ok", "task_uuid": "unknown", "host": "a"},
            {"event": "stats", "hosts": {"a": {"ok": 1, "failures": 1}}},
        ]
        (directory / "events.jsonl").write_text(
            "not json\n" + "".join(json.dumps(event) + "\n" for event in events))
        going = self.ok("run.result", {"id": run["id"]}, role="operator")
        self.assertEqual((going["done"], going["status"]), (False, "running"))
        self.finish(run["id"], rc=2)
        done = self.ok("run.result", {"id": run["id"], "wait": 5}, role="operator")
        self.assertEqual((done["done"], done["status"]), (True, "failed"))
        self.assertEqual(done["stats"], {"a": {"ok": 1, "failures": 1}})
        copy, restart = done["tasks"]
        self.assertEqual((copy["task"], copy["play"], copy["ok"], copy["changed"]),
                         ("Copy", "Deploy", 1, 1))
        self.assertEqual((restart["failed"], restart["unreachable"]), (1, 1))
        self.assertEqual(len(restart["problems"]["a"]), 500)
        self.assertEqual(restart["problems"]["b"], '["a"]')
        self.assertEqual(self.code("run.result", {"id": run["id"]}, role="viewer"), "forbidden")
        self.assertEqual(self.code("run.result", {"id": run["id"], "wait": 51}), "bad_request")


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.calls: list[tuple[str, dict]] = []
        self.tools = [
            {"name": "run_get", "description": "One run.", "command": "run.get", "fixed": {},
             "inputSchema": {"type": "object"}, "readOnly": True},
            {"name": "launch_deploy_abc123", "description": "Launch.", "command": "run.start",
             "fixed": {"template_id": "t1"}, "inputSchema": {"type": "object"},
             "readOnly": False},
        ]

        self.sessions: set[str] = set()

        def call(name, params):
            # The session is drawn at random: the tests see it apart.
            params = dict(params)
            self.sessions.add(params.pop("session", None))
            self.calls.append((name, params))
            if name == "mcp.tools":
                return {"ok": True, "data": {"tools": self.tools}}
            if name == "run.get":
                return {"ok": False, "error": {"code": "not_found", "message": "no such run: x"}}
            return {"ok": True, "data": {"id": "r1", "status": "running"}}

        self.server = mcp.Server(call)

    def ask(self, method, params=None, ident=1):
        return self.server.handle({"jsonrpc": "2.0", "id": ident, "method": method,
                                   "params": params or {}})

    def test_handshake_names_the_client_in_every_call(self):
        answer = self.ask("initialize", {"protocolVersion": "2025-03-26",
                                         "clientInfo": {"name": "claude code/2 \n", "version": "2"}})
        self.assertEqual(answer["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(answer["result"]["capabilities"], {"tools": {"listChanged": False}})
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0",
                                              "method": "notifications/initialized"}))
        self.ask("tools/list")
        self.assertEqual(self.calls, [("mcp.tools", {"via": "mcp:claude-code-2-"})])
        other = mcp.Server(lambda name, params: None)
        self.assertEqual(other.handle({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "1999-01-01"}})["result"]["protocolVersion"], mcp.PROTOCOLS[0])

    def test_tools_are_listed_without_what_is_ours(self):
        tools = self.ask("tools/list")["result"]["tools"]
        self.assertEqual(tools[0], {"name": "run_get", "description": "One run.",
                                    "inputSchema": {"type": "object"},
                                    "annotations": {"readOnlyHint": True}})

    def test_a_call_cannot_override_what_the_tool_fixes_or_where_it_comes_from(self):
        result = self.ask("tools/call", {"name": "launch_deploy_abc123", "arguments": {
            "answers": {"v": "1"}, "template_id": "other", "via": "interface"}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], {"id": "r1", "status": "running"})
        self.assertEqual(self.calls[-1], ("run.start", {"answers": {"v": "1"},
                                                        "template_id": "t1", "via": "mcp"}))

    def test_the_profile_goes_with_every_call_and_the_agent_cannot_change_it(self):
        self.server = mcp.Server(self.server._call, "observer")
        self.ask("tools/list")
        self.ask("tools/call", {"name": "launch_deploy_abc123",
                                "arguments": {"profile": "administrator"}})
        self.assertEqual(self.calls, [
            ("mcp.tools", {"via": "mcp", "profile": "observer"}),
            ("run.start", {"template_id": "t1", "via": "mcp", "profile": "observer"})])

    def test_one_session_for_every_call_of_a_server(self):
        self.ask("tools/list")
        self.ask("tools/call", {"name": "launch_deploy_abc123", "arguments": {
            "session": "0" * 16}})
        self.assertEqual(len(self.sessions), 1)
        session = self.sessions.pop()
        self.assertRegex(session, "^[0-9a-f]{16}$")
        mcp.Server(self.server._call).handle({"id": 1, "method": "tools/list"})
        self.assertNotIn(session, self.sessions)

    def test_the_command_line(self):
        source = io.StringIO('{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n')
        sink = io.StringIO()
        self.assertEqual(mcp.main(["fleet-mcp", "--profile", "diagnostic"], source, sink,
                                  self.server._call), 0)
        self.assertEqual(self.calls, [("mcp.tools", {"via": "mcp", "profile": "diagnostic"})])
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            for argv in (["fleet-mcp", "--profile"], ["fleet-mcp", "--profile", "A b"],
                         ["fleet-mcp", "x"]):
                self.assertEqual(mcp.main(argv, source, sink, self.server._call), 2, argv)

    def test_a_refusal_is_a_tool_error_not_a_protocol_error(self):
        result = self.ask("tools/call", {"name": "run_get", "arguments": {"id": "x"}})["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "not_found: no such run: x")

    def test_what_is_not_there(self):
        self.assertEqual(self.ask("tools/call", {"name": "fleet_secret"})["error"]["code"], -32602)
        self.assertEqual(self.ask("tools/call", {"name": "run_get", "arguments": []})["error"]["code"],
                         -32602)
        self.assertEqual(self.ask("resources/list")["error"]["code"], -32601)
        self.assertEqual(self.ask("ping")["result"], {})

    def test_an_unreachable_backend_is_said_in_words(self):
        def down(name, params):
            raise mcp.Unreachable("sudo: a password is required")

        server = mcp.Server(down)
        self.assertEqual(server.handle({"id": 1, "method": "tools/list"})["error"]["code"], -32000)
        server._tools = {"run_get": ("run.get", {})}
        result = server.handle({"id": 2, "method": "tools/call",
                                "params": {"name": "run_get"}})["result"]
        self.assertEqual((result["isError"], result["content"][0]["text"]),
                         (True, "sudo: a password is required"))

    def test_messages_are_lines(self):
        source = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n[1]\n'
                             '{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        sink = io.StringIO()
        self.assertEqual(mcp.serve(source, sink, self.server), 0)
        lines = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual([line.get("result", line.get("error", {}).get("code")) for line in lines],
                         [{}, -32700, -32700])


class TemplateToolTest(unittest.TestCase):
    def test_names_stay_within_what_a_tool_name_may_be(self):
        row = {"id": "tabcdef123456", "name": "Déploy — the Big One!!! " * 4, "survey": "[]",
               "playbook": "site.yml", "limit_hosts": ""}
        tool = agents.template_tool(row, "p", "i")
        self.assertRegex(tool["name"], r"^[a-z0-9_]{1,64}$")
        self.assertEqual(tool["inputSchema"]["required"], [])


if __name__ == "__main__":
    unittest.main()
