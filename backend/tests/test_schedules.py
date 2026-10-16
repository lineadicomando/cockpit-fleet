"""Schedules and the tick of the scheduler, with systemd-analyze replaced by a
clock that elapses every hour on the hour."""

import dataclasses
import fcntl
import os
import subprocess
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import paths, schedules, store
from cockpit_fleet.errors import BadRequest

HOUR = 3600.0
START = 1_800_000_000.0  # on the hour
REAL_ANALYZE = schedules.analyze


class ScheduleBase(Base):
    def setUp(self):
        super().setUp()
        self.clock = START + 10
        self.analyzed: list[tuple[str, str]] = []

        def analyze(calendar, timezone="", count=1):
            self.analyzed.append((calendar, timezone))
            if calendar == "bogus":
                raise BadRequest("not a valid OnCalendar expression: bogus")
            if calendar == "2020-01-01":
                return calendar + " 00:00:00", []
            first = (self.clock // HOUR + 1) * HOUR
            return calendar, [first + HOUR * index for index in range(count)]

        patches = [
            mock.patch.object(schedules, "analyze", analyze),
            mock.patch.object(store, "now", lambda: self.clock),
            mock.patch.object(paths, "SCHEDULER_LOCK", self.root / "scheduler.lock"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def schedule(self, template=None, **extra):
        if template is None:
            found = self.ok("template.list")["templates"]
            template = found[0] if found else self.template()
        return self.ok("schedule.create", {"name": extra.pop("name", "hourly"),
                                           "template_id": template["id"], "calendar": "hourly",
                                           **extra})

    def tick(self, at):
        self.clock = at
        return self.ok("scheduler.tick")["schedules"]

    def runs_of(self, schedule_id):
        return [run for run in self.ok("run.list")["runs"] if run["schedule_id"] == schedule_id]


class ScheduleTest(ScheduleBase):
    def test_created_by_an_administrator_with_its_next_time(self):
        schedule = self.schedule(limit="web*", answers={})
        self.assertEqual(schedule["next_at"], START + HOUR)
        self.assertEqual((schedule["template"], schedule["limit"], schedule["enabled"]),
                         ("deploy", "web*", True))
        self.assertEqual(self.ok("schedule.list", role="viewer")["schedules"][0]["id"],
                         schedule["id"])
        self.assertEqual(self.code("schedule.create", {"name": "x", "calendar": "daily",
                                                       "template_id": schedule["template_id"]},
                                   role="operator"), "forbidden")
        off = self.schedule(name="off", enabled=False)
        self.assertIsNone(off["next_at"])

    def test_what_a_schedule_is_made_of_is_checked(self):
        template = self.template()
        base = {"name": "s", "template_id": template["id"], "calendar": "hourly"}
        self.assertEqual(self.code("schedule.create", {**base, "calendar": "bogus"}), "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "calendar": "2020-01-01"}),
                         "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "calendar": "daily\n02:00"}),
                         "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "timezone": "Mars/Olympus"}),
                         "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "timezone": "../../etc/passwd"}),
                         "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "limit": "a b"}), "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "overlap": "maybe"}), "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "template_id": "tnope"}),
                         "not_found")
        self.assertTrue(self.ok("schedule.create", {**base, "timezone": "Europe/Rome"}))
        self.assertEqual(self.analyzed[-1], ("hourly", "Europe/Rome"))
        self.assertEqual(self.code("schedule.create", {**base, "name": "s"}), "conflict")

    def test_answers_are_checked_and_a_secret_is_never_stored(self):
        asked = self.template(survey=[{"name": "version", "type": "text", "required": True},
                                      {"name": "token", "type": "secret"}])
        base = {"name": "s", "template_id": asked["id"], "calendar": "hourly"}
        self.assertEqual(self.code("schedule.create", base), "bad_request")
        self.assertEqual(self.code("schedule.create", {**base, "answers": {"version": "1",
                                                                           "nope": 1}}),
                         "bad_request")
        self.assertEqual(self.code("schedule.create", {
            **base, "answers": {"version": "1", "token": "a-" + "0" * 32}}), "bad_request")
        self.assertEqual(self.ok("schedule.create", {**base, "answers": {"version": "1"}})
                         ["answers"], {"version": "1"})

        must = self.ok("template.update", {
            "id": asked["id"], "name": "deploy", "project_id": asked["project_id"],
            "playbook": "site.yml", "inventory_id": asked["inventory_id"],
            "survey": [{"name": "token", "type": "secret", "required": True}]})
        self.assertEqual(self.code("schedule.create", {**base, "name": "t",
                                                       "template_id": must["id"]}), "conflict")

    def test_an_operator_turns_it_off_and_on_and_runs_it_now(self):
        schedule = self.schedule()
        off = self.ok("schedule.disable", {"id": schedule["id"]}, role="operator")
        self.assertEqual((off["enabled"], off["next_at"]), (False, None))
        self.assertEqual(self.tick(START + 2 * HOUR + 5), [])
        self.clock = START + 3 * HOUR + 10
        on = self.ok("schedule.enable", {"id": schedule["id"]}, role="operator")
        self.assertEqual((on["enabled"], on["next_at"]), (True, START + 4 * HOUR))
        self.assertEqual(self.code("schedule.enable", {"id": schedule["id"]}, role="viewer"),
                         "forbidden")

        run = self.ok("schedule.run", {"id": schedule["id"]}, role="operator")
        self.assertEqual((run["created_by"], run["schedule_id"]), ("anna", schedule["id"]))
        self.assertEqual(self.ok("schedule.list")["schedules"][0]["next_at"], START + 4 * HOUR)
        self.assertEqual(self.code("schedule.update", {
            "id": schedule["id"], "name": "x", "template_id": schedule["template_id"],
            "calendar": "daily"}, role="operator"), "forbidden")

    def test_a_template_in_a_schedule_is_not_deleted_and_runs_outlive_their_schedule(self):
        schedule = self.schedule()
        self.assertEqual(self.code("template.delete", {"id": schedule["template_id"]}),
                         "conflict")
        run = self.ok("schedule.run", {"id": schedule["id"]})
        self.ok("schedule.delete", {"id": schedule["id"]})
        self.assertIsNone(self.ok("run.get", {"id": run["id"]})["schedule_id"])
        self.ok("template.delete", {"id": schedule["template_id"]})


class TickTest(ScheduleBase):
    def test_a_due_schedule_is_launched_as_itself_and_planned_again(self):
        schedule = self.schedule(limit="a", priority=3)
        self.assertEqual(self.tick(START + HOUR - 1), [])
        fired = self.tick(START + HOUR + 2)
        self.assertEqual(fired[0]["outcome"], "launched")
        run = self.ok("run.get", {"id": fired[0]["run_id"]})
        self.assertEqual((run["created_by"], run["schedule_id"], run["priority"]),
                         ("schedule:hourly", schedule["id"], 3))
        self.assertEqual(run["options"]["limit"], "a")
        listed = self.ok("schedule.list")["schedules"][0]
        self.assertEqual((listed["next_at"], listed["last_at"], listed["last_outcome"],
                          listed["last_run_id"], listed["last_status"]),
                         (START + 2 * HOUR, START + HOUR + 2, "launched", run["id"], "running"))
        # Once only, however many ticks come before the next time.
        self.assertEqual(self.tick(START + HOUR + 62), [])
        # The tick leaves no audit record, and it is not for an operator.
        commands = [row["command"] for row in self.ctx().conn.execute("SELECT command FROM audit")]
        self.assertNotIn("scheduler.tick", commands)
        self.assertEqual(self.code("scheduler.tick", role="operator"), "forbidden")

    def test_overlap_skip_queue_and_cancel(self):
        skip = self.schedule(name="skip")
        queue = self.schedule(name="queue", overlap="queue")
        cancel = self.schedule(name="cancel", overlap="cancel")
        self.config = dataclasses.replace(self.config, max_concurrent_runs=10)
        self.tick(START + HOUR + 1)
        first = {s["id"]: self.runs_of(s["id"])[0]["id"] for s in (skip, queue, cancel)}
        outcomes = {row["id"]: row["outcome"] for row in self.tick(START + 2 * HOUR + 1)}
        self.assertEqual(outcomes, {skip["id"]: "skipped", queue["id"]: "launched",
                                    cancel["id"]: "launched"})
        self.assertEqual(len(self.runs_of(skip["id"])), 1)
        self.assertEqual(len(self.runs_of(queue["id"])), 2)
        self.assertEqual(self.stopped, [first[cancel["id"]]])
        # The skipped one goes again once its run is over.
        self.finish(first[skip["id"]])
        outcomes = {row["id"]: row["outcome"] for row in self.tick(START + 3 * HOUR + 1)}
        self.assertEqual(outcomes[skip["id"]], "launched")

    def test_missed_times_are_caught_up_once_or_not_at_all(self):
        never = self.schedule(name="never")
        once = self.schedule(name="once", catch_up=True)
        # The controller was off for a day.
        outcomes = {row["id"]: row["outcome"] for row in self.tick(START + 25 * HOUR + 30)}
        self.assertEqual(outcomes, {never["id"]: "missed", once["id"]: "launched"})
        self.assertEqual((len(self.runs_of(never["id"])), len(self.runs_of(once["id"]))), (0, 1))
        listed = {s["id"]: s for s in self.ok("schedule.list")["schedules"]}
        self.assertEqual(listed[never["id"]]["next_at"], START + 26 * HOUR)
        # A few minutes late is not missed.
        self.assertEqual(self.tick(START + 26 * HOUR + 200)[0]["outcome"], "launched")

    def test_ten_on_the_same_minute_respect_the_limit_and_the_priority(self):
        template = self.template()
        made = [self.schedule(template, name=f"s{index}", overlap="queue", priority=index % 3)
                for index in range(10)]
        fired = self.tick(START + HOUR + 1)
        self.assertEqual([row["outcome"] for row in fired], ["launched"] * 10)
        statuses = [run["status"] for run in self.ok("run.list")["runs"]]
        self.assertEqual((statuses.count("running"), statuses.count("queued")), (2, 8))
        # The first two to start were of the highest priority.
        started = [self.ok("run.get", {"id": run_id}) for run_id in self.started]
        self.assertEqual({run["priority"] for run in started}, {2})
        # As runs end, the queue drains highest priority first, then oldest.
        for run_id in list(self.started):
            self.finish(run_id)
        self.ok("run.list")
        order = [self.ok("run.get", {"id": run_id})["priority"] for run_id in self.started]
        self.assertEqual(order, sorted(order, reverse=True))
        self.assertEqual(len(made), 10)

    def test_a_queued_run_left_by_a_reboot_is_started_by_the_tick(self):
        template = self.template()
        run_id = self.ok("run.start", {"template_id": template["id"]})["id"]
        self.ctx().conn.execute("UPDATE runs SET status = 'queued', started_at = NULL")
        self.started.clear()
        self.tick(START + 30)
        self.assertEqual(self.started, [run_id])

    def test_a_failure_is_recorded_and_does_not_stop_the_others(self):
        broken = self.schedule(name="broken")
        template = self.ok("template.list")["templates"][0]
        other = self.ok("template.create", {
            "name": "other", "project_id": template["project_id"], "playbook": "site.yml",
            "inventory_id": template["inventory_id"]})
        fine = self.schedule(other, name="fine")
        # The survey changed after the schedule was saved.
        self.ok("template.update", {
            "id": template["id"], "name": "deploy", "project_id": template["project_id"],
            "playbook": "site.yml", "inventory_id": template["inventory_id"],
            "survey": [{"name": "version", "type": "text", "required": True}]})
        outcomes = {row["id"]: row["outcome"] for row in self.tick(START + HOUR + 1)}
        self.assertEqual(outcomes, {broken["id"]: "error", fine["id"]: "launched"})
        listed = {s["id"]: s for s in self.ok("schedule.list")["schedules"]}
        self.assertIn("an answer is required", listed[broken["id"]]["last_error"])
        self.assertEqual(listed[broken["id"]]["next_at"], START + 2 * HOUR)

    def test_a_schedule_changed_meanwhile_is_left_to_the_change(self):
        schedule = self.schedule()
        row = self.ctx().conn.execute("SELECT * FROM schedules").fetchone()
        self.ctx().conn.execute("UPDATE schedules SET next_at = next_at + 60")
        self.clock = START + HOUR + 1
        self.assertEqual(schedules._fire(self.ctx(), row, self.clock),
                         {"id": schedule["id"], "outcome": "changed"})
        self.assertEqual(self.runs_of(schedule["id"]), [])

    def test_a_second_tick_while_one_works_does_nothing(self):
        self.schedule()
        descriptor = os.open(paths.SCHEDULER_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.clock = START + HOUR + 1
        self.assertEqual(self.ok("scheduler.tick"), {"busy": True, "schedules": []})

    def test_random_delay_is_added_within_its_bound(self):
        delays = {self.schedule(name=f"r{index}", random_delay=600)["next_at"] - (START + HOUR)
                  for index in range(20)}
        self.assertTrue(all(0 <= delay <= 600 for delay in delays))
        self.assertGreater(len(delays), 1)


class CalendarTest(ScheduleBase):
    def test_systemd_analyze_output_is_read_in_utc(self):
        output = ("  Original form: daily\n"
                  "Normalized form: *-*-* 00:00:00 Europe/Rome\n"
                  "    Next elapse: Thu 2026-10-15 00:00:00 CEST\n"
                  "       (in UTC): Wed 2026-10-14 22:00:00 UTC\n"
                  "       From now: 6h left\n"
                  "   Iteration #2: Fri 2026-10-16 00:00:00 CEST\n"
                  "       (in UTC): Thu 2026-10-15 22:00:00 UTC\n")
        # The real analyze, on a fake systemd-analyze.
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, output, "")
            normalized, times = REAL_ANALYZE("daily", "Europe/Rome", 2)
            argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["--", "daily Europe/Rome"])
        self.assertEqual(normalized, "*-*-* 00:00:00 Europe/Rome")
        self.assertEqual(times, [1792015200.0, 1792101600.0])
        # A controller in UTC, or in a zone with no offset, prints no "(in UTC)".
        for zone in ("UTC", "GMT"):
            output = ("  Original form: daily\n"
                      "Normalized form: *-*-* 00:00:00 Europe/Rome\n"
                      f"    Next elapse: Wed 2026-10-14 22:00:00 {zone}\n"
                      "       From now: 6h left\n"
                      f"   Iteration #2: Thu 2026-10-15 22:00:00 {zone}\n"
                      "       From now: 1 day 6h left\n")
            with mock.patch.object(subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, output, "")
                self.assertEqual(REAL_ANALYZE("daily", "Europe/Rome", 2)[1],
                                 [1792015200.0, 1792101600.0], zone)
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "Next elapse: never\n", "")
            self.assertEqual(REAL_ANALYZE("2020-01-01")[1], [])
            run.return_value = subprocess.CompletedProcess([], 1, "", "Failed to parse")
            with self.assertRaises(BadRequest):
                REAL_ANALYZE("bogus")

    def test_cron_lines_become_oncalendar_expressions(self):
        cases = {
            "*/15 * * * *": "*-*-* *:00/15:00",
            "0 2 * * *": "*-*-* 02:00:00",
            "30 6 * * 1-5": "Mon..Fri *-*-* 06:30:00",
            "0 0 1 */3 *": "*-01/3-01 00:00:00",
            "5,10 8-18 * jan,jul 0,6": "Sat,Sun *-01,07-* 08..18:05,10:00",
            "0 12 * * 7": "Sun *-*-* 12:00:00",
            "0 9 15 * *": "*-*-15 09:00:00",
            "@weekly": "Sun *-*-* 00:00:00",
            "@hourly": "*-*-* *:00:00",
        }
        for line, expected in cases.items():
            self.assertEqual(schedules.from_cron(line), expected, line)
        for line in ("* * * *", "61 * * * *", "0 0 1 * 1", "@reboot", "0 0 * * 1-x", "*/0 * * * *",
                     "5-1 * * * *"):
            with self.assertRaises(BadRequest, msg=line):
                schedules.from_cron(line)

    def test_preview_takes_a_calendar_or_a_cron_line(self):
        preview = self.ok("schedule.preview", {"cron": "0 2 * * *", "count": 3}, role="viewer")
        self.assertEqual(preview["calendar"], "*-*-* 02:00:00")
        self.assertEqual(len(preview["next"]), 3)
        self.assertEqual(self.code("schedule.preview", {}), "bad_request")
        self.assertEqual(self.code("schedule.preview", {"calendar": "daily", "cron": "@daily"}),
                         "bad_request")
