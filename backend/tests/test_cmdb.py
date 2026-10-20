"""The inventory of hosts: the slow kinds and their cycles, what the cmdb and
updates checks keep beside their state, its changes, and the drift that
compliance checks find."""

import dataclasses
import json

from test_checks import CheckBase

from cockpit_fleet import catalog, paths, runner
from cockpit_fleet.errors import BadRequest

GIB = 2**30


def snapshot(memory=8 * GIB, packages=None, services=None, mac="52:54:00:00:00:01",
             addresses=("10.0.0.1/24",)):
    if packages is None:
        packages = {"openssl": "3.5.1-1", "bash": "5.2-1"}
    if services is None:
        services = {"ssh.service": ("running", "enabled"), "cron.service": ("running", "enabled")}
    return {"state": 0, "summary": "2 CPUs", "metrics": [{"name": "cpus", "value": 2}],
            "details": {}, "cmdb": {
                "hardware": {"vendor": "QEMU", "model": "Standard PC", "serial": "",
                             "bios": "1.16", "virtual": "kvm", "cpu_model": "AMD EPYC",
                             "cpus": 2, "memory": memory, "boot_time": 1_700_000_000},
                "disks": [{"name": "vda", "size": 20 * GIB, "model": ""}],
                "interfaces": [{"name": "eth0", "mac": mac, "addresses": list(addresses)}],
                "package_manager": "dpkg",
                "packages": [{"name": name, "version": version, "arch": "amd64"}
                             for name, version in packages.items()],
                "services": [{"name": name, "state": state, "enabled": enabled}
                             for name, (state, enabled) in services.items()]}}


def updates(security=1, plain=2):
    listing = [{"name": f"sec{i}", "current": "1", "available": "2", "security": True,
                "source": "Debian-Security:13/stable-security"} for i in range(security)]
    listing += [{"name": f"pkg{i}", "current": "1", "available": "2", "security": False,
                 "source": "Debian:13/stable"} for i in range(plain)]
    return {"state": 0, "summary": "updates",
            "metrics": [{"name": "updates", "value": security + plain},
                        {"name": "security_updates", "value": security}],
            "details": {"manager": "apt"}, "updates": {"manager": "apt", "updates": listing}}


class CmdbBase(CheckBase):
    def cycle(self, at=None):
        queued = self.tick(at)["queued"]
        self.assertEqual(len(queued), 1, queued)
        return queued[0]

    def host_id(self, name="web01"):
        return next(host["id"] for host in self.ok("host.list", role="viewer")["hosts"]
                    if host["name"] == name)


class SlowKindsTest(CmdbBase):
    def test_flags_are_checked_and_slow_kinds_are_told_apart(self):
        self.assertEqual(catalog.check_params("updates", {"refresh": True}), {"refresh": True})
        self.assertEqual(catalog.check_params("updates", {"refresh": False}), {})
        with self.assertRaises(BadRequest):
            catalog.check_params("updates", {"refresh": "yes"})
        self.assertTrue(catalog.slow("cmdb") and catalog.slow("updates")
                        and catalog.slow("compliance"))
        self.assertFalse(catalog.slow("disk") or catalog.slow("playbook"))
        entry = next(item for item in self.ok("check.catalog", role="viewer")["catalog"]
                     if item["name"] == "updates")
        self.assertEqual((entry["slow"], entry["interval"]), (True, 6 * 3600))

    def test_slow_kinds_go_in_cycles_of_their_own_in_a_lane_of_their_own(self):
        self.config = dataclasses.replace(self.config, checks_max_concurrent=1,
                                          checks_slow_timeout=1200)
        inventory = self.inventory()
        self.check(inventory, "disk", group="web")
        self.check(inventory, "cmdb", group="web")
        self.check(inventory, "updates", group="web")
        queued = self.tick()["queued"]
        self.assertEqual(len(queued), 3)
        specs = {run: json.loads((paths.RUNS_DIR / run / "spec.json").read_text())
                 for run in queued}
        kinds = {run: {entry["check"] for entries in spec["plan"].values() for entry in entries}
                 for run, spec in specs.items()}
        self.assertEqual(sorted(sorted(found) for found in kinds.values()),
                         [["cmdb"], ["disk"], ["updates"]])
        for run, spec in specs.items():
            self.assertEqual(spec["timeout"], 60 if kinds[run] == {"disk"} else 1200)
        status = {run["id"]: run["status"] for run in self.cycles()}
        # One cycle of each lane runs: the disks do not wait behind the CMDB.
        disk = next(run for run in queued if kinds[run] == {"disk"})
        self.assertEqual(status[disk], "running")
        self.assertEqual(sorted(status[run] for run in queued if run != disk),
                         ["queued", "running"])

    def test_cycles_keep_long_lists_in_their_events(self):
        self.assertEqual(runner.check_env({"timeout": 1200})["ANSIBLE_TASK_TIMEOUT"], "1200")
        self.assertEqual(runner.check_env({})["FLEET_EVENTS_MAX_ITEMS"], "50000")
        with self.assertRaises(BadRequest):
            runner.check_env({"timeout": 7200})

    def test_the_catalog_is_pipelined_a_playbook_of_the_user_as_its_project_says(self):
        self.assertEqual(runner.check_env({})["ANSIBLE_PIPELINING"], "True")
        self.assertNotIn("ANSIBLE_PIPELINING", runner.check_env({"playbook": "site.yml"}))


