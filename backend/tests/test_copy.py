"""Many entries of the store moved, deleted or copied in one call; the token of a listing."""

import hashlib
import os
from unittest import mock

from test_storage import StoreBase

from cockpit_fleet import cli, copier, jobs, paths


def audited(test, command):
    return [entry for entry in test.ok("audit.list")["entries"] if entry["command"] == command]


class ManyTest(StoreBase):
    def setUp(self):
        super().setUp()
        for path in ("win/a.msi", "win/b.msi", "iso/c.iso"):
            self.assertTrue(self.put(path, path.encode())["ok"])

    def test_many_moves_are_one_call_and_one_record(self):
        moves = [{"path": "win/a.msi", "to": "apps/a.msi"}, {"path": "iso", "to": "apps/iso"}]
        done = self.ok("store.move", {"moves": moves}, role="operator")
        self.assertEqual(done["moved"], moves)
        self.assertEqual(set(self.listed()), {"apps/a.msi", "apps/iso/c.iso", "win/b.msi"})
        self.assertEqual(self.listed()["apps/iso/c.iso"]["origin"], "upload")
        self.assertEqual(len(audited(self, "store.move")), 1)

    def test_nothing_moves_when_one_cannot(self):
        for moves, code in (
                ([{"path": "win/a.msi", "to": "x.msi"}, {"path": "win/b.msi", "to": "iso/c.iso"}],
                 "conflict"),
                ([{"path": "win/a.msi", "to": "x.msi"}, {"path": "gone", "to": "y"}], "not_found"),
                ([{"path": "win/a.msi", "to": "x.msi"}, {"path": "win/b.msi", "to": "x.msi"}],
                 "bad_request"),
                ([{"path": "win", "to": "w"}, {"path": "win/a.msi", "to": "a.msi"}], "bad_request"),
                ([{"path": "win", "to": "w"}, {"path": "iso", "to": "win/iso"}], "bad_request"),
                ([{"path": "win", "to": "win/inner"}], "bad_request"),
                ([], "bad_request")):
            self.assertEqual(self.code("store.move", {"moves": moves}, role="operator"), code, moves)
        self.assertEqual(set(self.listed()), {"win/a.msi", "win/b.msi", "iso/c.iso"})

    def test_a_move_that_fails_half_way_is_undone(self):
        real = os.rename
        calls = []

        def rename(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError(5, "Input/output error")
            return real(*args, **kwargs)

        moves = [{"path": "win/a.msi", "to": "x.msi"}, {"path": "win/b.msi", "to": "y.msi"}]
        with mock.patch("os.rename", rename):
            self.assertFalse(self.call("store.move", {"moves": moves}, "operator")["ok"])
        self.assertTrue((paths.STORE_DIR / "win/a.msi").exists())
        self.assertFalse((paths.STORE_DIR / "x.msi").exists())

    def test_many_deletes_are_one_call_and_all_or_nothing(self):
        self.assertEqual(self.code("store.delete", {"paths": ["win/a.msi", "gone"]},
                                   role="operator"), "not_found")
        self.assertTrue((paths.STORE_DIR / "win/a.msi").exists())
        done = self.ok("store.delete", {"paths": ["win", "win/a.msi", "iso/c.iso"]},
                       role="operator")
        self.assertEqual(done["deleted"], ["win", "iso/c.iso"])
        self.assertEqual(set(self.listed()), set())
        self.assertEqual(len(audited(self, "store.delete")), 2)
        self.assertEqual(self.code("store.delete", {}, role="operator"), "bad_request")

    def test_a_listing_says_unchanged_to_its_own_token(self):
        first = self.ok("store.list", role="viewer")
        again = self.ok("store.list", {"since": first["token"]}, role="viewer")
        self.assertEqual(set(again), {"unchanged", "token", "free"})
        self.assertTrue(again["unchanged"])
        self.by_hand("by-hand.txt", b"new")
        changed = self.ok("store.list", {"since": first["token"]}, role="viewer")
        self.assertNotEqual(changed["token"], first["token"])
        self.assertIn("by-hand.txt", {file["path"] for file in changed["files"]})


class CopyTest(StoreBase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.put("win/a.msi", b"installer")["ok"])
        self.assertTrue(self.put("win/sub/b.cfg", b"config")["ok"])

    def test_a_small_copy_is_made_at_once_with_the_digest_of_its_source(self):
        done = self.ok("store.copy", {"copies": [{"path": "win", "to": "win2"},
                                                 {"path": "win/a.msi", "to": "a-copy.msi"}]},
                       role="operator")
        self.assertEqual(done["copied"], ["win2", "a-copy.msi"])
        self.assertEqual((paths.STORE_DIR / "win2/sub/b.cfg").read_bytes(), b"config")
        self.assertNotEqual((paths.STORE_DIR / "a-copy.msi").stat().st_ino,
                            (paths.STORE_DIR / "win/a.msi").stat().st_ino)
        copy = self.listed()["a-copy.msi"]
        self.assertEqual((copy["origin"], copy["source"], copy["updated_by"], copy["sha256"]),
                         ("copy", "win/a.msi", "anna", hashlib.sha256(b"installer").hexdigest()))
        self.assertEqual(self.listed()["win2/sub/b.cfg"]["source"], "win/sub/b.cfg")
        self.assertEqual(list(paths.STORE_UPLOADS.iterdir()), [])
        self.assertEqual(self.code("store.copy", {"copies": [{"path": "win/a.msi", "to": "x"}]},
                                   role="viewer"), "forbidden")

    def test_a_copy_refuses_what_is_there_and_a_folder_into_itself(self):
        for copies, code in (([{"path": "win/a.msi", "to": "win/sub/b.cfg"}], "conflict"),
                             ([{"path": "win", "to": "win/inner"}], "bad_request"),
                             ([{"path": "nothing", "to": "x"}], "not_found"),
                             ([], "bad_request")):
            self.assertEqual(self.code("store.copy", {"copies": copies}, role="operator"), code)

    def test_a_copy_that_fails_leaves_nothing(self):
        real = copier.copy_fd
        calls = []

        def copy_fd(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(*args, **kwargs)

        with mock.patch.object(copier, "copy_fd", copy_fd):
            answer = self.call("store.copy", {"copies": [{"path": "win", "to": "win2"}]},
                               "operator")
        self.assertFalse(answer["ok"], answer)
        self.assertFalse((paths.STORE_DIR / "win2").exists())
        self.assertEqual(set(self.listed()), {"win/a.msi", "win/sub/b.cfg"})
        self.assertEqual(list(paths.STORE_UPLOADS.iterdir()), [])

    def test_a_large_copy_is_a_job_that_gets_hard_links(self):
        with mock.patch.object(copier, "COPY_AT_ONCE", 4):
            queued = self.ok("store.copy", {"copies": [{"path": "win", "to": "copies/win"},
                                                       {"path": "win/a.msi", "to": "a.msi"}]},
                             role="operator")
        run_id = queued["run_id"]
        directory = paths.RUNS_DIR / run_id
        self.assertIn(run_id, self.started)
        self.assertEqual((directory / copier.IN / "0/sub/b.cfg").stat().st_ino,
                         (paths.STORE_DIR / "win/sub/b.cfg").stat().st_ino)
        self.assertEqual([lane[1] for lane in jobs.lanes(self.config)][1],
                         "kind IN ('download', 'copy')")
        listed = self.ok("store.downloads", role="operator")["downloads"][0]
        self.assertEqual((listed["kind"], listed["path"], listed["source"], listed["count"]),
                         ("copy", "copies/win and 1 more", "win", 2))

        # The job, as fleet-run runs it.
        said = []
        spec = {"kind": "copy", "items": [{"path": "win"}, {"path": "win/a.msi"}],
                "total": len(b"installer") * 2 + len(b"config")}
        result = copier.copy(spec, directory, said.append)
        self.assertEqual(result["files"], 3)
        self.finish(run_id, **result)
        self.ok("run.reconcile")
        self.assertEqual((paths.STORE_DIR / "copies/win/sub/b.cfg").read_bytes(), b"config")
        self.assertEqual((paths.STORE_DIR / "a.msi").read_bytes(), b"installer")
        file = self.listed()["copies/win/sub/b.cfg"]
        self.assertEqual((file["origin"], file["source"]), ("copy", "win/sub/b.cfg"))
        self.assertFalse((directory / "copy").exists())
        done = self.ok("store.downloads", role="operator")["downloads"][0]
        self.assertEqual((done["status"], done["size"]), ("successful", spec["total"]))

    def test_a_copy_job_stops_when_canceled(self):
        with mock.patch.object(copier, "COPY_AT_ONCE", 4):
            run_id = self.ok("store.copy", {"copies": [{"path": "win", "to": "w"}]},
                             role="operator")["run_id"]
        directory = paths.RUNS_DIR / run_id
        with self.assertRaisesRegex(ValueError, "canceled"):
            copier.copy({"items": [{"path": "win"}], "total": 15}, directory, print,
                        lambda: True)


class ArchiveTest(StoreBase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.put("win/a.msi", b"installer")["ok"])
        self.assertTrue(self.put("win/sub/b.cfg", b"config")["ok"])
        self.assertTrue(self.put("readme.txt", b"read me")["ok"])
        (paths.STORE_DIR / "win/empty").mkdir()

    def archive(self, paths_, role="viewer"):
        import contextlib
        import io
        import json
        import sys
        import tarfile
        ctx = self.ctx(role)
        answer = cli.dispatch("store.archive", json.dumps({"paths": paths_}).encode(), ctx)
        out = io.BytesIO()
        wrapper = io.TextIOWrapper(out)
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", wrapper), contextlib.redirect_stderr(err):
            status = cli._stream_out(answer, ctx)
        wrapper.detach()
        if status:
            return json.loads(err.getvalue())["error"]["code"]
        with tarfile.open(fileobj=io.BytesIO(out.getvalue()), mode="r:") as tar:
            return {member.name: (tar.extractfile(member).read() if member.isfile() else None)
                    for member in tar.getmembers()}

    def test_a_folder_and_a_file_are_one_tar_named_from_their_own_name(self):
        got = self.archive(["win", "readme.txt", "win/a.msi"])
        self.assertEqual(got, {"win": None, "win/a.msi": b"installer", "win/empty": None,
                               "win/sub": None, "win/sub/b.cfg": b"config",
                               "readme.txt": b"read me"})
        audit = self.ok("audit.list")["entries"][0]
        self.assertEqual((audit["command"], audit["user"]), ("store.archive", "anna"))

    def test_what_cannot_be_archived_fails_before_the_first_byte(self):
        self.assertTrue(self.put("other/a.msi", b"x")["ok"])
        self.assertEqual(self.archive(["win/a.msi", "other/a.msi"]), "bad_request")
        self.assertEqual(self.archive(["nothing"]), "not_found")
        self.assertEqual(self.archive(["../fleet.db"]), "bad_request")
        self.assertEqual(self.archive([]), "bad_request")
