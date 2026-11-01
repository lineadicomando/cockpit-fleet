"""Time series: the values of the checks kept as they came and as hourly and
daily aggregates, read back by span, and deleted past their retention."""

import dataclasses
import sqlite3
from contextlib import closing

from test_checks import CheckBase, disk

from cockpit_fleet import metrics

HOUR, DAY = metrics.HOUR, metrics.DAY


class MetricsBase(CheckBase):
    def setUp(self):
        super().setUp()
        # An even hour, so that the buckets of the test are easy to tell.
        self.clock = float(int(self.clock) - int(self.clock) % DAY + 10 * HOUR)
        self.lab = self.inventory()
        self.disk = self.check(self.lab, group="web", interval=600, max_attempts=1)

    def cycle(self, used: dict):
        """A tick at the next due time, its cycle answered with these disk uses."""
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {host: {"disk": disk(value)} for host, value in used.items()})

    def host(self, name):
        return next(host for host in self.ok("host.list")["hosts"] if host["name"] == name)

    def query(self, host, role="viewer", **params):
        return self.ok("metric.query", {"host_id": self.host(host)["id"], **params}, role=role)

    def count(self, table):
        with closing(sqlite3.connect(self.root / "fleet.db")) as conn:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


class RecordTest(MetricsBase):
    def test_each_metric_and_instance_is_a_series_of_values(self):
        for step, used in enumerate((10, 20, 30)):
            self.clock += step and 600
            self.cycle({"web01": used, "web02": 50})
        answer = self.query("web01", name="used_pct", since=int(self.clock) - HOUR)
        self.assertEqual(answer["resolution"], "raw")
        [series] = answer["series"]
        self.assertEqual((series["check"], series["name"], series["instance"], series["unit"]),
                         ("disk", "used_pct", "/", "%"))
        self.assertEqual([value for _, value in series["points"]], [10, 20, 30])
        self.assertEqual({entry["name"] for entry in self.query("web01")["series"]},
                         {"used_pct", "free"})

    def test_aggregates_are_kept_up_to_date_as_values_arrive(self):
        start = int(self.clock)
        # 10, 40, 20 and 50 at 10:00, 10:15, 10:30 and 10:45.
        for used in (10, 40, 20, 50):
            self.cycle({"web01": used})
            self.clock += 900
        hourly = self.query("web01", name="used_pct", resolution="hour", since=start - DAY)
        self.assertEqual(hourly["series"][0]["points"], [[start, 30.0, 10, 50]])
        daily = self.query("web01", name="used_pct", resolution="day", since=start - 7 * DAY)
        self.assertEqual(daily["series"][0]["points"], [[start - 10 * HOUR, 30.0, 10, 50]])

    def test_the_resolution_follows_the_span(self):
        self.cycle({"web01": 10})
        now = int(self.clock)
        self.assertEqual(self.query("web01", since=now - DAY)["resolution"], "raw")
        self.assertEqual(self.query("web01", since=now - 10 * DAY)["resolution"], "hour")
        self.assertEqual(self.query("web01", since=now - 90 * DAY)["resolution"], "day")
        # Values as they came are gone after raw_days: older spans read the hours.
        self.assertEqual(self.query("web01", since=now - 20 * DAY, until=now - 19 * DAY)
                         ["resolution"], "hour")

    def test_a_time_a_node_gives_is_believed_only_near_the_cycle(self):
        self.assertEqual(metrics.when(1000.5, 990, 1010), 1000)
        self.assertEqual(metrics.when(5000, 990, 1010), 1010)
        self.assertEqual(metrics.when("x", 990, 1010), 1010)

    def test_a_second_value_at_the_same_second_is_left_out(self):
        self.cycle({"web01": 10})
        series = self.query("web01", name="used_pct")["series"][0]
        ctx = self.ctx()
        host_id = self.host("web01")["id"]
        ts = series["points"][0][0]
        kept = metrics.record(ctx, host_id, self.disk["id"],
                              [{"name": "used_pct", "value": 99, "instance": "/"}], ts)
        self.assertEqual(kept, 0)
        hourly = self.query("web01", name="used_pct", resolution="hour")["series"][0]
        self.assertEqual(hourly["points"][0][1:], [10.0, 10, 10])

    def test_unreachable_hosts_and_failures_record_nothing(self):
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": "unreachable", "web02": "permission denied"})
        self.assertEqual(self.count("metric_points"), 0)

    def test_a_viewer_reads_them_and_bad_spans_are_refused(self):
        self.cycle({"web01": 10})
        self.assertEqual(self.code("metric.query", {"host_id": self.host("web01")["id"],
                                                    "since": 10, "until": 5}, role="viewer"),
                         "bad_request")
        self.assertEqual(self.code("metric.query", {"host_id": "h-nothere"}, role="viewer"),
                         "not_found")

    def test_long_answers_are_cut(self):
        self.cycle({"web01": 10})
        ctx = self.ctx()
        host_id = self.host("web01")["id"]
        start = int(self.clock) + 1
        for second in range(metrics.MAX_POINTS + 5):
            metrics.record(ctx, host_id, self.disk["id"],
                           [{"name": "used_pct", "value": 1, "instance": "/"}], start + second)
        answer = self.query("web01", name="used_pct", resolution="raw",
                            until=start + metrics.MAX_POINTS + 10)
        self.assertEqual(len(answer["series"][0]["points"]), metrics.MAX_POINTS)
        self.assertTrue(answer["series"][0]["truncated"])


class RetentionTest(MetricsBase):
    def test_the_tick_deletes_each_table_past_its_retention(self):
        self.config = dataclasses.replace(self.config, metrics_raw_days=1, metrics_hourly_days=3,
                                          metrics_daily_days=10)
        self.cycle({"web01": 10, "web02": 20})
        self.assertEqual(self.count("metric_points"), 4)
        self.tick(self.clock + 2 * DAY)
        self.assertEqual(self.count("metric_points"), 0)
        self.assertEqual(self.count("metric_hourly"), 4)
        self.tick(self.clock + 2 * DAY)
        self.assertEqual(self.count("metric_hourly"), 0)
        self.assertEqual(self.count("metric_daily"), 4)
        self.tick(self.clock + 8 * DAY)
        self.assertEqual(self.count("metric_daily"), 0)
        self.assertEqual(self.count("metric_series"), 0)

    def test_deleting_a_check_takes_its_series(self):
        self.cycle({"web01": 10, "web02": 20})
        self.ok("check.delete", {"id": self.disk["id"]})
        for table in ("metric_series", "metric_points", "metric_hourly", "metric_daily"):
            self.assertEqual(self.count(table), 0, table)

    def test_retention_reaches_the_values_through_their_key(self):
        conn = self.ctx().conn
        plan = " ".join(row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN DELETE FROM metric_points WHERE series_id IN"
            " (SELECT id FROM metric_series) AND ts < 0"))
        self.assertIn("USING PRIMARY KEY (series_id=? AND ts<?)", plan)
