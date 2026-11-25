"""Documents: texts in a history of their own, attachments beside them."""

import dataclasses
import hashlib
import io
import json
import os

from test_phase2 import Base

from cockpit_fleet import cli, documents, paths


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DocumentsTest(Base):
    def write(self, path, content, expected="", role="operator", **extra):
        return self.call("doc.write", {"path": path, "content": content, "expected": expected,
                                       **extra}, role)

    def written(self, path, content, expected="", **extra):
        answer = self.write(path, content, expected, **extra)
        self.assertTrue(answer["ok"], answer)
        return answer["data"]

    def put(self, path, data, role="operator", **extra):
        ctx = self.ctx(role)
        ctx.stdin = io.BytesIO(data)
        params = {"path": path, "size": len(data), **extra}
        return cli.dispatch("doc.put", json.dumps(params).encode(), ctx)

    def as_agent(self, name, params, role="operator"):
        self.config = dataclasses.replace(self.config, mcp_enabled=True, mcp_max_role="operator")
        return self.call(name, {**params, "via": "mcp:test"}, role)

    def history(self, path=""):
        return self.ok("doc.history", {"path": path} if path else {}, role="viewer")["commits"]

    def test_a_text_is_written_read_and_kept_in_the_history_by_its_author(self):
        done = self.written("reports/web01.md", "# web01\n\nDisk full.\n")
        self.assertTrue(done["created"])
        read = self.ok("doc.read", {"path": "reports/web01.md"}, role="viewer")
        self.assertEqual(read["content"], "# web01\n\nDisk full.\n")
        self.assertEqual(read["sha256"], done["sha256"])
        self.assertIsNone(read["next"])
        commits = self.history()
        self.assertEqual([(c["author"], c["subject"], c["paths"]) for c in commits],
                         [("anna", "Create reports/web01.md", ["reports/web01.md"])])
        self.assertEqual(commits[0]["revision"], done["revision"])
        self.assertFalse((paths.DOCUMENTS_DIR / ".git").exists())
        audit = self.ok("audit.list")["entries"][0]
        self.assertEqual(audit["command"], "doc.write")
        self.assertNotIn("Disk full", json.dumps(audit))

    def test_a_replacement_quotes_the_digest_of_what_was_read(self):
        first = self.written("a.md", "one\n")
        self.assertEqual(self.write("a.md", "two\n")["error"]["code"], "conflict")
        self.assertEqual(self.write("a.md", "two\n", sha(b"other"))["error"]["code"], "conflict")
        second = self.written("a.md", "two\n", first["sha256"], message="Second thoughts")
        self.assertFalse(second["created"])
        # Someone else wrote meanwhile: the first digest is stale.
        self.assertEqual(self.write("a.md", "three\n", first["sha256"])["error"]["code"],
                         "conflict")
        self.assertEqual(self.write("gone.md", "x", first["sha256"])["error"]["code"],
                         "conflict")
        self.assertEqual([c["subject"] for c in self.history("a.md")],
                         ["Second thoughts", "Create a.md"])

    def test_viewers_read_operators_write_and_only_texts_are_written(self):
        self.assertEqual(self.write("a.md", "x", role="viewer")["error"]["code"], "forbidden")
        for name, params in (("doc.mkdir", {"path": "d"}), ("doc.delete", {"path": "d"}),
                             ("doc.move", {"path": "a.md", "to": "b.md"}),
                             ("doc.restore", {"path": "a.md", "revision": "abcdef1"})):
            self.assertEqual(self.code(name, params, role="viewer"), "forbidden", name)
        self.assertEqual(self.write("a.txt", "x")["error"]["code"], "bad_request")
        self.assertEqual(self.write("../a.md", "x")["error"]["code"], "bad_request")
        self.assertEqual(self.write(".git/a.md", "x")["error"]["code"], "bad_request")
        self.ok("doc.list", role="viewer")

    def test_an_agent_reads_a_long_text_in_parts_cut_between_characters(self):
        text = "è" * 20000 + "\nfine\n"
        self.written("long.md", text)
        parts, offset = [], 0
        while offset is not None:
            answer = self.as_agent("doc.read", {"path": "long.md", "offset": offset})
            self.assertTrue(answer["ok"], answer)
            data = answer["data"]
            self.assertLessEqual(len(data["content"].encode()), documents.AGENT_CHUNK)
            parts.append(data["content"])
            offset = data["next"]
        self.assertGreater(len(parts), 2)
        self.assertEqual("".join(parts), text)
        # In the middle of a character.
        self.assertEqual(self.code("doc.read", {"path": "long.md", "offset": 1}), "bad_request")
        # A person gets it whole.
        self.assertEqual(self.ok("doc.read", {"path": "long.md"})["content"], text)

    def test_search_finds_every_word_in_name_or_text_the_most_hits_first(self):
        self.written("hosts/web01.md", "Apache restarted.\nDisk full on /var.\nDisk cleaned.\n")
        self.written("hosts/db01.md", "PostgreSQL vacuum.\nDisk fine.\n")
        self.written("notes/other.md", "Nothing here.\n")
        found = self.ok("doc.search", {"query": "DISK"}, role="viewer")
        self.assertEqual([m["path"] for m in found["matches"]], ["hosts/web01.md", "hosts/db01.md"])
        self.assertEqual([line["line"] for line in found["matches"][0]["lines"]], [2, 3])
        both = self.ok("doc.search", {"query": "disk web01"})["matches"]
        self.assertEqual([m["path"] for m in both], ["hosts/web01.md"])
        self.assertEqual(self.ok("doc.search", {"query": "nothing", "path": "hosts"})["matches"],
                         [])
        self.assertEqual(self.code("doc.search", {"query": "   "}), "bad_request")

    def test_attachments_stay_out_of_the_history_with_what_they_came_from(self):
        self.written("reports/r.md", "See the graph.\n")
        data = os.urandom(300_000)
        answer = self.put("reports/graph.png", data, sha256=sha(data), source="reports/r.md")
        self.assertTrue(answer["ok"], answer)
        self.assertIsNone(answer["data"]["revision"])
        listed = {f["path"]: f for f in self.ok("doc.list", role="viewer")["files"]}
        self.assertEqual((listed["reports/graph.png"]["origin"],
                          listed["reports/graph.png"]["source"],
                          listed["reports/graph.png"]["created_by"]),
                         ("upload", "reports/r.md", "anna"))
        self.assertTrue(listed["reports/r.md"]["text"])
        self.assertEqual(len(self.history()), 1)
        # Read as text, it is refused; got as bytes, it is whole.
        self.assertEqual(self.code("doc.read", {"path": "reports/graph.png"}), "bad_request")
        ctx = self.ctx("viewer")
        got = cli.dispatch("doc.get", json.dumps({"path": "reports/graph.png"}).encode(), ctx)
        self.assertTrue(got["ok"], got)
        with ctx.output as output:
            self.assertEqual(output.read(), data)
        self.assertEqual(self.put("reports/graph.png", b"x")["error"]["code"], "conflict")
        self.assertEqual(self.put("x.bin", b"abc", sha256=sha(b"abd"))["error"]["code"],
                         "bad_request")
        self.assertEqual(os.listdir(paths.DOCUMENTS_UPLOADS), [])

    def test_a_text_uploaded_goes_into_the_history(self):
        answer = self.put("imported.md", b"# Imported\n")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.history()[0]["subject"], "Upload imported.md")
        self.assertEqual(self.put("bad.md", b"\xff\xfe")["error"]["code"], "bad_request")

    def test_a_folder_moved_takes_its_texts_history_and_attachments_along(self):
        self.written("2026/web01.md", "one\n")
        self.put("2026/web01.png", b"png", source="2026/web01.md")
        self.ok("doc.move", {"path": "2026", "to": "archive/2026"}, role="operator")
        self.assertFalse((paths.DOCUMENTS_DIR / "2026").exists())
        listed = {f["path"]: f for f in self.ok("doc.list")["files"]}
        self.assertEqual(set(listed), {"archive/2026/web01.md", "archive/2026/web01.png"})
        self.assertEqual(listed["archive/2026/web01.png"]["origin"], "upload")
        self.assertEqual([c["subject"] for c in self.history("archive/2026/web01.md")],
                         ["Move 2026 to archive/2026", "Create 2026/web01.md"])
        # A text stays a text.
        self.assertEqual(self.code("doc.move", {"path": "archive/2026/web01.md",
                                                "to": "web01.txt"}, role="operator"),
                         "bad_request")
        self.assertEqual(self.code("doc.move", {"path": "nothing.md", "to": "b.md"},
                                   role="operator"), "not_found")

    def test_a_copy_of_a_folder(self):
        self.written("t/a.md", "a\n")
        self.put("t/sub/b.bin", b"b")
        self.ok("doc.mkdir", {"path": "t/empty"}, role="operator")
        self.ok("doc.copy", {"path": "t", "to": "u"}, role="operator")
        self.assertEqual((paths.DOCUMENTS_DIR / "u/a.md").read_text(), "a\n")
        self.assertEqual((paths.DOCUMENTS_DIR / "u/sub/b.bin").read_bytes(), b"b")
        self.assertTrue((paths.DOCUMENTS_DIR / "u/empty").is_dir())
        self.assertIn("u/sub/b.bin", {f["path"] for f in self.ok("doc.list")["files"]
                                      if f.get("origin")})
        self.assertEqual(self.code("doc.copy", {"path": "t", "to": "t/inside"}, role="operator"),
                         "bad_request")
        self.assertEqual(self.code("doc.copy", {"path": "t", "to": "u"}, role="operator"),
                         "conflict")

    def test_a_deleted_text_is_restored_from_the_history(self):
        first = self.written("r.md", "first\n")
        second = self.written("r.md", "second\n", first["sha256"])
        self.put("r.png", b"png")
        self.ok("doc.delete", {"paths": ["r.md", "r.png"]}, role="operator")
        self.assertEqual(self.ok("doc.list")["files"], [])
        restored = self.ok("doc.restore", {"path": "r.md", "revision": first["revision"]},
                           role="operator")
        self.assertEqual((paths.DOCUMENTS_DIR / "r.md").read_text(), "first\n")
        self.assertEqual(self.history()[0]["subject"],
                         f"Restore r.md as of {first['revision'][:12]}")
        self.assertEqual(restored["revision"], self.history()[0]["revision"])
        # Beside the one there now.
        self.ok("doc.restore", {"path": "r.md", "revision": second["revision"],
                                "to": "r-second.md"}, role="operator")
        self.assertEqual((paths.DOCUMENTS_DIR / "r-second.md").read_text(), "second\n")
        self.assertEqual(self.code("doc.restore", {"path": "nothing.md",
                                                   "revision": first["revision"]},
                                   role="operator"), "not_found")
        self.assertEqual(self.code("doc.restore", {"path": "r.md", "revision": "0" * 40},
                                   role="operator"), "not_found")
        self.assertEqual(self.code("doc.restore", {"path": "r.md", "revision": "HEAD"},
                                   role="operator"), "bad_request")

    def test_the_diff_of_a_change_and_between_two(self):
        first = self.written("d.md", "alpha\nbeta\n")
        second = self.written("d.md", "alpha\ngamma\n", first["sha256"])
        shown = self.ok("doc.diff", {"revision": second["revision"]}, role="viewer")["text"]
        self.assertIn("-beta", shown)
        self.assertIn("+gamma", shown)
        created = self.ok("doc.diff", {"revision": first["revision"]})["text"]
        self.assertIn("+alpha", created)
        between = self.ok("doc.diff", {"revision": first["revision"],
                                       "against": second["revision"]})["text"]
        self.assertIn("+beta", between)

    def test_a_change_made_by_hand_is_the_services_not_the_next_callers(self):
        self.written("a.md", "a\n")
        (paths.DOCUMENTS_DIR / "by-hand.md").write_text("by hand\n")
        self.written("b.md", "b\n")
        commits = self.history()
        self.assertEqual([(c["author"], c["paths"]) for c in commits[:2]],
                         [("anna", ["b.md"]), (paths.SERVICE_USER, ["by-hand.md"])])

    def test_the_documents_are_held_to_their_limit(self):
        self.config = dataclasses.replace(self.config, documents_max_mb=1)
        self.assertTrue(self.put("big.bin", b"x" * 700_000)["ok"])
        self.assertEqual(self.put("more.bin", b"x" * 400_000)["error"]["code"], "conflict")
        self.assertEqual(self.write("a.md", "y" * 400_000)["error"]["code"], "conflict")
        self.assertTrue(self.put("big.bin", b"x" * 900_000, replace=True)["ok"])

    def test_links_in_the_tree_are_not_followed(self):
        outside = self.root / "outside.md"
        outside.write_text("secret\n")
        paths.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        (paths.DOCUMENTS_DIR / "link.md").symlink_to(outside)
        (paths.DOCUMENTS_DIR / "dir").symlink_to(self.root)
        self.assertEqual(self.code("doc.read", {"path": "link.md"}), "conflict")
        self.assertIn(self.code("doc.read", {"path": "dir/outside.md"}), ("conflict", "not_found"))
        self.assertEqual(self.ok("doc.search", {"query": "secret"})["matches"], [])
        self.assertNotIn("link.md", {f["path"] for f in self.ok("doc.list")["files"]})
        self.assertEqual(self.code("doc.write", {"path": "dir/x.md", "content": "x"},
                                   role="operator"), "conflict")
        self.assertFalse((self.root / "x.md").exists())
