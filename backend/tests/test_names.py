import unittest

from cockpit_fleet.names import check_id


class CheckIdTest(unittest.TestCase):
    def test_accepts_plain_identifiers(self):
        for value in ("a", "run-01", "db_password", "0abc", "a" * 64):
            self.assertEqual(check_id(value), value)

    def test_rejects_anything_usable_as_path_or_option(self):
        for value in ("", "-rf", "_x", "../etc", "a/b", "a.b", "A", "a b", "a\n", "a" * 65, "é"):
            with self.assertRaises(ValueError, msg=repr(value)):
                check_id(value)

    def test_rejects_non_strings(self):
        for value in (None, 1, ["a"], b"a"):
            with self.assertRaises(ValueError):
                check_id(value)

    def test_error_names_what_was_checked(self):
        with self.assertRaisesRegex(ValueError, "invalid run id"):
            check_id("../x", "run id")


if __name__ == "__main__":
    unittest.main()
