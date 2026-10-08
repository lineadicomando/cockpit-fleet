import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cockpit_fleet import cli, journal, system  # noqa: F401  (system registers commands)
from cockpit_fleet.auth import Caller
from cockpit_fleet.errors import NotFound


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []
        patcher = mock.patch.object(journal, "send", lambda *a, **k: self.sent.append((a, k)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

        # Commands that exist only for these tests.
        saved = dict(cli.COMMANDS)
        self.addCleanup(lambda: (cli.COMMANDS.clear(), cli.COMMANDS.update(saved)))

        @cli.command("test.start", role="operator", mutating=True, redact=("token",))
        def start(ctx, params):
            return {"started": True}

        @cli.command("test.missing", role="operator", mutating=True)
        def missing(ctx, params):
            raise NotFound("no such thing")

        @cli.command("test.crash", role="viewer")
        def crash(ctx, params):
            raise RuntimeError("secret detail")

    def ctx(self, role, user="anna"):
        root = Path(self.tmp.name)
        return cli.Context(Caller(1000, user, role), root / "fleet.db", root / "config.toml")

    def call(self, name, role, params=None, raw=None):
        data = raw if raw is not None else (json.dumps(params).encode() if params is not None else b"")
        return cli.dispatch(name, data, self.ctx(role))

    def audit(self):
        rows = self.ctx("admin").conn.execute(
            "SELECT user, role, command, params, outcome, detail FROM audit ORDER BY id")
        return [dict(row) for row in rows]

    def test_each_role_sees_its_own_commands(self):
        seen = {role: self.call("whoami", role)["data"]["commands"]
                for role in ("viewer", "operator", "admin")}
        self.assertNotIn("test.start", seen["viewer"])
        self.assertIn("test.start", seen["operator"])
        self.assertNotIn("audit.list", seen["operator"])
        self.assertIn("audit.list", seen["admin"])
        self.assertTrue(set(seen["viewer"]) < set(seen["operator"]) < set(seen["admin"]))

    def test_insufficient_role_is_refused_and_recorded(self):
        answer = self.call("audit.list", "viewer")
        self.assertEqual(answer["error"]["code"], "forbidden")
        self.assertEqual(self.audit(), [{
            "user": "anna", "role": "viewer", "command": "audit.list", "params": "{}",
            "outcome": "denied", "detail": "requires admin",
        }])

    def test_no_role_is_refused_for_everything(self):
        self.assertEqual(self.call("whoami", None)["error"]["code"], "forbidden")
        self.assertEqual(self.audit()[0]["outcome"], "denied")

    def test_mutating_command_is_recorded_with_secrets_redacted(self):
        answer = self.call("test.start", "operator", {"name": "x", "token": "s3cret"})
        self.assertEqual(answer, {"ok": True, "data": {"started": True}})
        entry = self.audit()[0]
        self.assertEqual(entry["outcome"], "ok")
        self.assertEqual(json.loads(entry["params"]), {"name": "x", "token": "***"})
        self.assertNotIn("s3cret", json.dumps(self.sent, default=str))

    def test_failed_mutating_command_is_recorded_as_error(self):
        answer = self.call("test.missing", "admin")
        self.assertEqual(answer["error"], {"code": "not_found", "message": "no such thing"})
        self.assertEqual((self.audit()[0]["outcome"], self.audit()[0]["detail"]), ("error", "no such thing"))

    def test_read_only_command_leaves_no_audit_entry(self):
        self.assertTrue(self.call("whoami", "viewer")["ok"])
        self.assertEqual(self.audit(), [])

    def test_unexpected_exception_does_not_leak_details(self):
        answer = self.call("test.crash", "viewer")
        self.assertEqual(answer["error"]["code"], "internal")
        self.assertNotIn("secret detail", answer["error"]["message"])
        self.assertIn("secret detail", json.dumps(self.sent, default=str))

    def test_bad_input(self):
        for raw in (b"[1]", b"{", b'"x"', b"\xff\xfe", b"x" * (cli.MAX_INPUT + 1)):
            self.assertEqual(self.call("whoami", "viewer", raw=raw)["error"]["code"], "bad_request", raw[:10])
        self.assertEqual(self.call("nope", "admin")["error"]["code"], "bad_request")
        self.assertEqual(self.call("whoami", "viewer", {"extra": 1})["error"]["code"], "bad_request")

    def test_audit_list_pages_backwards(self):
        for _ in range(5):
            self.call("audit.list", "viewer")
        first = self.call("audit.list", "admin", {"limit": 2})["data"]
        self.assertEqual(len(first["entries"]), 2)
        self.assertTrue(first["more"])
        older = self.call("audit.list", "admin", {"limit": 10, "before": first["entries"][-1]["id"]})["data"]
        self.assertEqual(len(older["entries"]), 3)
        self.assertFalse(older["more"])
        ids = [e["id"] for e in first["entries"] + older["entries"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        for bad in ({"limit": 0}, {"limit": "5"}, {"before": -1}):
            self.assertEqual(self.call("audit.list", "admin", bad)["error"]["code"], "bad_request")

    def test_denial_is_still_answered_when_the_database_is_unusable(self):
        ctx = cli.Context(Caller(1000, "anna", "viewer"), Path(self.tmp.name) / "nodir" / "fleet.db")
        self.assertEqual(cli.dispatch("audit.list", b"", ctx)["error"]["code"], "forbidden")
        self.assertEqual(self.sent[0][1]["FLEET_OUTCOME"], "denied")

    def test_invalid_config_is_reported_by_config_show(self):
        ctx = self.ctx("admin")
        ctx.config_file.write_text("[oops]")
        answer = cli.dispatch("config.show", b"", ctx)
        self.assertEqual(answer["error"]["code"], "unavailable")
        self.assertIn("unknown section", answer["error"]["message"])

    def test_every_registered_command_declares_a_known_role(self):
        for spec in cli.COMMANDS.values():
            self.assertIn(spec.role, ("viewer", "operator", "admin"), spec.name)


class JournalTest(unittest.TestCase):
    def test_single_line_fields(self):
        data = journal.encode("hello", 6, {"FLEET_USER": "anna"})
        self.assertEqual(
            data,
            b"MESSAGE=hello\nPRIORITY=6\nSYSLOG_IDENTIFIER=cockpit-fleet\nFLEET_USER=anna\n",
        )

    def test_multi_line_value_uses_the_length_prefixed_form(self):
        data = journal.encode("a\nb", 6, {})
        self.assertTrue(data.startswith(b"MESSAGE\n\x03\x00\x00\x00\x00\x00\x00\x00a\nb\n"))


if __name__ == "__main__":
    unittest.main()
