"""Workflows: steps start by how the steps before them ended, pass on what
set_stats set, wait for approvals, and can be canceled and resumed."""

import json
import unittest
from unittest import mock

from test_approvals import ApprovalBase

from cockpit_fleet import jobs, overview, paths, runner, schedules, store, workflows  # noqa: F401
from cockpit_fleet.errors import BadRequest


class WorkflowBase(ApprovalBase):
    def setUp(self):
        super().setUp()
        # CI has no systemd-analyze: a schedule's times are made up.
        patch = mock.patch.object(schedules, "analyze",
                                  lambda calendar, timezone="", count=1: (calendar, [2e9] * count))
        patch.start()
        self.addCleanup(patch.stop)
        self.config = type(self.config)(**{**self.config.as_dict(), "max_concurrent_runs": 10})
        self.first = self.template()
        project_id, inventory_id = self.first["project_id"], self.first["inventory_id"]
        self.second = self.ok("template.create", {
            "name": "migrate", "project_id": project_id, "playbook": "site.yml",
            "inventory_id": inventory_id,
            "survey": [{"name": "colour", "type": "text", "required": True}]})
        self.third = self.ok("template.create", {
            "name": "notify", "project_id": project_id, "playbook": "site.yml",
            "inventory_id": inventory_id})

    def chain(self, **second):
        """deploy, then migrate on success, then notify always; cleanup on failure."""
        return self.ok("workflow.create", {"name": "release", "nodes": [
            {"id": "a", "template_id": self.first["id"], "limit": "a"},
            {"id": "b", "template_id": self.second["id"], "answers": {"colour": "blue"},
             **second},
            {"id": "c", "name": "tell", "template_id": self.third["id"]},
            {"id": "x", "name": "cleanup", "template_id": self.third["id"]},
        ], "edges": [
            {"from": "a", "to": "b", "condition": "success"},
            {"from": "b", "to": "c", "condition": "always"},
            {"from": "a", "to": "x", "condition": "failure"},
        ]})

    def steps(self, run_id):
        progress = self.ok("workflow.progress", {"id": run_id}, role="viewer")
        return {node["id"]: (node["state"], node["run"]["id"] if node["run"] else None)
                for node in progress["nodes"]}

    def end(self, run_id, rc=0, artifacts=None):
        directory = paths.RUNS_DIR / run_id
        if artifacts is not None:
            (directory / "events.jsonl").write_text(json.dumps(
                {"event": "stats", "hosts": {"a": {"ok": 1}}, "artifacts": artifacts}) + "\n")
        self.finish(run_id, rc)
        self.ok("run.list", role="viewer")  # reconcile, as a page polling would