class CmdbTest(CmdbBase):
    def setUp(self):
        super().setUp()
        self.inventory_row = self.inventory()
        self.check(self.inventory_row, "cmdb", group="web", interval=6 * 3600, max_attempts=3)

    def collect(self, at=None, **web01):
        run = self.cycle(at)
        self.answer(run, {"web01": {"cmdb": snapshot(**web01)}, "web02": {"cmdb": snapshot()}})

    def test_the_first_snapshot_is_kept_and_records_no_change(self):
        self.collect()
        host = self.ok("cmdb.get", {"host_id": self.host_id()}, role="viewer")
        self.assertEqual(host["hardware"]["memory"], 8 * GIB)
        self.assertEqual(host["hardware"]["disks"], [{"name": "vda", "size": 20 * GIB,
                                                      "model": ""}])
        self.assertEqual([item["name"] for item in host["package_list"]], ["bash", "openssl"])
        self.assertEqual(host["service_list"][0], {"name": "cron.service", "state": "running",
                                                   "enabled": "enabled"})
        self.assertEqual(host["changes"], [])
        self.assertEqual(self.states()[("web01", "cmdb")]["state"], "ok")
        listed = {host["name"]: host for host in self.ok("cmdb.list", role="viewer")["hosts"]}
        self.assertEqual((listed["web01"]["packages"], listed["web01"]["services"]), (2, 2))
        self.assertEqual(listed["win01"]["hardware"], {})

    def test_changes_are_recorded_and_hardware_is_told_about_at_once(self):
        self.collect()
        self.collect(self.clock + 6 * 3600 + 1, memory=16 * GIB,
                     packages={"openssl": "3.5.4-1", "curl": "8.0"},
                     services={"ssh.service": ("stopped", "disabled"),
                               "nginx.service": ("running", "enabled")},
                     addresses=("10.0.0.9/24",))
        changes = self.ok("cmdb.get", {"host_id": self.host_id()}, role="viewer")["changes"]
        found = {(change["area"], change["name"]): (change["before"], change["after"])
                 for change in changes}
        self.assertEqual(found[("package", "openssl amd64")], ("3.5.1-1", "3.5.4-1"))
        self.assertEqual(found[("package", "bash amd64")], ("5.2-1", ""))
        self.assertEqual(found[("package", "curl amd64")], ("", "8.0"))
        self.assertEqual(found[("service", "ssh.service")], ("enabled", "disabled"))
        self.assertEqual(found[("service", "nginx.service")], ("", "enabled"))
        self.assertEqual(found[("service", "cron.service")], ("enabled", ""))
        self.assertEqual(found[("hardware", "memory")], ("8.0 GiB", "16.0 GiB"))
        self.assertEqual(found[("network", "eth0")], ("10.0.0.1/24", "10.0.0.9/24"))
        state = self.states()[("web01", "cmdb")]
        # With three attempts it is still hard at once: it lasts one cycle.
        self.assertEqual((state["state"], state["state_type"]), ("warning", "hard"))
        self.assertIn("hardware changed: memory 8.0 GiB → 16.0 GiB", state["summary"])
        self.assertEqual(self.states()[("web02", "cmdb")]["state"], "ok")
        self.collect(self.clock + 6 * 3600 + 1, memory=16 * GIB,
                     packages={"openssl": "3.5.4-1", "curl": "8.0"},
                     services={"ssh.service": ("stopped", "disabled"),
                               "nginx.service": ("running", "enabled")},
                     addresses=("10.0.0.9/24",))
        self.assertEqual(self.states()[("web01", "cmdb")]["state"], "ok")

    def test_the_memory_the_kernel_keeps_is_not_a_change(self):
        self.collect()
        self.collect(self.clock + 6 * 3600 + 1, memory=8 * GIB - 50 * 2**20)
        self.assertEqual(self.states()[("web01", "cmdb")]["state"], "ok")

    def test_what_a_node_sends_is_cleaned(self):
        run = self.cycle()
        bad = snapshot()
        bad["cmdb"]["hardware"]["cpus"] = "many"
        bad["cmdb"]["packages"] = [{"name": "ok", "version": "1"}, "junk", {"version": "2"},
                                   {"name": "x" * 1000, "version": "y\x00z"}]
        bad["cmdb"]["services"] = {"not": "a list"}
        bad["cmdb"]["interfaces"] = [{"name": "eth0", "addresses": "10.0.0.1"}]
        self.answer(run, {"web01": {"cmdb": bad}, "web02": {"cmdb": {"state": 0}}})
        host = self.ok("cmdb.get", {"host_id": self.host_id()}, role="viewer")
        self.assertIsNone(host["hardware"]["cpus"])
        self.assertEqual([item["name"] for item in host["package_list"]], ["ok", "x" * 256])
        self.assertEqual(host["package_list"][1]["version"], "yz")
        self.assertEqual(host["service_list"], [])
        self.assertEqual(host["hardware"]["interfaces"],
                         [{"name": "eth0", "mac": "", "addresses": []}])
        # A result without data keeps nothing and does not fail.
        self.assertEqual(self.ok("cmdb.get", {"host_id": self.host_id("web02")},
                                 role="viewer")["hardware"], {})

    def test_packages_are_found_across_hosts(self):
        self.collect()
        found = self.ok("cmdb.packages", {"name": "ssl"}, role="viewer")
        self.assertEqual(sorted((row["host"], row["version"]) for row in found["packages"]),
                         [("web01", "3.5.1-1"), ("web02", "3.5.1-1")])
        self.assertEqual(self.ok("cmdb.packages", {"name": "%"}, role="viewer")["packages"], [])

    def test_changes_older_than_a_year_go(self):
        self.collect()
        self.collect(self.clock + 6 * 3600 + 1, packages={"openssl": "3.5.4-1"})
        self.assertTrue(self.ok("cmdb.get", {"host_id": self.host_id()}, role="viewer")["changes"])
        self.tick(self.clock + 366 * 86400)
        self.assertEqual(self.ok("cmdb.get", {"host_id": self.host_id()},
                                 role="viewer")["changes"], [])


