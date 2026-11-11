"""Downloads into the store: the job against a server of the test, and the
commands that queue it and take what it leaves."""

import hashlib
import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_storage import StoreBase

from cockpit_fleet import fetch, jobs, paths, storage
from cockpit_fleet.errors import BadRequest

DATA = bytes(range(256)) * 4096  # 1 MiB


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.drop_once = False
        self.ignore_range = False
        self.requests: list[dict] = []
        # The version of the file served, as ETag and Last-Modified.
        self.etag = '"v1"'
        self.modified = "Tue, 10 Nov 2026 10:00:00 GMT"

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        server = self.server
        server.requests.append({"path": self.path, "range": self.headers.get("Range"),
                                "if_none_match": self.headers.get("If-None-Match"),
                                "if_modified_since": self.headers.get("If-Modified-Since"),
                                "if_range": self.headers.get("If-Range")})
        if self.path == "/missing":
            self.send_error(404)
            return
        if self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
            return
        if self.path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/data")
            self.end_headers()
            return
        if self.headers.get("If-None-Match") == server.etag:
            self.send_response(304)
            self.send_header("ETag", server.etag)
            self.end_headers()
            return
        start = 0
        ranged = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        if if_range is not None and if_range not in (server.etag, server.modified):
            ranged = None
        if ranged and not server.ignore_range:
            start = int(ranged.split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(DATA) - start))
        self.send_header("ETag", server.etag)
        self.send_header("Last-Modified", server.modified)
        self.end_headers()
        if server.drop_once:
            server.drop_once = False
            self.wfile.write(DATA[start:start + 300000])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(DATA[start:])


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)
        self.said: list[str] = []
        # The server of the test is on the loopback, which downloads refuse.
        patch = mock.patch.object(fetch, "refused", lambda address: False)
        patch.start()
        self.addCleanup(patch.stop)

    def download(self, path="/data", **spec):
        spec = {"url": self.server.base + path, "max_bytes": 10 * len(DATA), **spec}
        return fetch.download(spec, self.run_dir, self.said.append, sleep=lambda s: None)

    def test_a_file_arrives_with_its_digest_and_progress(self):
        result = self.download(sha256=hashlib.sha256(DATA).hexdigest())
        self.assertEqual((result["size"], result["sha256"]), (len(DATA), hashlib.sha256(DATA).hexdigest()))
        self.assertEqual((self.run_dir / fetch.TARGET).read_bytes(), DATA)
        progress = json.loads((self.run_dir / fetch.PROGRESS).read_text())
        self.assertEqual((progress["received"], progress["total"]), (len(DATA), len(DATA)))

    def test_a_dropped_connection_is_taken_up_where_it_stopped(self):
        self.server.drop_once = True
        result = self.download()
        self.assertEqual(result["sha256"], hashlib.sha256(DATA).hexdigest())
        self.assertEqual(self.server.requests[1]["range"], "bytes=300000-")
        self.assertTrue(any("trying again" in line for line in self.said))

    def test_a_server_that_ignores_range_sends_it_all_again(self):
        self.server.drop_once = True
        self.server.ignore_range = True
        result = self.download()
        self.assertEqual(result["sha256"], hashlib.sha256(DATA).hexdigest())
        self.assertEqual((self.run_dir / fetch.TARGET).read_bytes(), DATA)

    def test_redirects_are_followed_within_http(self):
        self.assertEqual(self.download("/moved")["url"], self.server.base + "/data")
        with self.assertRaisesRegex(ValueError, "302"):
            self.download("/to-file")

    def test_what_fails_says_why(self):
        with self.assertRaisesRegex(ValueError, "404"):
            self.download("/missing")
        with self.assertRaisesRegex(ValueError, "digest"):
            self.download(sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "room"):
            self.download(max_bytes=1000)
        with self.assertRaisesRegex(ValueError, "canceled"):
            fetch.download({"url": self.server.base + "/data", "max_bytes": len(DATA)},
                           self.run_dir, self.said.append, lambda: True)


