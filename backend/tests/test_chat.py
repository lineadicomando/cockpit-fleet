"""The chat: the agents config.toml names, the start of one under the limit
on open sessions, and fleet-chat, which runs it."""

import io
import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import chat, chat_agent, cli
from cockpit_fleet.config import ChatAgent, Config, ConfigError, parse
from cockpit_fleet.mcp import Unreachable

AGENT = ChatAgent("claude", ("/opt/agent/bin/acp", "--stdio"), "Claude", "claude /login",
                  ("/opt/agent/bin/claude", "--mcp-config", "{mcp_config}", "--tools", "",
                   "--append-system-prompt", "{instructions}"))


class ConfigTest(unittest.TestCase):
    def test_off_unless_an_agent_is_named(self):
        self.assertEqual(parse("").chat_agents, ())
        self.assertEqual(parse("[chat]\nmax_sessions = 3").chat_agents, ())

    def test_agents_and_the_limit(self):
        config = parse("""
            [chat]
            max_sessions = 5
            [chat.agents.claude]
            command = ["/opt/agent/bin/acp", "--stdio"]
            label = "Claude"
            login = "claude /login"
            terminal = ["/opt/agent/bin/claude", "--mcp-config", "{mcp_config}", "--tools", "",
                        "--append-system-prompt", "{instructions}"]
            [chat.agents.other-1]
            command = ["/usr/local/bin/other"]
        """)
        self.assertEqual(config.chat_max_sessions, 5)
        self.assertEqual(config.chat_agents,
                         (AGENT, ChatAgent("other-1", ("/usr/local/bin/other",))))
        json.dumps(config.as_dict())

    def test_errors_name_the_problem(self):
        cases = {
            "[chat]\nenabled = true": "unknown key in \\[chat\\]",
            "[chat]\nmax_sessions = 0": "chat.max_sessions",
            "[chat]\nagents = 3": "chat.agents",
            "[chat.agents]\nclaude = 3": "\\[chat.agents.claude\\] must be a table",
            "[chat.agents.Claude]\ncommand = [\"/x\"]": "the name of an agent",
            "[chat.agents.a]\nlabel = \"A\"": "chat.agents.a.command",
            "[chat.agents.a]\ncommand = \"/x\"": "chat.agents.a.command",
            "[chat.agents.a]\ncommand = []": "chat.agents.a.command",
            "[chat.agents.a]\ncommand = [\"/x\", 3]": "chat.agents.a.command",
            "[chat.agents.a]\ncommand = [\"node\", \"x.js\"]": "absolute path",
            "[chat.agents.a]\ncommand = [\"/x\"]\nargs = []": "unknown key in \\[chat.agents.a\\]",
            "[chat.agents.a]\ncommand = [\"/x\"]\nlabel = \"a\\nb\"": "chat.agents.a.label",
            "[chat.agents.a]\ncommand = [\"/x\"]\nlogin = 3": "chat.agents.a.login",
            "[chat.agents.a]\ncommand = [\"/x\"]\nterminal = [\"claude\"]": "absolute path",
            "[chat.agents.a]\ncommand = [\"/x\"]\nterminal = [\"\"]": "chat.agents.a.terminal",
        }
        for text, message in cases.items():
            with self.assertRaisesRegex(ConfigError, message, msg=text):
                parse(text)


class SessionsTest(unittest.TestCase):
    def test_counts_the_launchers_that_are_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            lines = {
                "10": ["/usr/bin/python3", "-I", chat.LAUNCHER, "claude"],
                "11": [chat.LAUNCHER, "claude"],
                "12": ["/usr/bin/python3", "-I", "/usr/libexec/cockpit-fleet/fleetctl", "whoami"],
                # Naming it as an argument of something else is not running it.
                "13": ["/usr/bin/sudo", "-u", "anna", "grep", chat.LAUNCHER],
                "14": [],
            }
            for pid, words in lines.items():
                (proc / pid).mkdir()
                (proc / pid / "cmdline").write_bytes(b"".join(w.encode() + b"\0" for w in words))
            (proc / "15").mkdir()  # gone meanwhile: no cmdline
            (proc / "self").mkdir()
            self.assertEqual(chat.open_sessions(proc), 2)


