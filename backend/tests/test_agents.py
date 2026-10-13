"""Agents: declared parameters as tool schemas, the ceiling on what a call
made for an agent may do, templates as tools, and the MCP server itself."""

import io
import json
import unittest
from dataclasses import replace

from test_phase2 import Base

from cockpit_fleet import agents, cli, mcp, paths


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
                                                        "content": "", "path": "hosts"}),
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
        answer = self.via("credential.create", {"name": "x", "kind": "vault"}, mcp_enabled=True)
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

    def test_via_must_be_a_plain_name(self):
        self.config = replace(self.config, mcp_enabled=True)
        for bad in ("", "x" * 100, "a\nb", 3, ["mcp"], "-x"):
            self.assertEqual(self.call("whoami", {"via": bad})["error"]["code"], "bad_request", bad)


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
        self.assertEqual((tool["command"], tool["fixed"], tool["readOnly"]),
                         ("run.cancel", {}, False))
        self.assertEqual(tool["inputSchema"]["required"], ["id"])
        self.assertEqual(tool["description"], "Cancel a run that is queued or running.")

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
        self.assertEqual((tool["command"], tool["fixed"]),
                         ("run.start", {"template_id": template["id"]}))
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

        def call(name, params):
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
