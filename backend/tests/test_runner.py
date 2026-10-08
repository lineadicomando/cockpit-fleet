import json
import tempfile
import unittest
from pathlib import Path

from cockpit_fleet.errors import BadRequest
from cockpit_fleet.runner import build_argv, build_env


class BuildArgvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.run = root / "run"
        self.runtime = root / "runtime"
        self.creds = root / "creds"
        (self.run / "project").mkdir(parents=True)
        self.runtime.mkdir()
        self.creds.mkdir()
        (self.run / "project" / "site.yml").write_text("")
        (self.run / "project" / "hosts").write_text("")
        self.spec = {"playbook": "site.yml", "inventory": "hosts"}

    def tearDown(self):
        self.tmp.cleanup()

    def argv(self, creds=True, **extra):
        return build_argv({**self.spec, **extra}, self.run, self.runtime, self.creds if creds else None)

    def test_minimal_spec(self):
        project = (self.run / "project").resolve()
        self.assertEqual(self.argv(), [
            "/usr/bin/ansible-playbook",
            "--inventory", str(project / "hosts"),
            "--forks", "5",
            str(project / "site.yml"),
        ])

    def test_playbook_is_always_the_last_argument(self):
        argv = self.argv(limit="web*", check=True, diff=True, verbosity=2, tags=["a", "b"])
        self.assertTrue(argv[-1].endswith("/site.yml"))
        self.assertIn("--check", argv)
        self.assertIn("--diff", argv)
        self.assertIn("-vv", argv)
        self.assertEqual(argv[argv.index("--limit") + 1], "web*")
        self.assertEqual(argv[argv.index("--tags") + 1], "a,b")

    def test_flags_need_a_real_true(self):
        argv = self.argv(check="yes", diff=1)
        self.assertNotIn("--check", argv)
        self.assertNotIn("--diff", argv)

    def test_extra_vars_go_to_a_file_not_to_argv(self):
        argv = self.argv(extra_vars={"password": "s3cret", "n": 1})
        value = argv[argv.index("--extra-vars") + 1]
        self.assertEqual(value, f"@{self.runtime / 'extra_vars.json'}")
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertEqual(json.loads((self.runtime / "extra_vars.json").read_text())["n"], 1)

    def test_credentials_present_become_file_options(self):
        (self.creds / "fleet_private_key").write_text("k")
        (self.creds / "fleet_connection_password").write_text("v")
        (self.creds / "unrelated").write_text("x")
        argv = self.argv()
        self.assertEqual(argv[argv.index("--private-key") + 1], str(self.creds / "fleet_private_key"))
        self.assertIn("--connection-password-file", argv)
        self.assertNotIn("--become-password-file", argv)
        self.assertNotIn(str(self.creds / "unrelated"), argv)

    def test_vault_identifier_cannot_smuggle_a_path(self):
        (self.creds / "fleet_vault_password_0").write_text("v")
        good = self.argv(vaults=[{"file": "vault_password_0", "id": "prod"}])
        self.assertEqual(good[good.index("--vault-id") + 1],
                         f"prod@{self.creds / 'fleet_vault_password_0'}")
        with self.assertRaises(BadRequest):
            self.argv(vaults=[{"file": "vault_password_0", "id": "x@/etc/shadow"}])

    def test_no_credentials_directory(self):
        self.assertNotIn("--private-key", self.argv(creds=False))

    def test_rejects_values_that_would_become_options(self):
        bad = [
            {"limit": "-e@/etc/shadow"},
            {"user": "-oProxyCommand=id"},
            {"playbook": "../../etc/passwd"},
            {"playbook": "/etc/passwd"},
            {"inventory": "missing"},
            {"forks": 0},
            {"forks": "5"},
            {"verbosity": 9},
            {"tags": ["ok", "--bad"]},
            {"skip_tags": "a"},
            {"extra_vars": ["a=b"]},
            {"vaults": [{"file": "../../etc/shadow", "id": "x"}]},

            {"vaults": "vault_password_0"},
        ]
        for extra in bad:
            with self.assertRaises(BadRequest, msg=extra):
                self.argv(**extra)


class BuildEnvTest(unittest.TestCase):
    def test_environment_is_closed_and_strict_about_host_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, runtime = Path(tmp) / "run", Path(tmp) / "runtime"
            run.mkdir()
            runtime.mkdir()
            env = build_env(run, runtime)
            self.assertEqual(env["PATH"], "/usr/bin:/bin")
            self.assertIn("StrictHostKeyChecking=yes", env["ANSIBLE_SSH_ARGS"])
            self.assertIn(f"UserKnownHostsFile={run / 'known_hosts'}", env["ANSIBLE_SSH_ARGS"])
            self.assertTrue(env["ANSIBLE_CONFIG"].endswith(".cfg"))
            self.assertTrue(Path(env["ANSIBLE_CONFIG"]).is_file())
            self.assertEqual(env["FLEET_EVENTS_FILE"], str(run / "events.jsonl"))
            self.assertNotIn("SUDO_UID", env)


if __name__ == "__main__":
    unittest.main()