class CommandsTest(Base):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, mcp_enabled=True, chat_agents=(AGENT,),
                              chat_max_sessions=2)
        self.open = 1
        patch = mock.patch.object(chat, "open_sessions", lambda: self.open)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_page_learns_what_it_may_start_and_not_how(self):
        data = self.ok("chat.agents", role="viewer")
        self.assertEqual(data["agents"],
                         [{"name": "claude", "label": "Claude", "login": "claude /login",
                           "installed": False, "terminal": True, "setup": ""}])
        self.assertEqual((data["unavailable"], data["max_sessions"], data["open_sessions"],
                          data["max_role"]), ("", 2, 1, "operator"))
        self.assertNotIn("/opt/agent", json.dumps(data))

    def test_an_agent_that_is_not_installed_is_told_with_how_to_install_ours(self):
        # Not where ours may be installed on the machine of the test.
        self.enterContext(mock.patch.object(chat, "AGENT_DIR", "/nonexistent/agent/"))
        here = ChatAgent("here", ("/bin/sh",))
        ours = ChatAgent("ours", (chat.AGENT_DIR + "bin/claude-agent-acp",))
        self.config = replace(self.config, chat_agents=(here, ours, AGENT))
        agents = {agent["name"]: agent for agent in self.ok("chat.agents", role="viewer")["agents"]}
        self.assertEqual((agents["here"]["installed"], agents["here"]["setup"]), (True, ""))
        self.assertEqual((agents["ours"]["installed"], agents["ours"]["setup"]),
                         (False, chat.SETUP))
        self.assertEqual((agents["claude"]["installed"], agents["claude"]["setup"]), (False, ""))

    def test_the_guide_asks_for_what_is_missing_only(self):
        guide = self.ok("chat.agents", role="viewer")["guide"]
        self.assertEqual((guide["configured"], guide["mcp_enabled"], guide["command"],
                          guide["login"]),
                         (True, True, chat.SETUP, "/opt/cockpit-fleet-agent/bin/claude /login"))
        self.config = Config()
        guide = self.ok("chat.agents")["guide"]
        self.assertEqual((guide["configured"], guide["mcp_enabled"]), (False, False))
        self.assertEqual(guide["command"], chat.SETUP + " -e chat_agent_configure=true"
                                                        " -e chat_agent_enable_mcp=true")
        self.assertNotIn("\n", guide["command"])

    def test_why_the_chat_cannot_be_used(self):
        self.config = replace(self.config, mcp_enabled=False)
        self.assertIn("[mcp]", self.ok("chat.agents")["unavailable"])
        self.assertEqual(self.code("chat.start", {"agent": "claude"}), "unavailable")
        self.config = Config()
        self.assertIn("[chat]", self.ok("chat.agents")["unavailable"])
        self.assertEqual(self.code("chat.start", {"agent": "claude"}), "unavailable")

    def test_start_gives_the_command_line_and_leaves_a_record(self):
        data = self.ok("chat.start", {"agent": "claude"}, role="viewer")
        self.assertEqual(data, {"agent": "claude", "profile": "assisted",
                                "command": ["/opt/agent/bin/acp", "--stdio"]})
        row = self.ctx().conn.execute("SELECT * FROM audit").fetchone()
        self.assertEqual((row["command"], row["outcome"], row["user"]),
                         ("chat.start", "ok", "anna"))

    def test_its_program_for_a_terminal_when_it_has_one(self):
        data = self.ok("chat.start", {"agent": "claude", "terminal": True}, role="viewer")
        self.assertEqual(data["command"][0], "/opt/agent/bin/claude")
        self.config = replace(self.config, chat_agents=(ChatAgent("plain", ("/bin/sh",)),))
        self.assertEqual(self.code("chat.start", {"agent": "plain", "terminal": True}), "conflict")

    def test_a_profile_must_be_enabled(self):
        self.assertEqual(self.ok("chat.start", {"agent": "claude", "profile": "observer"},
                                 role="viewer")["profile"], "observer")
        for profile in ("administrator", "nope"):
            self.assertEqual(self.code("chat.start", {"agent": "claude", "profile": profile}),
                             "conflict")
        self.config = replace(self.config, chat_profiles=("administrator",),
                              chat_default_profile="administrator")
        self.assertEqual(self.ok("chat.start", {"agent": "claude"})["profile"], "administrator")
        agents = self.ok("chat.agents")
        self.assertEqual((agents["profiles"], agents["default_profile"]),
                         (["administrator"], "administrator"))

    def test_unknown_agent(self):
        self.assertEqual(self.code("chat.start", {"agent": "nope"}), "not_found")
        self.assertEqual(self.code("chat.start", {}), "bad_request")

    def test_the_limit_counts_the_session_that_is_asking(self):
        self.open = 2
        self.ok("chat.start", {"agent": "claude"})
        self.open = 3
        self.assertEqual(self.code("chat.start", {"agent": "claude"}), "conflict")

    def test_neither_is_a_tool_for_an_agent(self):
        self.assertFalse(cli.COMMANDS["chat.agents"].tool)
        self.assertFalse(cli.COMMANDS["chat.start"].tool)


