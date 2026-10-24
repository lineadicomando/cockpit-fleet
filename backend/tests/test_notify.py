"""Notification channels, rules, the outbox and its delivery, with the
sending replaced by a fake, and a real HTTP server for the requests."""

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import channels, jobs, notify, paths, store, transfer  # noqa: F401
from cockpit_fleet.errors import BadRequest

START = 1_800_000_000.0
TELEGRAM = {"kind": "telegram", "settings": {"chat_id": "-100123"}}
WEBHOOK = {"kind": "webhook", "settings": {"url": "https://hooks.example.org/x"}}


class NotifyBase(Base):
    def setUp(self):
        super().setUp()
        self.clock = START
        self.sent: list[tuple[str, dict, str, channels.Message, str]] = []
        self.failing: set[str] = set()
        (self.root / "secrets" / "notify").mkdir()
        self.creds = self.root / "creds"
        self.creds.mkdir()

        def send(kind, settings, secret, message, proxy, reference):
            if settings.get("url", "").endswith("/down"):
                raise channels.SendError("cannot connect: refused")
            self.sent.append((kind, settings, secret, message, reference))

        patches = [
            mock.patch.object(store, "now", lambda: self.clock),
            mock.patch.object(paths, "NOTIFY_WAKE", self.root / "notify.wake"),
            mock.patch.object(paths, "NOTIFY_LOCK", self.root / "notify.lock"),
            mock.patch.object(paths, "NOTIFY_SECRETS_DIR", self.root / "secrets" / "notify"),
            mock.patch.object(channels, "send", send),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def channel(self, name="ops", base=TELEGRAM, secret="123:ABC", **extra):  # noqa: S107
        channel = self.ok("channel.create", {"name": name, **base, **extra})
        if secret:
            (paths.NOTIFY_SECRETS_DIR / f"{channel['secret_name']}.cred").write_bytes(b"x")
            (self.creds / f"notify_{channel['secret_name']}.cred").write_text(secret + "\n")
        return channel

    def rule(self, channel, events=("failed",), name=None, **extra):
        return self.ok("rule.create", {"name": name or f"r-{channel['name']}-{len(events)}",
                                       "channel_id": channel["id"], "events": list(events),
                                       **extra})

    def run_ending(self, rc=0, stats=None, template=None):
        template = template or self.template()
        run = self.ok("run.start", {"template_id": template["id"]}, role="operator")
        if stats is not None:
            events = paths.RUNS_DIR / run["id"] / "events.jsonl"
            events.write_text(json.dumps({"event": "stats", "hosts": stats}) + "\n")
        self.finish(run["id"], rc)
        self.ok("run.list")
        return run

    def deliver(self, at=None):
        if at is not None:
            self.clock = at
        return notify.deliver(self.ctx(), self.creds)["channels"]

    def notifications(self):
        return self.ok("notification.list", role="viewer")["notifications"]


class ChannelTest(NotifyBase):
    def test_settings_are_checked_by_kind(self):
        for kind, settings in (
                ("telegram", {"chat_id": "not a chat"}),
                ("telegram", {"chat_id": "1", "server": "ftp://x"}),
                ("webhook", {"url": "https://user:pw@example.org/"}),
                ("webhook", {"url": "https://example.org/", "template": "{not json"}),
                ("webhook", {"url": "https://example.org/", "method": "GET"}),
                ("webhook", {"url": "https://example.org/", "header": "X: y"}),
                ("smtp", {"host": "mail", "sender": "fleet@example.org", "recipients": []}),
                ("smtp", {"host": "mail", "sender": "nobody", "recipients": ["a@b.c"]}),
                ("smtp", {"host": "mail", "sender": "f@b.c", "recipients": ["a@b.c"],
                          "security": "none", "username": "fleet"}),
                ("matrix", {"homeserver": "https://m.org", "room_id": "#alias:m.org"}),
                ("matrix", {"homeserver": "https://m.org", "room_id": "!a:m.org", "x": 1}),
                ("matrix", {"homeserver": "https://m.org", "room_id": "!a b"})):
            self.assertEqual(self.code("channel.create", {"name": "c", "kind": kind,
                                                          "settings": settings}),
                             "bad_request", (kind, settings))
        mail = self.ok("channel.create", {"name": "mail", "kind": "smtp", "settings": {
            "host": "mail.example.org", "sender": "fleet@example.org",
            "recipients": ["ops@example.org"], "security": "tls"}})
        self.assertEqual(mail["settings"]["port"], 465)
        self.assertFalse(mail["secret_required"])
        # A room of version 12 or later has no server in its id.
        self.ok("channel.create", {"name": "room", "kind": "matrix", "settings": {
            "homeserver": "https://matrix.org",
            "room_id": "!Qx7Tz2dPfL0aWm_9RkVbN4sYc8HuJe3oGi5XnUt6Ay1"}})
        hook = self.ok("channel.create", {"name": "hook", "kind": "webhook", "settings": {
            "url": "https://hooks.slack.com/services/$secret"}})
        self.assertEqual(hook["settings"]["template"], channels.DEFAULT_TEMPLATE)
        self.assertEqual(self.code("channel.create", {"name": "t", "kind": "telegram",
                                                      "settings": {"chat_id": "1",
                                                                   "server": "https://x/$secret"}}),
                         "bad_request")

    def test_the_secret_is_reported_never_read_and_must_go_before_the_channel(self):
        channel = self.channel(secret="")
        self.assertFalse(channel["secret"])
        self.assertTrue(channel["secret_name"].startswith(paths.NOTIFY_SECRET_PREFIX))
        self.assertEqual(self.code("channel.test", {"id": channel["id"]}), "conflict")
        secret = paths.NOTIFY_SECRETS_DIR / f"{channel['secret_name']}.cred"
        secret.write_bytes(b"ciphertext")
        listed = self.ok("channel.list", role="viewer")["channels"][0]
        self.assertTrue(listed["secret"])
        self.assertNotIn("ciphertext", json.dumps(listed))
        self.assertEqual(self.code("channel.delete", {"id": channel["id"]}), "conflict")
        secret.unlink()
        self.ok("channel.delete", {"id": channel["id"]})

    def test_only_administrators_change_channels_and_rules(self):
        channel = self.channel()
        for name, params in (("channel.create", {"name": "x", **TELEGRAM}),
                             ("channel.test", {"id": channel["id"]}),
                             ("rule.create", {"name": "r", "channel_id": channel["id"],
                                              "events": ["failed"]})):
            self.assertEqual(self.code(name, params, role="operator"), "forbidden")
        self.assertEqual(self.code("notification.deliver"), "unavailable")

    def test_rules_name_events_and_a_scope_that_exists(self):
        channel = self.channel()
        base = {"name": "r", "channel_id": channel["id"], "events": ["failed"]}
        self.assertEqual(self.code("rule.create", {**base, "events": []}), "bad_request")
        self.assertEqual(self.code("rule.create", {**base, "events": ["exploded"]}), "bad_request")
        self.assertEqual(self.code("rule.create", {**base, "scope": "template"}), "bad_request")
        self.assertEqual(self.code("rule.create", {**base, "scope": "template",
                                                   "scope_id": "t000"}), "not_found")
        self.assertEqual(self.code("rule.create", {**base, "scope_id": "t000"}), "bad_request")
        template = self.template()
        rule = self.ok("rule.create", {**base, "scope": "template", "scope_id": template["id"],
                                       "events": ["changed", "failed"]})
        self.assertEqual((rule["events"], rule["scope_name"], rule["channel"]),
                         (["failed", "changed"], "deploy", "ops"))


class OutboxTest(NotifyBase):
    def test_a_failed_run_goes_once_to_each_channel_its_rules_name(self):
        ops, hook = self.channel(), self.channel("hook", WEBHOOK, secret="")
        self.rule(ops, ("failed",))
        self.rule(ops, ("failed", "succeeded"), name="twice")
        self.rule(hook, ("failed",))
        run = self.run_ending(rc=2, stats={"a": {"failures": 1}, "b": {"ok": 3}})
        found = self.notifications()
        self.assertEqual(sorted((n["channel"], n["event"], n["state"]) for n in found),
                         [("hook", "failed", "pending"), ("ops", "failed", "pending")])
        self.assertTrue(paths.NOTIFY_WAKE.exists())
        results = self.deliver()
        self.assertEqual(sorted(r["outcome"] for r in results), ["sent", "sent"])
        kind, _settings, secret, message, _reference = next(s for s in self.sent
                                                            if s[0] == "telegram")
        self.assertEqual(secret, "123:ABC")
        self.assertEqual(message.title, "Fleet: Run failed: deploy")
        self.assertIn("Hosts: 1 ok, 0 changed, 1 failed, 0 unreachable", message.text)
        self.assertIn("Failed: a", message.text)
        self.assertIn(f"/fleet#/runs/{run['id']}", message.link)
        self.assertEqual({n["state"] for n in self.notifications()}, {"sent"})
        self.assertEqual(self.deliver(), [])

    def test_what_a_run_is_told_as_follows_the_rules(self):
        ops = self.channel(language="it")
        template = self.template()
        self.rule(ops, ("succeeded", "changed"))
        self.run_ending(stats={"a": {"changed": 2}}, template=template)
        self.run_ending(stats={"a": {"ok": 2}}, template=template)
        self.run_ending(rc=2, template=template)
        self.assertEqual([n["event"] for n in self.notifications()], ["succeeded", "changed"])
        self.deliver(START + 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][3].title, "Fleet: 2 run terminate")

    def test_scope_and_kind_of_job(self):
        ops = self.channel()
        template = self.template()
        other = self.ok("template.create", {"name": "other", "project_id": template["project_id"],
                                            "playbook": "site.yml",
                                            "inventory_id": template["inventory_id"]})
        self.rule(ops, ("failed",), scope="template", scope_id=other["id"])
        self.run_ending(rc=2, template=template)
        self.assertEqual(self.notifications(), [])
        self.run_ending(rc=2, template=other)
        self.assertEqual(len(self.notifications()), 1)
        # A job that serves a project is not a run anyone is told about.
        self.assertEqual(notify.run_ended(self.ctx(), "nothing"), 0)

    def test_a_channel_turned_off_gets_nothing_and_drops_what_it_had(self):
        ops = self.channel()
        self.rule(ops)
        self.run_ending(rc=2)
        self.ok("channel.update", {"id": ops["id"], "name": "ops", "settings": ops["settings"],
                                   "enabled": False})
        self.assertEqual([n["state"] for n in self.notifications()], ["failed"])
        self.run_ending(rc=2, template=self.ok("template.list")["templates"][0])
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(self.deliver(), [])

    def test_a_window_groups_what_ends_close_together(self):
        ops = self.channel(window=300)
        self.rule(ops)
        template = self.template()
        self.run_ending(rc=2, template=template)
        self.assertEqual(self.deliver(), [])
        self.clock = START + 100
        self.run_ending(rc=2, template=template)
        self.assertEqual(self.deliver(START + 299), [])
        self.assertEqual([r["outcome"] for r in self.deliver(START + 300)], ["sent"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.sent[0][3].events), 2)

    def test_beyond_the_limit_events_wait_and_leave_as_one(self):
        ops = self.channel(max_per_hour=2)
        self.rule(ops)
        template = self.template()
        for minute in range(5):
            self.clock = START + minute * 60
            self.run_ending(rc=2, template=template)
            self.deliver()
        self.assertEqual(len(self.sent), 2)
        pending = [n for n in self.notifications() if n["state"] == "pending"]
        self.assertEqual(len(pending), 3)
        self.assertEqual({n["next_at"] for n in pending}, {START + 3600})
        self.deliver(START + 3600)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(len(self.sent[2][3].events), 3)

    def test_a_channel_that_fails_is_tried_again_later_and_holds_up_no_other(self):
        down = self.channel("down", {"kind": "webhook",
                                     "settings": {"url": "https://example.org/down"}}, secret="")
        ops = self.channel()
        self.rule(down)
        self.rule(ops)
        self.run_ending(rc=2)
        results = {r["channel"]: r["outcome"] for r in self.deliver()}
        self.assertEqual(results, {"down": "error", "ops": "sent"})
        failing = next(n for n in self.notifications() if n["channel"] == "down")
        self.assertEqual((failing["state"], failing["attempts"], failing["next_at"]),
                         ("pending", 1, START + 60))
        self.assertIn("refused", failing["error"])
        self.assertEqual(self.deliver(START + 59), [])
        self.deliver(START + 60)
        self.deliver(START + 180)
        failing = next(n for n in self.notifications() if n["channel"] == "down")
        self.assertEqual((failing["attempts"], failing["next_at"]), (3, START + 180 + 240))
        # The tick wakes the delivery when a time to try again has come.
        paths.NOTIFY_WAKE.unlink()
        self.clock = START + 420
        notify.wake_if_due(self.ctx())
        self.assertTrue(paths.NOTIFY_WAKE.exists())
        # After a day it is given up, and can be tried again by hand.
        self.deliver(START + notify.GIVE_UP_AFTER + 1)
        failing = next(n for n in self.notifications() if n["channel"] == "down")
        self.assertEqual(failing["state"], "failed")
        self.assertEqual(self.code("notification.retry", {"id": failing["id"]},
                                   role="operator"), "forbidden")
        self.ok("notification.retry", {"id": failing["id"]})
        failing = next(n for n in self.notifications() if n["channel"] == "down")
        self.assertEqual((failing["state"], failing["attempts"]), ("pending", 0))

    def test_a_test_message_goes_at_once_and_alone(self):
        ops = self.channel(window=600)
        self.rule(ops)
        self.run_ending(rc=2)
        self.ok("channel.test", {"id": ops["id"]})
        self.assertEqual([r["outcome"] for r in self.deliver()], ["sent"])
        self.assertEqual(self.sent[0][3].title, "Fleet: Test message")
        self.assertIn("ops", self.sent[0][3].text)
        self.assertEqual([n["event"] for n in self.notifications()
                          if n["state"] == "pending"], ["failed"])

    def test_channels_and_rules_are_exported_and_imported_by_name(self):
        ops = self.channel()
        template = self.template()
        self.rule(ops, ("failed", "canceled"), name="deploys", scope="template",
                  scope_id=template["id"])
        document = self.ok("config.export")
        self.assertEqual(document["channels"][0]["settings"]["chat_id"], "-100123")
        self.assertEqual(document["rules"][0]["scope_name"], "deploy")
        self.assertNotIn("123:ABC", json.dumps(document))
        report = self.ok("config.import", {"document": document})
        self.assertEqual(report["failed"], 0)
        (paths.NOTIFY_SECRETS_DIR / f"{ops['secret_name']}.cred").unlink()
        self.ok("rule.delete", {"id": self.ok("rule.list")["rules"][0]["id"]})
        self.ok("channel.delete", {"id": ops["id"]})
        report = self.ok("config.import", {"document": document})
        self.assertEqual((report["created"], report["failed"]), (2, 0), report)
        rule = self.ok("rule.list")["rules"][0]
        self.assertEqual((rule["channel"], rule["scope_name"], rule["events"]),
                         ("ops", "deploy", ["failed", "canceled"]))


