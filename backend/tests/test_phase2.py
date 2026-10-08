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
from cockpit_fleet.errors import BadRequest, Conflict


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
            mock.patch.object(paths, "PROJECTS_DIR", self.root / "projects"),
            mock.patch.object(paths, "SECRETS_DIR", self.root / "secrets"),
            mock.patch.object(jobs, "_fleet_unit", fleet_unit),
            mock.patch.object(jobs, "_unit_active", lambda run_id: run_id in self.active),
            mock.patch.object(journal, "send", lambda *a, **k: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(max_concurrent_runs=2, local_project_roots=(str(self.root / "src"),))

    def ctx(self, role="admin", user="anna"):
        ctx = cli.Context(Caller(1000, user, role), self.root / "fleet.db", self.root / "config.toml")
        ctx._config = self.config
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
        self.assertEqual(credential["secrets"],
                         {"private_key": False, "password": False, "become_password": False})
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
        self.assertEqual(spec["limit"], "web*:&web01")
        self.assertEqual((spec["user"], spec["check"], spec["diff"]), ("maint", True, True))
        self.assertEqual(oct(directory.stat().st_mode & 0o777), "0o700")

        # The specification is exactly what the unit will turn into a command line.
        (directory / "runtime").mkdir()
        argv = runner.build_argv(spec, directory, directory / "runtime", None)
        self.assertEqual(argv[argv.index("--limit") + 1], "web*:&web01")
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

    def test_queue_respects_the_concurrency_limit(self):
        template = self.template()
        ids = [self.ok("run.start", {"template_id": template["id"]})["id"] for _ in range(4)]
        statuses = lambda: [self.ok("run.get", {"id": i})["status"] for i in ids]  # noqa: E731
        self.assertEqual(statuses(), ["running", "running", "queued", "queued"])
        self.finish(ids[0])
        self.assertEqual(statuses(), ["successful", "running", "running", "queued"])
        self.assertEqual(self.started, ids[:3])

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
            "_meta": {"hostvars": {"web01": {"ansible_host": "10.0.0.1", "ansible_password": "hunter2"},
                                   "db01": {}}},
            "all": {"children": ["ungrouped", "prod"]},
            "prod": {"children": ["web"], "hosts": ["db01"]},
            "web": {"hosts": ["web01"]},
        }
        (paths.RUNS_DIR / run_id / "inventory.json").write_text(json.dumps(listing))
        self.finish(run_id)
        got = self.ok("inventory.get", {"id": inventory["id"]}, role="viewer")
        self.assertEqual(got["hosts"], [
            {"name": "db01", "address": "", "groups": ["prod"]},
            {"name": "web01", "address": "10.0.0.1", "groups": ["prod", "web"]},
        ])
        self.assertNotIn("hunter2", json.dumps(got))
        self.assertEqual(self.ok("inventory.list")["inventories"][0]["host_count"], 2)

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
        helper = Path(env["GIT_ASKPASS"])
        self.assertEqual(oct(helper.stat().st_mode & 0o777), "0o700")
        self.assertNotIn("s3cret-token", helper.read_text())
        self.assertEqual((self.runtime / "git-user").read_text(), "deploy")

    def test_inventory_listing_command(self):
        (self.run / "inventory").mkdir()
        (self.run / "inventory" / "hosts.yml").write_text("")
        spec = {"kind": "inventory", "inventory": "hosts.yml", "inventory_source": "managed"}
        argv = runner.build_inventory_argv(spec, self.run, None)
        self.assertEqual(argv[:3], ["/usr/bin/ansible-inventory", "--inventory",
                                    str((self.run / "inventory" / "hosts.yml").resolve())])
        self.assertEqual(argv[-2:], ["--output", str(self.run / "inventory.json")])
        with self.assertRaises(BadRequest):
            runner.build_inventory_argv({**spec, "inventory": "../project/x"}, self.run, None)


if __name__ == "__main__":
    unittest.main()