class WorkflowTest(WorkflowBase):
    def test_steps_follow_how_the_steps_before_them_ended(self):
        workflow = self.chain()
        run = self.ok_as("workflow.launch", {"id": workflow["id"]}, user="olga")
        self.assertEqual((run["kind"], run["status"]), ("workflow", "running"))
        steps = self.steps(run["id"])
        self.assertEqual(steps["a"][0], "active")
        self.assertEqual({key: value[0] for key, value in steps.items() if key != "a"},
                         {"b": "waiting", "c": "waiting", "x": "waiting"})
        step_a = self.ok("run.get", {"id": steps["a"][1]})
        self.assertEqual((step_a["workflow_run_id"], step_a["workflow_node"], step_a["created_by"],
                          step_a["options"]["limit"]), (run["id"], "a", "olga", "a"))
        # The workflow run has no unit and takes no place in the queue.
        self.assertEqual(self.started, [steps["a"][1]])

        self.end(steps["a"][1], artifacts={"version": "1.2", "tpl": "{{ lookup('pipe', 'id') }}",
                                           "ansible_user": "root", "not a name": 1})
        steps = self.steps(run["id"])
        self.assertEqual((steps["b"][0], steps["x"][0]), ("active", "skipped"))
        spec = json.loads((paths.RUNS_DIR / steps["b"][1] / "spec.json").read_text())
        self.assertEqual(spec["workflow_vars"],
                         {"version": "1.2", "tpl": "{{ lookup('pipe', 'id') }}"})
        self.assertEqual(spec["survey_vars"], {"colour": "blue"})

        self.end(steps["b"][1], rc=2)
        steps = self.steps(run["id"])
        self.assertEqual(steps["c"][0], "active")  # always
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "running")
        self.end(steps["c"][1])
        ended = self.ok("run.get", {"id": run["id"]})
        # b failed, and nothing after it was for its failure.
        self.assertEqual(ended["status"], "failed")
        self.assertIn("migrate: failed", ended["error"])
        listed = self.ok("workflow.list", role="viewer")["workflows"][0]
        self.assertEqual(listed["last_run"]["status"], "failed")

    def test_a_failure_with_a_step_for_it_is_handled(self):
        workflow = self.chain()
        run = self.ok("workflow.launch", {"id": workflow["id"]}, role="operator")
        self.end(self.steps(run["id"])["a"][1], rc=2)
        steps = self.steps(run["id"])
        self.assertEqual((steps["b"][0], steps["c"][0], steps["x"][0]),
                         ("skipped", "skipped", "active"))
        self.end(steps["x"][1])
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "successful")

    def test_three_steps_with_an_approval_at_the_second(self):
        workflow = self.chain(needs_approval=True)
        run = self.ok_as("workflow.launch", {"id": workflow["id"]}, user="olga")
        self.end(self.steps(run["id"])["a"][1])
        second = self.steps(run["id"])["b"][1]
        self.assertEqual(self.ok("run.get", {"id": second})["status"], "pending_approval")
        self.assertEqual(self.code_as("approval.approve", {"id": second}, user="olga",
                                      rights=("approve",)), "forbidden")
        self.ok_as("approval.approve", {"id": second}, user="piero", role="viewer",
                   rights=("approve",))
        self.end(second)
        self.end(self.steps(run["id"])["c"][1])
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "successful")

    def test_a_checkpoint_between_steps_is_passed_by_who_launched_it(self):
        workflow = self.chain(approval="confirm")
        node = self.ok("workflow.list")["workflows"][0]["nodes"][1]
        self.assertEqual((node["approval"], node["needs_approval"]), ("confirm", True))
        run = self.ok_as("workflow.launch", {"id": workflow["id"]}, user="olga")
        self.end(self.steps(run["id"])["a"][1])
        second = self.steps(run["id"])["b"][1]
        self.assertEqual(self.code_as("approval.approve", {"id": second}, user="piero"),
                         "forbidden")
        self.ok_as("approval.approve", {"id": second}, user="olga")
        self.assertEqual(self.steps(run["id"])["b"][0], "active")

    def test_a_step_waits_as_the_stronger_of_its_mode_and_its_template_s(self):
        template = {key: self.second[key] for key in ("id", "name", "project_id", "playbook",
                                                      "inventory_id", "survey")}
        self.ok("template.update", {**template, "approval": "other"})
        workflow = self.chain(approval="confirm")
        run = self.ok_as("workflow.launch", {"id": workflow["id"]}, user="olga")
        self.end(self.steps(run["id"])["a"][1])
        second = self.steps(run["id"])["b"][1]
        self.assertEqual(self.ok("run.get", {"id": second})["approval"]["mode"], "other")
        self.assertEqual(self.code_as("approval.approve", {"id": second}, user="olga"),
                         "forbidden")

    def test_a_run_from_before_modes_resumes_with_its_steps_as_they_were(self):
        workflow = self.chain()
        run = self.ok_as("workflow.launch", {"id": workflow["id"]}, user="olga")
        # Its steps as a run kept them before modes: the cleanup waited for another person.
        conn = self.ctx().conn
        options = json.loads(conn.execute("SELECT options FROM runs WHERE id = ?",
                                          (run["id"],)).fetchone()[0])
        for node in options["nodes"]:
            node.pop("approval")
            node["needs_approval"] = node["id"] == "x"
        conn.execute("UPDATE runs SET options = ? WHERE id = ?",
                     (json.dumps(options), run["id"]))
        conn.commit()
        nodes = {node["id"]: node for node in self.ok("workflow.progress",
                                                      {"id": run["id"]})["nodes"]}
        self.assertEqual((nodes["x"]["approval"], nodes["a"]["approval"]), ("other", "none"))
        self.end(self.steps(run["id"])["a"][1], rc=2)
        cleanup = self.steps(run["id"])["x"][1]
        self.assertEqual(self.ok("run.get", {"id": cleanup})["approval"]["mode"], "other")
        self.ok_as("approval.reject", {"id": cleanup}, user="piero", role="admin")
        again = self.ok_as("workflow.resume", {"id": run["id"]}, user="olga")
        self.end(self.steps(again["id"])["a"][1], rc=2)
        cleanup = self.steps(again["id"])["x"][1]
        self.assertEqual(self.ok("run.get", {"id": cleanup})["approval"]["mode"], "other")

    def test_a_rejected_step_fails_the_workflow(self):
        workflow = self.chain(needs_approval=True)
        run = self.ok_as("workflow.launch", {"id": workflow["id"]}, user="olga")
        self.end(self.steps(run["id"])["a"][1])
        self.ok_as("approval.reject", {"id": self.steps(run["id"])["b"][1]}, user="piero",
                   role="admin")
        steps = self.steps(run["id"])
        self.assertEqual(steps["c"][0], "active")
        self.end(steps["c"][1])
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "failed")

    def test_cancel_stops_what_runs_and_starts_nothing_more(self):
        workflow = self.chain()
        run = self.ok("workflow.launch", {"id": workflow["id"]}, role="operator")
        first = self.steps(run["id"])["a"][1]
        self.ok("run.cancel", {"id": run["id"]}, role="operator")
        self.assertIn(first, self.stopped)
        # Its unit stopped: what it leaves ends it, and the workflow with it.
        self.end(first, rc=-15)
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "canceled")
        self.assertEqual(self.ok("run.get", {"id": first})["status"], "canceled")
        self.assertEqual([state for state, _ in self.steps(run["id"]).values()].count("active"), 0)

    def test_resume_runs_again_from_the_failed_step(self):
        workflow = self.chain()
        run = self.ok("workflow.launch", {"id": workflow["id"]}, role="operator")
        first = self.steps(run["id"])["a"][1]
        self.end(first, artifacts={"version": "2"})
        self.end(self.steps(run["id"])["b"][1], rc=2)
        self.end(self.steps(run["id"])["c"][1])
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "failed")

        again = self.ok("workflow.resume", {"id": run["id"]}, role="operator")
        progress = self.ok("workflow.progress", {"id": again["id"]})
        self.assertEqual(progress["resumed_from"], run["id"])
        nodes = {node["id"]: node for node in progress["nodes"]}
        self.assertTrue(nodes["a"]["carried"])
        self.assertEqual(nodes["a"]["run"]["id"], first)
        self.assertEqual(nodes["b"]["state"], "active")
        spec = json.loads((paths.RUNS_DIR / nodes["b"]["run"]["id"] / "spec.json").read_text())
        self.assertEqual(spec["workflow_vars"], {"version": "2"})
        self.end(nodes["b"]["run"]["id"])
        self.end(self.steps(again["id"])["c"][1])
        self.assertEqual(self.ok("run.get", {"id": again["id"]})["status"], "successful")
        self.assertEqual(self.code("workflow.resume", {"id": again["id"]}), "conflict")

    def test_a_step_that_cannot_start_counts_as_failed(self):
        workflow = self.chain()
        run = self.ok("workflow.launch", {"id": workflow["id"]}, role="operator")
        # The answer the step stores no longer fits the survey of its template.
        conn = self.ctx().conn
        conn.execute("UPDATE templates SET survey = ? WHERE id = ?", (json.dumps(
            [{"name": "size", "type": "text", "required": True}]), self.second["id"]))
        self.end(self.steps(run["id"])["a"][1])
        progress = self.ok("workflow.progress", {"id": run["id"]})
        nodes = {node["id"]: node for node in progress["nodes"]}
        self.assertEqual(nodes["b"]["state"], "failure")
        self.assertIn("colour", nodes["b"]["error"])
        self.end(nodes["c"]["run"]["id"])
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "failed")

    def test_the_graph_is_checked(self):
        node = {"id": "a", "template_id": self.first["id"]}
        bad = [
            ([], []),
            ([node, node], []),
            ([node, {**node, "id": "b"}], [{"from": "a", "to": "b", "condition": "maybe"}]),
            ([node, {**node, "id": "b"}], [{"from": "a", "to": "b", "condition": "success"},
                                           {"from": "b", "to": "a", "condition": "success"}]),
            ([node], [{"from": "a", "to": "a", "condition": "always"}]),
            ([{**node, "limit": "a;b"}], []),
            ([{**node, "answers": {"nope": 1}}], []),
            ([{**node, "extra": 1}], []),
        ]
        for nodes, edges in bad:
            self.assertEqual(self.code("workflow.create", {"name": "w", "nodes": nodes,
                                                           "edges": edges}),
                             "bad_request", (nodes, edges))
        secret = self.ok("template.create", {
            "name": "s", "project_id": self.first["project_id"], "playbook": "site.yml",
            "inventory_id": self.first["inventory_id"],
            "survey": [{"name": "token", "type": "secret", "required": True}]})
        self.assertEqual(self.code("workflow.create", {"name": "w", "nodes": [
            {"id": "a", "template_id": secret["id"]}]}), "conflict")

    def test_roles_and_a_template_in_use(self):
        workflow = self.chain()
        self.assertEqual(self.code("workflow.create", {"name": "x", "nodes": []},
                                   role="operator"), "forbidden")
        self.assertEqual(self.code("workflow.launch", {"id": workflow["id"]}, role="viewer"),
                         "forbidden")
        self.assertEqual(self.code("template.delete", {"id": self.third["id"]}), "conflict")
        self.ok("workflow.delete", {"id": workflow["id"]})
        self.ok("template.delete", {"id": self.third["id"]})

    def elsewhere(self):
        """A template alike, of another project on another inventory."""
        project = self.ok("project.create", {"name": "other", "kind": "managed"})
        inventory = self.ok("inventory.create", {"name": "other", "kind": "managed",
                                                 "content": "all:\n  hosts:\n    a:\n"})
        return self.ok("template.create", {
            "name": "deploy there", "project_id": project["id"], "playbook": "site.yml",
            "inventory_id": inventory["id"]})

    def test_a_workflow_of_a_project_or_an_inventory_takes_its_steps_from_them(self):
        other = self.elsewhere()
        here = {"project_id": self.first["project_id"],
                "inventory_id": self.first["inventory_id"]}
        # Same project, another inventory.
        mixed = self.ok("template.create", {
            "name": "mixed", "project_id": here["project_id"], "playbook": "site.yml",
            "inventory_id": other["inventory_id"]})
        steps = lambda *templates: {"nodes": [  # noqa: E731
            {"id": f"s{at}", "template_id": template["id"]}
            for at, template in enumerate(templates)], "edges": []}
        for scope in (here, {"project_id": here["project_id"]},
                      {"inventory_id": here["inventory_id"]}):
            self.assertEqual(self.code("workflow.create", {
                "name": "w", **scope, **steps(self.first, other)}), "bad_request", scope)
        self.assertEqual(self.code("workflow.create", {
            "name": "w", **here, **steps(self.first, mixed)}), "bad_request")
        self.assertEqual(self.code("workflow.create", {
            "name": "w", "project_id": "p-nope", **steps(self.first)}), "not_found")
        workflow = self.ok("workflow.create", {
            "name": "w", "project_id": here["project_id"], **steps(self.first, mixed)})
        self.assertEqual((workflow["project_id"], workflow["inventory_id"]),
                         (here["project_id"], None))
        self.assertEqual(self.code("workflow.update", {
            "id": workflow["id"], "name": "w", **here, **steps(self.first, mixed)}),
            "bad_request")
        # Left out, a workflow takes its steps from anywhere, as before.
        workflow = self.ok("workflow.update", {"id": workflow["id"], "name": "w",
                                               **steps(self.first, other)})
        self.assertEqual((workflow["project_id"], workflow["inventory_id"]), (None, None))

    def test_progress_tells_the_inventory_of_each_step(self):
        other = self.elsewhere()
        workflow = self.ok("workflow.create", {"name": "w", "nodes": [
            {"id": "a", "template_id": self.first["id"]},
            {"id": "b", "template_id": other["id"]},
        ], "edges": [{"from": "a", "to": "b", "condition": "success"}]})
        run = self.ok("workflow.launch", {"id": workflow["id"]})
        names = {row["id"]: row["name"] for row in self.ok("inventory.list")["inventories"]}
        nodes = {node["id"]: node for node in
                 self.ok("workflow.progress", {"id": run["id"]}, role="viewer")["nodes"]}
        # One started, from its run; one not yet, from its template.
        self.assertIsNotNone(nodes["a"]["run"])
        self.assertIsNone(nodes["b"]["run"])
        self.assertEqual((nodes["a"]["inventory_id"], nodes["a"]["inventory"]),
                         (self.first["inventory_id"], names[self.first["inventory_id"]]))
        self.assertEqual((nodes["b"]["inventory_id"], nodes["b"]["inventory"]),
                         (other["inventory_id"], "other"))

    def test_a_workflow_runs_every_step_on_its_inventory_or_the_one_chosen(self):
        lab = self.ok("inventory.create", {"name": "lab", "kind": "managed",
                                           "content": "all:\n  hosts:\n    a:\n"})
        alone = self.ok("inventory.create", {"name": "alone", "kind": "managed", "content": ""})
        params = {key: self.first[key] for key in ("name", "project_id", "playbook",
                                                   "inventory_id")}
        self.ok("template.update", {"id": self.first["id"], **params,
                                    "inventory_ids": [lab["id"], alone["id"]]})
        params = {key: self.third[key] for key in ("name", "project_id", "playbook",
                                                   "inventory_id")}
        self.ok("template.update", {"id": self.third["id"], **params, "any_inventory": True})
        nodes = {"nodes": [{"id": "a", "template_id": self.first["id"]},
                           {"id": "b", "template_id": self.third["id"]}],
                 "edges": [{"from": "a", "to": "b", "condition": "success"}]}
        free = self.ok("workflow.create", {"name": "free", **nodes})
        self.assertEqual(sorted(free["inventory_ids"]),
                         sorted([self.first["inventory_id"], lab["id"], alone["id"]]))
        run = self.ok("workflow.launch", {"id": free["id"], "inventory_id": lab["id"]})
        steps = self.steps(run["id"])
        self.assertEqual(self.ok("run.get", {"id": steps["a"][1]})["inventory_id"], lab["id"])
        progress = self.ok("workflow.progress", {"id": run["id"]}, role="viewer")["nodes"]
        self.assertEqual([node["inventory"] for node in progress], ["lab", "lab"])
        self.end(steps["a"][1])
        self.assertEqual(self.ok("run.get", {"id": self.steps(run["id"])["b"][1]})
                         ["inventory_id"], lab["id"])
        # A step that does not allow it: refused at launch, naming the step.
        nodes["nodes"].append({"id": "c", "name": "migrate", "template_id": self.second["id"],
                               "answers": {"colour": "red"}})
        self.ok("workflow.update", {"id": free["id"], "name": "free", **nodes})
        answer = self.call("workflow.launch", {"id": free["id"], "inventory_id": lab["id"]})
        self.assertEqual(answer["error"]["code"], "conflict")
        self.assertIn("migrate", answer["error"]["message"])
        # A workflow on an inventory takes the templates that allow it, and no other.
        nodes["nodes"].pop()
        fixed = self.ok("workflow.create", {"name": "fixed", "inventory_id": alone["id"], **nodes})
        self.assertEqual(fixed["inventory_ids"], [alone["id"]])
        self.assertEqual(self.code("workflow.launch", {"id": fixed["id"],
                                                       "inventory_id": lab["id"]}), "conflict")
        run = self.ok("workflow.launch", {"id": fixed["id"]})
        self.assertEqual(self.ok("run.get", {"id": self.steps(run["id"])["a"][1]})
                         ["inventory_id"], alone["id"])
        # Its templates keep allowing it.
        self.assertEqual(self.code("template.update", {
            "id": self.first["id"], **{key: self.first[key] for key in
                                       ("name", "project_id", "playbook", "inventory_id")},
            "inventory_ids": [lab["id"]]}), "conflict")

    def test_the_list_tells_the_last_run_on_each_inventory(self):
        lab = self.ok("inventory.create", {"name": "lab", "kind": "managed", "content": ""})
        params = {key: self.first[key] for key in ("name", "project_id", "playbook",
                                                   "inventory_id")}
        self.ok("template.update", {"id": self.first["id"], **params, "inventory_ids": [lab["id"]]})
        nodes = {"nodes": [{"id": "a", "template_id": self.first["id"]}], "edges": []}
        free = self.ok("workflow.create", {"name": "free", **nodes})
        fixed = self.ok("workflow.create", {"name": "fixed", "inventory_id": lab["id"], **nodes})
        listed = lambda: {one["name"]: one for one in  # noqa: E731
                          self.ok("workflow.list", role="viewer")["workflows"]}
        self.assertEqual(listed()["free"]["last_runs"], {})
        on_lab = self.ok("workflow.launch", {"id": free["id"], "inventory_id": lab["id"]})
        own = self.ok("workflow.launch", {"id": free["id"]})
        on_fixed = self.ok("workflow.launch", {"id": fixed["id"]})
        workflows = listed()
        # Each step's own is "", the one chosen at launch or the workflow's by its id.
        self.assertEqual({key: run["id"] for key, run in workflows["free"]["last_runs"].items()},
                         {lab["id"]: on_lab["id"], "": own["id"]})
        self.assertEqual(workflows["free"]["last_run"]["id"], own["id"])
        self.assertEqual({key: run["id"] for key, run in workflows["fixed"]["last_runs"].items()},
                         {lab["id"]: on_fixed["id"]})
        self.assertEqual(set(workflows["fixed"]["last_runs"][lab["id"]]),
                         {"id", "status", "created_at"})

    def test_a_step_with_no_default_inventory_needs_one_chosen(self):
        lab = self.ok("inventory.create", {"name": "lab", "kind": "managed", "content": ""})
        nodes = {"nodes": [{"id": "a", "template_id": self.first["id"]}], "edges": []}
        free = self.ok("workflow.create", {"name": "free", **nodes})
        relying = self.ok("schedule.create", {"name": "nightly", "workflow_id": free["id"],
                                              "calendar": "daily"})
        params = {key: self.first[key] for key in ("name", "project_id", "playbook")}
        self.assertEqual(self.code("template.update", {"id": self.first["id"], **params,
                                                       "inventory_ids": [lab["id"]]}),
                         "conflict")
        self.ok("schedule.delete", {"id": relying["id"]})
        self.ok("template.update", {"id": self.first["id"], **params,
                                    "inventory_ids": [lab["id"]]})
        listed = {one["id"]: one for one in self.ok("workflow.list")["workflows"]}
        self.assertEqual(listed[free["id"]]["inventory_ids"], [lab["id"]])
        self.assertTrue(listed[free["id"]]["needs_inventory"])
        answer = self.call("workflow.launch", {"id": free["id"]})
        self.assertEqual(answer["error"]["code"], "bad_request")
        self.assertIn("no default inventory", answer["error"]["message"])
        self.assertEqual(self.code("schedule.create", {
            "name": "nightly", "workflow_id": free["id"], "calendar": "daily"}), "bad_request")
        self.ok("schedule.create", {"name": "nightly", "workflow_id": free["id"],
                                    "calendar": "daily", "inventory_id": lab["id"]})
        run = self.ok("workflow.launch", {"id": free["id"], "inventory_id": lab["id"]})
        self.assertEqual(self.ok("run.get", {"id": self.steps(run["id"])["a"][1]})
                         ["inventory_id"], lab["id"])
        # A step with none is not added while a schedule takes each step's default.
        other = self.ok("workflow.create", {"name": "other", "nodes": [
            {"id": "b", "template_id": self.third["id"]}], "edges": []})
        self.ok("schedule.create", {"name": "weekly", "workflow_id": other["id"],
                                    "calendar": "weekly"})
        self.assertEqual(self.code("workflow.update", {"id": other["id"], "name": "other", **nodes}),
                         "conflict")

    def test_a_template_stays_where_a_workflow_takes_its_steps_from(self):
        other = self.elsewhere()
        workflow = self.ok("workflow.create", {
            "name": "w", "inventory_id": self.first["inventory_id"],
            "nodes": [{"id": "a", "template_id": self.third["id"]}], "edges": []})
        change = {"id": self.third["id"], "name": "notify", "playbook": "site.yml",
                  "project_id": self.third["project_id"],
                  "inventory_id": self.third["inventory_id"]}
        self.assertEqual(self.code("template.update",
                                   {**change, "inventory_id": other["inventory_id"]}),
                         "conflict")
        # The project is free: this workflow names an inventory only.
        self.ok("template.update", {**change, "project_id": other["project_id"]})
        self.ok("workflow.delete", {"id": workflow["id"]})
        self.ok("template.update", {**change, "inventory_id": other["inventory_id"]})

    def test_a_run_checks_where_its_steps_are_from(self):
        other = self.elsewhere()
        workflow = self.ok("workflow.create", {
            "name": "w", "inventory_id": self.first["inventory_id"], "nodes": [
                {"id": "a", "template_id": self.first["id"]},
                {"id": "b", "template_id": self.third["id"]}],
            "edges": [{"from": "a", "to": "b", "condition": "success"}]})
        run = self.ok("workflow.launch", {"id": workflow["id"]}, role="operator")
        # The workflow no longer uses the template, which then moves: the run still does.
        self.ok("workflow.update", {"id": workflow["id"], "name": "w", "nodes": [
            {"id": "a", "template_id": self.first["id"]}], "edges": []})
        self.ok("template.update", {
            "id": self.third["id"], "name": "notify", "playbook": "site.yml",
            "project_id": self.third["project_id"], "inventory_id": other["inventory_id"]})
        self.end(self.steps(run["id"])["a"][1])
        nodes = {node["id"]: node for node in
                 self.ok("workflow.progress", {"id": run["id"]})["nodes"]}
        self.assertEqual(nodes["b"]["state"], "failure")
        self.assertIn("inventory", nodes["b"]["error"])
        self.assertEqual(self.ok("run.get", {"id": run["id"]})["status"], "failed")
        again = self.ok("workflow.resume", {"id": run["id"]}, role="operator")
        self.assertEqual(self.steps(again["id"])["b"][0], "failure")

    def test_a_running_workflow_keeps_its_steps_when_changed(self):
        workflow = self.chain()
        run = self.ok("workflow.launch", {"id": workflow["id"]}, role="operator")
        self.ok("workflow.update", {"id": workflow["id"], "name": "release", "nodes": [
            {"id": "z", "template_id": self.third["id"]}], "edges": []})
        self.assertEqual(sorted(self.steps(run["id"])), ["a", "b", "c", "x"])

    def test_the_tick_takes_up_a_workflow_left_behind(self):
        workflow = self.chain()
        run = self.ok("workflow.launch", {"id": workflow["id"]}, role="operator")
        first = self.steps(run["id"])["a"][1]
        # The step ended, and its end was recorded, without advancing the workflow.
        self.ctx().conn.execute("UPDATE runs SET status = 'successful', finished_at = 1"
                                " WHERE id = ?", (first,))
        self.active.discard(first)
        jobs.reconcile(self.ctx())
        self.assertEqual(self.steps(run["id"])["b"][0], "active")