class UpdatesTest(CmdbBase):
    def test_security_updates_are_a_warning_and_their_list_is_kept(self):
        inventory = self.inventory()
        self.check(inventory, "updates", group="web", max_attempts=1)
        run = self.cycle()
        self.answer(run, {"web01": {"updates": updates(security=2, plain=3)},
                          "web02": {"updates": updates(security=0, plain=1)}})
        states = self.states()
        self.assertEqual(states[("web01", "updates")]["state"], "warning")
        self.assertIn("security_updates 2", states[("web01", "updates")]["summary"])
        self.assertEqual(states[("web02", "updates")]["state"], "ok")
        host = self.ok("cmdb.get", {"host_id": self.host_id()}, role="viewer")
        self.assertEqual([(item["name"], item["security"]) for item in host["update_list"]][:3],
                         [("sec0", True), ("sec1", True), ("pkg0", False)])
        self.assertEqual(host["hardware"]["update_manager"], "apt")
        listed = {host["name"]: host for host in self.ok("cmdb.list", role="viewer")["hosts"]}
        self.assertEqual((listed["web01"]["updates"], listed["web01"]["security_updates"]),
                         (5, 2))
        # The next search replaces the list.
        run = self.cycle(self.clock + 6 * 3600 + 1)
        self.answer(run, {"web01": {"updates": updates(security=0, plain=0)},
                          "web02": {"updates": updates(security=0, plain=0)}})
        self.assertEqual(self.ok("cmdb.get", {"host_id": self.host_id()},
                                 role="viewer")["update_list"], [])
        self.assertEqual(self.states()[("web01", "updates")]["state"], "ok")


