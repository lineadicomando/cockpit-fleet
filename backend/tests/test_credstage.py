"""fleet-unit's copy of a run's ciphertext: only regular files of the
service account, reached without following a link."""

import os
import tempfile
import unittest
from pathlib import Path

from cockpit_fleet import credstage


class StageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "lib" / "cockpit-fleet"
        self.creds = self.state / "runs" / "r1" / "creds"
        self.creds.mkdir(parents=True)
        self.staging = self.root / "staging"
        self.uid = os.getuid()

    def stage(self):
        return credstage.stage(self.state, "r1", self.staging, self.uid)

    def test_regular_files_are_copied_where_only_the_owner_reads(self):
        (self.creds / "ssh_key").write_bytes(b"cipher")
        target = self.stage()
        self.assertEqual(target, self.staging / "r1")
        self.assertEqual((target / "ssh_key").read_bytes(), b"cipher")
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(target / "ssh_key").st_mode & 0o777, 0o600)
        credstage.discard(self.staging, "r1")
        self.assertFalse(target.exists())

    def test_no_creds_directory_gives_an_empty_one(self):
        self.creds.rmdir()
        self.assertEqual(list(self.stage().iterdir()), [])

    def test_a_link_anywhere_on_the_path_is_refused(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "other").write_bytes(b"root's")
        self.creds.rmdir()
        self.creds.symlink_to(elsewhere)
        with self.assertRaises(OSError):
            self.stage()
        self.creds.unlink()
        run = self.state / "runs" / "r1"
        run.rmdir()
        run.symlink_to(elsewhere)
        with self.assertRaises(OSError):
            self.stage()
        self.assertFalse((self.staging / "r1").exists())

    def test_a_link_a_fifo_a_hard_link_or_a_large_file_in_creds_is_refused(self):
        outside = self.root / "outside"
        outside.write_bytes(b"x")
        cases = {
            "link": lambda path: path.symlink_to(outside),
            "fifo": lambda path: os.mkfifo(path),
            "hard": lambda path: os.link(outside, path),
            "large": lambda path: path.write_bytes(b"x" * (credstage.MAX_SIZE + 1)),
            ".hidden": lambda path: path.write_bytes(b"x"),
        }
        for name, make in cases.items():
            with self.subTest(name):
                make(self.creds / name)
                with self.assertRaises((ValueError, OSError)):
                    self.stage()
                (self.creds / name).unlink()
                self.assertFalse((self.staging / "r1").exists())

    def test_a_directory_of_someone_else_is_refused(self):
        with self.assertRaises(ValueError):
            credstage.stage(self.state, "r1", self.staging, self.uid + 1)

    def test_a_staging_directory_others_can_reach_is_refused(self):
        self.staging.mkdir()
        os.chmod(self.staging, 0o755)  # noqa: S103
        with self.assertRaises(ValueError):
            self.stage()


if __name__ == "__main__":
    unittest.main()
