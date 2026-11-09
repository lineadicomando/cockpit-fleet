"""Checks: the catalog, the host registry, cycles planned by the tick, what a
cycle leaves turned into states, and the notifications of hard changes, with
the run unit replaced by a fake that writes the events a node would give."""

import dataclasses
import fcntl
import json
import os
from unittest import mock

from test_notify import NotifyBase

from cockpit_fleet import (  # noqa: F401
    catalog,
    checks,
    health,
    jobs,
    paths,
    runner,
    schedules,
    store,
)
from cockpit_fleet.errors import BadRequest, Conflict

LISTING = {
    "_meta": {"hostvars": {"web01": {"ansible_host": "10.0.0.1"}, "web02": {},
                           "win01": {"ansible_shell_type": "powershell"}}},
    "all": {"children": ["ungrouped", "web", "windows"]},
    "web": {"hosts": ["web01", "web02"]},
    "windows": {"hosts": ["win01"]},
}


def disk(used, instance="/"):
    return {"state": 0, "summary": f"fullest {instance} at {used}%",
            "metrics": [{"name": "used_pct", "value": used, "unit": "%", "instance": instance},
                        {"name": "free", "value": 1000, "unit": "B", "instance": instance}],
            "details": {instance: "ext4"}}


class CheckBase(NotifyBase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(paths, "SCHEDULER_LOCK", self.root / "scheduler.lock")
        patch.start()
        self.addCleanup(patch.stop)

    def inventory(self, listing=LISTING):
        inventory = self.ok("inventory.create", {"name": "lab", "kind": "managed", "content": "x"})
        run_id = self.ok("inventory.parse", {"id": inventory["id"]})["run_id"]
        (paths.RUNS_DIR / run_id / "inventory.json").write_text(json.dumps(listing))
        self.finish(run_id)
        self.ok("run.list")  # reconcile
        return inventory

    def check(self, inventory, kind="disk", **extra):
        return self.ok("check.create", {"name": extra.pop("name", kind), "kind": kind,
                                        "inventory_id": inventory["id"], **extra})

    def tick(self, at=None):
        if at is not None:
            self.clock = at
        return self.ok("scheduler.tick")["checks"]

    def cycles(self):
        return self.ok("run.list", {"kind": "check"})["runs"]

    def answer(self, run_id, hosts: dict, rc=0):
        """What the events callback writes when the nodes answer."""
        spec = json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())
        lines = []
        for host, outcome in hosts.items():
            if outcome == "unreachable":
                lines.append({"event": "host_unreachable", "host": host, "ts": self.clock,
                              "result": {"msg": "ssh: connect to host: timed out"}})
                continue
            if isinstance(outcome, str):
                lines.append({"event": "host_failed", "host": host, "ts": self.clock,
                              "result": {"msg": outcome}})
                continue
            results = [{"id": entry["id"], "check": entry["check"], **outcome[entry["check"]]}
                       for entry in spec["plan"].get(host, []) if entry["check"] in outcome]
            results += outcome.get("extra", [])
            lines.append({"event": "host_ok", "host": host, "ts": self.clock,
                          "result": {"fleet_results": results,
                                     "fleet_host": {"platform": "linux", "os": "Debian 13",
                                                    "time": outcome.get("time", self.clock)}}})
        (paths.RUNS_DIR / run_id / "events.jsonl").write_text(
            "".join(json.dumps(line) + "\n" for line in lines))
        self.finish(run_id, rc=rc)
        self.ok("run.list")  # reconcile

    def states(self, **params):
        return {(state["host"], state["check"]): state
                for state in self.ok("state.list", params, role="viewer")["states"]}


class CatalogTest(CheckBase):
    def test_parameters_are_checked_against_the_catalog(self):
        self.assertEqual(catalog.check_params("services", {"names": ["ssh", "cron"]}),
                         {"names": ["ssh", "cron"]})
        for kind, params in (("services", {}), ("services", {"names": ["-x"]}),
                             ("services", {"names": ["a;b"]}), ("disk", {"nope": []}),
                             ("disk", {"mounts": "/"}), ("certificates", {"paths": ["a\nb"]}),
                             ("bogus", {})):
            with self.assertRaises(BadRequest, msg=(kind, params)):
                catalog.check_params(kind, params)

    def test_thresholds_go_either_way(self):
        high = {"used_pct": {"warning": 80, "critical": 90}}
        low = {"days_left": {"warning": 30, "critical": 7}}
        metric = lambda name, value: [{"name": name, "value": value, "instance": "/x"}]  # noqa: E731
        self.assertEqual(catalog.evaluate(metric("used_pct", 50), high)[0], catalog.OK)
        self.assertEqual(catalog.evaluate(metric("used_pct", 85), high)[0], catalog.WARNING)
        state, crossed = catalog.evaluate(metric("used_pct", 91), high)
        self.assertEqual(state, catalog.CRITICAL)
        self.assertEqual(crossed, ["used_pct on /x 91 (critical 90)"])
        self.assertEqual(catalog.evaluate(metric("days_left", 40), low)[0], catalog.OK)
        self.assertEqual(catalog.evaluate(metric("days_left", 20), low)[0], catalog.WARNING)
        self.assertEqual(catalog.evaluate(metric("days_left", 3), low)[0], catalog.CRITICAL)
        only = {"not_running": {"warning": None, "critical": 1}}
        self.assertEqual(catalog.evaluate(metric("not_running", 0), only)[0], catalog.OK)
        self.assertEqual(catalog.evaluate(metric("not_running", 2), only)[0], catalog.CRITICAL)
        self.assertEqual(catalog.worst(catalog.WARNING, catalog.UNKNOWN), catalog.UNKNOWN)
        self.assertEqual(catalog.worst(catalog.CRITICAL, catalog.UNKNOWN), catalog.CRITICAL)
        for bad in ({"Used": {}}, {"x": {"warning": "1"}}, {"x": {"low": 1}}, []):
            with self.assertRaises(BadRequest, msg=bad):
                catalog.check_thresholds("disk", bad)

    def test_what_a_node_sends_is_cleaned(self):
        metrics = catalog.clean_metrics([
            {"name": "used_pct", "value": 1.5, "unit": "%", "instance": "/", "max": 100},
            {"name": "BAD NAME", "value": 1}, {"name": "x", "value": "1"},
            {"name": "y", "value": float("nan")}, {"name": "z", "value": True}, "nope"])
        self.assertEqual(metrics, [{"name": "used_pct", "value": 1.5, "unit": "%",
                                    "instance": "/", "max": 100}])
        self.assertEqual(catalog.clean_state(7), catalog.UNKNOWN)
        self.assertEqual(catalog.clean_state(True), catalog.UNKNOWN)
        self.assertEqual(catalog.clean_details({"x": "y" * 20000}),
                         {"note": "details too large, left out"})