class ComposeTest(unittest.TestCase):
    EVENT = {"event": "failed", "run_id": "r1", "title": "deploy", "template": "deploy",
             "project": "site", "schedule": "nightly", "created_by": "schedule:nightly",
             "check": True, "hosts": {"counts": {"ok": 1, "changed": 0, "failed": 1,
                                                 "unreachable": 1},
                                      "failed": ["b", "c"]}, "error": ""}

    def test_one_event_in_italian(self):
        message = notify.compose("it", [self.EVENT], "https://fleet.example.org:9090")
        self.assertEqual(message.title, "Fleet: Run fallita: deploy (prova)")
        self.assertIn("Pianificazione: nightly", message.text)
        self.assertIn("Host: 1 ok, 0 modificati, 1 falliti, 1 irraggiungibili", message.text)
        self.assertTrue(message.text.endswith("https://fleet.example.org:9090/fleet#/runs/r1"))

    def test_many_events_are_listed_up_to_a_point(self):
        events = [{**self.EVENT, "run_id": f"r{index}"} for index in range(25)]
        message = notify.compose("en", events, "https://h:9090")
        self.assertEqual(message.title, "Fleet: 25 runs ended")
        self.assertIn("and 5 more", message.text)
        self.assertEqual(len(message.events), 25)
        self.assertEqual(message.events[3]["link"], "https://h:9090/fleet#/runs/r3")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.server.seen.append((self.command, self.path, dict(self.headers),
                                 self.rfile.read(int(self.headers["Content-Length"]))))
        status, body = self.server.answers.pop(0) if self.server.answers else (200, b"{}")
        if status in (301, 307):
            self.send_response(status)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    do_PUT = do_POST  # noqa: N815

    def log_message(self, *args):
        pass


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.seen, self.server.answers = [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.message = channels.Message('Fleet: "quoted"', "line\n{}", "https://h/fleet", [
            {"event": "failed", "run_id": "r1"}])

    def test_telegram_puts_the_token_in_the_path_and_never_in_an_error(self):
        settings = channels.check_settings("telegram", {"chat_id": "42", "server": self.base})
        channels.send("telegram", settings, "123:SECRET", self.message, "", "ref")
        method, path, _headers, body = self.server.seen[0]
        self.assertEqual((method, path), ("POST", "/bot123:SECRET/sendMessage"))
        self.assertEqual(json.loads(body)["chat_id"], "42")
        self.server.answers.append((401, json.dumps(
            {"ok": False, "description": "Unauthorized for 123:SECRET"}).encode()))
        with self.assertRaises(channels.SendError) as raised:
            channels.send("telegram", settings, "123:SECRET", self.message, "", "ref")
        self.assertEqual(str(raised.exception), "HTTP 401: Unauthorized for ********")

    def test_a_webhook_fills_its_template_inside_strings_only(self):
        settings = channels.check_settings("webhook", {
            "url": self.base + "/hook/$secret", "header": "X-Token",
            "template": '{"text": "$title: $text", "all": "$events", "n": "$count", "k": 1}'})
        channels.send("webhook", settings, "abc/def", self.message, "", "ref")
        _method, path, headers, body = self.server.seen[0]
        self.assertEqual(path, "/hook/abc/def")
        self.assertNotIn("X-Token", headers)
        sent = json.loads(body)
        self.assertEqual(sent["text"], 'Fleet: "quoted": line\n{}')
        self.assertEqual((sent["all"][0]["run_id"], sent["n"], sent["k"]), ("r1", "1", 1))
        settings = channels.check_settings("webhook", {"url": self.base + "/plain",
                                                       "header": "X-Token"})
        channels.send("webhook", settings, "tok", self.message, "", "ref")
        self.assertEqual(self.server.seen[1][2]["X-Token"], "tok")
        with self.assertRaises(channels.SendError):
            channels.send("webhook", {**settings, "url": self.base + "/$secret"}, "a b",
                          self.message, "", "ref")

    def test_a_redirect_is_not_followed(self):
        settings = channels.check_settings("webhook", {"url": self.base + "/moved"})
        self.server.answers.append((307, b""))
        with self.assertRaises(channels.SendError) as raised:
            channels.send("webhook", settings, "tok", self.message, "", "ref")
        self.assertEqual(str(raised.exception), "HTTP 307")
        self.assertEqual(len(self.server.seen), 1)

    def test_matrix_names_the_transaction_after_the_message(self):
        settings = channels.check_settings("matrix", {"homeserver": self.base,
                                                      "room_id": "!room:example.org"})
        channels.send("matrix", settings, "syt_token", self.message, "", "fleet-7-2")
        method, path, headers, body = self.server.seen[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(path, "/_matrix/client/v3/rooms/%21room%3Aexample.org/send/"
                               "m.room.message/fleet-7-2")
        self.assertEqual(headers["Authorization"], "Bearer syt_token")
        self.assertEqual(json.loads(body)["msgtype"], "m.text")

    def test_an_unreachable_server_is_a_send_error(self):
        settings = channels.check_settings("webhook", {"url": "http://127.0.0.1:1/x"})
        with self.assertRaises(channels.SendError) as raised:
            channels.send("webhook", settings, "", self.message, "", "ref")
        self.assertIn("cannot connect", str(raised.exception))
        with self.assertRaises(channels.SendError):
            channels.send("telegram", {"chat_id": "1", "server": self.base}, "", self.message,
                          "", "ref")
        self.assertRaises(BadRequest, channels.check_url, "javascript:alert(1)")

    def test_what_a_server_or_a_secret_makes_go_wrong_is_a_send_error_naming_no_secret(self):
        # A server that does not speak HTTP.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def answer():
            connection, _ = listener.accept()
            with connection:
                connection.recv(65536)
                connection.sendall(b"garbage\r\n\r\n")
        threading.Thread(target=answer, daemon=True).start()
        settings = channels.check_settings(
            "webhook", {"url": f"http://127.0.0.1:{listener.getsockname()[1]}/x"})
        with self.assertRaises(channels.SendError):
            channels.send("webhook", settings, "", self.message, "", "ref")

        # Secrets with what a header or a URL cannot hold.
        matrix = channels.check_settings("matrix", {"homeserver": self.base,
                                                    "room_id": "!room:example.org"})
        telegram = channels.check_settings("telegram", {"chat_id": "42", "server": self.base})
        for kind, settings, secret in (("matrix", matrix, "syt_one\ntwo"),
                                       ("telegram", telegram, "123:ABC DEF")):
            with self.assertRaises(channels.SendError) as raised:
                channels.send(kind, settings, secret, self.message, "", "ref")
            self.assertNotIn("ABC", str(raised.exception))
            self.assertNotIn("syt_", str(raised.exception))
        self.assertEqual(self.server.seen, [])

        # A host name with a line break in the subject of a mail.
        mail = channels.check_settings("smtp", {"host": "127.0.0.1", "port": 1,
                                                "security": "none", "sender": "f@example.org",
                                                "recipients": ["a@example.org"]})
        title = channels.Message("disk on evil\nBcc: x@example.org", "t", "", [])
        with self.assertRaises(channels.SendError):
            channels.send("smtp", mail, "", title, "", "ref")

    def test_an_error_quotes_no_form_of_the_secret_and_no_body_that_is_not_an_api_error(self):
        self.assertEqual(channels._mask("b'Bearer tok\\nen' and tok:en", "tok\nen"),
                         "b'Bearer ********' and tok:en")
        self.assertEqual(channels._mask("x 123%3AABC y 123:ABC", "123:ABC"), "x ******** y ********")
        self.assertEqual(channels._reason(b"<html>internal page</html>"), "")
        self.assertEqual(channels._reason(b'{"error": "no such room"}'), "no such room")


if __name__ == "__main__":
    unittest.main()
