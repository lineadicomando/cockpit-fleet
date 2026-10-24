"""Workflows: steps start by how the steps before them ended, pass on what
set_stats set, wait for approvals, and can be canceled and resumed."""

import json
import unittest

from test_approvals import ApprovalBase

from cockpit_fleet import jobs, paths, runner, workflows  # noqa: F401
from cockpit_fleet.errors import BadRequest


class WorkflowBase(ApprovalBase):
    def setUp(self):
        super().setUp()
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
