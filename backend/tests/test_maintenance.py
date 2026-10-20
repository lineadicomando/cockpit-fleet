"""Maintenance at each tick: the output of old runs deleted, directories with
no run swept, free pages of the database given back."""

import dataclasses
import os
import sqlite3

from test_checks import CheckBase

from cockpit_fleet import agents, db, maintenance, paths  # noqa: F401  (run.result)

DAY = maintenance.DAY


class RunsTest(CheckBase):
    def ended_run(self):
        run = self.run_ending(stats={"a": {"ok": 1}})
        (paths.RUNS_DIR / run["id"] / "stdout.log").write_text("PLAY [all]\n")
        return run

    def test_the_output_of_an_old_run_is_deleted_and_its_outcome_kept(self):
        run = self.ended_run()
        self.tick(self.clock + 89 * DAY)
        self.assertTrue((paths.RUNS_DIR / run["id"]).exists())
        self.assertIsNone(self.ok("run.get", {"id": run["id"]})["pruned_at"])
        self.tick(self.clock + 2 * DAY)
        self.assertFalse((paths.RUNS_DIR / run["id"]).exists())
        kept = self.ok("run.get", {"id": run["id"]}, role="viewer")
        self.assertEqual((kept["status"], kept["pruned_at"]), ("successful", self.clock))
        self.assertEqual(kept["stats"], {"a": {"ok": 1}})
        self.assertEqual(self.ok("run.log", {"id": run["id"]}, role="operator")["text"], "")
        self.assertEqual(self.ok("run.result", {"id": run["id"]}, role="operator")["tasks"], [])

    def test_keep_days_set_to_zero_keeps_it_for_ever(self):
        self.config = dataclasses.replace(self.config, runs_keep_days=0)
        run = self.ended_run()
        self.tick(self.clock + 1000 * DAY)
        self.assertTrue((paths.RUNS_DIR / run["id"] / "stdout.log").exists())

    def test_a_run_still_going_is_left_alone(self):
        template = self.template()
        run = self.ok("run.start", {"template_id": template["id"]}, role="operator")
        self.tick(self.clock + 100 * DAY)
        self.assertTrue((paths.RUNS_DIR / run["id"]).exists())

    def test_directories_with_no_run_are_swept_once_a_day_old_and_hourly(self):
        old, new = paths.RUNS_DIR / "r-gone", paths.RUNS_DIR / "r-being-made"
        for directory, age in ((old, 2 * DAY), (new, 60)):
            directory.mkdir()
            os.utime(directory, (self.clock - age, self.clock - age))
        run = self.ended_run()
        os.utime(paths.RUNS_DIR / run["id"], (self.clock - 2 * DAY, self.clock - 2 * DAY))
        self.assertEqual(self.ok("scheduler.tick")["maintenance"]["orphans"], 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue((paths.RUNS_DIR / run["id"]).exists())
        os.utime(new, (self.clock - 2 * DAY, self.clock - 2 * DAY))
        self.clock += 600
        self.assertEqual(self.ok("scheduler.tick")["maintenance"]["orphans"], 0)
        self.clock += 3600
        self.assertEqual(self.ok("scheduler.tick")["maintenance"]["orphans"], 1)
        self.assertFalse(new.exists())


class VacuumTest(CheckBase):
    def database(self):
        return sqlite3.connect(self.root / "fleet.db")

    def test_a_new_database_gives_back_free_pages_a_few_at_a_time(self):
        self.ok("run.list")
        with self.database() as conn:
            self.assertEqual(conn.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
            conn.execute("CREATE TABLE filler (x BLOB)")
            conn.executemany("INSERT INTO filler VALUES (zeroblob(3000))", [()] * 2000)
        with self.database() as conn:
            conn.execute("DELETE FROM filler")
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        self.assertGreater(free, 1000)
        answer = self.ok("scheduler.tick")["maintenance"]["vacuum"]
        self.assertEqual(answer, {"incremental": True, "freed": free})
        with self.database() as conn:
            self.assertEqual(conn.execute("PRAGMA freelist_count").fetchone()[0], 0)
        self.assertEqual(self.ok("scheduler.tick")["maintenance"]["vacuum"]["freed"], 0)

    def test_a_small_database_made_before_is_converted(self):
        self.ok("run.list")
        conn = self.database()
        conn.execute("PRAGMA auto_vacuum = NONE")
        conn.execute("VACUUM")
        self.assertEqual(conn.execute("PRAGMA auto_vacuum").fetchone()[0], 0)
        conn.close()
        self.assertTrue(self.ok("scheduler.tick")["maintenance"]["vacuum"]["converted"])
        with self.database() as conn:
            self.assertEqual(conn.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                             len(db.MIGRATIONS))
