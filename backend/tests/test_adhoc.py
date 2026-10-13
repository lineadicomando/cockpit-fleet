"""Ad-hoc commands: one module on some hosts, as a playbook written for the job."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_phase2 import Base

from cockpit_fleet import paths, runner
from cockpit_fleet.config import ADHOC_MODULES, ConfigError, parse
from cockpit_fleet.errors import BadRequest


class AdhocPlaybookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.run, self.runtime = root / "run", root / "runtime"
        (self.run / "inventory").mkdir(parents=True)
        (self.run / "project").mkdir()
        self.runtime.mkdir()
        (self.run / "inventory" / "hosts").write_text("")
        self.spec = {"kind": "adhoc", "module": "ansible.builtin.command", "limit": "web*",
                     "inventory": "hosts", "inventory_source": "managed"}

    def task(self, **extra):
        return json.loads(runner.adhoc_playbook({**self.spec, **extra}))[0]["tasks"][0]

    def test_arguments_are_those_of_a_task(self):
        self.assertEqual(self.task()["ansible.builtin.command"], {})
        self.assertEqual(self.task(free_form="uptime")["ansible.builtin.command"], "uptime")
        self.assertEqual(self.task(args={"cmd": "uptime"})["ansible.builtin.command"],
                         {"cmd": "uptime"})
        both = self.task(free_form="ls", args={"chdir": "/srv"})
        self.assertEqual((both["ansible.builtin.command"], both["args"]), ("ls", {"chdir": "/srv"}))

    def test_the_play_is_fixed_and_the_limit_chooses_the_hosts(self):
        play = json.loads(runner.adhoc_playbook(self.spec))[0]
        self.assertEqual((play["hosts"], play["gather_facts"], len(play["tasks"])),
                         ("all", False, 1))

    def test_module_and_arguments_are_in_the_file_not_in_argv(self):
        argv = runner.build_adhoc_argv({**self.spec, "free_form": "echo s3cret", "check": True},
                                       self.run, self.runtime, None)
        self.assertEqual(argv[-1], str(self.runtime / "adhoc.yml"))
        self.assertEqual(argv[argv.index("--limit") + 1], "web*")
        self.assertIn("--check", argv)
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertNotIn("ansible.builtin.command", " ".join(argv))
        self.assertIn("s3cret", (self.runtime / "adhoc.yml").read_text())

    def test_rejects_what_is_not_a_full_module_name_or_has_no_limit(self):
        bad = [{"module": "command"}, {"module": "ansible.builtin"}, {"module": "a.b.c: x"},
               {"module": "-a.b.c"}, {"module": None}, {"limit": ""}, {"limit": None},
               {"limit": "-e@/etc/shadow"}, {"free_form": ["id"]}, {"args": "cmd=id"}]
        for extra in bad:
            with self.assertRaises(BadRequest, msg=extra):
                runner.build_adhoc_argv({**self.spec, **extra}, self.run, self.runtime, None)


class AdhocConfigTest(unittest.TestCase):
    def test_default_and_explicit_list(self):
        self.assertEqual(parse("").adhoc_modules, ADHOC_MODULES)
        self.assertEqual(parse("[adhoc]\nmodules = []").adhoc_modules, ())
        config = parse('[adhoc]\nmodules = ["ansible.builtin.ping", "ansible.builtin.ping"]')
        self.assertEqual(config.adhoc_modules, ("ansible.builtin.ping",))

    def test_errors_name_the_problem(self):
        for text in ('[adhoc]\nmodules = ["ping"]', '[adhoc]\nmodules = ["ansible.builtin.*"]',
                     '[adhoc]\nmodules = "ansible.builtin.ping"', "[adhoc]\nmodules = [1]",
                     "[adhoc]\nmodule = []"):
            with self.assertRaisesRegex(ConfigError, "adhoc", msg=text):
                parse(text)


class AdhocCommandTest(Base):
    def setUp(self):
        super().setUp()
        self.inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed",
                                                      "content": "all:\n  hosts:\n    a:\n"})
        self.params = {"inventory_id": self.inventory["id"], "module": "ansible.builtin.command",
                       "free_form": "uptime", "limit": "all"}

    def test_run_directory_and_record(self):
        machine = self.ok("credential.create", {"name": "ssh", "kind": "machine",
                                                "username": "maint"})
        vault = self.ok("credential.create", {"name": "v", "kind": "vault", "vault_id": "prod"})
        (paths.SECRETS_DIR / f"c-{machine['id']}-password.cred").write_bytes(b"PW")
        (paths.SECRETS_DIR / f"c-{vault['id']}-vault_password.cred").write_bytes(b"VP")
        run = self.ok("run.adhoc", {**self.params, "machine_credential_id": machine["id"],
                                    "vault_credential_ids": [vault["id"]], "become": True})
        directory = paths.RUNS_DIR / run["id"]
        spec = json.loads((directory / "spec.json").read_text())

        self.assertEqual((run["kind"], run["status"], run["template_id"]),
                         ("adhoc", "running", None))
        self.assertEqual(run["title"], "ansible.builtin.command [all]")
        self.assertEqual(run["options"]["free_form"], "uptime")
        self.assertEqual(list((directory / "project").iterdir()), [])
        self.assertEqual((directory / "creds" / "connection_password").read_bytes(), b"PW")
        self.assertEqual(spec["vaults"], [{"file": "vault_password_0", "id": "prod"}])
        self.assertEqual((spec["user"], spec["forks"]), ("maint", self.config.default_forks))

        (directory / "runtime").mkdir()
        argv = runner.build_adhoc_argv(spec, directory, directory / "runtime", None)
        self.assertEqual(argv[argv.index("--inventory") + 1],
                         str((directory / "inventory" / "hosts.yml").resolve()))
        self.assertIn("--become", argv)

    def test_administrators_only_and_audited(self):
        self.assertEqual(self.code("run.adhoc", self.params, role="operator"), "forbidden")
        self.ok("run.adhoc", self.params)
        entries = self.ok("audit.list")["entries"]
        self.assertEqual([(e["outcome"], e["role"]) for e in entries if e["command"] == "run.adhoc"],
                         [("ok", "admin"), ("denied", "operator")])
        self.assertIn("uptime", entries[0]["params"])

    def test_module_must_be_allowed_in_the_configuration(self):
        refused = {**self.params, "module": "ansible.builtin.copy"}
        self.assertEqual(self.code("run.adhoc", refused), "conflict")
        self.assertEqual(self.code("run.adhoc", {**self.params, "module": "command"}),
                         "bad_request")
        self.config = replace(self.config, adhoc_modules=())
        self.assertEqual(self.code("run.adhoc", self.params), "conflict")
        self.assertEqual(self.started, [])
        self.assertEqual(list(paths.RUNS_DIR.iterdir()), [])

    def test_what_becomes_a_command_line_is_validated(self):
        for extra in ({"limit": ""}, {"limit": "-e@/etc/shadow"}, {"forks": 0},
                      {"forks": self.config.max_forks + 1}, {"args": "cmd=id"},
                      {"args": {"x": "y" * 70_000}}, {"free_form": ["id"]}, {"extra_vars": {}}):
            self.assertEqual(self.code("run.adhoc", {**self.params, **extra}), "bad_request", extra)
        del self.params["limit"]
        self.assertEqual(self.code("run.adhoc", self.params), "bad_request")

    def test_a_file_inventory_brings_its_project(self):
        project = self.local_project()
        other = self.local_project("other")
        inventory = self.ok("inventory.create", {
            "name": "file", "kind": "file", "project_id": project["id"],
            "path": "inventories/hosts.yml"})
        params = {**self.params, "inventory_id": inventory["id"]}
        self.assertEqual(self.code("run.adhoc", {**params, "project_id": other["id"]}),
                         "bad_request")
        run = self.ok("run.adhoc", params)
        directory = paths.RUNS_DIR / run["id"]
        self.assertEqual(run["project_id"], project["id"])
        self.assertTrue((directory / "project" / "inventories" / "hosts.yml").is_file())
        self.assertFalse((directory / "inventory").exists())

    def test_a_project_can_be_given_for_its_collections(self):
        project = self.local_project()
        run = self.ok("run.adhoc", {**self.params, "project_id": project["id"]})
        directory = paths.RUNS_DIR / run["id"]
        self.assertTrue((directory / "project" / "site.yml").is_file())
        self.assertTrue((directory / "inventory" / "hosts.yml").is_file())

    def test_outcome_is_collected_like_a_playbook(self):
        run = self.ok("run.adhoc", self.params)
        directory = paths.RUNS_DIR / run["id"]
        (directory / "events.jsonl").write_text(
            '{"event": "stats", "hosts": {"a": {"ok": 1, "failures": 0}}}\n')
        self.finish(run["id"], rc=0)
        done = self.ok("run.get", {"id": run["id"]}, role="viewer")
        self.assertEqual((done["status"], done["stats"]), ("successful", {"a": {"ok": 1,
                                                                               "failures": 0}}))
        self.assertFalse((directory / "project").exists())


if __name__ == "__main__":
    unittest.main()
