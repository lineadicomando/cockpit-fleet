"""Sets of variables that job templates take before their own."""

import json
import tempfile
import unittest
from pathlib import Path

from test_phase2 import Base

from cockpit_fleet import paths, runner
from cockpit_fleet.errors import BadRequest


class VariableSetTest(Base):
    def spec_of(self, run_id):
        return json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())

    def test_made_changed_and_seen_by_every_role_but_changed_by_administrators(self):
        made = self.ok("varset.create", {"name": "school", "description": "Lab rooms",
                                         "variables": {"domain": "school.edu",
                                                       "rooms": ["a", "b"]}})
        self.assertEqual((made["name"], made["variables"]["rooms"], made["templates"]),
                         ("school", ["a", "b"], []))
        listed = self.ok("varset.list", role="viewer")["variable_sets"]
        self.assertEqual([one["name"] for one in listed], ["school"])
        changed = self.ok("varset.update", {"id": made["id"], "name": "school",
                                            "variables": {"domain": "other.edu"}})
        self.assertEqual((changed["variables"], changed["description"]),
                         ({"domain": "other.edu"}, ""))
        for name, params in (("varset.create", {"name": "x"}),
                             ("varset.update", {"id": made["id"], "name": "x"}),
                             ("varset.delete", {"id": made["id"]})):
            self.assertEqual(self.code(name, params, role="operator"), "forbidden", name)
        self.assertEqual(self.code("varset.create", {"name": "school"}), "conflict")
        self.assertEqual(self.code("varset.update", {"id": "v000000000000", "name": "y"}),
                         "not_found")

    def test_names_ansible_would_refuse_and_too_much_are_refused(self):
        for variables in ({"my-var": 1}, {"1st": 1}, {"": 1}, {"a b": 1},
                          {"big": "x" * (64 * 1024)}):
            self.assertEqual(self.code("varset.create", {"name": "bad",
                                                         "variables": variables}),
                             "bad_request", variables)
        self.assertEqual(self.code("varset.create", {"name": "bad", "variables": [1]}),
                         "bad_request")

    def test_a_template_takes_them_in_order_and_its_own_win(self):
        first = self.ok("varset.create", {"name": "first", "variables": {
            "domain": "one.edu", "rooms": 1, "only_first": True}})
        second = self.ok("varset.create", {"name": "second", "variables": {
            "rooms": 2, "iso": "win.iso"}})
        template = self.template(variable_set_ids=[first["id"], second["id"]],
                                 extra_vars={"iso": "mine.iso"},
                                 survey=[{"name": "rooms", "type": "number"}])
        self.assertEqual(template["variable_set_ids"], [first["id"], second["id"]])
        run = self.ok("run.start", {"template_id": template["id"]}, role="operator")
        spec = self.spec_of(run["id"])
        self.assertEqual(spec["extra_vars"], {"domain": "one.edu", "rooms": 2,
                                              "only_first": True, "iso": "mine.iso"})
        self.assertEqual(run["options"]["variable_sets"],
                         [{"id": first["id"], "name": "first"},
                          {"id": second["id"], "name": "second"}])
        # A survey's answer still wins, in a file of its own after these.
        run = self.ok("run.start", {"template_id": template["id"], "answers": {"rooms": 3}},
                      role="operator")
        self.assertEqual(self.spec_of(run["id"])["survey_vars"], {"rooms": 3})
        # A change of a set is in the next run.
        self.ok("varset.update", {"id": second["id"], "name": "second",
                                  "variables": {"rooms": 4}})
        run = self.ok("run.start", {"template_id": template["id"]}, role="operator")
        self.assertEqual(self.spec_of(run["id"])["extra_vars"]["rooms"], 4)
        # Which templates use a set.
        listed = {one["name"]: one for one in self.ok("varset.list")["variable_sets"]}
        self.assertEqual(listed["first"]["templates"], [{"id": template["id"], "name": "deploy"}])

    def test_a_set_in_use_stays_and_a_template_names_each_once(self):
        used = self.ok("varset.create", {"name": "used"})
        template = self.template(variable_set_ids=[used["id"]])
        self.assertEqual(self.code("varset.delete", {"id": used["id"]}), "conflict")
        update = {key: template[key] for key in ("id", "name", "project_id", "playbook",
                                                 "inventory_id")}
        self.assertEqual(self.code("template.update", {**update,
                                                       "variable_set_ids": [used["id"]] * 2}),
                         "bad_request")
        self.assertEqual(self.code("template.update", {**update,
                                                       "variable_set_ids": ["v000000000000"]}),
                         "not_found")
        # Taken off the template, it can go; a run with none records none.
        self.assertEqual(self.ok("template.update", update)["variable_set_ids"], [])
        self.ok("varset.delete", {"id": used["id"]})
        run = self.ok("run.start", {"template_id": template["id"]}, role="operator")
        self.assertNotIn("variable_sets", run["options"])
        # Deleting a template frees its sets.
        again = self.ok("varset.create", {"name": "again"})
        self.ok("template.update", {**update, "variable_set_ids": [again["id"]]})
        self.ok("template.delete", {"id": template["id"]})
        self.ok("varset.delete", {"id": again["id"]})

    def test_the_template_own_variables_follow_the_same_rules(self):
        template = self.template()
        update = {key: template[key] for key in ("id", "name", "project_id", "playbook",
                                                 "inventory_id")}
        self.assertEqual(self.code("template.update", {**update, "extra_vars": {"bad-name": 1}}),
                         "bad_request")


