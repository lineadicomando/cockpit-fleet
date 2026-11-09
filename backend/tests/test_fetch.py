"""Downloads into the store: the job against a server of the test, and the
commands that queue it and take what it leaves."""

import hashlib
import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from test_storage import StoreBase

from cockpit_fleet import fetch, jobs, paths
from cockpit_fleet.errors import BadRequest

DATA = bytes(range(256)) * 4096  # 1 MiB


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.drop_once = False
        self.ignore_range = False
        self.requests: list[dict] = []

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        server = self.server
        server.requests.append({"path": self.path, "range": self.headers.get("Range")})
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
        start = 0
        ranged = self.headers.get("Range")
        if ranged and not server.ignore_range:
            start = int(ranged.split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(DATA) - start))
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