class RunnerTest(CheckBase):
    def test_the_plan_is_data_and_the_hosts_a_file(self):
        directory = self.root / "runs" / "rx"
        (directory / "project").mkdir(parents=True)
        (directory / "inventory").mkdir()
        (directory / "inventory" / "hosts.yml").write_text("all:\n")
        runtime = self.root / "rt"
        runtime.mkdir()
        plan = {"web01": [{"id": "k1", "check": "services",
                           "params": {"names": ["{{ lookup('pipe', 'id') }}"]}}]}
        spec = {"kind": "check", "plan": plan, "timeout": 30, "forks": 7,
                "inventory": "hosts.yml", "inventory_source": "managed", "vaults": [],
                "limit": "ignored", "extra_vars": {"ignored": 1}}
        argv = runner.build_check_argv(spec, directory, runtime, None)
        self.assertEqual(argv[-1], str(runtime / "checks.yml"))
        self.assertIn(f"@{runtime / 'hosts.limit'}", argv)
        self.assertIn(f"@{runtime / 'plan.yml'}", argv)
        self.assertNotIn("ignored", " ".join(argv))
        self.assertEqual(argv[argv.index("--forks") + 1], "7")
        self.assertEqual((runtime / "hosts.limit").read_text(), "web01\n")
        text = (runtime / "plan.yml").read_text()
        self.assertIn('!unsafe "{{ lookup(\'pipe\', \'id\') }}"', text)
        self.assertIn('"id": !unsafe "k1"', text)
        playbook = json.loads((runtime / "checks.yml").read_text())
        task = playbook[0]["tasks"][0]
        self.assertEqual((task["timeout"], playbook[0]["strategy"]), (30, "free"))
        self.assertIn(runner.CHECKS_MODULE, task)
        for bad in ({}, {"-x": plan["web01"]}, {"a b": plan["web01"]},
                    {"web01": [{"id": "K", "check": "disk", "params": {}}]},
                    {"web01": [{"id": "k", "check": "disk", "params": {}, "more": 1}]}):
            with self.assertRaises(BadRequest, msg=bad):
                runner.check_plan(bad)