class RefusedTest(unittest.TestCase):
    def test_the_controller_and_link_local_addresses_are_refused(self):
        for address in ("127.0.0.1", "127.8.0.1", "::1", "169.254.169.254", "fe80::1%eth0",
                        "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1", "nonsense"):  # noqa: S104
            self.assertTrue(fetch.refused(address, frozenset()), address)
        for address in ("192.168.1.10", "10.0.0.5", "8.8.8.8", "2001:db8::1"):
            self.assertFalse(fetch.refused(address, frozenset()), address)

    def test_the_addresses_of_the_controller_are_refused(self):
        fib = """Main:
  +-- 0.0.0.0/0 3 0 5
     +-- 192.168.140.0/24 2 0 2
        |-- 192.168.140.0
           /24 link UNICAST
        |-- 192.168.140.10
           /32 host LOCAL
        |-- 192.168.140.255
           /32 link BROADCAST
"""
        inet6 = "20010db8000000000000000000000010 02 40 00 80 eth0\n"
        own = fetch.local_addresses(fib, inet6)
        self.assertEqual(own, {"192.168.140.10", "2001:db8::10"})
        self.assertTrue(fetch.refused("192.168.140.10", own))
        self.assertTrue(fetch.refused("2001:db8::10", own))
        self.assertFalse(fetch.refused("192.168.140.20", own))
        self.assertFalse(fetch.refused("192.168.140.255", own))

    def test_a_download_from_the_loopback_is_refused_where_it_connects(self):
        server = Server()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as run_dir:
            with self.assertRaisesRegex(ValueError, "may not connect"):
                fetch.download({"url": server.base + "/data", "max_bytes": len(DATA)},
                               Path(run_dir), lambda line: None)
        self.assertEqual(server.requests, [])

    def test_urls(self):
        for url in ("https://example.org/a.iso", "http://mirror.lan:8080/x?y=1"):
            self.assertEqual(fetch.check_url(url), url)
        for url in ("ftp://example.org/a", "file:///etc/passwd", "https://user:pw@example.org/",
                    "https://example.org/a b", "https://", "https://example.org:port/",
                    "https://example.org/\n", 1, "x" * 5000):
            with self.assertRaises(BadRequest, msg=url):
                fetch.check_url(url)


class FetchCommandTest(StoreBase):
    def test_an_operator_queues_a_download_beside_the_playbooks(self):
        self.assertEqual(self.code("store.fetch", {"url": "https://example.org/a.iso",
                                                   "path": "a.iso"}, role="viewer"), "forbidden")
        self.assertEqual(self.code("store.fetch", {"url": "file:///etc/passwd", "path": "x"},
                                   role="operator"), "bad_request")
        queued = self.ok("store.fetch", {"url": "https://example.org/w.iso",
                                         "path": "iso/w.iso"}, role="operator")
        run_id = queued["run_id"]
        spec = json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())
        self.assertEqual((spec["kind"], spec["url"]), ("download", "https://example.org/w.iso"))
        self.assertGreater(spec["max_bytes"], 0)
        self.assertIn(run_id, self.started)
        # Its lane is its own: playbooks still start while it runs.
        self.assertEqual([lane[0] for lane in jobs.lanes(self.config)][:2], ["runs", "downloads"])
        listed = self.ok("store.downloads", role="operator")["downloads"]
        self.assertEqual((listed[0]["path"], listed[0]["status"]), ("iso/w.iso", "running"))

        # What the job leaves when it ends.
        directory = paths.RUNS_DIR / run_id
        (directory / "download").mkdir()
        (directory / "download" / "file").write_bytes(DATA)
        (directory / fetch.PROGRESS).write_text(json.dumps({"received": len(DATA), "total": len(DATA)}))
        self.finish(run_id, sha256=hashlib.sha256(DATA).hexdigest(), size=len(DATA))
        self.ok("run.reconcile")
        self.assertEqual((paths.STORE_DIR / "iso/w.iso").read_bytes(), DATA)
        file = self.listed()["iso/w.iso"]
        self.assertEqual((file["origin"], file["source"], file["sha256"], file["updated_by"]),
                         ("url", "https://example.org/w.iso", hashlib.sha256(DATA).hexdigest(), "anna"))
        done = self.ok("store.downloads", role="operator")["downloads"][0]
        self.assertEqual((done["status"], done["size"]), ("successful", len(DATA)))
        self.assertFalse((directory / "download").exists())

        self.assertEqual(self.code("store.fetch", {"url": "https://example.org/w.iso",
                                                   "path": "iso/w.iso"}, role="operator"),
                         "conflict")

    def test_a_file_put_there_meanwhile_is_not_replaced_unless_asked(self):
        run_id = self.ok("store.fetch", {"url": "https://example.org/a", "path": "a"},
                         role="operator")["run_id"]
        self.by_hand("a", b"meanwhile")
        directory = paths.RUNS_DIR / run_id
        (directory / "download").mkdir()
        (directory / "download" / "file").write_bytes(b"downloaded")
        self.finish(run_id)
        self.ok("run.reconcile")
        run = self.ok("run.get", {"id": run_id}, role="viewer")
        self.assertEqual(run["status"], "error")
        self.assertIn("already in the store", run["error"])
        self.assertEqual((paths.STORE_DIR / "a").read_bytes(), b"meanwhile")


