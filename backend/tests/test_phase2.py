"""Projects, credentials, inventories, templates and the life cycle of runs,
with the run unit replaced by a fake that just writes a result."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cockpit_fleet import cli, jobs, journal, paths, resources, runner, runs, system  # noqa: F401
from cockpit_fleet.auth import Caller
from cockpit_fleet.config import Config
from cockpit_fleet.errors import BadRequest, Conflict, Unavailable


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("runs", "projects", "secrets", "src"):
            (self.root / name).mkdir()
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.active: set[str] = set()

        def fleet_unit(verb, run_id):
            (self.started if verb == "start" else self.stopped).append(run_id)
            if verb == "start":
                self.active.add(run_id)
            else:
                self.active.discard(run_id)

        patches = [
            mock.patch.object(paths, "RUNS_DIR", self.root / "runs"),
            mock.patch.object(paths, "SWEEP_STAMP", self.root / "sweep.stamp"),
            mock.patch.object(paths, "PROJECTS_DIR", self.root / "projects"),
            mock.patch.object(paths, "INVENTORIES_DIR", self.root / "inventories"),
            mock.patch.object(paths, "SECRETS_DIR", self.root / "secrets"),
            mock.patch.object(paths, "STORE_DIR", self.root / "store"),
            mock.patch.object(paths, "STORE_UPLOADS", self.root / "store-uploads"),
            mock.patch.object(paths, "STORE_FARMS", self.root / "store-farms"),
            mock.patch.object(paths, "STORE_LOCK", self.root / "store.lock"),
            mock.patch.object(jobs, "_fleet_unit", fleet_unit),
            mock.patch.object(jobs, "_unit_active", lambda run_id: run_id in self.active),
            mock.patch.object(journal, "send", lambda *a, **k: None),
            # The directories of a test are under /tmp, which units do not see.
            mock.patch.object(jobs, "HIDDEN_FROM_UNITS", ()),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(max_concurrent_runs=2, local_project_roots=(str(self.root / "src"),))

    def ctx(self, role="admin", user="anna"):
        ctx = cli.Context(Caller(1000, user, role), self.root / "fleet.db", self.root / "config.toml")
        ctx._config = self.config
        self.addCleanup(ctx.close)
        return ctx

    def call(self, name, params=None, role="admin"):
        return cli.dispatch(name, json.dumps(params or {}).encode(), self.ctx(role))

    def ok(self, name, params=None, role="admin"):
        answer = self.call(name, params, role)
        self.assertTrue(answer["ok"], answer)
        return answer["data"]

    def code(self, name, params=None, role="admin"):
        answer = self.call(name, params, role)
        self.assertFalse(answer["ok"], answer)
        return answer["error"]["code"]

    def finish(self, run_id, rc=0, **extra):
        """What fleet-run leaves behind when the unit ends."""
        (paths.RUNS_DIR / run_id / "result.json").write_text(json.dumps({"rc": rc, **extra}))
        self.active.discard(run_id)

    def local_project(self, name="site"):
        tree = self.root / "src" / name
        (tree / "inventories").mkdir(parents=True)
        (tree / "site.yml").write_text("- hosts: all\n")
        (tree / "inventories" / "hosts.yml").write_text("all:\n")
        (tree / ".git").mkdir()
        (tree / ".git" / "config").write_text("")
        return self.ok("project.create", {"name": name, "kind": "local", "path": str(tree)})

    def template(self, **extra):
        project = self.local_project()
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed",
                                                 "content": "all:\n  hosts:\n    a:\n"})
        return self.ok("template.create", {"name": "deploy", "project_id": project["id"],
                                           "playbook": "site.yml", "inventory_id": inventory["id"],
                                           **extra})


class ResourceTest(Base):
    def test_local_project_must_be_under_an_allowed_root(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        self.assertEqual(self.code("project.create", {"name": "x", "kind": "local",
                                                      "path": str(outside)}), "conflict")
        escape = str(self.root / "src" / ".." / "elsewhere")
        self.assertEqual(self.code("project.create", {"name": "y", "kind": "local",
                                                      "path": escape}), "conflict")
        self.assertEqual(self.code("project.create", {"name": "z", "kind": "local",
                                                      "path": "relative"}), "bad_request")

    def test_git_project_accepts_only_safe_urls_and_refs(self):
        good = {"name": "g", "kind": "git", "url": "https://example.org/a/b.git", "ref": "v1.0"}
        self.assertEqual(self.ok("project.create", good)["ref"], "v1.0")
        for url in ("file:///etc", "/srv/repo", "ext::sh -c id", "--upload-pack=id",
                    "http://example.org/a.git", "https://example.org/a b"):
            self.assertEqual(self.code("project.create", {**good, "name": url, "url": url}),
                             "bad_request", url)
        for ref in ("--exec=id", "a..b", "x.lock", "a b"):
            self.assertEqual(self.code("project.create", {**good, "name": ref, "ref": ref}),
                             "bad_request", ref)

    def test_names_are_unique_and_roles_enforced(self):
        self.local_project("one")
        tree = str(self.root / "src" / "one")
        self.assertEqual(self.code("project.create", {"name": "one", "kind": "local", "path": tree}),
                         "conflict")
        self.assertEqual(self.code("project.create", {"name": "two", "kind": "local", "path": tree},
                                   role="operator"), "forbidden")
        self.assertEqual(len(self.ok("project.list", role="viewer")["projects"]), 1)

    def test_credential_reports_which_secrets_exist_never_their_value(self):
        credential = self.ok("credential.create", {"name": "ssh", "kind": "machine",
                                                   "username": "maint"})
        self.assertEqual(credential["secrets"], {"private_key": False, "key_passphrase": False,
                                                 "password": False, "become_password": False})
        name = credential["secret_names"]["password"]
        (paths.SECRETS_DIR / f"{name}.cred").write_bytes(b"ciphertext")
        listed = self.ok("credential.list", role="viewer")["credentials"][0]
        self.assertTrue(listed["secrets"]["password"])
        self.assertNotIn("ciphertext", json.dumps(listed))
        # Its secrets must go first, so that none is left behind without an owner.
        self.assertEqual(self.code("credential.delete", {"id": credential["id"]}), "conflict")
        (paths.SECRETS_DIR / f"{name}.cred").unlink()
        self.ok("credential.delete", {"id": credential["id"]})

    def test_credential_kind_is_checked_where_it_is_used(self):
        vault = self.ok("credential.create", {"name": "v", "kind": "vault"})
        project = self.local_project()
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed", "content": ""})
        params = {"name": "t", "project_id": project["id"], "playbook": "site.yml",
                  "inventory_id": inventory["id"], "machine_credential_id": vault["id"]}
        self.assertEqual(self.code("template.create", params), "bad_request")

    def test_things_in_use_cannot_be_deleted(self):
        template = self.template()
        self.assertEqual(self.code("project.delete", {"id": template["project_id"]}), "conflict")
        self.assertEqual(self.code("inventory.delete", {"id": template["inventory_id"]}), "conflict")
        self.ok("template.delete", {"id": template["id"]})
        self.ok("project.delete", {"id": template["project_id"]})

    def test_template_validates_what_becomes_a_command_line(self):
        template = self.template()
        base = {"id": template["id"], "name": "deploy", "project_id": template["project_id"],
                "playbook": "site.yml", "inventory_id": template["inventory_id"]}
        for bad in ({"playbook": "../x.yml"}, {"playbook": "/etc/passwd"}, {"limit_hosts": "-e x"},
                    {"tags": ["ok", "--x"]}, {"forks": 51}, {"verbosity": 9}, {"extra_vars": [1]},
                    {"become": "yes"}, {"unknown": 1}):
            self.assertEqual(self.code("template.update", {**base, **bad}), "bad_request", bad)
        updated = self.ok("template.update", {**base, "tags": ["a"], "become": True, "forks": 10})
        self.assertEqual((updated["tags"], updated["become"], updated["forks"]), (["a"], True, 10))

    def test_managed_inventory_text_is_for_administrators_only(self):
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed",
                                                 "content": "a ansible_password=hunter2"})
        self.assertIn("hunter2", self.ok("inventory.get", {"id": inventory["id"]})["content"])
        self.assertEqual(self.ok("inventory.get", {"id": inventory["id"]}, role="operator")["content"], "")
        audit = self.ctx().conn.execute("SELECT params FROM audit").fetchall()
        self.assertNotIn("hunter2", json.dumps([row["params"] for row in audit]))

    def test_host_key_fingerprint_and_validation(self):
        key = "AAAAC3NzaC1lZDI1NTE5AAAAIN2fabcdefghijklmnopqrstuvwxyz0123456789ABCD"
        self.ok("hostkey.add", {"host": "[10.0.0.5]:2222", "key_type": "ssh-ed25519", "key": key})
        self.ok("hostkey.add", {"host": "[10.0.0.5]:2222", "key_type": "ssh-ed25519", "key": key})
        keys = self.ok("hostkey.list", role="viewer")["keys"]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0]["fingerprint"].startswith("SHA256:"))
        for bad in ({"host": "a b"}, {"host": "-oProxyCommand=x"}, {"key": "not base64!"},
                    {"key_type": "ssh-rsa x"}):
            params = {"host": "h", "key_type": "ssh-ed25519", "key": key, **bad}
            self.assertEqual(self.code("hostkey.add", params), "bad_request", bad)
        self.ok("hostkey.remove", {"id": keys[0]["id"]})
        self.assertEqual(self.code("hostkey.remove", {"id": keys[0]["id"]}), "not_found")


class RunTest(Base):
    def test_run_directory_holds_a_copy_of_the_project_and_its_inputs(self):
        credential = self.ok("credential.create", {"name": "ssh", "kind": "machine",
                                                   "username": "maint"})
        (paths.SECRETS_DIR / f"c-{credential['id']}-private_key.cred").write_bytes(b"KEY")
        self.ok("hostkey.add", {"host": "10.0.0.5", "key_type": "ssh-ed25519", "key": "AAAA"})
        template = self.template(machine_credential_id=credential["id"], limit_hosts="web*")
        run = self.ok("run.start", {"template_id": template["id"], "limit": "web01", "check": True},
                      role="operator")
        directory = paths.RUNS_DIR / run["id"]
        spec = json.loads((directory / "spec.json").read_text())

        self.assertEqual(run["status"], "running")
        self.assertEqual(self.started, [run["id"]])
        self.assertTrue((directory / "project" / "site.yml").is_file())
        self.assertFalse((directory / "project" / ".git").exists())
        self.assertEqual((directory / "creds" / "private_key").read_bytes(), b"KEY")
        self.assertFalse((directory / "creds" / "connection_password").exists())
        self.assertEqual((directory / "known_hosts").read_text(), "10.0.0.5 ssh-ed25519 AAAA\n")
        self.assertEqual((directory / "inventory" / "hosts.yml").read_text(), "all:\n  hosts:\n    a:\n")
        self.assertEqual(spec["limits"], ["web*", "web01"])
        self.assertNotIn("limit", spec)
        self.assertEqual((spec["user"], spec["check"], spec["diff"]), ("maint", True, True))
        self.assertEqual(oct(directory.stat().st_mode & 0o777), "0o700")

        # The specification is exactly what the unit will turn into a command line.
        (directory / "runtime").mkdir()
        plan = runner.limit_plan(spec, directory, directory / "runtime", None)
        self.assertEqual([command[command.index("--limit") + 1] for command, _ in plan],
                         ["web*", "web01"])
        limit = directory / "runtime" / "limit"
        argv = runner.build_argv(spec, directory, directory / "runtime", None, limit)
        self.assertEqual(argv[argv.index("--limit") + 1], f"@{limit}")
        self.assertIn("--check", argv)

    def test_outcome_is_collected_and_the_copy_removed(self):
        run = self.ok("run.start", {"template_id": self.template()["id"]})
        directory = paths.RUNS_DIR / run["id"]
        (directory / "events.jsonl").write_text(
            '{"event": "host_ok"}\n{"event": "stats", "hosts": {"a": {"ok": 2, "failures": 0}}}\n')
        (directory / "stdout.log").write_text("PLAY RECAP\n")
        self.finish(run["id"], rc=0)
        done = self.ok("run.get", {"id": run["id"]}, role="viewer")
        self.assertEqual((done["status"], done["rc"]), ("successful", 0))
        self.assertEqual(done["stats"], {"a": {"ok": 2, "failures": 0}})
        self.assertFalse((directory / "project").exists())
        self.assertFalse((directory / "creds").exists())
        self.assertTrue((directory / "stdout.log").exists())

    def test_failed_and_lost_runs(self):
        template = self.template()
        failed = self.ok("run.start", {"template_id": template["id"]})
        self.finish(failed["id"], rc=2)
        self.assertEqual(self.ok("run.get", {"id": failed["id"]})["status"], "failed")

        lost = self.ok("run.start", {"template_id": template["id"]})
        self.active.discard(lost["id"])
        # Just started: the unit may simply not be up yet.
        self.assertEqual(self.ok("run.get", {"id": lost["id"]})["status"], "running")
        self.ctx().conn.execute("UPDATE runs SET started_at = started_at - 60 WHERE id = ?", (lost["id"],))
        self.assertEqual(self.ok("run.get", {"id": lost["id"]})["status"], "error")

    def test_templates_tell_their_last_run(self):
        template = self.template()
        other = self.ok("template.create", {"name": "other", "project_id": template["project_id"],
                                            "playbook": "site.yml",
                                            "inventory_id": template["inventory_id"]})
        self.assertIsNone(other["last_run"])
        failed = self.ok("run.start", {"template_id": template["id"]})
        self.finish(failed["id"], rc=2)
        self.ok("run.get", {"id": failed["id"]})
        listed = {item["name"]: item for item in self.ok("template.list", role="viewer")["templates"]}
        self.assertEqual((listed["deploy"]["last_run"]["id"], listed["deploy"]["last_run"]["status"]),
                         (failed["id"], "failed"))
        self.assertIsNotNone(listed["deploy"]["last_run"]["finished_at"])
        self.assertIsNone(listed["other"]["last_run"])
        again = self.ok("run.start", {"template_id": template["id"]})
        listed = {item["name"]: item for item in self.ok("template.list")["templates"]}
        self.assertEqual((listed["deploy"]["last_run"]["id"], listed["deploy"]["last_run"]["status"]),
                         (again["id"], "running"))
        self.assertEqual(self.ok("template.update", {**{key: template[key] for key in (
            "id", "name", "project_id", "playbook", "inventory_id")}})["last_run"]["id"], again["id"])

    def test_queue_respects_the_concurrency_limit(self):
        template = self.template()
        ids = [self.ok("run.start", {"template_id": template["id"]})["id"] for _ in range(4)]
        statuses = lambda: [self.ok("run.get", {"id": i})["status"] for i in ids]  # noqa: E731
        self.assertEqual(statuses(), ["running", "running", "queued", "queued"])
        self.finish(ids[0])
        self.assertEqual(statuses(), ["successful", "running", "running", "queued"])
        self.assertEqual(self.started, ids[:3])

    def second(self, template, name="other", inventory_id=None, **extra):
        """Another template of the same project, on the same inventory unless told."""
        return self.ok("template.create", {"name": name, "project_id": template["project_id"],
                                           "playbook": "site.yml",
                                           "inventory_id": inventory_id or template["inventory_id"],
                                           **extra})

    def test_a_run_in_series_waits_for_those_before_it_on_its_inventory(self):
        first = self.template()
        other = self.second(first)
        elsewhere = self.ok("inventory.create", {"name": "inv2", "kind": "managed",
                                                 "content": "all:\n  hosts:\n    b:\n"})
        apart = self.second(first, "apart", elsewhere["id"])
        a = self.ok("run.start", {"template_id": first["id"]})
        b = self.ok("run.start", {"template_id": other["id"], "serial": True}, role="operator")
        self.assertEqual((a["status"], b["status"]), ("running", "queued"))
        self.assertTrue(b["options"]["serial"])
        self.assertEqual(self.ok("run.get", {"id": b["id"]}, role="viewer")["waiting_for"],
                         {"id": a["id"], "title": "deploy"})
        # It takes no place meanwhile: a run on another inventory starts.
        c = self.ok("run.start", {"template_id": apart["id"]})
        self.assertEqual(c["status"], "running")
        # A third in series waits for the second, which is the nearer.
        self.finish(c["id"])
        d = self.ok("run.start", {"template_id": first["id"], "serial": True})
        listed = {run["id"]: run for run in self.ok("run.list")["runs"]}
        self.assertEqual(listed[d["id"]]["waiting_for"]["id"], b["id"])
        self.assertIsNone(listed[a["id"]]["waiting_for"])
        # A failure does not hold it back: the order is all it asks for.
        self.finish(a["id"], rc=2)
        self.assertEqual([self.ok("run.get", {"id": i})["status"] for i in (a["id"], b["id"], d["id"])],
                         ["failed", "running", "queued"])
        self.finish(b["id"])
        self.assertEqual(self.ok("run.get", {"id": d["id"]})["status"], "running")
        self.assertEqual(self.started, [a["id"], c["id"], b["id"], d["id"]])

    def test_a_run_not_in_series_does_not_wait(self):
        first = self.template()
        a = self.ok("run.start", {"template_id": first["id"], "serial": True})
        b = self.ok("run.start", {"template_id": self.second(first)["id"]})
        self.assertEqual((a["status"], b["status"]), ("running", "running"))
        self.assertNotIn("serial", b["options"])

    def test_a_run_in_series_does_not_wait_for_an_approval(self):
        first = self.template()
        gated = self.second(first, "gated", needs_approval=True)
        waiting = self.ok("run.start", {"template_id": gated["id"], "serial": True})
        self.assertEqual(waiting["status"], "pending_approval")
        later = self.ok("run.start", {"template_id": first["id"], "serial": True})
        self.assertEqual(later["status"], "running")
        # Approved while a run launched after it runs: it waits for that one.
        approved = self.ok("approval.approve", {"id": waiting["id"]})
        self.assertEqual(approved["status"], "queued")
        self.assertEqual(self.ok("run.get", {"id": waiting["id"]})["waiting_for"]["id"],
                         later["id"])
        self.finish(later["id"])
        self.assertEqual(self.ok("run.get", {"id": waiting["id"]})["status"], "running")

    def test_a_run_chosen_in_the_same_pass_holds_back_one_in_series(self):
        first = self.template()
        elsewhere = self.ok("inventory.create", {"name": "inv2", "kind": "managed",
                                                 "content": "all:\n  hosts:\n    b:\n"})
        apart = self.second(first, "apart", elsewhere["id"])
        busy = [self.ok("run.start", {"template_id": apart["id"]})["id"] for _ in range(2)]
        serial = self.ok("run.start", {"template_id": first["id"], "serial": True})
        urgent = self.ok("run.start", {"template_id": first["id"]})
        self.ctx().conn.execute("UPDATE runs SET priority = 5 WHERE id = ?", (urgent["id"],))
        for run_id in busy:
            self.finish(run_id)
        # Both places are free in one pass: the urgent one goes first, alone.
        self.assertEqual(self.ok("run.get", {"id": urgent["id"]})["status"], "running")
        got = self.ok("run.get", {"id": serial["id"]})
        self.assertEqual((got["status"], got["waiting_for"]["id"]), ("queued", urgent["id"]))

    def test_an_adhoc_command_in_series(self):
        first = self.template()
        a = self.ok("run.start", {"template_id": first["id"]})
        adhoc = self.ok("run.adhoc", {"inventory_id": first["inventory_id"], "limit": "all",
                                      "module": "ansible.builtin.ping", "serial": True})
        self.assertEqual((adhoc["status"], adhoc["options"]["serial"]), ("queued", True))
        self.finish(a["id"])
        self.assertEqual(self.ok("run.get", {"id": adhoc["id"]})["status"], "running")

    def test_cancel_a_queued_and_a_running_run(self):
        template = self.template()
        ids = [self.ok("run.start", {"template_id": template["id"]})["id"] for _ in range(3)]
        self.assertEqual(self.ok("run.cancel", {"id": ids[2]}, role="operator")["status"], "canceled")
        self.assertEqual(self.stopped, [])

        def stop(verb, run_id):
            self.stopped.append(run_id)
            self.finish(run_id, rc=-15, terminated=True)

        with mock.patch.object(jobs, "_fleet_unit", stop):
            self.assertEqual(self.ok("run.cancel", {"id": ids[0]})["status"], "canceled")
        self.assertEqual(self.stopped, [ids[0]])
        self.assertEqual(self.code("run.cancel", {"id": ids[0]}), "bad_request")
        self.assertEqual(self.code("run.cancel", {"id": ids[1]}, role="viewer"), "forbidden")

    def test_a_run_whose_unit_cannot_start_ends_and_leaves_nothing_behind(self):
        template = self.template()

        def start(verb, run_id):
            raise Unavailable("fleet-unit: no such unit")

        with mock.patch.object(jobs, "_fleet_unit", start):
            run = self.ok("run.start", {"template_id": template["id"]})
        got = self.ok("run.get", {"id": run["id"]})
        self.assertEqual((got["status"], got["error"]), ("error", "fleet-unit: no such unit"))
        self.assertFalse((paths.RUNS_DIR / run["id"]).exists())
        # The next one is not held back by it.
        with mock.patch.object(jobs, "_fleet_unit", start):
            self.ok("run.start", {"template_id": template["id"]})
        self.assertEqual(self.ok("run.start", {"template_id": template["id"]})["status"],
                         "running")

    def test_log_is_read_in_chunks_and_events_by_whole_lines(self):
        run = self.ok("run.start", {"template_id": self.template()["id"]})
        directory = paths.RUNS_DIR / run["id"]
        (directory / "stdout.log").write_bytes(b"one\ntwo\n")
        (directory / "events.jsonl").write_bytes(b'{"a": 1}\n{"b": 2}\n{"c"')
        first = self.ok("run.log", {"id": run["id"]}, role="operator")
        self.assertEqual((first["text"], first["offset"]), ("one\ntwo\n", 8))
        self.assertEqual(self.ok("run.log", {"id": run["id"], "offset": 4})["text"], "two\n")
        events = self.ok("run.events", {"id": run["id"]})
        self.assertEqual((events["text"], events["offset"]), ('{"a": 1}\n{"b": 2}\n', 18))
        self.assertEqual(self.code("run.log", {"id": run["id"]}, role="viewer"), "forbidden")
        self.assertEqual(self.code("run.log", {"id": "../../etc"}), "bad_request")

    def test_a_link_or_a_fifo_the_run_left_is_read_as_missing(self):
        run = self.ok("run.start", {"template_id": self.template()["id"]})
        directory = paths.RUNS_DIR / run["id"]
        elsewhere = self.root / "fleet.db.copy"
        elsewhere.write_text("not the run's")
        (directory / "stdout.log").symlink_to(elsewhere)
        os.mkfifo(directory / "events.jsonl")
        os.mkfifo(directory / "result.json")
        self.assertEqual(self.ok("run.log", {"id": run["id"]})["text"], "")
        self.assertEqual(self.ok("run.events", {"id": run["id"]})["text"], "")
        self.assertEqual(jobs._log_tail(directory), "no output")
        self.assertEqual(jobs._last_stats(directory), {})
        with self.assertRaises(FileNotFoundError):
            jobs.read_run_file(directory, "result.json")
        (directory / "big").write_bytes(b"x" * 11)
        with self.assertRaises(ValueError):
            jobs.read_run_file(directory, "big", 10)

    def test_history_lists_newest_first_and_filters(self):
        template = self.template()
        ids = [self.ok("run.start", {"template_id": template["id"]})["id"] for _ in range(3)]
        self.finish(ids[0], rc=1)
        listed = self.ok("run.list", {"limit": 2}, role="viewer")
        self.assertEqual([run["id"] for run in listed["runs"]], [ids[2], ids[1]])
        self.assertTrue(listed["more"])
        older = self.ok("run.list", {"before": listed["runs"][-1]["cursor"]})
        self.assertEqual([run["id"] for run in older["runs"]], [ids[0]])
        self.assertEqual([r["id"] for r in self.ok("run.list", {"status": "failed"})["runs"]], [ids[0]])
        self.assertEqual(self.code("run.list", {"status": "nope"}), "bad_request")

    def test_inventory_job_stores_hosts_without_their_variables(self):
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed", "content": "x"})
        run_id = self.ok("inventory.parse", {"id": inventory["id"]}, role="operator")["run_id"]
        listing = {
            "_meta": {"hostvars": {"web01": {"ansible_host": "10.0.0.1", "ansible_password": "hunter2",
                                             "ansible_port": 2222, "ansible_connection": "ssh"},
                                   "db01": {}}},
            "all": {"children": ["ungrouped", "prod"]},
            "prod": {"children": ["web"], "hosts": ["db01"]},
            "web": {"hosts": ["web01"]},
        }
        (paths.RUNS_DIR / run_id / "inventory.json").write_text(json.dumps(listing))
        self.finish(run_id)
        got = self.ok("inventory.get", {"id": inventory["id"]}, role="viewer")
        self.assertEqual(got["hosts"], [
            {"name": "db01", "address": "", "port": None, "connection": "", "groups": ["prod"]},
            {"name": "web01", "address": "10.0.0.1", "port": 2222, "connection": "ssh",
             "groups": ["prod", "web"]},
        ])
        self.assertNotIn("hunter2", json.dumps(got))
        self.assertEqual(self.ok("inventory.list")["inventories"][0]["host_count"], 2)

    def test_why_an_inventory_was_not_read_is_for_administrators(self):
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed", "content": "x"})
        run_id = self.ok("inventory.parse", {"id": inventory["id"]}, role="operator")["run_id"]
        quoted = "ERROR! could not parse\n  4  ansible_password: hunter2\n"
        (paths.RUNS_DIR / run_id / "stdout.log").write_text(quoted)
        self.finish(run_id, rc=1)
        self.assertIn("hunter2", self.ok("inventory.get", {"id": inventory["id"]})["parse_error"])
        for role in ("viewer", "operator"):
            for name in ("inventory.get", "inventory.list"):
                self.assertNotIn("hunter2", json.dumps(self.ok(name, {"id": inventory["id"]}
                                                               if name == "inventory.get" else {},
                                                               role=role)))
        self.assertEqual(self.code("run.log", {"id": run_id}, role="operator"), "forbidden")
        self.assertIn("hunter2", self.ok("run.log", {"id": run_id})["text"])

    def test_failed_inventory_job_keeps_the_error_for_the_interface(self):
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed", "content": "x"})
        run_id = self.ok("inventory.parse", {"id": inventory["id"]})["run_id"]
        (paths.RUNS_DIR / run_id / "stdout.log").write_text("ERROR! could not parse")
        self.finish(run_id, rc=1)
        self.assertIn("could not parse", self.ok("inventory.get", {"id": inventory["id"]})["parse_error"])

    def test_sync_job_replaces_the_project_tree(self):
        project = self.ok("project.create", {"name": "g", "kind": "git",
                                             "url": "https://example.org/a.git"})
        self.assertEqual(self.code("run.start", {"template_id": "tnone"}), "not_found")
        for revision, content in (("abc123", "one"), ("def456", "two")):
            run_id = self.ok("project.sync", {"id": project["id"]})["run_id"]
            spec = json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())
            self.assertEqual((spec["kind"], spec["url"], spec["ref"]),
                             ("sync", "https://example.org/a.git", "main"))
            checkout = paths.RUNS_DIR / run_id / "checkout"
            checkout.mkdir()
            (checkout / "site.yml").write_text(content)
            self.finish(run_id, revision=revision)
            listed = self.ok("project.list")["projects"][0]
            self.assertEqual(listed["revision"], revision)
            self.assertEqual((paths.PROJECTS_DIR / project["id"] / "tree" / "site.yml").read_text(), content)
        self.assertEqual(self.ok("project.files", {"id": project["id"]})["files"], ["site.yml"])


class ClosingPhase2Test(Base):
    def test_credential_and_project_can_be_edited(self):
        credential = self.ok("credential.create", {"name": "ssh", "kind": "machine", "username": "a"})
        updated = self.ok("credential.update", {"id": credential["id"], "name": "ssh2", "username": "b"})
        self.assertEqual((updated["name"], updated["username"], updated["kind"]), ("ssh2", "b", "machine"))
        self.assertEqual(self.code("credential.update", {"id": credential["id"], "name": "x",
                                                         "username": "-oProxy"}), "bad_request")

        project = self.ok("project.create", {"name": "g", "kind": "git",
                                             "url": "https://example.org/a.git"})
        changed = self.ok("project.update", {"id": project["id"], "name": "g2",
                                             "url": "https://example.org/b.git", "ref": "v2"})
        self.assertEqual((changed["name"], changed["url"], changed["ref"]),
                         ("g2", "https://example.org/b.git", "v2"))
        # A git project cannot be turned into a local one: path is not a git parameter.
        self.assertEqual(self.code("project.update", {"id": project["id"], "name": "g",
                                                      "path": "/etc"}), "bad_request")
        self.assertEqual(self.code("project.update", {"id": project["id"], "name": "g",
                                                      "url": "file:///etc"}), "bad_request")

    def test_several_vaults_reach_the_command_line_with_their_identifiers(self):
        dev = self.ok("credential.create", {"name": "dev", "kind": "vault", "vault_id": "dev"})
        prod = self.ok("credential.create", {"name": "prod", "kind": "vault", "vault_id": "prod"})
        for credential in (dev, prod):
            (paths.SECRETS_DIR / f"c-{credential['id']}-vault_password.cred").write_bytes(b"x")
        template = self.template(vault_credential_ids=[prod["id"], dev["id"]])
        self.assertEqual(template["vault_credential_ids"], [prod["id"], dev["id"]])
        run = self.ok("run.start", {"template_id": template["id"]})
        directory = paths.RUNS_DIR / run["id"]
        spec = json.loads((directory / "spec.json").read_text())
        self.assertEqual(spec["vaults"], [{"file": "vault_password_0", "id": "prod"},
                                          {"file": "vault_password_1", "id": "dev"}])
        # What the unit sees once systemd has decrypted creds/ into fleet_* files.
        creds = self.root / "decrypted"
        creds.mkdir()
        for name in ("vault_password_0", "vault_password_1"):
            self.assertTrue((directory / "creds" / name).is_file())
            (creds / f"fleet_{name}").write_text("x")
        (directory / "runtime").mkdir()
        argv = runner.build_argv(spec, directory, directory / "runtime", creds)
        ids = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--vault-id"]
        self.assertEqual(ids, [f"prod@{creds / 'fleet_vault_password_0'}",
                               f"dev@{creds / 'fleet_vault_password_1'}"])

    def test_vaults_used_together_need_distinct_identifiers(self):
        one = self.ok("credential.create", {"name": "one", "kind": "vault"})
        two = self.ok("credential.create", {"name": "two", "kind": "vault"})
        machine = self.ok("credential.create", {"name": "m", "kind": "machine"})
        template = self.template(vault_credential_ids=[one["id"]])
        base = {"id": template["id"], "name": "deploy", "project_id": template["project_id"],
                "playbook": "site.yml", "inventory_id": template["inventory_id"]}
        for bad in ([one["id"], two["id"]], [one["id"], one["id"]], [machine["id"]], ["../x"]):
            self.assertEqual(self.code("template.update", {**base, "vault_credential_ids": bad}),
                             "bad_request", bad)
        self.assertEqual(self.code("credential.delete", {"id": one["id"]}), "conflict")
        cleared = self.ok("template.update", base)
        self.assertEqual(cleared["vault_credential_ids"], [])
        self.ok("credential.delete", {"id": one["id"]})

    def test_inventory_scan_marks_new_trusted_and_changed_keys(self):
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed", "content": "x"})
        hosts = [
            {"name": "a", "address": "10.0.0.1", "port": None, "connection": "", "groups": []},
            {"name": "b", "address": "10.0.0.2", "port": None, "connection": "ssh", "groups": []},
            {"name": "c", "address": "10.0.0.3", "port": 2222, "connection": "", "groups": []},
            {"name": "win", "address": "10.0.0.9", "port": None, "connection": "winrm", "groups": []},
            {"name": "self", "address": "", "port": None, "connection": "local", "groups": []},
            {"name": "named.example.org", "address": "", "port": None, "connection": "", "groups": []},
        ]
        self.ctx().conn.execute("UPDATE inventories SET hosts = ? WHERE id = ?",
                                (json.dumps(hosts), inventory["id"]))
        self.ok("hostkey.add", {"host": "10.0.0.1", "key_type": "ssh-ed25519", "key": "AAAA"})
        self.ok("hostkey.add", {"host": "10.0.0.2", "key_type": "ssh-ed25519", "key": "AAAA"})
        asked = []

        def keyscan(names, port):
            asked.append((sorted(names), port))
            if port == 2222:
                return [("[10.0.0.3]:2222", "ssh-ed25519", "CCCC")]
            return [("10.0.0.1", "ssh-ed25519", "AAAA"), ("10.0.0.2", "ssh-ed25519", "BBBB")]

        with mock.patch.object(resources, "_keyscan", keyscan):
            result = self.ok("hostkey.scan", {"inventory_id": inventory["id"]})
        self.assertEqual(asked, [(["10.0.0.1", "10.0.0.2", "named.example.org"], 22),
                                 (["10.0.0.3"], 2222)])
        self.assertEqual({key["host"]: key["state"] for key in result["keys"]},
                         {"10.0.0.1": "trusted", "10.0.0.2": "changed", "[10.0.0.3]:2222": "new"})
        self.assertEqual(result["unanswered"], ["named.example.org"])

    def test_a_changed_host_key_needs_an_explicit_replacement(self):
        key = {"host": "10.0.0.2", "key_type": "ssh-ed25519"}
        self.ok("hostkey.add", {**key, "key": "AAAA"})
        self.ok("hostkey.add", {**key, "key": "AAAA"})
        self.assertEqual(self.code("hostkey.add", {**key, "key": "BBBB"}), "conflict")
        self.ok("hostkey.add", {**key, "key": "BBBB", "replace": True})
        self.assertEqual(len(self.ok("hostkey.list")["keys"]), 1)

    def test_passphrase_protected_key_goes_through_an_agent(self):
        creds = self.root / "decrypted"
        runtime = self.root / "runtime"
        creds.mkdir()
        runtime.mkdir()
        (creds / "fleet_private_key").write_text("KEY")
        self.assertFalse(runner.needs_agent(creds))
        (creds / "fleet_key_passphrase").write_text("s3cret-phrase")
        self.assertTrue(runner.needs_agent(creds))
        agent, add, env, socket = runner.agent_plan(creds, runtime)
        self.assertEqual(agent, ["/usr/bin/ssh-agent", "-D", "-a", socket])
        self.assertEqual(add[-1], str(creds / "fleet_private_key"))
        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
        self.assertNotIn("s3cret-phrase", json.dumps([agent, add, env]))
        self.assertEqual(env["SSH_ASKPASS"], "/usr/libexec/cockpit-fleet/fleet-askpass")
        self.assertEqual(env["FLEET_ASKPASS_FILE"], str(creds / "fleet_key_passphrase"))
        # The key is offered by the agent, so it is not passed as a file too.
        template = self.template()
        run = self.ok("run.start", {"template_id": template["id"]})
        directory = paths.RUNS_DIR / run["id"]
        spec = json.loads((directory / "spec.json").read_text())
        argv = runner.build_argv(spec, directory, runtime, creds)
        self.assertNotIn("--private-key", argv)

    def test_migration_moves_the_single_vault_into_the_list(self):
        import sqlite3

        from cockpit_fleet import db
        path = self.root / "old.db"
        with mock.patch.object(db, "MIGRATIONS", db.MIGRATIONS[:2]):
            conn = db.connect(path)
        conn.execute("INSERT INTO credentials (id, name, kind, created_at) VALUES ('c1', 'v', 'vault', 1)")
        conn.execute("INSERT INTO projects (id, name, kind, created_at) VALUES ('p1', 'p', 'git', 1)")
        conn.execute("INSERT INTO inventories (id, name, kind, created_at) VALUES ('i1', 'i', 'managed', 1)")
        conn.execute("INSERT INTO templates (id, name, project_id, playbook, inventory_id,"
                     " vault_credential_id, created_at, updated_at)"
                     " VALUES ('t1', 't', 'p1', 'site.yml', 'i1', 'c1', 1, 1)")
        conn.close()
        conn = db.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(db.schema_version(conn), len(db.MIGRATIONS))
        rows = conn.execute("SELECT template_id, credential_id, position FROM template_vaults")
        self.assertEqual([tuple(row) for row in rows], [("t1", "c1", 0)])
        self.assertIsInstance(conn, sqlite3.Connection)


class AskpassTest(unittest.TestCase):
    HELPER = Path(__file__).resolve().parents[1] / "libexec" / "fleet-askpass"

    def ask(self, prompt, **env):
        import subprocess
        return subprocess.run([str(self.HELPER), prompt], env={"PATH": "/usr/bin:/bin", **env},
                              capture_output=True, text=True, check=True).stdout

    def test_prints_the_secret_or_the_user_name_depending_on_the_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret"
            secret.write_text("s3cret")
            env = {"FLEET_ASKPASS_FILE": str(secret), "FLEET_ASKPASS_USER": "deploy"}
            self.assertEqual(self.ask("Enter passphrase for key '/x':", **env), "s3cret")
            self.assertEqual(self.ask("Password for 'https://deploy@example.org':", **env), "s3cret")
            self.assertEqual(self.ask("Username for 'https://example.org':", **env), "deploy\n")


class SnapshotTest(unittest.TestCase):
    def test_symlinks_are_copied_as_links_and_git_is_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree, copy = Path(tmp) / "tree", Path(tmp) / "copy"
            (tree / "roles" / "a").mkdir(parents=True)
            (tree / ".git").mkdir()
            (tree / ".git" / "HEAD").write_text("x")
            (tree / "roles" / "a" / "main.yml").write_text("task")
            os.symlink("/etc/shadow", tree / "leak")
            os.symlink("roles", tree / "linked-dir")
            jobs.snapshot(tree, copy)
            self.assertEqual((copy / "roles" / "a" / "main.yml").read_text(), "task")
            self.assertFalse((copy / ".git").exists())
            self.assertTrue((copy / "leak").is_symlink())
            self.assertEqual(os.readlink(copy / "leak"), "/etc/shadow")
            self.assertTrue((copy / "linked-dir").is_symlink())

    def test_a_tree_reached_through_a_link_is_not_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "elsewhere").mkdir()
            (root / "elsewhere" / "fleet.db").write_text("not the project's")
            (root / "tree").symlink_to(root / "elsewhere")
            with self.assertRaises(Conflict):
                jobs.snapshot(root / "tree", root / "copy")
            (root / "src").mkdir()
            (root / "src" / "sub").symlink_to(root / "elsewhere")
            with self.assertRaises(Conflict):
                jobs.snapshot(root / "src" / "sub" , root / "copy2")
            self.assertFalse((root / "copy" / "fleet.db").exists())

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_what_cannot_be_read_is_left_out_and_told_in_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree, run = Path(tmp) / "tree", Path(tmp) / "run"
            (tree / "logs").mkdir(parents=True)
            (tree / "logs" / "a.log").write_text("x")
            (tree / "reports").mkdir()
            (tree / "reports" / "secret.log").write_text("x")
            (tree / "site.yml").write_text("play")
            # Readable but not searchable: its names are listed, nothing of them is read.
            (tree / "tools").mkdir()
            (tree / "tools" / "x").write_text("x")
            (tree / "logs").chmod(0o000)
            (tree / "reports" / "secret.log").chmod(0o000)
            (tree / "tools").chmod(0o400)
            try:
                skipped = jobs.snapshot(tree, run / "project")
            finally:
                (tree / "logs").chmod(0o700)
                (tree / "tools").chmod(0o700)
            self.assertEqual(skipped, ["logs/", "reports/secret.log", "tools/x"])
            self.assertEqual((run / "project" / "site.yml").read_text(), "play")
            self.assertFalse((run / "project" / "reports" / "secret.log").exists())
            self.assertEqual((run / "stdout.log").read_text().splitlines(), [
                "fleet: 3 paths of the project were left out of this run:"
                " the service account cannot read them",
                "  logs/", "  reports/secret.log", "  tools/x"])
            # The collections of a project are told apart from the project.
            (tree / "logs").chmod(0o000)
            try:
                jobs.snapshot(tree, Path(tmp) / "c" / "collections", "collections")
            finally:
                (tree / "logs").chmod(0o700)
            self.assertEqual((Path(tmp) / "c" / "stdout.log").read_text().splitlines()[0],
                             "fleet: 2 paths of the collections were left out of this run:"
                             " the service account cannot read them")
            # Nothing is said of a project read whole.
            (tree / "reports" / "secret.log").chmod(0o600)
            self.assertEqual(jobs.snapshot(tree, Path(tmp) / "whole" / "project"), [])
            self.assertFalse((Path(tmp) / "whole" / "stdout.log").exists())

    def test_oversized_project_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            for index in range(5):
                (tree / f"f{index}").write_text("x")
            with mock.patch.object(jobs, "MAX_PROJECT_FILES", 3), self.assertRaises(Conflict):
                jobs.snapshot(tree, Path(tmp) / "copy")


class RunnerPhase2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run"
        self.runtime = Path(self.tmp.name) / "runtime"
        (self.run / "project" / "roles").mkdir(parents=True)
        self.runtime.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def test_project_config_is_filtered(self):
        (self.run / "project" / "ansible.cfg").write_text(
            "[defaults]\n"
            "collections_path = .:/usr/share/evil:../outside\n"
            "roles_path = roles:/etc\n"
            "vault_password_file = .vault-pass\n"
            "callback_plugins = /tmp/x\n"
            "host_key_checking = False\n"
            "inventory = hosts\n"
            "gathering = smart\n"
            "[ssh_connection]\n"
            "pipelining = True\n"
            "ssh_args = -o StrictHostKeyChecking=no\n"
            "[privilege_escalation]\n"
            "become = True\n"
        )
        text, collections = runner.generate_config(self.run / "project")
        project = str((self.run / "project").resolve())
        self.assertEqual(collections, [project])
        self.assertIn(f"roles_path = {project}/roles\n", text)
        self.assertIn("gathering = smart", text)
        self.assertIn("pipelining = True", text)
        self.assertIn("become = True", text)
        for forbidden in ("vault_password_file", "callback_plugins", "host_key_checking",
                          "ssh_args", "inventory", "/usr/share/evil", "/etc", "outside"):
            self.assertNotIn(forbidden, text)

    def test_missing_or_broken_project_config_gives_an_empty_one(self):
        self.assertEqual(runner.generate_config(self.run / "project"), ("[defaults]\n", []))
        (self.run / "project" / "ansible.cfg").write_text("not an ini file [[[")
        self.assertEqual(runner.generate_config(self.run / "project"), ("[defaults]\n", []))

    def test_sync_commands_cannot_be_given_options_or_local_transports(self):
        spec = {"kind": "sync", "url": "https://example.org/a.git", "ref": "main"}
        commands, env = runner.build_sync(spec, self.run, self.runtime, None)
        fetch = commands[1]
        self.assertEqual(fetch[-3:], ["--", "https://example.org/a.git", "main"])
        self.assertIn("--no-recurse-submodules", fetch)
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "https:ssh")
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertIn("StrictHostKeyChecking=yes", env["GIT_SSH_COMMAND"])
        for command in commands:
            self.assertIn("core.hooksPath=/dev/null", command)
        for bad in ({"url": "file:///x"}, {"url": "--upload-pack=x"}, {"ref": "--x"}, {"ref": None}):
            with self.assertRaises(BadRequest, msg=bad):
                runner.build_sync({**spec, **bad}, self.run, self.runtime, None)

    def test_sync_token_is_read_by_a_helper_never_put_in_argv_or_env(self):
        creds = Path(self.tmp.name) / "creds"
        creds.mkdir()
        (creds / "fleet_git_token").write_text("s3cret-token")
        spec = {"kind": "sync", "url": "https://example.org/a.git", "ref": "main", "user": "deploy"}
        commands, env = runner.build_sync(spec, self.run, self.runtime, creds)
        self.assertNotIn("s3cret-token", json.dumps([commands, env]))
        self.assertEqual(env["GIT_ASKPASS"], "/usr/libexec/cockpit-fleet/fleet-askpass")
        self.assertEqual(env["FLEET_ASKPASS_USER"], "deploy")
        self.assertEqual(env["FLEET_ASKPASS_FILE"], str(creds / "fleet_git_token"))
        # Nothing executable is written to the scratch directory: it is noexec.
        self.assertEqual([p.name for p in self.runtime.iterdir() if p.is_file()], [])

    def test_inventory_listing_command(self):
        (self.run / "inventory").mkdir()
        (self.run / "inventory" / "hosts.yml").write_text("")
        spec = {"kind": "inventory", "inventory": "hosts.yml", "inventory_source": "managed"}
        argv = runner.build_inventory_argv(spec, self.run, self.runtime, None)
        self.assertEqual(argv[:3], ["/usr/bin/ansible-inventory", "--inventory",
                                    str((self.run / "inventory" / "hosts.yml").resolve())])
        # The listing holds the variables, vaulted ones decrypted: never on disk.
        self.assertEqual(argv[-2:], ["--output", str(self.runtime / runner.LISTING)])
        with self.assertRaises(BadRequest):
            runner.build_inventory_argv({**spec, "inventory": "../project/x"}, self.run,
                                        self.runtime, None)

    def test_inventory_listing_reduced(self):
        listing = {
            "_meta": {"hostvars": {
                "pc1": {"ansible_host": "10.0.0.1", "ansible_port": 2222,
                        "ansible_connection": "ssh", "ansible_password": "secret"},
                "pc2": {"license_key": "secret"}}},
            "all": {"children": ["ungrouped", "lab"], "vars": {"token": "secret"}},
            "lab": {"hosts": ["pc1", "pc2"], "vars": {"key": "secret"}},
            "ungrouped": {"hosts": ["solo"]},
        }
        reduced = runner.listing_hosts(listing)
        self.assertNotIn("secret", json.dumps(reduced))
        self.assertEqual(reduced["_meta"]["hostvars"]["pc1"],
                         {"ansible_host": "10.0.0.1", "ansible_port": 2222,
                          "ansible_connection": "ssh"})
        self.assertEqual(reduced["lab"], {"hosts": ["pc1", "pc2"]})
        self.assertEqual(jobs.parse_inventory(reduced), jobs.parse_inventory(listing))
        odd = runner.listing_hosts({"_meta": {"hostvars": {"pc3": "secret"}}})
        self.assertEqual(odd, {"_meta": {"hostvars": {}}})
        with self.assertRaises(ValueError):
            runner.listing_hosts([])


if __name__ == "__main__":
    unittest.main()