class SecretVariablesTest(Base):
    def spec_of(self, run_id):
        return json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())

    def put(self, secret_name, value):
        """What fleet-secret set leaves: here, the value as its ciphertext."""
        (paths.SECRETS_DIR / f"{secret_name}.cred").write_text(value)

    def test_names_here_values_apart_and_kept_when_left_out(self):
        made = self.ok("varset.create", {"name": "licenses", "variables": {"seats": 12},
                                         "secrets": ["ansible_vault_serial", "auth_code"]})
        secrets = {one["name"]: one for one in made["secrets"]}
        self.assertEqual(sorted(secrets), ["ansible_vault_serial", "auth_code"])
        self.assertFalse(secrets["auth_code"]["set"])
        name = secrets["auth_code"]["secret_name"]
        self.assertRegex(name, r"^s-[0-9a-f]{16}$")
        self.put(name, "XYZ")
        listed = self.ok("varset.list", role="viewer")["variable_sets"][0]
        self.assertTrue({one["name"]: one for one in listed["secrets"]}["auth_code"]["set"])
        self.assertNotIn("XYZ", json.dumps(listed))
        # Left out of an update, they stay; a name kept keeps its slot.
        kept = self.ok("varset.update", {"id": made["id"], "name": "licenses"})
        self.assertEqual(kept["secrets"], listed["secrets"])
        fewer = self.ok("varset.update", {"id": made["id"], "name": "licenses",
                                          "secrets": ["auth_code", "new_one"]})
        slots = {one["name"]: one["secret_name"] for one in fewer["secrets"]}
        self.assertEqual(slots["auth_code"], name)
        self.assertNotEqual(slots["new_one"], name)
        for bad in (["seats"], ["a-b"], ["x", "x"], [1]):
            self.assertEqual(self.code("varset.update", {"id": made["id"], "name": "licenses",
                                                         "variables": {"seats": 1},
                                                         "secrets": bad}), "bad_request", bad)

    def test_an_inventory_takes_sets_before_its_template(self):
        inv_set = self.ok("varset.create", {"name": "inv", "variables": {"a": "inv", "b": "inv"},
                                            "secrets": ["serial", "shadowed"]})
        tpl_set = self.ok("varset.create", {"name": "tpl", "variables": {"b": "tpl",
                                                                         "shadowed": "plain"}})
        template = self.template(variable_set_ids=[tpl_set["id"]], extra_vars={"c": "own"})
        inventory = self.ok("inventory.update", {"id": template["inventory_id"],
                                                 "variable_set_ids": [inv_set["id"]]})
        self.assertEqual(inventory["variable_set_ids"], [inv_set["id"]])
        slots = {one["name"]: one["secret_name"] for one in inv_set["secrets"]}
        # A secret with no value stops the launch, and leaves nothing behind.
        self.assertEqual(self.code("run.start", {"template_id": template["id"]},
                                   role="operator"), "bad_request")
        self.assertEqual(list(paths.RUNS_DIR.iterdir()), [])
        self.put(slots["serial"], "AB-12")
        run = self.ok("run.start", {"template_id": template["id"]}, role="operator")
        spec = self.spec_of(run["id"])
        # A plain variable of a later set wins over a secret of an earlier one.
        self.assertEqual(spec["extra_vars"], {"a": "inv", "b": "tpl", "shadowed": "plain",
                                              "c": "own"})
        self.assertEqual(spec["secret_vars"], {"serial": "secret_var_0"})
        self.assertEqual((paths.RUNS_DIR / run["id"] / "creds" / "secret_var_0").read_text(),
                         "AB-12")
        self.assertEqual([one["name"] for one in run["options"]["variable_sets"]], ["inv", "tpl"])
        self.assertEqual(run["options"]["secret_vars"], ["serial"])
        self.assertNotIn("AB-12", json.dumps(run) + json.dumps(spec))
        # Changing the inventory otherwise keeps its sets; a set in use stays.
        self.assertEqual(self.ok("inventory.update", {"id": template["inventory_id"],
                                                      "name": "renamed"})["variable_set_ids"],
                         [inv_set["id"]])
        self.assertEqual(self.code("varset.delete", {"id": inv_set["id"]}), "conflict")
        listed = {one["name"]: one for one in self.ok("varset.list")["variable_sets"]}
        self.assertEqual(listed["inv"]["inventories"],
                         [{"id": template["inventory_id"], "name": "renamed"}])
        for bad in ([inv_set["id"]] * 2, ["v000000000000"]):
            self.assertNotEqual(self.code("inventory.update", {"id": template["inventory_id"],
                                                               "variable_set_ids": bad}), "ok")
        self.ok("inventory.update", {"id": template["inventory_id"], "variable_set_ids": []})
        self.ok("varset.delete", {"id": inv_set["id"]})

    def test_an_adhoc_command_takes_the_sets_of_its_inventory(self):
        secret = self.ok("varset.create", {"name": "s", "variables": {"x": 1},
                                           "secrets": ["ansible_become_password"]})
        self.put(secret["secrets"][0]["secret_name"], "pw")
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed",
                                                 "content": "all:\n  hosts:\n    a:\n",
                                                 "variable_set_ids": [secret["id"]]})
        run = self.ok("run.adhoc", {"inventory_id": inventory["id"],
                                    "module": "ansible.builtin.command",
                                    "free_form": "uptime", "limit": "all"})
        spec = self.spec_of(run["id"])
        self.assertEqual((spec["extra_vars"], spec["secret_vars"]),
                         ({"x": 1}, {"ansible_become_password": "secret_var_0"}))
        self.assertEqual(run["options"]["secret_vars"], ["ansible_become_password"])
        # Deleting the inventory frees its sets.
        self.ok("inventory.delete", {"id": inventory["id"]})
        self.ok("varset.delete", {"id": secret["id"]})


class SecretVariablesFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.run, self.runtime, self.creds = root / "run", root / "runtime", root / "creds"
        for directory in (self.run / "project", self.runtime, self.creds):
            directory.mkdir(parents=True)
        (self.run / "project" / "site.yml").write_text("")
        (self.run / "project" / "hosts").write_text("")
        self.spec = {"playbook": "site.yml", "inventory": "hosts", "extra_vars": {"n": 1},
                     "secret_vars": {"ansible_vault_serial": "secret_var_0"}}

    def test_they_reach_ansible_in_a_private_file_after_the_plain_ones(self):
        (self.creds / "fleet_secret_var_0").write_text('AB{{ x }}"-12')
        argv = runner.build_argv(self.spec, self.run, self.runtime, self.creds)
        files = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--extra-vars"]
        hidden = self.runtime / "secret_vars.yml"
        self.assertEqual(files, [f"@{self.runtime / 'extra_vars.json'}", f"@{hidden}"])
        self.assertEqual(hidden.read_text(), 'ansible_vault_serial: !unsafe "AB{{ x }}\\"-12"\n')
        self.assertEqual(oct(hidden.stat().st_mode & 0o077), "0o0")
        self.assertNotIn("AB{{", " ".join(argv))
        # A check cycle keeps them too.
        self.assertIn(f"@{hidden}", runner.build_check_argv(
            {**self.spec, "plan": {"a": [{"check": "disk", "id": "c1", "params": {}}]}},
            self.run, self.runtime, self.creds))

    def test_a_run_does_not_go_on_without_one(self):
        for creds in (self.creds, None):
            with self.assertRaises(BadRequest):
                runner.build_argv(self.spec, self.run, self.runtime, creds)
        (self.creds / "fleet_secret_var_0").write_text("x")
        for bad in ([], {"a": "../private_key"}, {"a": "survey_0"}, {"a": 1}, {"a-b": "secret_var_0"}):
            with self.assertRaises(BadRequest, msg=bad):
                runner.build_argv({**self.spec, "secret_vars": bad}, self.run, self.runtime,
                                  self.creds)