class CycleTest(CheckBase):
    def test_hosts_come_from_the_inventory_and_states_from_the_groups(self):
        inventory = self.inventory()
        hosts = self.ok("host.list", role="viewer")["hosts"]
        self.assertEqual([host["name"] for host in hosts], ["web01", "web02", "win01"])
        self.assertEqual(hosts[0]["groups"], ["web"])
        self.check(inventory, group="web")
        self.check(inventory, "reachability")
        self.assertEqual(len(self.states()), 5)
        self.assertEqual(self.code("check.create", {"name": "x", "kind": "disk",
                                                    "inventory_id": inventory["id"],
                                                    "group": "nope"}), "conflict")
        # A host gone from the inventory takes its states with it.
        listing = json.loads(json.dumps(LISTING))
        listing["web"]["hosts"] = ["web01"]
        del listing["_meta"]["hostvars"]["web02"]
        run_id = self.ok("inventory.parse", {"id": inventory["id"]})["run_id"]
        (paths.RUNS_DIR / run_id / "inventory.json").write_text(json.dumps(listing))
        self.finish(run_id)
        self.ok("run.list")
        self.assertEqual(sorted(self.states()), [("web01", "disk"), ("web01", "reachability"),
                                                 ("win01", "reachability")])

    def test_a_check_on_an_inventory_never_listed_lists_it_first(self):
        inventory = self.ok("inventory.create", {"name": "new", "kind": "managed",
                                                 "content": "x"})
        self.check(inventory)
        runs = self.ok("run.list", {"kind": "inventory"})["runs"]
        self.assertEqual([run["inventory_id"] for run in runs], [inventory["id"]])

    def test_the_tick_plans_one_cycle_per_shard_with_a_limit_of_its_own(self):
        self.config = dataclasses.replace(self.config, checks_shard_size=2,
                                          checks_max_concurrent=1, max_concurrent_runs=1)
        inventory = self.inventory()
        self.check(inventory, "reachability")
        self.check(inventory, "disk", group="web")
        template = self.template()
        self.ok("run.start", {"template_id": template["id"]}, role="operator")
        queued = self.tick()["queued"]
        self.assertEqual(len(queued), 2)
        cycles = {run["id"]: run for run in self.cycles()}
        # The playbook holds the only slot of runs; one cycle still starts.
        self.assertEqual(sorted(run["status"] for run in cycles.values()), ["queued", "running"])
        self.assertNotIn("check", {run["kind"] for run in self.ok("run.list")["runs"]})
        spec = json.loads((paths.RUNS_DIR / queued[0] / "spec.json").read_text())
        self.assertEqual(spec["kind"], "check")
        self.assertEqual(sorted(spec["plan"]), ["web01", "web02"])
        self.assertEqual(sorted(entry["check"] for entry in spec["plan"]["web01"]),
                         ["disk", "reachability"])
        # Nothing is planned twice while a cycle holds it.
        self.assertEqual(self.tick(self.clock + 600)["queued"], [])

    def test_results_become_states_soft_then_hard(self):
        inventory = self.inventory()
        check = self.check(inventory, group="web", interval=300, retry_interval=60,
                           max_attempts=3, thresholds={"used_pct": {"warning": 80,
                                                                    "critical": 90}})
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(50)}, "web02": {"disk": disk(95)}})
        states = self.states()
        self.assertEqual(states[("web01", "disk")]["state"], "ok")
        self.assertEqual(states[("web01", "disk")]["next_at"], self.clock + 300)
        bad = states[("web02", "disk")]
        self.assertEqual((bad["state"], bad["hard_state"], bad["state_type"], bad["attempt"]),
                         ("critical", "ok", "soft", 1))
        self.assertEqual(bad["next_at"], self.clock + 60)
        self.assertIn("used_pct on / 95% (critical 90)", bad["summary"])
        host = self.ok("host.list")["hosts"][0]
        self.assertEqual((host["platform"], host["os"], host["reachable"]),
                         ("linux", "Debian 13", True))

        for attempt in (2, 3):
            run_id = self.tick(self.clock + 60)["queued"][0]
            spec = json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())
            self.assertEqual(list(spec["plan"]), ["web02"])
            self.answer(run_id, {"web02": {"disk": disk(96)}})
            state = self.states()[("web02", "disk")]
            self.assertEqual(state["attempt"], attempt)
        self.assertEqual((state["hard_state"], state["state_type"]), ("critical", "hard"))
        self.assertEqual(state["next_at"], self.clock + 300)
        # A recovery is hard at once.
        run_id = self.tick(self.clock + 300)["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(50)}, "web02": {"disk": disk(40)}})
        state = self.states()[("web02", "disk")]
        self.assertEqual((state["state"], state["hard_state"], state["attempt"]), ("ok", "ok", 0))
        changes = self.ok("host.get", {"id": state["host_id"]}, role="viewer")["changes"]
        self.assertEqual([(c["state"], c["state_type"]) for c in reversed(changes)],
                         [("critical", "soft"), ("critical", "hard"), ("ok", "hard")])
        self.assertEqual(self.ok("check.list")["checks"][0]["states"]["ok"], 2)
        self.assertEqual(check["effective_thresholds"]["inodes_used_pct"],
                         {"warning": 85, "critical": 95})

    def test_the_next_time_keeps_to_the_ticks(self):
        inventory = self.inventory()
        self.check(inventory, group="web", interval=60)
        planned = self.clock
        run_id = self.tick()["queued"][0]
        self.clock += 7  # the cycle takes its time
        self.answer(run_id, {"web01": {"disk": disk(1)}, "web02": {"disk": disk(1)}})
        self.assertEqual(self.states()[("web01", "disk")]["next_at"], planned + 60)
        # The next tick comes a little before that, and takes it.
        self.assertEqual(len(self.tick(planned + 59.5)["queued"]), 1)

    def test_only_what_was_planned_for_the_host_is_taken(self):
        inventory = self.inventory()
        self.check(inventory, group="web", max_attempts=1)
        windows = self.check(inventory, name="windows disk", group="windows", max_attempts=1)
        run_id = self.tick()["queued"][0]
        forged = {"id": windows["id"], "check": "disk", **disk(99)}
        self.answer(run_id, {"web01": {"disk": disk(10), "extra": [forged]},
                             "web02": {"extra": []}, "intruder": {"disk": disk(99)}})
        states = self.states()
        self.assertEqual(states[("web01", "disk")]["state"], "ok")
        self.assertEqual(states[("win01", "windows disk")]["summary"], "no result for this host")
        self.assertEqual(states[("web02", "disk")]["state"], "unknown")
        self.assertEqual(states[("web02", "disk")]["summary"],
                         "the node sent no result for this check")

    def test_an_unreachable_host_is_told_about_once_not_once_per_check(self):
        channel = self.channel()
        self.rule(channel, events=("problem", "recovery"))
        inventory = self.inventory()
        self.check(inventory, "reachability", group="web", max_attempts=1)
        self.check(inventory, "disk", group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": "unreachable", "web02": {"disk": disk(10),
                                                               "reachability": {"state": 0}}},
                    rc=4)
        states = self.states()
        self.assertEqual(states[("web01", "reachability")]["hard_state"], "critical")
        self.assertIn("timed out", states[("web01", "reachability")]["summary"])
        self.assertEqual((states[("web01", "disk")]["state"], states[("web01", "disk")]["hard_state"],
                          states[("web01", "disk")]["suppressed"]), ("unknown", "ok", True))
        told = self.ok("notification.list")["notifications"]
        self.assertEqual([(n["event"], n["title"]) for n in told],
                         [("problem", "reachability on web01")])
        # Ansible says 4 for an unreachable host: the cycle itself went well.
        self.assertEqual(self.cycles()[0]["status"], "successful")

    def test_a_check_that_keeps_changing_flaps_and_is_not_told_about(self):
        self.assertFalse(checks._flapping([0, 2, 2, 0, 0], False))
        self.assertFalse(checks._flapping([0, 2, 2, 0, 0, 0, 0, 0, 0, 0], False))
        self.assertTrue(checks._flapping([0, 2] * 5, False))
        # It ends when the changes are fewer, not at once.
        self.assertTrue(checks._flapping([0, 2, 0, 2, 0, 0, 0, 0, 2, 2, 2], True))
        self.assertFalse(checks._flapping([0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], True))
        channel = self.channel()
        self.rule(channel, events=("problem", "recovery"))
        inventory = self.inventory()
        self.check(inventory, group="web", max_attempts=1)
        for used in (10, 99) * 6:
            run_id = self.tick(self.clock + 300)["queued"][0]
            self.answer(run_id, {"web01": {"disk": disk(used)}, "web02": {"disk": disk(10)}})
        state = self.states()[("web01", "disk")]
        self.assertTrue(state["flapping"])
        told = len(self.ok("notification.list")["notifications"])
        for used in (10, 99):
            run_id = self.tick(self.clock + 300)["queued"][0]
            self.answer(run_id, {"web01": {"disk": disk(used)}, "web02": {"disk": disk(10)}})
        self.assertEqual(len(self.ok("notification.list")["notifications"]), told)

    def test_a_task_that_fails_is_unknown(self):
        inventory = self.inventory()
        self.check(inventory, group="web")
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": "The task was cut short: timeout (60s)"}, rc=2)
        states = self.states()
        self.assertEqual(states[("web01", "disk")]["state"], "unknown")
        self.assertIn("timeout", states[("web01", "disk")]["summary"])
        self.assertEqual(states[("web02", "disk")]["summary"], "no result for this host")
        self.assertFalse(states[("web02", "disk")]["running"])

    def test_a_plan_the_run_rewrote_frees_the_cycle(self):
        # The run directory is the run's to write: a playbook on localhost
        # could leave anything in spec.json. Nothing of the cycle is taken,
        # and reconciling still ends it.
        inventory = self.inventory()
        self.check(inventory, group="web")
        for spec in ("[]", '{"plan": [1]}', '{"plan": {"web01": [{"id": 1}]}}'):
            run_id = self.tick(self.clock + 600)["queued"][0]
            (paths.RUNS_DIR / run_id / "spec.json").write_text(spec)
            self.answer(run_id, {"web01": "a task failed"})
            states = self.states()
            self.assertEqual(states[("web01", "disk")]["state"], "unknown")
            self.assertIn("could not be read back", states[("web01", "disk")]["summary"])
            self.assertFalse(states[("web01", "disk")]["running"])
            self.assertEqual(self.ok("run.get", {"id": run_id})["status"], "successful")

    def test_the_clock_is_compared_with_the_controller(self):
        inventory = self.inventory()
        self.check(inventory, "clock", group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"clock": {"state": 0}, "time": self.clock - 2},
                             "web02": {"clock": {"state": 0}, "time": self.clock + 120}})
        states = self.states()
        self.assertEqual(states[("web01", "clock")]["state"], "ok")
        self.assertEqual(states[("web01", "clock")]["metrics"],
                         [{"name": "offset", "value": 2.0, "unit": "s"}])
        self.assertEqual(states[("web02", "clock")]["state"], "critical")

    def test_cycles_are_removed_after_a_day(self):
        inventory = self.inventory()
        self.check(inventory, group="web")
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(1)}, "web02": {"disk": disk(1)}})
        self.ok("run.list")
        self.tick(self.clock + checks.KEEP_CYCLES + 1)
        self.assertNotIn(run_id, [run["id"] for run in self.cycles()])
        self.assertFalse((paths.RUNS_DIR / run_id).exists())