class LauncherTest(unittest.TestCase):
    def run_it(self, answer, popen=None, argv=("fleet-chat", "claude")):
        sink = io.StringIO()
        self.asked = []

        def call(name, params):
            self.asked.append((name, params))
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(chat_agent.signal, "signal"):
            status = chat_agent.main(list(argv), sink, call, popen)
        return status, [json.loads(line) for line in sink.getvalue().splitlines()]

    def test_runs_what_the_backend_names_and_says_it_is_ready(self):
        started = {}

        class Child:
            def wait(self):
                return 7

        def popen(command, env):
            started.update(command=command, env=env)
            return Child()

        answer = {"ok": True, "data": {"agent": "claude", "profile": "assisted",
                                       "command": ["/opt/agent/bin/acp"]}}
        status, said = self.run_it(answer, popen)
        self.assertEqual(status, 7)
        self.assertEqual(self.asked, [("chat.start", {"agent": "claude"})])
        self.assertEqual(started["command"], ["/opt/agent/bin/acp"])
        self.assertEqual(started["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertEqual(said[0]["method"], "_fleet/ready")
        self.assertEqual(said[0]["params"]["mcp"]["command"],
                         "/usr/libexec/cockpit-fleet/fleet-mcp")
        self.assertTrue(said[0]["params"]["cwd"].startswith("/"))
        self.assertEqual(said[0]["params"]["mcp"]["args"], ["--profile", "assisted"])
        self.assertEqual(said[0]["params"]["profile"], "assisted")
        answer["data"]["profile"] = "observer"
        self.run_it(answer, popen, ("fleet-chat", "--profile", "observer", "claude"))
        self.assertEqual(self.asked, [("chat.start", {"agent": "claude", "profile": "observer"})])

    def test_a_refusal_reaches_the_page_and_nothing_is_started(self):
        def popen(command, env):
            raise AssertionError("started")

        error = {"code": "conflict", "message": "2 chat sessions are open already"}
        status, said = self.run_it({"ok": False, "error": error}, popen)
        self.assertEqual(status, 1)
        self.assertEqual(said, [{"jsonrpc": "2.0", "method": "_fleet/error", "params": error}])
        status, said = self.run_it(Unreachable("a password is required"), popen)
        self.assertEqual(said[0]["params"]["code"], "unreachable")

    def test_an_agent_that_is_not_there(self):
        def popen(command, env):
            raise FileNotFoundError(2, "No such file", command[0])

        status, said = self.run_it({"ok": True, "data": {"agent": "claude",
                                                         "command": ["/nowhere"]}}, popen)
        self.assertEqual(status, 1)
        self.assertEqual(said[0]["params"]["code"], "unavailable")

    def test_wants_one_name(self):
        for argv in (("fleet-chat",), ("fleet-chat", "--profile", "claude"),
                     ("fleet-chat", "claude", "--profile", "x"),
                     ("fleet-chat", "--profile", "", "claude")):
            self.assertEqual(self.run_it(None, argv=argv)[0], 2, argv)
        self.assertEqual(self.asked, [])


if __name__ == "__main__":
    unittest.main()


class TerminalLauncherTest(unittest.TestCase):
    def test_the_program_gets_fleet_mcp_alone_in_a_file_that_goes_away(self):
        seen = {}

        def call(name, params):
            seen["asked"] = (name, params)
            return {"ok": True, "data": {"agent": "claude", "profile": "observer",
                                         "command": list(AGENT.terminal)}}

        class Done:
            returncode = 0

        def run(argv, env, cwd, check):
            config = Path(argv[2])
            seen.update(argv=argv, env=env, config=json.loads(config.read_text()), path=config,
                        mode=config.parent.stat().st_mode & 0o777, cwd=cwd)
            return Done()

        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        with mock.patch.object(chat_agent.signal, "signal"), \
                mock.patch.dict(os.environ, {"HOME": home, "XDG_STATE_HOME": ""}):
            status = chat_agent.main(["fleet-chat", "--terminal", "--profile", "observer",
                                      "claude"], call=call, run=run)
            again = chat_agent.workspace()
        self.assertEqual(status, 0)
        self.assertEqual(seen["asked"], ("chat.start", {"agent": "claude", "profile": "observer",
                                                        "terminal": True, "via": "cli"}))
        self.assertEqual(seen["argv"][3:5], ["--tools", ""])
        self.assertEqual(seen["argv"][-1], chat_agent.INSTRUCTIONS)
        self.assertEqual(seen["config"], {"mcpServers": {"cockpit-fleet": {
            "type": "stdio", "command": "/usr/libexec/cockpit-fleet/fleet-mcp",
            "args": ["--profile", "observer"]}}})
        self.assertEqual(seen["mode"], 0o700)
        self.assertFalse(seen["path"].exists())
        self.assertEqual(seen["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        # One directory of its own, the same each time, so that the agent asks once.
        self.assertEqual(seen["cwd"], os.path.join(home, ".local/state/cockpit-fleet/assistant"))
        self.assertEqual(again, seen["cwd"])
        self.assertEqual(Path(seen["cwd"]).stat().st_mode & 0o777, 0o700)

    def test_a_refusal_is_said_and_nothing_runs(self):
        def run(argv, env, check):
            raise AssertionError("started")

        refused = {"ok": False, "error": {"code": "conflict", "message": "no program"}}
        with mock.patch("sys.stderr", new_callable=io.StringIO) as said:
            status = chat_agent.main(["fleet-chat", "--terminal", "x"],
                                     call=lambda name, params: refused, run=run)
        self.assertEqual(status, 3)
        self.assertIn("no program", said.getvalue())
