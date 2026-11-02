"""Sets of variables that job templates take before their own."""

import json

from test_phase2 import Base

from cockpit_fleet import paths


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
