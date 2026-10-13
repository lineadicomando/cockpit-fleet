"""Projects uploaded as an archive: what is unpacked, what is refused."""

import base64
import hashlib
import io
import os
import shutil
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import archive, cli, paths
from cockpit_fleet.config import Config
from cockpit_fleet.errors import BadRequest

PLAYBOOK = b"- hosts: all\n"


def tar(*members, mode="w:gz") -> bytes:
    """A tar of (name, content) pairs, or of ready TarInfo objects."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as out:
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                out.addfile(member)
                continue
            name, content = member
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                out.addfile(info)
            else:
                info.size = len(content)
                out.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def special(name, kind, target="", mode=0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.linkname, info.mode = kind, target, mode
    return info


def zipped(*members) -> bytes:
    """A zip of (name, content) pairs, or of (ZipInfo, content) ones."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name, content in members:
            out.writestr(name, content)
    return buffer.getvalue()


def unix(name, mode) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system, info.external_attr = 3, mode << 16
    return info


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "checkout"
        # Something a hostile archive would like to reach.
        (self.root / "outside").write_text("untouched")

    def extract(self, data: bytes) -> dict:
        (self.root / "archive").write_bytes(data)
        return archive.extract(self.root / "archive", self.tree)

    def refused(self, data: bytes, text: str):
        with self.assertRaises(BadRequest) as caught:
            self.extract(data)
        self.assertIn(text, str(caught.exception))
        # All or nothing: no tree, no leftover, nothing written elsewhere.
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["archive", "outside"])
        self.assertEqual((self.root / "outside").read_text(), "untouched")

    def test_tar_in_every_compression_and_zip(self):
        members = (("site.yml", PLAYBOOK), ("roles/web/tasks/main.yml", b"- debug:\n"),
                   ("empty", None))
        for mode in ("w", "w:gz", "w:bz2", "w:xz"):
            with self.subTest(mode=mode):
                done = self.extract(tar(*members, mode=mode))
                self.assertEqual((done["files"], done["bytes"]), (2, len(PLAYBOOK) + 9))
                self.assertEqual((self.tree / "site.yml").read_bytes(), PLAYBOOK)
                self.assertTrue((self.tree / "empty").is_dir())
                shutil.rmtree(self.tree)
        done = self.extract(zipped(("site.yml", PLAYBOOK), ("roles/web/tasks/main.yml", b"x"),
                                   ("empty/", b"")))
        self.assertEqual(done["files"], 2)
        self.assertEqual((self.tree / "roles/web/tasks/main.yml").read_bytes(), b"x")
        self.assertTrue((self.tree / "empty").is_dir())

    def test_modes_are_reduced_to_private_and_executable_or_not(self):
        script = special("run.sh", tarfile.REGTYPE, mode=0o6755)
        self.extract(tar(script, ("site.yml", PLAYBOOK),
                         special("data", tarfile.DIRTYPE, mode=0o777)))
        self.assertEqual(stat.S_IMODE(os.lstat(self.tree / "run.sh").st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.lstat(self.tree / "site.yml").st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.lstat(self.tree / "data").st_mode), 0o700)

    def test_a_single_top_directory_is_not_kept(self):
        done = self.extract(tar(("proj-1.0/site.yml", PLAYBOOK), ("proj-1.0/roles/a.yml", b"a"),
                                special("proj-1.0/latest", tarfile.SYMTYPE, "roles/a.yml")))
        self.assertEqual(done["stripped"], "proj-1.0")
        self.assertEqual(sorted(p.name for p in self.tree.iterdir()),
                         ["latest", "roles", "site.yml"])
        self.assertEqual((self.tree / "latest").read_bytes(), b"a")
        shutil.rmtree(self.tree)
        # A link out of that directory would leave the project.
        self.refused(tar(("proj/site.yml", PLAYBOOK), special("proj/up", tarfile.SYMTYPE, "..")),
                     "points outside")

    def test_git_and_the_root_itself_are_left_out(self):
        done = self.extract(tar((".", None), ("./site.yml", PLAYBOOK), ("./.git/config", b"x"),
                                ("./.GIT/hooks/pre-commit", b"x"), ("./sub/.git", b"gitdir: x")))
        self.assertEqual((done["files"], done["skipped"]), (1, 4))
        self.assertEqual([p.name for p in self.tree.iterdir()], ["site.yml"])

    def test_paths_that_leave_the_tree_are_refused(self):
        for name in ("../outside", "a/../../outside", "/etc/passwd", "a\\b", "a/\x1b[2J"):
            with self.subTest(name=name):
                self.refused(tar(("site.yml", PLAYBOOK), (name, b"evil")), "the archive")
        self.refused(zipped(("site.yml", PLAYBOOK), ("../outside", b"evil")), "leaves it")
        self.refused(tar(("/".join("d" * 1 for _ in range(40)), b"x")), "too deep")

    def test_links_stay_inside_or_are_refused(self):
        self.extract(tar(("roles/common/x.yml", b"x"),
                         special("roles/web", tarfile.SYMTYPE, "common"),
                         special("deep/link", tarfile.SYMTYPE, "../roles/common/x.yml"),
                         special("dangling", tarfile.SYMTYPE, "not/there")))
        self.assertEqual(os.readlink(self.tree / "roles/web"), "common")
        self.assertEqual((self.tree / "deep/link").read_bytes(), b"x")
        shutil.rmtree(self.tree)
        for target in ("/etc/passwd", "../outside", "a/../../outside", ""):
            with self.subTest(target=target):
                self.refused(tar(("site.yml", PLAYBOOK),
                                 special("link", tarfile.SYMTYPE, target)), "points outside")
        # Each target stays inside when read alone; followed, the second leaves.
        self.refused(tar(("x/file", b"x"), ("y/file", b"y"),
                         special("x/up", tarfile.SYMTYPE, ".."),
                         special("x/out", tarfile.SYMTYPE, "up/..")), "points outside")

    def test_nothing_is_written_through_a_link(self):
        # The link comes first, then a file "under" it.
        self.refused(tar(special("etc", tarfile.SYMTYPE, "."), ("etc/passwd", b"evil"),
                         ("site.yml", PLAYBOOK)), "twice")
        self.refused(tar(("a", b"file"), ("a/b", b"x")), "both a file and a directory")
        self.refused(tar(("a", b"1"), ("a", b"2")), "twice")
        link = unix("link", stat.S_IFLNK | 0o777)
        self.refused(zipped(("site.yml", PLAYBOOK), (link, "../outside")), "points outside")
        self.extract(zipped(("site.yml", PLAYBOOK), (unix("link", stat.S_IFLNK | 0o777),
                                                     "site.yml")))
        self.assertEqual(os.readlink(self.tree / "link"), "site.yml")

    def test_hard_links_and_special_files_are_refused(self):
        for kind, what in ((tarfile.LNKTYPE, "hard link"), (tarfile.CHRTYPE, "device"),
                           (tarfile.BLKTYPE, "device"), (tarfile.FIFOTYPE, "special file")):
            with self.subTest(kind=kind):
                self.refused(tar(("site.yml", PLAYBOOK), special("x", kind, "site.yml")), what)
        self.refused(zipped(("site.yml", PLAYBOOK), (unix("pipe", stat.S_IFIFO | 0o644), b"")),
                     "special file")

    def test_limits_on_size_and_number(self):
        with mock.patch.object(archive, "MAX_BYTES", 1024 * 1024):
            # 4 MB of zeros in a few kB: the size is counted as it comes out.
            self.refused(tar(("zeros", bytes(4 * 1024 * 1024))), "unpacks to more than")
            self.refused(zipped(("zeros", bytes(4 * 1024 * 1024))), "unpacks to more than")
            self.refused(tar(*((f"f{i}", bytes(300 * 1024)) for i in range(4))), "unpacks to more")
        with mock.patch.object(archive, "MAX_FILES", 3):
            self.refused(tar(*((f"f{i}", b"x") for i in range(4))), "more than 3 files")
        with mock.patch.object(archive, "MAX_MEMBERS", 3):
            self.refused(tar(*((f".git/{i}", b"x") for i in range(4))), "more than 3 members")

    def test_what_is_not_an_archive_is_refused(self):
        whole = tar(("site.yml", PLAYBOOK), ("big", os.urandom(64 * 1024)))
        for data, text in ((b"just some text\n" * 100, "not a tar or zip"),
                           (whole[:len(whole) // 2], "damaged"),
                           (b"PK\x03\x04 and then nothing of a zip", "cannot be read"),
                           (tar(("empty", None)), "holds no files"),
                           (tar((".git/config", b"x")), "holds no files")):
            with self.subTest(text=text, size=len(data)):
                self.refused(data, text)

    def test_an_encrypted_zip_is_refused(self):
        data = bytearray(zipped(("site.yml", PLAYBOOK)))
        # Flag bit 0 in the central directory entry is what says "encrypted".
        central = data.rindex(b"PK\x01\x02")
        data[central + 8] |= 1
        self.refused(bytes(data), "encrypted")

    def test_an_existing_destination_is_not_touched(self):
        self.tree.mkdir()
        (self.tree / "kept").write_text("kept")
        with self.assertRaises(OSError):
            self.extract(tar(("site.yml", PLAYBOOK)))
        self.assertEqual((self.tree / "kept").read_text(), "kept")


class UploadTest(Base):
    def setUp(self):
        super().setUp()
        self.project = self.ok("project.create", {"name": "shipped", "kind": "archive"})
        self.id = self.project["id"]

    def upload(self, data: bytes, **extra):
        return self.ok("project.upload", {"id": self.id, "filename": "site.tar.gz",
                                          "data": base64.b64encode(data).decode(), **extra})

    def unpack(self, run_id):
        """What fleet-run does for an unpack job, without the unit."""
        directory = paths.RUNS_DIR / run_id
        done = archive.extract(directory / "archive", directory / "checkout")
        self.finish(run_id, 0, **done)

    def project_now(self):
        return self.ok("project.list")["projects"][0]

    def test_a_new_project_has_no_files_until_an_archive_arrives(self):
        self.assertEqual((self.project["kind"], self.project["revision"]), ("archive", ""))
        self.assertEqual(self.code("file.list", {"id": self.id}), "conflict")

    def test_the_archive_becomes_the_tree_and_its_digest_the_revision(self):
        data = tar(("site.yml", PLAYBOOK), ("group_vars/all.yml", b"a: 1\n"))
        queued = self.upload(data)
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual((queued["sha256"], queued["size"]), (digest, len(data)))
        directory = paths.RUNS_DIR / queued["run_id"]
        self.assertEqual((directory / "archive").read_bytes(), data)
        self.assertEqual(self.started, [queued["run_id"]])

        self.unpack(queued["run_id"])
        project = self.project_now()
        self.assertEqual((project["revision"], project["sync_error"]), (digest, ""))
        tree = paths.PROJECTS_DIR / self.id / "tree"
        self.assertEqual((tree / "site.yml").read_bytes(), PLAYBOOK)
        run = self.ok("run.get", {"id": queued["run_id"]})
        self.assertEqual((run["kind"], run["status"], run["revision"]),
                         ("unpack", "successful", digest))
        self.assertEqual(run["options"]["filename"], "site.tar.gz")
        # Neither the archive nor the staging area outlives the job.
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["result.json", "spec.json"])
        listed = self.ok("file.list", {"id": self.id})
        self.assertEqual(([f["path"] for f in listed["files"]], listed["editable"]),
                         (["site.yml", "group_vars/all.yml"], False))
        self.assertEqual(self.code("file.write", {"id": self.id, "path": "x.yml", "content": "",
                                                  "expected": ""}), "conflict")

    def test_a_second_archive_replaces_the_first_as_a_whole(self):
        self.unpack(self.upload(tar(("old.yml", b"old"), ("site.yml", PLAYBOOK)))["run_id"])
        second = tar(("new.yml", b"new"))
        self.unpack(self.upload(second)["run_id"])
        self.assertEqual(self.project_now()["revision"], hashlib.sha256(second).hexdigest())
        tree = paths.PROJECTS_DIR / self.id / "tree"
        self.assertEqual([p.name for p in tree.iterdir()], ["new.yml"])

    def test_a_refused_archive_leaves_the_project_as_it_was(self):
        first = tar(("site.yml", PLAYBOOK))
        self.unpack(self.upload(first)["run_id"])
        queued = self.upload(tar(("../x", b"evil")))
        directory = paths.RUNS_DIR / queued["run_id"]
        with self.assertRaises(BadRequest) as caught:
            archive.extract(directory / "archive", directory / "checkout")
        (directory / "stdout.log").write_text(f"fleet-run: {caught.exception}\n")
        self.finish(queued["run_id"], 2, error=str(caught.exception))
        project = self.project_now()
        self.assertEqual(project["revision"], hashlib.sha256(first).hexdigest())
        self.assertIn("leaves it", project["sync_error"])
        self.assertEqual(self.ok("run.get", {"id": queued["run_id"]})["status"], "failed")
        self.assertTrue((paths.PROJECTS_DIR / self.id / "tree" / "site.yml").is_file())

    def test_a_job_cannot_make_the_tree_a_link(self):
        queued = self.upload(tar(("site.yml", PLAYBOOK)))
        os.symlink("/etc", paths.RUNS_DIR / queued["run_id"] / "checkout")
        self.finish(queued["run_id"], 0)
        self.assertEqual(self.ok("run.get", {"id": queued["run_id"]})["status"], "error")
        self.assertFalse(os.path.lexists(paths.PROJECTS_DIR / self.id / "tree"))

    def test_what_is_uploaded_is_checked_and_never_recorded(self):
        data = base64.b64encode(tar(("site.yml", PLAYBOOK))).decode()
        for params in ({"id": self.id, "data": "not base64!"}, {"id": self.id, "data": ""},
                       {"id": self.id}, {"id": self.id, "data": data, "extra": 1}):
            self.assertEqual(self.code("project.upload", params), "bad_request")
        self.assertEqual(self.code("project.upload", {"id": self.id, "data": data},
                                   role="operator"), "forbidden")
        other = self.local_project()
        self.assertEqual(self.code("project.upload", {"id": other["id"], "data": data}),
                         "bad_request")
        self.config = Config(max_upload_mb=1)
        big = base64.b64encode(os.urandom(1024 * 1024 + 1)).decode()
        self.assertEqual(self.code("project.upload", {"id": self.id, "data": big}), "bad_request")
        self.assertEqual(list(paths.RUNS_DIR.iterdir()), [])

        self.ok("project.upload", {"id": self.id, "data": data, "filename": "C:\\tmp\\a\x07.tgz"})
        entries = self.ok("audit.list")["entries"]
        self.assertTrue(all(data not in entry["params"] for entry in entries))
        self.assertIn('"data": "***"', entries[0]["params"])
        run = self.ok("run.list")["runs"][0]
        self.assertEqual(run["options"]["filename"], "a.tgz")

    def test_only_this_command_takes_a_large_input(self):
        large = b'{"x": "' + b"a" * (cli.MAX_INPUT) + b'"}'
        self.assertEqual(cli.dispatch("project.list", large, self.ctx())["error"]["code"],
                         "bad_request")
        # Read and parsed: what is refused is the content, not the size.
        answer = cli.dispatch("project.upload", large, self.ctx())
        self.assertEqual(answer["error"]["message"], "id is required")
        limit = cli.COMMANDS["project.upload"].max_input
        self.assertIn("too large", cli.dispatch("project.upload", b" " * (limit + 1),
                                                self.ctx())["error"]["message"])
        # The other one takes a whole exported configuration.
        self.assertLessEqual({name for name, spec in cli.COMMANDS.items()
                              if spec.max_input != cli.MAX_INPUT},
                             {"project.upload", "config.import"})


if __name__ == "__main__":
    unittest.main()