class PlaybookCheckTest(CheckBase):
    def own_check(self, **extra):
        project = self.local_project()
        (self.root / "src" / "site" / "queue.yml").write_text("- hosts: all\n")
        inventory = self.inventory()
        return inventory, self.ok("check.create", {
            "name": "mail queue", "kind": "playbook", "inventory_id": inventory["id"],
            "group": "web", "project_id": project["id"], "playbook": "queue.yml",
            "max_attempts": 1, "thresholds": {"queued": {"warning": 10, "critical": 100}},
            **extra})

    def test_a_playbook_of_the_user_runs_alone_on_its_hosts(self):
        inventory, check = self.own_check()
        self.check(inventory, group="web")
        queued = self.tick()["queued"]
        self.assertEqual(len(queued), 2)
        specs = [json.loads((paths.RUNS_DIR / run / "spec.json").read_text()) for run in queued]
        own = next(spec for spec in specs if spec.get("playbook"))
        self.assertEqual(own["playbook"], "queue.yml")
        self.assertEqual(sorted(own["plan"]), ["web01", "web02"])
        self.assertEqual({entry["id"] for entries in own["plan"].values() for entry in entries},
                         {check["id"]})
        run = next(run for run, spec in zip(queued, specs, strict=True) if spec.get("playbook"))
        self.assertTrue((paths.RUNS_DIR / run / "project" / "queue.yml").is_file())

    def test_its_last_result_counts_with_the_thresholds_applied(self):
        _, check = self.own_check()
        run = self.tick()["queued"][0]
        result = lambda value: {"fleet_result": {"state": 0, "summary": f"{value} waiting",  # noqa: E731
                                                 "metrics": [{"name": "queued", "value": value}]}}
        lines = [
            {"event": "host_ok", "host": "web01", "ts": self.clock, "result": {"stdout": "x"}},
            {"event": "host_ok", "host": "web01", "ts": self.clock, "result": result(150)},
            {"event": "host_ok", "host": "web02", "ts": self.clock, "result": result(3)},
            {"event": "host_failed", "host": "web02", "ts": self.clock, "ignored": True,
             "result": {"msg": "ignored"}},
            {"event": "host_ok", "host": "win01", "ts": self.clock, "result": {"stdout": "x"}},
        ]
        (paths.RUNS_DIR / run / "events.jsonl").write_text(
            "".join(json.dumps(line) + "\n" for line in lines))
        self.finish(run)
        self.ok("run.list")
        states = self.states()
        self.assertEqual(states[("web01", "mail queue")]["state"], "critical")
        self.assertIn("queued 150 (critical 100)", states[("web01", "mail queue")]["summary"])
        self.assertEqual(states[("web02", "mail queue")]["state"], "ok")
        self.assertEqual(check["effective_thresholds"], {"queued": {"warning": 10, "critical": 100}})

    def test_a_task_that_fails_or_no_result_is_unknown(self):
        self.own_check()
        run = self.tick()["queued"][0]
        lines = [
            {"event": "host_failed", "host": "web01", "ts": self.clock, "result": {"msg": "boom"}},
            {"event": "host_ok", "host": "web01", "ts": self.clock,
             "result": {"fleet_result": {"state": 0}}},
            {"event": "host_ok", "host": "web02", "ts": self.clock, "result": {"stdout": "x"}},
        ]
        (paths.RUNS_DIR / run / "events.jsonl").write_text(
            "".join(json.dumps(line) + "\n" for line in lines))
        self.finish(run, rc=2)
        self.ok("run.list")
        states = self.states()
        self.assertEqual((states[("web01", "mail queue")]["state"],
                          states[("web01", "mail queue")]["summary"]), ("unknown", "boom"))
        self.assertIn("fleet_result", states[("web02", "mail queue")]["summary"])

    def test_where_the_playbook_is_is_checked(self):
        project = self.local_project()
        inventory = self.inventory()
        base = {"name": "x", "kind": "playbook", "inventory_id": inventory["id"],
                "project_id": project["id"]}
        for bad in ({"playbook": "../etc/x.yml"}, {"playbook": "/etc/x.yml"}, {}):
            self.assertEqual(self.code("check.create", {**base, **bad}), "bad_request", bad)
        self.assertEqual(self.code("check.create", {"name": "y", "kind": "disk",
                                                    "inventory_id": inventory["id"],
                                                    "playbook": "a.yml"}), "bad_request")
        self.assertEqual(self.code("check.create", {**base, "playbook": "a.yml",
                                                    "params": {"names": ["x"]}}), "bad_request")


