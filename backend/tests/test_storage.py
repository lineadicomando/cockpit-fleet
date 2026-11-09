"""The store: files put there from the browser or by hand, and the farm a run gets."""

import hashlib
import io
import json
import os
from collections import namedtuple
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import cli, maintenance, paths, runner, storage


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StoreBase(Base):
    def put(self, path, data, role="operator", sent=None, **extra):
        """store.put as fleetctl makes it: the parameters, then the bytes."""
        ctx = self.ctx(role)
        ctx.stdin = io.BytesIO(data if sent is None else sent)
        params = {"path": path, "size": len(data), **extra}
        return cli.dispatch("store.put", json.dumps(params).encode(), ctx)

    def by_hand(self, path, data):
        target = paths.STORE_DIR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def listed(self, role="viewer"):
        return {f["path"]: f for f in self.ok("store.list", role=role)["files"]}


class StoreTest(StoreBase):
    def test_an_upload_lands_in_the_store_with_its_digest_and_who_put_it(self):
        data = os.urandom(3 * 1024 * 1024 + 5)
        answer = self.put("apps/setup.msi", data, sha256=sha(data))
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["data"]["sha256"], sha(data))
        self.assertEqual((paths.STORE_DIR / "apps/setup.msi").read_bytes(), data)
        listed = self.listed()["apps/setup.msi"]
        self.assertEqual((listed["origin"], listed["updated_by"], listed["size"]),
                         ("upload", "anna", len(data)))
        self.assertIn("apps", self.ok("store.list", role="viewer")["directories"])
        self.assertEqual(list(paths.STORE_UPLOADS.iterdir()), [])
        audit = self.ok("audit.list")["entries"][0]
        self.assertEqual((audit["command"], audit["outcome"]), ("store.put", "ok"))

    def test_viewers_read_but_do_not_write(self):
        self.assertEqual(self.put("a.bin", b"x", role="viewer")["error"]["code"], "forbidden")
        for name, params in (("store.mkdir", {"path": "d"}), ("store.delete", {"path": "d"}),
                             ("store.move", {"path": "a", "to": "b"})):
            self.assertEqual(self.code(name, params, role="viewer"), "forbidden", name)
        self.ok("store.list", role="viewer")

    def test_an_upload_that_stops_is_resumed_from_what_arrived(self):
        data = os.urandom(2 * 1024 * 1024)
        broken = self.put("iso/win.iso", data, sent=data[:1500000])
        self.assertEqual(broken["error"]["code"], "conflict")
        self.assertFalse((paths.STORE_DIR / "iso/win.iso").exists())
        pending = self.ok("store.uploads", role="operator")["uploads"]
        self.assertEqual([(u["path"], u["received"], u["size"]) for u in pending],
                         [("iso/win.iso", 1500000, len(data))])
        # Another user's uploads are their own.
        other = self.ctx("operator", "bruno")
        self.assertEqual(cli.dispatch("store.uploads", b"{}", other)["data"]["uploads"], [])
        wrong = self.put("iso/win.iso", data, sent=data[1000:], offset=1000)
        self.assertEqual(wrong["error"]["code"], "conflict")
        done = self.put("iso/win.iso", data, sent=data[1500000:], offset=1500000)
        self.assertEqual(done["data"]["sha256"], sha(data))
        self.assertEqual((paths.STORE_DIR / "iso/win.iso").read_bytes(), data)
        self.assertEqual(self.ok("store.uploads", role="operator")["uploads"], [])

    def test_what_did_not_arrive_as_promised_is_dropped(self):
        data = b"installer"
        self.assertEqual(self.put("a.exe", data, sha256=sha(b"other"))["error"]["code"],
                         "bad_request")
        self.assertEqual(self.put("a.exe", data, sent=data + b"more")["error"]["code"],
                         "bad_request")
        self.assertFalse((paths.STORE_DIR / "a.exe").exists())
        self.assertEqual(self.ok("store.uploads", role="operator")["uploads"], [])
        ctx = self.ctx("operator")
        answer = cli.dispatch("store.put", json.dumps({"path": "a", "size": 1}).encode(), ctx)
        self.assertEqual(answer["error"]["code"], "bad_request")

    def test_a_file_is_replaced_only_when_asked(self):
        self.assertTrue(self.put("a.txt", b"one")["ok"])
        self.assertEqual(self.put("a.txt", b"two")["error"]["code"], "conflict")
        self.assertTrue(self.put("a.txt", b"two", replace=True)["ok"])
        self.assertEqual((paths.STORE_DIR / "a.txt").read_bytes(), b"two")
        self.ok("store.mkdir", {"path": "dir"}, role="operator")
        self.assertEqual(self.put("dir", b"x", replace=True)["error"]["code"], "conflict")

    def test_paths_stay_in_the_store(self):
        for path in ("../escape", "/etc/passwd", "a/../../b", ".git/config", "a\\b", ""):
            self.assertEqual(self.put(path, b"x")["error"]["code"], "bad_request", path)
            self.assertEqual(self.code("store.get", {"path": path}, role="viewer"),
                             "bad_request", path)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_text("no")
        paths.STORE_DIR.mkdir(exist_ok=True)
        (paths.STORE_DIR / "link").symlink_to(outside)
        (paths.STORE_DIR / "file-link").symlink_to(outside / "secret")
        self.assertEqual(self.put("link/x", b"x")["error"]["code"], "conflict")
        self.assertEqual(self.code("store.get", {"path": "file-link"}, role="viewer"),
                         "not_found")
        self.assertEqual(self.code("store.read", {"path": "file-link"}, role="viewer"),
                         "conflict")
        self.assertNotIn("file-link", self.listed())
        self.assertEqual(self.code("store.delete", {"path": "link/secret"}, role="operator"),
                         "conflict")
        self.assertTrue((outside / "secret").exists())

    def test_a_file_put_by_hand_appears_and_the_tick_takes_its_digest(self):
        self.by_hand("win_iso/w.iso", b"image")
        listed = self.listed()["win_iso/w.iso"]
        self.assertEqual((listed["origin"], listed["sha256"], listed["usable"]),
                         ("found", None, True))
        self.assertEqual(storage.digest_pending(self.ctx(), 0), 1)
        self.assertEqual(self.listed()["win_iso/w.iso"]["sha256"], sha(b"image"))
        # Changed outside the backend: its digest is taken again.
        (paths.STORE_DIR / "win_iso/w.iso").write_bytes(b"image 2")
        self.assertIsNone(self.listed()["win_iso/w.iso"]["sha256"])
        self.assertIn("store", maintenance.tidy(self.ctx()))
        self.assertEqual(self.ok("store.get", {"path": "win_iso/w.iso"}, role="viewer")["sha256"],
                         sha(b"image 2"))
        (paths.STORE_DIR / "win_iso/w.iso").unlink()
        self.assertEqual(self.listed(), {})

    def test_read_gives_the_bytes_and_is_audited(self):
        self.by_hand("a/b.bin", b"\x00\x01payload")
        ctx = self.ctx("viewer")
        answer = cli.dispatch("store.read", b'{"path": "a/b.bin"}', ctx)
        self.assertEqual(answer["data"], {"path": "a/b.bin", "size": 9})
        with ctx.output as source:
            self.assertEqual(source.read(), b"\x00\x01payload")
        audit = self.ok("audit.list")["entries"][0]
        self.assertEqual((audit["command"], audit["user"]), ("store.read", "anna"))
        self.assertEqual(self.code("store.read", {"path": "a"}, role="viewer"), "conflict")
        self.assertEqual(self.code("store.read", {"path": "none"}, role="viewer"), "not_found")

    def test_move_and_delete_keep_the_rows_with_their_files(self):
        self.assertTrue(self.put("apps/one/a.msi", b"a")["ok"])
        self.assertTrue(self.put("apps/one/b.msi", b"b")["ok"])
        self.ok("store.move", {"path": "apps/one", "to": "apps/two"}, role="operator")
        listed = self.listed()
        self.assertEqual(set(listed), {"apps/two/a.msi", "apps/two/b.msi"})
        self.assertEqual(listed["apps/two/a.msi"]["origin"], "upload")
        self.assertEqual(self.code("store.move", {"path": "apps", "to": "apps/two/x"},
                                   role="operator"), "bad_request")
        self.assertEqual(self.code("store.move", {"path": "apps/two/a.msi",
                                                  "to": "apps/two/b.msi"}, role="operator"),
                         "conflict")
        self.ok("store.move", {"path": "apps/two/a.msi", "to": "top.msi"}, role="operator")
        self.ok("store.delete", {"path": "apps"}, role="operator")
        self.assertEqual(set(self.listed()), {"top.msi"})
        self.assertFalse((paths.STORE_DIR / "apps").exists())
        self.assertEqual(self.code("store.delete", {"path": "apps"}, role="operator"),
                         "not_found")

    def test_a_full_disk_or_the_limit_refuses_a_file(self):
        Usage = namedtuple("Usage", "total used free")
        with mock.patch.object(storage.shutil, "disk_usage",
                               lambda path: Usage(10, 0, storage.MIN_FREE + 10)):
            self.assertTrue(self.put("small", b"0123456789")["ok"])
            self.assertEqual(self.put("big", b"0123456789!")["error"]["code"], "conflict")
        self.config = self.config.__class__(**{**self.config.as_dict(), "store_max_gb": 1})
        with mock.patch.object(storage, "_usage", lambda conn: 1024 ** 3 - 5):
            self.assertEqual(self.put("more", b"0123456789")["error"]["code"], "conflict")
            # Replacing a file counts what it frees.
            self.assertTrue(self.put("small", b"01234", replace=True)["ok"])