class ScheduledWorkflowTest(WorkflowBase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(paths, "SCHEDULER_LOCK", self.root / "scheduler.lock")
        patch.start()
        self.addCleanup(patch.stop)

    def schedule(self, workflow, **extra):
        return self.ok("schedule.create", {"name": "nightly", "workflow_id": workflow["id"],
                                           "calendar": "daily", **extra})

    def fire(self, schedule):
        conn = self.ctx().conn
        conn.execute("UPDATE schedules SET next_at = ? WHERE id = ?",
                     (store.now() - 1, schedule["id"]))
        return self.ok("scheduler.tick")["schedules"]

    def test_a_schedule_launches_a_workflow_at_its_time(self):
        workflow = self.chain()
        schedule = self.schedule(workflow, priority=3)
        listed = self.ok("schedule.list", role="viewer")["schedules"][0]
        self.assertEqual((listed["workflow_id"], listed["workflow"], listed["template_id"],
                          listed["template"]), (workflow["id"], "release", None, ""))
        fired = self.fire(schedule)
        self.assertEqual(fired[0]["outcome"], "launched", fired)
        run = self.ok("run.get", {"id": fired[0]["run_id"]})
        self.assertEqual((run["kind"], run["schedule_id"], run["created_by"], run["priority"]),
                         ("workflow", schedule["id"], "schedule:nightly", 3))
        step = self.ok("run.get", {"id": self.steps(run["id"])["a"][1]})
        self.assertEqual((step["created_by"], step["priority"], step["schedule_id"]),
                         ("schedule:nightly", 3, None))
        # Still running: the next time is skipped.
        self.assertEqual(self.fire(schedule)[0]["outcome"], "skipped")
        upcoming = self.ok("overview.get", role="viewer")["upcoming"]["schedules"]
        self.assertEqual((upcoming[0]["workflow"], upcoming[0]["template"]), ("release", ""))

    def test_a_workflow_that_waits_for_a_person_is_not_scheduled(self):
        self.assertEqual(self.code("schedule.create", {
            "name": "n", "workflow_id": self.chain(approval="confirm")["id"],
            "calendar": "daily"}), "bad_request")
        self.ok("workflow.delete", {"id": self.ok("workflow.list")["workflows"][0]["id"]})
        template = {key: self.second[key] for key in ("id", "name", "project_id", "playbook",
                                                      "inventory_id", "survey")}
        self.ok("template.update", {**template, "approval": "confirm"})
        workflow = self.chain()
        self.assertEqual(self.code("schedule.create", {
            "name": "n", "workflow_id": workflow["id"], "calendar": "daily"}), "bad_request")

    def test_a_schedule_names_a_template_or_a_workflow_and_what_fits_it(self):
        workflow = self.chain()
        self.assertEqual(self.code("schedule.create", {"name": "n", "calendar": "daily"}),
                         "bad_request")
        for extra in ({"template_id": self.first["id"]}, {"limit": "a"}, {"check": False},
                      {"answers": {}}, {"forks": 2}):
            params = {"name": "n", "calendar": "daily", "workflow_id": workflow["id"], **extra}
            self.assertEqual(self.code("schedule.create", params), "bad_request", extra)

    def test_an_approval_asked_after_the_schedule_was_saved(self):
        workflow = self.chain()
        schedule = self.schedule(workflow)
        template = {key: self.second[key] for key in ("id", "name", "project_id", "playbook",
                                                      "inventory_id", "survey")}
        self.ok("template.update", {**template, "approval": "other"})
        fired = self.fire(schedule)
        self.assertEqual(fired[0]["outcome"], "error")
        self.assertIn("migrate waits for an approval",
                      self.ok("schedule.list")["schedules"][0]["last_error"])
        # Asked while a scheduled run is under way: the step fails, nobody waits.
        self.ok("template.update", {**template, "approval": "none"})
        run = self.fire(schedule)[0]["run_id"]
        self.ok("template.update", {**template, "approval": "other"})
        self.end(self.steps(run)["a"][1])
        steps = self.steps(run)
        self.assertEqual(steps["b"][1], None)
        self.end(steps["c"][1])
        ended = self.ok("run.get", {"id": run})
        self.assertEqual(ended["status"], "failed")
        self.assertIn("waits for an approval", ended["error"])

    def test_a_scheduled_workflow_is_kept_and_exported_by_name(self):
        workflow = self.chain()
        self.schedule(workflow, overlap="queue")
        self.assertEqual(self.code("workflow.delete", {"id": workflow["id"]}), "conflict")
        document = self.ok("config.export")
        exported = document["schedules"][0]
        self.assertEqual(exported["workflow"], "release")
        self.assertNotIn("template", exported)
        self.ok("schedule.delete", {"id": self.ok("schedule.list")["schedules"][0]["id"]})
        result = self.ok("config.import", {"document": document})
        self.assertEqual(result["failed"], 0, result)
        self.assertEqual(self.ok("config.export")["schedules"], document["schedules"])


class WorkflowTransferTest(WorkflowBase):
    def test_a_workflow_is_exported_by_the_names_of_its_templates(self):
        self.chain(needs_approval=True)
        document = self.ok("config.export")
        exported = document["workflows"][0]
        self.assertEqual([node["template"] for node in exported["nodes"]],
                         ["deploy", "migrate", "notify", "notify"])
        self.assertNotIn("template_id", json.dumps(exported))
        self.ok("workflow.delete", {"id": self.ok("workflow.list")["workflows"][0]["id"]})
        result = self.ok("config.import", {"document": document})
        self.assertEqual(result["failed"], 0, result)
        again = self.ok("config.export")
        self.assertEqual(again["workflows"], document["workflows"])

    def test_a_workflow_is_exported_with_where_its_steps_are_from(self):
        self.ok("workflow.create", {
            "name": "w", "project_id": self.first["project_id"],
            "inventory_id": self.first["inventory_id"],
            "nodes": [{"id": "a", "template_id": self.first["id"]}], "edges": []})
        document = self.ok("config.export")
        exported = document["workflows"][0]
        self.assertEqual((exported["project"], exported["inventory"]), ("site", "inv"))
        self.ok("workflow.delete", {"id": self.ok("workflow.list")["workflows"][0]["id"]})
        self.assertEqual(self.ok("config.import", {"document": document})["failed"], 0)
        again = self.ok("workflow.list")["workflows"][0]
        self.assertEqual((again["project_id"], again["inventory_id"]),
                         (self.first["project_id"], self.first["inventory_id"]))
        # A document from before names neither.
        del exported["project"], exported["inventory"]
        exported["name"] = "old"
        self.ok("config.import", {"document": document})
        old = self.ok("workflow.list")["workflows"][0]
        self.assertEqual((old["name"], old["project_id"], old["inventory_id"]),
                         ("old", None, None))


class PlanTest(unittest.TestCase):
    def test_a_step_with_several_before_it_waits_for_all(self):
        options = {"nodes": [{"id": n} for n in "abc"],
                   "edges": [{"from": "a", "to": "c", "condition": "success"},
                             {"from": "b", "to": "c", "condition": "failure"}]}
        done = {"status": "successful"}
        running = {"status": "running"}
        self.assertEqual(workflows.plan(options, {"a": done, "b": running}, {})["c"], "waiting")
        self.assertEqual(workflows.plan(options, {"a": done, "b": done}, {})["c"], "ready")
        failed = {"status": "failed"}
        self.assertEqual(workflows.plan(options, {"a": failed, "b": done}, {})["c"], "skipped")


class VarsFileTest(unittest.TestCase):
    def test_what_was_passed_on_is_never_rendered(self):
        text = runner.workflow_vars_file({"plain": "{{ x }}", "deep": {"list": ["{% y %}", 1,
                                                                                True, None]}})
        self.assertEqual(text, 'plain: !unsafe "{{ x }}"\n'
                               'deep: {"list": [!unsafe "{% y %}", 1, true, null]}\n')
        for bad in ({"not a name": 1}, {"x": object()}, ["x"]):
            with self.assertRaises(BadRequest):
                runner.workflow_vars_file(bad)


if __name__ == "__main__":
    unittest.main()