class ComplianceTest(CmdbBase):
    def compliance(self, **extra):
        project = self.local_project()
        (self.root / "src" / "site" / "baseline.yml").write_text("- hosts: all\n")
        inventory = self.inventory()
        check = self.ok("check.create", {
            "name": "baseline", "kind": "compliance", "inventory_id": inventory["id"],
            "group": "web", "project_id": project["id"], "playbook": "baseline.yml",
            "max_attempts": 1, **extra})
        return inventory, check

    def events(self, run, lines):
        (paths.RUNS_DIR / run / "events.jsonl").write_text(
            "".join(json.dumps({"ts": self.clock, **line}) + "\n" for line in lines))
        self.finish(run, rc=2)
        self.ok("run.list")

    def test_it_runs_alone_in_check_and_diff_mode(self):
        _, check = self.compliance()
        self.assertEqual(check["effective_thresholds"],
                         {"changed": {"warning": 1, "critical": None}})
        run = self.cycle()
        spec = json.loads((paths.RUNS_DIR / run / "spec.json").read_text())
        self.assertEqual((spec["playbook"], spec["compliance"]), ("baseline.yml", True))
        options = next(item for item in self.cycles() if item["id"] == run)["options"]
        self.assertTrue(options["slow"])
        directory = paths.RUNS_DIR / run
        (directory / "inventory").mkdir(exist_ok=True)
        runtime = self.root / "rt"
        runtime.mkdir()
        spec.update(inventory="hosts.yml", inventory_source="managed")
        (directory / "inventory" / "hosts.yml").write_text("all:\n")
        argv = runner.build_check_argv(spec, directory, runtime, None)
        self.assertIn("--check", argv)
        self.assertIn("--diff", argv)
        self.assertEqual(argv[-1], str(directory / "project" / "baseline.yml"))

    def test_what_would_change_is_drift_and_its_diffs_are_for_operators(self):
        _, check = self.compliance()
        run = self.cycle()
        self.events(run, [
            {"event": "task_start", "task": "motd", "task_uuid": "t1"},
            {"event": "host_ok", "host": "web01", "task_uuid": "t1", "changed": True,
             "diff": "--- before\n+++ after\n-old\n+new\n"},
            {"event": "host_ok", "host": "web02", "task_uuid": "t1", "changed": False},
            {"event": "task_start", "task": "packages", "task_uuid": "t2"},
            {"event": "item_ok", "host": "web01", "task_uuid": "t2", "changed": True,
             "diff": "+ curl\n"},
            {"event": "item_ok", "host": "web01", "task_uuid": "t2", "changed": True,
             "diff": "+ vim\n"},
            {"event": "host_ok", "host": "web01", "task_uuid": "t2", "changed": True},
            {"event": "host_skipped", "host": "web02", "task_uuid": "t2", "changed": False},
        ])
        states = self.states()
        web01 = states[("web01", "baseline")]
        self.assertEqual((web01["state"], web01["details"]), ("warning",
                                                               {"tasks": ["motd", "packages"]}))
        self.assertIn("2 tasks would change", web01["summary"])
        self.assertEqual(states[("web02", "baseline")]["state"], "ok")
        host = self.host_id()
        self.assertEqual(self.code("drift.get", {"host_id": host}, role="viewer"), "forbidden")
        drift = self.ok("drift.get", {"host_id": host}, role="operator")["drift"]
        self.assertEqual(drift[0]["check_id"], check["id"])
        self.assertEqual(drift[0]["changes"],
                         [{"task": "motd", "diff": "--- before\n+++ after\n-old\n+new"},
                          {"task": "packages", "diff": "+ curl\n\n+ vim"}])
        listed = {host["name"]: host for host in self.ok("cmdb.list", role="viewer")["hosts"]}
        self.assertEqual((listed["web01"]["compliant"], listed["web02"]["compliant"],
                          listed["win01"]["compliant"]), (False, True, None))

    def test_a_task_that_fails_is_unknown_and_an_unreachable_host_suppressed(self):
        self.compliance()
        run = self.cycle()
        self.events(run, [
            {"event": "task_start", "task": "motd", "task_uuid": "t1"},
            {"event": "host_failed", "host": "web01", "task_uuid": "t1",
             "result": {"msg": "boom"}},
            {"event": "host_ok", "host": "web01", "task_uuid": "t2", "changed": True},
            {"event": "host_unreachable", "host": "web02", "result": {"msg": "timed out"}},
        ])
        states = self.states()
        self.assertEqual((states[("web01", "baseline")]["state"],
                          states[("web01", "baseline")]["summary"]), ("unknown", "boom"))
        self.assertTrue(states[("web02", "baseline")]["suppressed"])

    def test_it_is_exported_with_its_playbook(self):
        self.compliance()
        document = self.ok("config.export")
        item = next(check for check in document["checks"] if check["name"] == "baseline")
        self.assertEqual((item["kind"], item["project"], item["playbook"]),
                         ("compliance", "site", "baseline.yml"))