class ConditionalTest(DownloadTest):
    def test_a_file_not_changed_there_is_not_downloaded_again(self):
        result = self.download(etag='"v1"', last_modified=self.server.modified)
        self.assertEqual((result["rc"], result["unchanged"]), (0, True))
        self.assertEqual(self.server.requests[0]["if_none_match"], '"v1"')
        self.assertFalse((self.run_dir / fetch.TARGET).exists())

    def test_a_file_changed_there_comes_whole_with_its_validators(self):
        self.server.etag = '"v2"'
        result = self.download(etag='"v1"')
        self.assertEqual((result["sha256"], result["etag"], result["last_modified"]),
                         (hashlib.sha256(DATA).hexdigest(), '"v2"', self.server.modified))

    def test_what_arrived_before_is_taken_up_when_the_file_is_the_same(self):
        (self.run_dir / "download").mkdir()
        (self.run_dir / fetch.TARGET).write_bytes(DATA[:400000])
        result = self.download(partial={"etag": '"v1"'})
        self.assertEqual(result["sha256"], hashlib.sha256(DATA).hexdigest())
        self.assertEqual((self.server.requests[0]["range"], self.server.requests[0]["if_range"]),
                         ("bytes=400000-", '"v1"'))

    def test_what_arrived_before_is_dropped_when_the_file_changed(self):
        (self.run_dir / "download").mkdir()
        (self.run_dir / fetch.TARGET).write_bytes(b"x" * 400000)
        result = self.download(partial={"etag": '"old"'})
        self.assertEqual(result["sha256"], hashlib.sha256(DATA).hexdigest())
        self.assertIn("the server sends the whole file again", self.said)

    def test_a_file_with_another_digest_is_not_kept_to_be_taken_up(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            self.download(sha256="0" * 64)
        self.assertFalse((self.run_dir / fetch.TARGET).exists())

    def test_what_arrived_with_no_validator_is_not_taken_up(self):
        (self.run_dir / "download").mkdir()
        (self.run_dir / fetch.TARGET).write_bytes(b"x" * 400000)
        result = self.download(partial={"etag": 'W/"weak"'})
        self.assertEqual(result["sha256"], hashlib.sha256(DATA).hexdigest())
        self.assertIsNone(self.server.requests[0]["range"])

    def test_a_download_that_fails_says_what_arrived_and_which_version(self):
        with self.assertRaises(ValueError):
            self.download("/missing")
        self.server.drop_once = True
        with mock.patch.object(fetch, "_RETRIES", 0), self.assertRaises(ValueError):
            self.download()
        progress = json.loads((self.run_dir / fetch.PROGRESS).read_text())
        self.assertEqual((progress["received"], progress["etag"]), (300000, '"v1"'))


class SourceTest(StoreBase):
    def fetched(self, path="iso/w.iso", data=DATA, every="never", **result):
        run_id = self.ok("store.fetch", {"url": "https://example.org/w.iso", "path": path,
                                         "every": every, "replace": True},
                         role="operator")["run_id"]
        self.left(run_id, data, **result)
        return run_id

    def left(self, run_id, data, **result):
        directory = paths.RUNS_DIR / run_id
        if data is not None:
            (directory / "download").mkdir(exist_ok=True)
            (directory / fetch.TARGET).write_bytes(data)
        self.finish(run_id, **({"sha256": hashlib.sha256(data).hexdigest(), "etag": '"v1"',
                                 "last_modified": "Tue, 10 Nov 2026 10:00:00 GMT"}
                                if data is not None else {}) | result)
        self.ok("run.reconcile")

    def source(self, path="iso/w.iso"):
        return self.listed()[path]["refresh"]

    def spec(self, run_id):
        return json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())

    def test_a_download_keeps_its_url_as_the_source_of_the_file(self):
        self.fetched(every="weekly")
        source = self.source()
        self.assertEqual((source["url"], source["every"], source["error"]),
                         ("https://example.org/w.iso", "weekly", ""))
        self.assertIsNotNone(source["checked_at"])
        self.assertEqual(self.listed(role="viewer")["iso/w.iso"]["refresh"]["every"], "weekly")

    def test_a_check_asks_conditionally_and_a_304_changes_nothing(self):
        self.fetched()
        before = self.listed()["iso/w.iso"]
        run_id = self.ok("store.refresh", {"paths": ["iso/w.iso"]}, role="operator")["run_ids"][0]
        spec = self.spec(run_id)
        self.assertEqual((spec["etag"], spec["last_modified"]),
                         ('"v1"', "Tue, 10 Nov 2026 10:00:00 GMT"))
        self.left(run_id, None, unchanged=True)
        after = self.listed()["iso/w.iso"]
        self.assertEqual((after["updated_at"], after["sha256"]), (before["updated_at"], before["sha256"]))
        self.assertGreaterEqual(after["refresh"]["checked_at"], before["refresh"]["checked_at"])
        self.assertEqual(after["refresh"]["changed_at"], before["refresh"]["changed_at"])
        done = self.ok("store.downloads", role="operator")["downloads"][0]
        self.assertEqual((done["refresh"], done["unchanged"]), (True, True))

    def test_a_check_that_finds_another_version_replaces_the_file(self):
        self.fetched()
        changed_at = self.source()["changed_at"]
        run_id = self.ok("store.refresh", {"paths": ["iso/w.iso"]}, role="operator")["run_ids"][0]
        self.left(run_id, b"version 2")
        self.assertEqual((paths.STORE_DIR / "iso/w.iso").read_bytes(), b"version 2")
        self.assertGreater(self.source()["changed_at"], changed_at)
        self.assertEqual(self.code("store.refresh", {"paths": ["nothing"]}, role="operator"),
                         "not_found")

    def test_a_check_that_fails_says_so_on_the_file(self):
        self.fetched()
        run_id = self.ok("store.refresh", {"paths": ["iso/w.iso"]}, role="operator")["run_ids"][0]
        self.finish(run_id, rc=2, error="the server answered 500 Internal Server Error")
        self.ok("run.reconcile")
        self.assertIn("500", self.source()["error"])
        self.assertEqual((paths.STORE_DIR / "iso/w.iso").read_bytes(), DATA)

    def test_one_check_at_a_time_for_a_file(self):
        self.fetched()
        first = self.ok("store.refresh", {"paths": ["iso/w.iso"]}, role="operator")
        again = self.ok("store.refresh", {"paths": ["iso/w.iso"]}, role="operator")
        self.assertEqual((len(first["run_ids"]), again["skipped"]), (1, ["iso/w.iso"]))

    def test_the_tick_checks_what_is_due_as_who_set_how_often(self):
        self.fetched()
        self.assertEqual(self.ok("store.track", {"path": "iso/w.iso", "every": "daily"},
                                 role="operator")["every"], "daily")
        self.assertEqual(storage.refresh_due(self.ctx("admin"), time.time()), 0)
        later = time.time() + 86400 + 60
        self.assertEqual(storage.refresh_due(self.ctx("admin"), later), 1)
        run = self.ok("run.list", role="viewer")["runs"][0]
        self.assertEqual((run["kind"], run["created_by"]), ("download", "anna"))
        audit = [e for e in self.ok("audit.list")["entries"] if e["command"] == "store.refresh"]
        self.assertEqual(len(audit), 1)
        # Under way: not queued again.
        self.assertEqual(storage.refresh_due(self.ctx("admin"), later + 86400 * 2), 0)
        self.assertEqual(self.code("store.track", {"path": "nothing", "every": "daily"},
                                   role="operator"), "not_found")
        self.assertEqual(self.code("store.track", {"path": "iso/w.iso", "every": "daily"},
                                   role="viewer"), "forbidden")

    def test_the_source_follows_a_move_and_leaves_with_a_replacement(self):
        self.fetched()
        self.ok("store.move", {"path": "iso", "to": "images"}, role="operator")
        self.assertEqual(self.source("images/w.iso")["url"], "https://example.org/w.iso")
        self.assertTrue(self.put("images/w.iso", b"by hand", replace=True)["ok"])
        self.assertIsNone(self.source("images/w.iso"))
        self.fetched("b.iso")
        self.ok("store.delete", {"path": "b.iso"}, role="operator")
        conn = self.ctx("admin").conn
        self.assertEqual(conn.execute("SELECT count(*) FROM store_sources").fetchone()[0], 0)

    def test_try_again_takes_up_what_arrived(self):
        run_id = self.ok("store.fetch", {"url": "https://example.org/w.iso", "path": "w.iso"},
                         role="operator")["run_id"]
        directory = paths.RUNS_DIR / run_id
        (directory / "download").mkdir()
        (directory / fetch.TARGET).write_bytes(DATA[:1000])
        (directory / fetch.PROGRESS).write_text(json.dumps({"received": 1000, "etag": '"v1"'}))
        self.finish(run_id, rc=2, error="the download failed: timed out")
        self.ok("run.reconcile")
        listed = self.ok("store.downloads", role="operator")["downloads"][0]
        self.assertEqual((listed["status"], listed["partial"]), ("failed", 1000))
        self.assertEqual(self.code("store.fetch", {"url": "https://example.org/other",
                                                   "path": "w.iso", "resume": run_id},
                                   role="operator"), "bad_request")
        again = self.ok("store.fetch", {"url": "https://example.org/w.iso", "path": "w.iso",
                                        "resume": run_id}, role="operator")["run_id"]
        self.assertEqual(self.spec(again)["partial"], {"etag": '"v1"'})
        self.assertEqual((paths.RUNS_DIR / again / fetch.TARGET).read_bytes(), DATA[:1000])
        self.assertFalse((directory / fetch.TARGET).exists())
        self.assertEqual(self.code("store.fetch", {"url": "https://example.org/w.iso",
                                                   "path": "w.iso", "resume": run_id},
                                   role="operator"), "conflict")

    def test_what_a_failed_download_left_goes_after_a_week(self):
        run_id = self.ok("store.fetch", {"url": "https://example.org/w.iso", "path": "w.iso"},
                         role="operator")["run_id"]
        directory = paths.RUNS_DIR / run_id
        (directory / "download").mkdir()
        (directory / fetch.TARGET).write_bytes(b"part")
        self.finish(run_id, rc=2, error="failed")
        self.ok("run.reconcile")
        storage.refresh_due(self.ctx("admin"), time.time())
        self.assertTrue((directory / fetch.TARGET).exists())
        storage.refresh_due(self.ctx("admin"), time.time() + 8 * 86400)
        self.assertFalse((directory / "download").exists())
