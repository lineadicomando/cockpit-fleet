import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cockpit_fleet import db
from cockpit_fleet.errors import Unavailable


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "fleet.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_database_is_migrated_to_the_latest_schema(self):
        conn = db.connect(self.path)
        self.assertEqual(db.schema_version(conn), len(db.MIGRATIONS))
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.execute("SELECT id, ts, user, command, outcome FROM audit")

    def test_reopening_does_not_migrate_again(self):
        db.connect(self.path).close()
        conn = db.connect(self.path)
        self.assertEqual(db.schema_version(conn), len(db.MIGRATIONS))

    def test_later_migrations_apply_on_top_of_existing_data(self):
        conn = db.connect(self.path)
        conn.execute("INSERT INTO audit (ts, uid, user, command, params, outcome)"
                     " VALUES (1, 1, 'u', 'c', '{}', 'ok')")
        conn.close()
        extra = [*db.MIGRATIONS, ["CREATE TABLE later (x INTEGER)"]]
        with mock.patch.object(db, "MIGRATIONS", extra):
            conn = db.connect(self.path)
            self.assertEqual(db.schema_version(conn), len(extra))
            conn.execute("SELECT x FROM later")
            self.assertEqual(conn.execute("SELECT count(*) FROM audit").fetchone()[0], 1)

    def test_failed_migration_leaves_the_previous_version(self):
        db.connect(self.path).close()
        broken = [*db.MIGRATIONS, ["CREATE TABLE ok_table (x INTEGER)", "THIS IS NOT SQL"]]
        with mock.patch.object(db, "MIGRATIONS", broken), self.assertRaises(Unavailable):
            db.connect(self.path)
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master")]
        self.assertNotIn("ok_table", tables)

    def test_database_from_a_newer_version_is_refused(self):
        conn = sqlite3.connect(self.path)
        conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
        conn.close()
        with self.assertRaisesRegex(Unavailable, "newer version"):
            db.connect(self.path)

    def test_unusable_path_is_reported_not_raised_raw(self):
        with self.assertRaises(Unavailable):
            db.connect(Path(self.tmp.name) / "missing-dir" / "fleet.db")

    def test_outcome_is_constrained(self):
        conn = db.connect(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO audit (ts, uid, user, command, params, outcome)"
                         " VALUES (1, 1, 'u', 'c', '{}', 'maybe')")


if __name__ == "__main__":
    unittest.main()
