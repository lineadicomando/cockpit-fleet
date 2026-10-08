import os
import tempfile
import unittest
from pathlib import Path

from cockpit_fleet.errors import BadRequest
from cockpit_fleet.validators import (
    check_int,
    check_limit,
    check_relative_path,
    check_tag,
    check_user,
    resolve_inside,
)


class PatternTest(unittest.TestCase):
    def test_limit_accepts_ansible_host_patterns(self):
        for value in ("web01", "web*", "all:!db", "web:&staging", "node[1:3]", "~web[0-9]", "a,b"):
            self.assertEqual(check_limit(value), value)

    def test_limit_rejects_options_and_shell_characters(self):
        for value in ("-e@/etc/shadow", "", "a b", "a;b", "$(id)", "a\nb", "x" * 513, None, 3):
            with self.assertRaises(BadRequest, msg=repr(value)):
                check_limit(value)

    def test_user_accepts_local_and_domain_accounts(self):
        for value in ("maint", "SCHOOL\\administrator", "user@example.com", "svc_ansible"):
            self.assertEqual(check_user(value), value)

    def test_user_rejects_options_and_separators(self):
        for value in ("-oProxyCommand=x", "a b", "a/b", "", "a:b", None):
            with self.assertRaises(BadRequest, msg=repr(value)):
                check_user(value)

    def test_tag(self):
        self.assertEqual(check_tag("deploy:web"), "deploy:web")
        for value in ("a,b", "-t", "", "a b"):
            with self.assertRaises(BadRequest, msg=repr(value)):
                check_tag(value)

    def test_int_rejects_booleans_and_out_of_range(self):
        self.assertEqual(check_int(5, "forks", 1, 10), 5)
        for value in (True, 0, 11, "5", 5.0, None):
            with self.assertRaises(BadRequest, msg=repr(value)):
                check_int(value, "forks", 1, 10)


class PathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "project"
        (self.base / "sub").mkdir(parents=True)
        (self.base / "site.yml").write_text("")
        (self.base / "sub" / "inv").write_text("")
        (Path(self.tmp.name) / "outside").write_text("secret")

    def tearDown(self):
        self.tmp.cleanup()

    def test_relative_path_rejects_escapes(self):
        for value in ("/etc/passwd", "../x", "a/../../x", "-v", "", "a\x00b", None, "x" * 1025):
            with self.assertRaises(BadRequest, msg=repr(value)):
                check_relative_path(value)

    def test_resolve_inside_returns_existing_files(self):
        self.assertEqual(resolve_inside(self.base, "site.yml"), (self.base / "site.yml").resolve())
        self.assertEqual(resolve_inside(self.base, "sub/inv"), (self.base / "sub" / "inv").resolve())

    def test_resolve_inside_rejects_missing_files_and_directories(self):
        for value in ("nope.yml", "sub"):
            with self.assertRaises(BadRequest, msg=value):
                resolve_inside(self.base, value)

    def test_resolve_inside_rejects_a_symlink_leading_outside(self):
        os.symlink(Path(self.tmp.name) / "outside", self.base / "link")
        with self.assertRaisesRegex(BadRequest, "leaves its directory"):
            resolve_inside(self.base, "link")

    def test_resolve_inside_accepts_a_symlink_staying_inside(self):
        os.symlink(self.base / "site.yml", self.base / "alias.yml")
        self.assertEqual(resolve_inside(self.base, "alias.yml"), (self.base / "site.yml").resolve())


if __name__ == "__main__":
    unittest.main()