class FarmTest(StoreBase):
    def launch(self):
        if not hasattr(self, "template_id"):
            self.template_id = self.template()["id"]
        run = self.ok("run.start", {"template_id": self.template_id}, role="operator")
        return run["id"], paths.RUNS_DIR / run["id"]

    def test_a_run_gets_the_store_as_hard_links_and_gives_back_what_it_wrote(self):
        kept = self.by_hand("apps/kept.msi", b"kept")
        old = self.by_hand("apps/old.msi", b"old")
        self.by_hand("apps/deleted.msi", b"stays")
        edited = self.by_hand("notes.txt", b"v1")
        self.ok("store.list", role="viewer")
        run_id, directory = self.launch()
        farm = directory / "store"
        self.assertEqual((farm / "apps/kept.msi").stat().st_ino, kept.stat().st_ino)
        self.assertTrue((directory / "scratch/remote_tmp").is_dir())
        manifest = json.loads((paths.STORE_FARMS / f"{run_id}.json").read_text())
        self.assertEqual(set(manifest), {"apps/kept.msi", "apps/old.msi", "apps/deleted.msi",
                                         "notes.txt"})

        # What the playbook does: get_url replaces a file by a rename, a fetch
        # adds one, a task deletes one and another writes through a link.
        (farm / "apps/new.tmp").write_bytes(b"new")
        os.replace(farm / "apps/new.tmp", farm / "apps/old.msi")
        (farm / "pulled").mkdir()
        (farm / "pulled/hostname").write_bytes(b"node1\n")
        (farm / "empty").mkdir()
        (farm / "apps/deleted.msi").unlink()
        with open(farm / "notes.txt", "ab") as notes:
            notes.write(b" v2")
        (farm / "linked").write_bytes(b"twice")
        os.link(farm / "linked", directory / "other-link")
        (farm / "bad-link").symlink_to("/etc/passwd")

        self.finish(run_id)
        self.ok("run.reconcile")
        self.assertEqual(old.read_bytes(), b"new")
        self.assertEqual((paths.STORE_DIR / "pulled/hostname").read_bytes(), b"node1\n")
        self.assertTrue((paths.STORE_DIR / "empty").is_dir())
        self.assertEqual((paths.STORE_DIR / "apps/deleted.msi").read_bytes(), b"stays")
        self.assertEqual(edited.read_bytes(), b"v1 v2")
        self.assertFalse((paths.STORE_DIR / "linked").exists())
        self.assertFalse((paths.STORE_DIR / "bad-link").exists())
        self.assertFalse(farm.exists())
        self.assertFalse((paths.STORE_FARMS / f"{run_id}.json").exists())

        listed = self.listed()
        for path in ("apps/old.msi", "pulled/hostname", "notes.txt"):
            self.assertEqual((listed[path]["origin"], listed[path]["source"],
                              listed[path]["updated_by"]), ("run", run_id, "anna"), path)
        self.assertEqual(listed["apps/kept.msi"]["origin"], "found")
        run = self.ok("run.get", {"id": run_id}, role="viewer")
        self.assertEqual(run["options"]["store"]["counts"], {"added": 1, "replaced": 1, "changed": 1, "skipped": 1})
        intake = [e for e in self.ok("audit.list")["entries"] if e["command"] == "store.intake"]
        self.assertEqual(len(intake), 1)
        self.assertEqual(intake[0]["user"], "anna")

    def test_a_file_replaced_in_the_store_during_the_run_is_not_taken_back(self):
        target = self.by_hand("a.msi", b"before")
        run_id, directory = self.launch()
        target.unlink()
        target.write_bytes(b"uploaded meanwhile")
        self.finish(run_id)
        self.ok("run.reconcile")
        self.assertEqual(target.read_bytes(), b"uploaded meanwhile")

    def test_the_playbook_is_told_where_the_store_is(self):
        run_id, directory = self.launch()
        runtime = self.root / "runtime"
        runtime.mkdir()
        spec = json.loads((directory / "spec.json").read_text())
        argv = runner.build_argv(spec, directory, runtime, None)
        first = argv.index("--extra-vars")
        self.assertEqual(argv[first + 1], f"@{runtime / 'fleet_store.json'}")
        self.assertEqual(json.loads((runtime / "fleet_store.json").read_text()),
                         {"fleet_store": str(directory / "store")})

    def test_a_canceled_run_forgets_its_farm(self):
        self.config = self.config.__class__(**{**self.config.as_dict(), "max_concurrent_runs": 1})
        first, _ = self.launch()
        second, _ = self.launch()
        self.assertFalse((paths.STORE_FARMS / f"{second}.json").exists())
        self.ok("run.cancel", {"id": first}, role="operator")
        self.finish(first, rc=-15, terminated=True)
        self.ok("run.reconcile")
        self.assertFalse((paths.STORE_FARMS / f"{first}.json").exists())
        self.assertTrue((paths.STORE_FARMS / f"{second}.json").exists())

    def test_a_store_on_another_file_system_leaves_the_run_without_it(self):
        def cross(*args, **kwargs):
            raise OSError(18, "Invalid cross-device link")

        self.by_hand("a.msi", b"x")
        with mock.patch.object(storage.os, "link", cross):
            run_id, directory = self.launch()
        self.assertFalse((directory / "store").exists())
        self.assertIn("not on the file system of the runs",
                      (directory / "stdout.log").read_text())
        self.finish(run_id)
        self.ok("run.reconcile")
        self.assertEqual(self.ok("run.get", {"id": run_id}, role="viewer")["status"],
                         "successful")


class StreamOutTest(StoreBase):
    def test_fleetctl_writes_the_bytes_or_the_answer_to_stderr(self):
        import contextlib
        import sys
        self.by_hand("a.bin", b"bytes")
        ctx = self.ctx("viewer")
        answer = cli.dispatch("store.read", b'{"path": "a.bin"}', ctx)
        out = io.BytesIO()
        wrapper = io.TextIOWrapper(out)
        with mock.patch.object(sys, "stdout", wrapper):
            self.assertEqual(cli._stream_out(answer, ctx), 0)
        self.assertEqual(out.getvalue(), b"bytes")
        wrapper.detach()
        ctx = self.ctx("viewer")
        answer = cli.dispatch("store.read", b'{"path": "none"}', ctx)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli._stream_out(answer, ctx), 1)
        self.assertEqual(json.loads(err.getvalue())["error"]["code"], "not_found")