class NotifyChecksTest(CheckBase):
    def problem(self, used=97):
        inventory = self.inventory()
        check = self.check(inventory, group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(used)}, "web02": {"disk": disk(10)}})
        return inventory, check, self.states()[("web01", "disk")]

    def told(self):
        return [(n["event"], n["title"]) for n in self.ok("notification.list")["notifications"]]

    def test_problem_and_recovery_reach_the_channels_of_the_rules(self):
        channel = self.channel()
        other = self.channel("other", secret="")
        self.rule(channel, events=("problem", "recovery"))
        inventory = self.ok("inventory.list")["inventories"]
        self.assertEqual(inventory, [])
        inventory, check, state = self.problem()
        # A rule on a group the host is not in says nothing.
        self.rule(other, events=("problem",), scope="group", scope_id=inventory["id"],
                  group="windows")
        self.assertEqual(self.told(), [("problem", "disk on web01")])
        rows = self.ctx().conn.execute("SELECT payload, host_id FROM notifications").fetchall()
        payload = json.loads(rows[0]["payload"])
        self.assertEqual((payload["state"], rows[0]["host_id"]), ("critical", state["host_id"]))
        from cockpit_fleet import notify
        message = notify.compose("it", [payload], "https://fleet:9090")
        self.assertEqual(message.title, "Fleet: Problema: disk on web01 CRITICO")
        self.assertIn(f"https://fleet:9090/fleet#/hosts/{state['host_id']}", message.text)
        run_id = self.tick(self.clock + 300)["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(20)}, "web02": {"disk": disk(10)}})
        self.assertEqual(self.told()[0], ("recovery", "disk on web01"))
        self.assertEqual(len(self.told()), 2)

    def test_rules_keep_runs_and_checks_apart(self):
        channel = self.channel()
        template = self.template()
        for scope, events in (("template", ["problem"]), ("check", ["failed"])):
            target = template["id"] if scope == "template" else "k1"
            self.assertEqual(self.code("rule.create", {
                "name": scope, "channel_id": channel["id"], "events": events, "scope": scope,
                "scope_id": target}), "bad_request" if scope == "template" else "not_found")
        inventory = self.inventory()
        check = self.check(inventory)
        self.assertEqual(self.code("rule.create", {
            "name": "c", "channel_id": channel["id"], "events": ["failed"], "scope": "check",
            "scope_id": check["id"]}), "bad_request")
        self.assertEqual(self.code("rule.create", {
            "name": "g", "channel_id": channel["id"], "events": ["problem"], "scope": "group",
            "scope_id": inventory["id"]}), "bad_request")
        # A run of an inventory scoped by a rule on that inventory is told about.
        self.rule(channel, events=("failed", "problem"), scope="inventory",
                  scope_id=template["inventory_id"])
        self.run_ending(rc=2, template=template)
        self.assertEqual(self.told()[0][0], "failed")

    def test_acknowledged_problems_are_not_reminded(self):
        channel = self.channel()
        self.rule(channel, events=("problem", "reminder"))
        _, check, state = self.problem()
        self.assertEqual(self.code("state.ack", {"host_id": state["host_id"],
                                                 "check_id": check["id"]}, role="viewer"),
                         "forbidden")
        self.tick(self.clock + 25 * 3600)  # past the reminder, results still due
        self.assertEqual([event for event, _ in self.told()], ["reminder", "problem"])
        self.ok("state.ack", {"host_id": state["host_id"], "check_id": check["id"],
                              "comment": "on it"}, role="operator")
        acked = self.states()[("web01", "disk")]
        self.assertEqual((acked["acknowledged"], acked["ack_by"], acked["ack_comment"]),
                         (True, "anna", "on it"))
        self.tick(self.clock + 25 * 3600)
        self.assertEqual(len(self.told()), 2)
        healthy = {"id": state["host_id"], "check_id": check["id"]}
        self.assertEqual(self.code("state.ack", {"host_id": self.states()[("web02", "disk")]
                                                 ["host_id"], "check_id": check["id"]}),
                         "conflict")
        self.assertTrue(healthy)

    def test_maintenance_holds_back_until_it_ends(self):
        channel = self.channel()
        self.rule(channel, events=("problem",))
        inventory = self.inventory()
        host = [h for h in self.ok("host.list")["hosts"] if h["name"] == "web01"][0]
        window = self.ok("maintenance.create", {"inventory_id": inventory["id"],
                                                "host_id": host["id"],
                                                "ends_at": int(self.clock + 3600)},
                         role="operator")
        self.assertEqual(self.code("maintenance.create", {"inventory_id": inventory["id"],
                                                          "group": "web",
                                                          "ends_at": int(self.clock - 1)},
                                   role="operator"), "bad_request")
        self.assertEqual(self.code("maintenance.create", {"inventory_id": inventory["id"],
                                                          "ends_at": int(self.clock + 9)},
                                   role="operator"), "bad_request")
        self.check(inventory, group="web", max_attempts=1)
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(99)}, "web02": {"disk": disk(10)}})
        self.assertEqual(self.told(), [])
        self.assertTrue(self.states()[("web01", "disk")]["in_maintenance"])
        self.ok("maintenance.delete", {"id": window["id"]}, role="operator")
        self.tick()
        self.assertEqual(self.told(), [("problem", "disk on web01")])
        listed = self.ok("maintenance.list", role="viewer")["maintenances"]
        self.assertEqual(listed[0]["ends_at"], self.clock)

    def test_check_run_plans_at_once_for_an_operator(self):
        inventory = self.inventory()
        check = self.check(inventory, group="web")
        run_id = self.tick()["queued"][0]
        self.answer(run_id, {"web01": {"disk": disk(1)}, "web02": {"disk": disk(1)}})
        host = self.states()[("web01", "disk")]["host_id"]
        runs = self.ok("check.run", {"id": check["id"], "host_id": host}, role="operator")["runs"]
        spec = json.loads((paths.RUNS_DIR / runs[0] / "spec.json").read_text())
        self.assertEqual(list(spec["plan"]), ["web01"])
        self.assertEqual(self.code("check.run", {"id": check["id"], "host_id": host},
                                   role="operator"), "conflict")
        self.assertEqual(self.code("check.run", {"id": check["id"]}, role="viewer"), "forbidden")
        self.assertEqual(self.code("check.create", {"name": "x", "kind": "disk",
                                                    "inventory_id": inventory["id"]},
                                   role="operator"), "forbidden")
        audit = [row["command"] for row in self.ctx().conn.execute("SELECT command FROM audit")]
        self.assertIn("check.run", audit)

    def test_check_run_waits_for_no_tick_and_tells_when_one_is_planning(self):
        inventory = self.inventory()
        check = self.check(inventory, group="web")
        descriptor = os.open(paths.SCHEDULER_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            answer = self.call("check.run", {"id": check["id"]}, role="operator")
        finally:
            os.close(descriptor)
        self.assertEqual(answer["error"]["code"], "conflict")
        self.assertIn("planning", answer["error"]["message"])
        self.assertEqual(self.ok("run.list")["runs"][1:], [])
        self.assertEqual(len(self.ok("check.run", {"id": check["id"]}, role="operator")["runs"]), 1)


class StoreTest(CheckBase):
    def test_deleting_a_check_takes_its_states(self):
        inventory = self.inventory()
        check = self.check(inventory)
        self.assertEqual(self.code("inventory.delete", {"id": inventory["id"]}), "conflict")
        self.ok("check.delete", {"id": check["id"]})
        self.assertEqual(self.states(), {})
        self.ok("inventory.delete", {"id": inventory["id"]})
        self.assertEqual(self.ok("host.list")["hosts"], [])
        self.assertEqual(jobs.ACTIVE, ("queued", "running"))
        self.assertTrue(store.now())


class SizingTest(CheckBase):
    def measured(self, hosts, forks, seconds, peak=None):
        return {"hosts": hosts, "forks": forks, "started_at": 0.0, "finished_at": seconds,
                "memory_peak": peak}

    def test_with_nothing_measured_the_configured_values_hold(self):
        self.assertEqual(checks.size([], 20, 50, 10**9, 150),
                         {"forks": 20, "shard_size": 50, "basis": "configured"})

    def test_forks_fit_the_memory_a_cycle_may_take(self):
        mb = 1024 * 1024
        # 100 MB of base and 30 MB a fork, seen with 5 and 10 forks.
        history = [self.measured(50, 5, 20, 250 * mb), self.measured(50, 10, 10, 400 * mb)]
        sizing = checks.size(history, 20, 50, 500 * mb, 300)
        self.assertEqual((sizing["memory_base"], sizing["memory_per_fork"]), (100 * mb, 30 * mb))
        self.assertEqual(sizing["forks"], 13)
        # One number of forks only: all of it counts per fork.
        sizing = checks.size([self.measured(50, 10, 10, 400 * mb)], 20, 50, 500 * mb, 300)
        self.assertEqual(sizing["forks"], 12)
        # Never fewer than one, never more than the most.
        self.assertEqual(checks.size(history, 20, 50, 10 * mb, 300)["forks"], 1)
        self.assertEqual(checks.size(history, 4, 50, 10**12, 300)["forks"], 4)

    def test_a_shard_is_what_the_forks_get_through_in_the_target(self):
        # 40 hosts in 60 s with 10 forks: 15 s a host for a fork.
        history = [self.measured(40, 10, 60)]
        sizing = checks.size(history, 10, 50, 0, 30)
        self.assertEqual((sizing["seconds_per_host"], sizing["shard_size"]), (15, 20))
        # Never fewer hosts than forks, never more than the most.
        self.assertEqual(checks.size(history, 10, 50, 0, 1)["shard_size"], 10)
        self.assertEqual(checks.size(history, 10, 50, 0, 3600)["shard_size"], 50)
        # Fewer hosts than forks: each host had a fork of its own.
        self.assertEqual(checks.size([self.measured(2, 10, 8)], 10, 50, 0, 30)["seconds_per_host"],
                         8)

    def test_shards_are_as_even_as_they_can_be(self):
        names = [f"h{i:02}" for i in range(51)]
        self.assertEqual([len(shard) for shard in checks.shards(names, 50)], [26, 25])
        self.assertEqual([len(shard) for shard in checks.shards(names[:50], 50)], [50])
        self.assertEqual(sum(checks.shards(names, 7), []), names)
        self.assertEqual(checks.shards([], 50), [])

    def test_the_budget_is_shared_by_the_cycles_that_may_run_at_once(self):
        self.config = dataclasses.replace(self.config, checks_memory_mb=800,
                                          checks_max_concurrent=2)
        self.assertEqual(checks.memory_budget(self.ctx()), 200 * 1024 * 1024)
        self.config = dataclasses.replace(self.config, checks_memory_mb=0)
        with mock.patch.object(checks, "_memory_total", lambda: 4096):
            self.assertEqual(checks.memory_budget(self.ctx()), 512)

    def rows(self):
        import sqlite3
        conn = sqlite3.connect(self.root / "fleet.db")
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute("SELECT * FROM cycles ORDER BY started_at")]
        finally:
            conn.close()

    def test_what_a_cycle_took_is_kept_and_sizes_the_next(self):
        mb = 1024 * 1024
        self.config = dataclasses.replace(self.config, checks_memory_mb=2 * 150,
                                          checks_max_concurrent=1, checks_forks=20)
        inventory = self.inventory()
        self.check(inventory, group="web", interval=60)
        run_id = self.tick()["queued"][0]
        spec = json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())
        self.assertEqual(spec["forks"], 20)
        (paths.RUNS_DIR / run_id / "events.jsonl").write_text("")
        self.clock += 4
        self.finish(run_id, finished_at=self.clock,
                    usage={"memory_peak": 200 * mb, "cpu_usec": 3_000_000})
        self.ok("run.list")
        [row] = self.rows()
        self.assertEqual((row["run_id"], row["inventory_id"], row["grp"], row["status"]),
                         (run_id, inventory["id"], "", "successful"))
        self.assertEqual((row["hosts"], row["checks"], row["forks"], row["memory_peak"],
                          row["cpu_usec"], row["finished_at"] - row["started_at"]),
                         (2, 2, 20, 200 * mb, 3_000_000, 4))
        # Two hosts took 200 MB with a fork each: 150 MB fit one.
        run_id = self.tick(self.clock + 60)["queued"][0]
        spec = json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())
        self.assertEqual(spec["forks"], 1)
        options = self.ok("run.get", {"id": run_id})["options"]
        self.assertEqual((options["forks"], options["group"]), (1, ""))
        # What a run wrote is believed only as far as its shape.
        self.finish(run_id, finished_at=-5, usage={"memory_peak": "lots", "cpu_usec": True})
        self.ok("run.list")
        row = self.rows()[1]
        self.assertEqual((row["memory_peak"], row["cpu_usec"], row["finished_at"]),
                         (None, None, self.clock))
        # Kept a month, after the cycle itself is gone.
        self.tick(self.clock + 2 * 86400)
        self.assertNotIn(run_id, [run["id"] for run in self.cycles()])
        self.assertEqual(len(self.rows()), 2)
        self.tick(self.clock + 30 * 86400)
        self.assertEqual(self.rows(), [])


