"""The overview: what the first page shows, summed from runs, states, their
changes and what the hosts are made of."""

from test_checks import CheckBase, disk

from cockpit_fleet import overview, store  # noqa: F401

DAY = 86400


class OverviewTest(CheckBase):
    def overview(self, **params):
        return self.ok("overview.get", params, role="viewer")

    def test_an_empty_fleet_has_a_window_of_empty_days(self):
        found = self.overview()
        self.assertEqual(len(found["runs"]["days"]), 14)
        self.assertEqual(found["hosts"]["total"], 0)
        self.assertEqual(found["problems"], {"open": 0, "unacknowledged": 0, "top": []})
        self.assertIsNone(found["runs"]["success_rate"])
        self.assertIsNone(found["alerts"]["time_to_recover"])
        self.assertEqual(found["runs"]["queue"],
                         {"pending_approval": 0, "queued": 0, "running": 0})
        # The days begin at midnight where the browser is.
        for offset in (0, 120, -300):
            days = self.overview(days=7, tz_offset=offset)["runs"]["days"]
            self.assertEqual(len(days), 7)
            for day in days:
                self.assertEqual((day["start"] + offset * 60) % DAY, 0)
            self.assertLessEqual(days[-1]["start"], self.clock)
            self.assertGreater(days[-1]["start"] + DAY, self.clock)
        for bad in ({"days": 0}, {"days": 91}, {"tz_offset": 900}, {"days": "7"}):
            self.assertEqual(self.code("overview.get", bad, role="viewer"), "bad_request", bad)

    def test_runs_are_summed_by_day_outcome_template_and_host(self):
        template = self.template()
        self.run_ending(stats={"a": {"ok": 2}, "b": {"ok": 1}}, template=template)
        self.clock += DAY
        self.run_ending(rc=2, stats={"a": {"ok": 1, "failures": 1}, "b": {"ok": 1}},
                        template=template)
        failed = self.run_ending(rc=4, stats={"a": {"unreachable": 1}}, template=template)
        runs = self.overview()["runs"]
        self.assertEqual((runs["successful"], runs["failed"], runs["stopped"]), (1, 2, 0))
        self.assertAlmostEqual(runs["success_rate"], 1 / 3, places=3)
        self.assertEqual([(d["successful"], d["failed"]) for d in runs["days"][-2:]],
                         [(1, 0), (0, 2)])
        self.assertEqual(sum(d["successful"] + d["failed"] for d in runs["days"]), 3)
        self.assertEqual(runs["templates"], [{"id": template["id"], "name": "deploy",
                                              "runs": 3, "failed": 2}])
        self.assertEqual(runs["hosts"], [{"name": "a", "failed": 2}])
        self.assertEqual(runs["recent_failures"][0]["id"], failed["id"])
        # Out of the window, nothing.
        self.clock += 20 * DAY
        runs = self.overview()["runs"]
        self.assertEqual((runs["successful"], runs["failed"], runs["templates"]), (0, 0, []))
        self.assertEqual(self.overview(days=30)["runs"]["failed"], 2)

    def test_problems_recoveries_and_the_time_to_recover(self):
        inventory = self.inventory()
        self.check(inventory, group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(99)}, "web02": {"disk": disk(10)}})
        found = self.overview()
        self.assertEqual(found["hosts"]["total"], 3)
        self.assertEqual(found["hosts"]["reachable"], 2)
        self.assertEqual(found["hosts"]["systems"][0], {"name": "Debian 13", "hosts": 2})
        self.assertEqual((found["states"]["critical"], found["states"]["ok"]), (1, 1))
        self.assertEqual((found["problems"]["open"], found["problems"]["unacknowledged"]), (1, 1))
        [top] = found["problems"]["top"]
        self.assertEqual((top["host"], top["check"], top["state"], top["since"]),
                         ("web01", "disk", "critical", self.clock))
        self.assertEqual(found["alerts"]["days"][-1]["problems"], 1)
        began = self.clock
        # Recovered two hours later, as a hard change.
        self.clock += 2 * 3600
        run_id = self.tick(self.clock + 300)["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(10)}, "web02": {"disk": disk(10)}})
        alerts = self.overview()["alerts"]
        self.assertEqual(alerts["recovered"], 1)
        self.assertEqual(alerts["time_to_recover"], self.clock - began)
        self.assertEqual(sum(d["problems"] for d in alerts["days"]), 1)
        self.assertEqual(sum(d["recoveries"] for d in alerts["days"]), 1)
        self.assertEqual(self.overview()["problems"]["open"], 0)

    def test_a_host_that_cannot_be_reached_is_no_problem_of_its_checks(self):
        inventory = self.inventory()
        self.check(inventory, group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(10)}, "web02": {"disk": disk(10)}})
        # Off for the night: its disk is unknown, but no hard problem began.
        run_id = self.tick(self.clock + 3600)["queued"][0]
        self.answer(run_id, {"web01": "unreachable", "web02": {"disk": disk(10)}})
        self.assertEqual(self.states()[("web01", "disk")]["state"], "unknown")
        run_id = self.tick(self.clock + 8 * 3600)["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(10)}, "web02": {"disk": disk(10)}})
        alerts = self.overview()["alerts"]
        self.assertEqual(sum(d["problems"] for d in alerts["days"]), 0)
        self.assertEqual(sum(d["recoveries"] for d in alerts["days"]), 0)
        self.assertIsNone(alerts["time_to_recover"])

    def test_a_problem_begun_before_the_window_is_timed_from_its_start(self):
        inventory = self.inventory()
        self.check(inventory, group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(99)}, "web02": {"disk": disk(10)}})
        began = self.clock
        run_id = self.tick(self.clock + 10 * DAY)["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(10)}, "web02": {"disk": disk(10)}})
        alerts = self.overview(days=7)["alerts"]
        self.assertEqual(sum(d["problems"] for d in alerts["days"]), 0)
        self.assertEqual(sum(d["recoveries"] for d in alerts["days"]), 1)
        self.assertEqual(alerts["time_to_recover"], self.clock - began)

    def test_acknowledged_problems_go_after_the_others(self):
        inventory = self.inventory()
        check = self.check(inventory, group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(99)}, "web02": {"disk": disk(85)}})
        top = self.overview()["problems"]["top"]
        self.assertEqual([p["host"] for p in top], ["web01", "web02"])
        self.ok("state.ack", {"host_id": top[0]["host_id"], "check_id": check["id"]},
                role="operator")
        problems = self.overview()["problems"]
        self.assertEqual([p["host"] for p in problems["top"]], ["web02", "web01"])
        self.assertEqual((problems["open"], problems["unacknowledged"]), (2, 1))

    def test_what_is_due_and_what_the_hosts_lack(self):
        inventory = self.inventory()
        host = [h for h in self.ok("host.list")["hosts"] if h["name"] == "web01"][0]
        self.ok("maintenance.create", {"inventory_id": inventory["id"], "host_id": host["id"],
                                       "ends_at": int(self.clock + 3600)}, role="operator")
        conn = self.ctx().conn
        conn.execute("INSERT INTO cmdb_hosts (host_id, updates_at, boot_at) VALUES (?, ?, ?)",
                     (host["id"], self.clock, self.clock - 40 * DAY))
        conn.executemany("INSERT INTO cmdb_updates (host_id, name, security) VALUES (?, ?, ?)",
                         [(host["id"], "openssl", 1), (host["id"], "vim", 0)])
        conn.commit()
        found = self.overview()
        self.assertEqual(found["hosts"]["in_maintenance"], 1)
        self.assertEqual(found["hosts"]["uptime"], {"days": 30, "known": 1, "longer": 1})
        self.assertEqual(found["updates"], {"updates": 2, "security": 1, "hosts": 1,
                                            "security_hosts": 1, "known": 1})
        [window] = found["upcoming"]["maintenances"]
        self.assertEqual((window["host"], window["inventory"], window["active"]),
                         ("web01", "lab", True))
        self.assertEqual(found["compliance"], {"compliant": 0, "drifted": 0})
        self.assertEqual(found["controller"]["behind"], 0)