class LateTest(CheckBase):
    """A cycle that has not ended when its checks are due again: they are
    skipped, counted once each, and a group that keeps falling behind is told."""

    def setUp(self):
        super().setUp()
        self.config = dataclasses.replace(self.config, checks_late_after=3)

    def lag(self):
        return [dict(row) for row in self.ctx().conn.execute("SELECT * FROM cycle_lag")]

    def told(self):
        return [(n["event"], n["title"]) for n in self.ok("notification.list")["notifications"]]

    def report(self):
        return self.ok("system.health", role="viewer")

    def test_a_cycle_still_held_when_due_again_skips_and_is_counted_once(self):
        channel = self.channel()
        self.rule(channel, events=("late", "caught_up"))
        inventory = self.inventory()
        self.check(inventory, group="web", interval=60)
        start = self.clock
        run_id = self.tick()["queued"][0]
        # Within the interval nothing is skipped, however many ticks.
        self.tick(start + 30)
        self.assertEqual(self.lag(), [])
        # Due again within the slack of a tick: one due time skipped, for the
        # two hosts together, and counted once however many ticks see it.
        for at in (start + 55, start + 60, start + 100):
            self.assertEqual(self.tick(at)["queued"], [])
        [lag] = self.lag()
        self.assertEqual((lag["inventory_id"], lag["grp"], lag["streak"], lag["told"]),
                         (inventory["id"], "", 1, 0))
        self.assertEqual(self.ok("run.get", {"id": run_id})["options"]["skipped"], 2)
        self.tick(start + 125)
        [group] = self.report()["groups"]
        self.assertEqual((group["skipped"], group["active"], group["behind"]["streak"],
                          group["behind"]["told"]), (4, 1, 2, False))
        self.assertEqual(self.told(), [])
        # The third due time in a row: told, once.
        self.tick(start + 185)
        self.tick(start + 190)
        self.assertEqual(self.told(), [("late", "check cycles of lab")])
        payload = json.loads(self.ctx().conn.execute(
            "SELECT payload FROM notifications").fetchone()["payload"])
        from cockpit_fleet import notify
        message = notify.compose("it", [payload], "https://fleet:9090")
        self.assertEqual(message.title, "Fleet: In ritardo: check cycles of lab")
        self.assertIn("https://fleet:9090/fleet#/system", message.text)
        self.assertIn("3 due times skipped in a row", message.text)

        # It ends: what it skipped stays with what it took, and the group is
        # still behind until a cycle ends with nothing skipped.
        self.answer(run_id, {"web01": {"disk": disk(10)}, "web02": {"disk": disk(10)}})
        cycle = self.ctx().conn.execute("SELECT skipped FROM cycles WHERE run_id = ?",
                                        (run_id,)).fetchone()
        self.assertEqual(cycle["skipped"], 6)
        self.assertEqual(len(self.lag()), 1)
        states = self.states()
        self.assertEqual({state["next_at"] for state in states.values()}, {start + 60})
        # The next is due at once, and ends in time.
        run_id = self.tick(start + 200)["queued"][0]
        self.clock += 5
        self.answer(run_id, {"web01": {"disk": disk(10)}, "web02": {"disk": disk(10)}})
        self.assertEqual(self.lag(), [])
        self.assertEqual(self.told()[0], ("caught_up", "check cycles of lab"))
        [group] = self.report()["groups"]
        self.assertEqual((group["cycles"], group["skipped"], group["active"], group["behind"]),
                         (2, 6, 0, None))

    def test_a_shard_on_time_is_not_the_group_on_time(self):
        self.config = dataclasses.replace(self.config, checks_shard_size=1,
                                          checks_max_concurrent=2, checks_late_after=1)
        channel = self.channel()
        self.rule(channel, events=("late", "caught_up"))
        inventory = self.inventory()
        self.check(inventory, group="web", interval=60)
        start = self.clock
        first, second = self.tick()["queued"]
        self.tick(start + 65)
        self.assertEqual(self.told(), [("late", "check cycles of lab")])
        # The second shard ends: it was behind too, so nothing changes.
        self.answer(second, {name: {"disk": disk(10)} for name in
                             json.loads((paths.RUNS_DIR / second / "spec.json").read_text())
                             ["plan"]})
        self.assertEqual(len(self.lag()), 1)
        # A new cycle of one host ends in time, while the first still holds
        # the other: still behind.
        [again] = self.tick(start + 66)["queued"]
        self.answer(again, {name: {"disk": disk(10)} for name in
                            json.loads((paths.RUNS_DIR / again / "spec.json").read_text())
                            ["plan"]})
        self.assertEqual(len(self.lag()), 1)
        self.assertEqual(len(self.told()), 1)

    def test_a_cycle_released_without_an_end_frees_its_count(self):
        inventory = self.inventory()
        self.check(inventory, group="web", interval=60)
        run_id = self.tick()["queued"][0]
        self.tick(self.clock + 65)
        self.ctx().conn.execute("UPDATE runs SET status = 'error' WHERE id = ?", (run_id,))
        self.tick(self.clock + 1)
        skipped = {row["skipped"] for row in self.ctx().conn.execute(
            "SELECT skipped FROM check_states")}
        self.assertEqual(skipped, {0})

    def test_rules_about_cycles_are_for_everything_or_an_inventory(self):
        channel = self.channel()
        other = self.channel("other", secret="")
        inventory = self.inventory()
        check = self.check(inventory, group="web", interval=60)
        template = self.template()
        for scope, target in (("template", template["id"]), ("check", check["id"]),
                              ("group", inventory["id"])):
            self.assertEqual(self.code("rule.create", {
                "name": scope, "channel_id": channel["id"], "events": ["late"],
                "scope": scope, "scope_id": target,
                **({"group": "web"} if scope == "group" else {})}), "bad_request")
        self.rule(channel, events=("late",), scope="inventory", scope_id=inventory["id"])
        elsewhere = self.ok("inventory.create", {"name": "far", "kind": "managed",
                                                 "content": "x"})
        self.rule(other, events=("late",), scope="inventory", scope_id=elsewhere["id"])
        self.config = dataclasses.replace(self.config, checks_late_after=1)
        self.tick()
        self.tick(self.clock + 65)
        self.assertEqual([(n["event"], n["channel"]) for n in
                          self.ok("notification.list")["notifications"]], [("late", "ops")])


class UnpreparedTest(CheckBase):
    """A cycle that cannot be prepared leaves its checks unknown, which is no
    problem of the nodes: the cause is told once, and its end."""

    def told(self):
        return [(n["event"], n["title"]) for n in self.ok("notification.list")["notifications"]]

    def test_told_once_while_it_lasts_and_when_it_ends(self):
        channel = self.channel()
        self.rule(channel, events=("unprepared", "prepared"))
        inventory = self.inventory()
        self.check(inventory, group="web", interval=60, retry_interval=60)
        failing = mock.patch.object(jobs, "write_known_hosts",
                                    side_effect=Conflict("/home/x is not a directory"))
        with failing:
            self.assertEqual(self.tick()["queued"], [])
            self.tick(self.clock + 60)
        self.assertEqual({state["state"] for state in self.states().values()}, {"unknown"})
        self.assertEqual(self.told(), [("unprepared", "check cycles of lab")])
        [group] = self.ok("system.health", role="viewer")["groups"]
        self.assertEqual(group["unprepared"]["reason"], "/home/x is not a directory")
        payload = json.loads(self.ctx().conn.execute(
            "SELECT payload FROM notifications").fetchone()["payload"])
        from cockpit_fleet import notify
        message = notify.compose("it", [payload], "https://fleet:9090")
        self.assertEqual(message.title, "Fleet: Controlli bloccati: check cycles of lab")
        self.assertIn("/home/x is not a directory", message.text)
        # It can again: told, and the page says nothing more of it.
        self.assertEqual(len(self.tick(self.clock + 60)["queued"]), 1)
        self.assertEqual(self.told()[0], ("prepared", "check cycles of lab"))
        [group] = self.ok("system.health", role="viewer")["groups"]
        self.assertIsNone(group["unprepared"])
        self.assertEqual(len(self.told()), 2)

    def test_rules_about_it_are_for_everything_or_an_inventory(self):
        channel = self.channel()
        template = self.template()
        self.assertEqual(self.code("rule.create", {
            "name": "x", "channel_id": channel["id"], "events": ["unprepared"],
            "scope": "template", "scope_id": template["id"]}), "bad_request")


class HealthTest(CheckBase):
    def test_what_the_page_of_health_reads(self):
        self.config = dataclasses.replace(self.config, checks_memory_mb=400,
                                          checks_max_concurrent=1)
        inventory = self.inventory()
        self.check(inventory, group="web", interval=300)
        self.check(inventory, "reachability", interval=120)
        self.check(inventory, "cmdb", interval=86400)
        report = self.ok("system.health", role="viewer")
        self.assertEqual(report["memory"], {"budget": 200 * 1024 * 1024, "configured_mb": 400,
                                            "cycles_at_once": 2})
        groups = {(g["runs"], g["name"]): g for g in report["groups"]}
        self.assertEqual(sorted(groups), [("checks", ""), ("slow", "cmdb")])
        fast = groups[("checks", "")]
        self.assertEqual((fast["interval"], fast["checks"], fast["cycles"], fast["last"],
                          fast["sizing"]["basis"]), (120, 2, 0, None, "configured"))
        self.assertEqual([lane["lane"] for lane in report["queue"]], ["runs", "downloads", "checks", "slow"])
        self.assertTrue(report["database"]["bytes"] > 0)
        self.assertTrue(report["database"]["incremental"])
        queued = self.tick()["queued"]
        self.clock += 10
        report = self.ok("system.health", role="viewer")
        lanes = {lane["lane"]: lane for lane in report["queue"]}
        self.assertEqual((lanes["checks"]["running"], lanes["slow"]["running"]), (1, 1))
        for run_id in queued:
            (paths.RUNS_DIR / run_id / "events.jsonl").write_text("")
            self.finish(run_id, finished_at=self.clock, usage={"memory_peak": 50 * 1024 * 1024})
        self.ok("run.list")
        groups = {(g["runs"], g["name"]): g for g in self.ok("system.health")["groups"]}
        fast = groups[("checks", "")]
        self.assertEqual((fast["cycles"], fast["duration_max"], fast["memory_peak"],
                          fast["last"]["hosts"], fast["sizing"]["basis"]),
                         (1, 10, 50 * 1024 * 1024, 3, "measured"))
