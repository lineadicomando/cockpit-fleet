"""Projects and inventories kept as starters: offered as such, used by no run."""

import json
import os
import unittest

from test_checks import LISTING, CheckBase

from cockpit_fleet import paths

PLAYBOOK = "- hosts: all\n  tasks: []\n"


@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class SavedStarterTest(CheckBase):
    def project(self, name="model"):
        project = self.ok("project.create", {"name": name, "kind": "managed"})
        self.ok("file.change", {"id": project["id"], "operations": [
            {"op": "write", "path": "site.yaml", "content": PLAYBOOK},
            {"op": "mkdir", "path": "files"}]})
        return project

    def managed_inventory(self, name="pcs"):
        return self.ok("inventory.create", {"name": name, "kind": "managed", "format": "ini",
                                            "path": "hosts", "content": "[web]\na\n"})

    def saved(self):
        return {one["ref"]: one for one in self.ok("starter.list")["starters"]
                if one["source"] == "saved"}

    def test_a_project_kept_as_a_starter_is_offered_and_copied(self):
        model = self.project()
        self.assertFalse(model["starter"])
        self.assertTrue(self.ok("project.starter", {"id": model["id"], "starter": True})["starter"])
        ref = f"saved/{model['id']}"
        offered = self.saved()[ref]
        self.assertEqual((offered["kind"], offered["title"], offered["files"], offered["error"]),
                         ("project", "model", ["site.yaml"], ""))
        made = self.ok("project.create", {"name": "web", "kind": "managed", "starter": ref})
        self.assertFalse(made["starter"])
        listed = self.ok("file.list", {"id": made["id"]})
        self.assertEqual(([one["path"] for one in listed["files"]], listed["directories"]),
                         (["site.yaml"], ["files"]))
        commits = self.ok("file.history", {"id": made["id"]})["commits"]
        self.assertEqual([one["subject"] for one in commits], ["Start from model"])
        # Still a project of the list, edited as before; usable again when unmarked.
        self.ok("file.write", {"id": model["id"], "path": "more.yaml", "content": PLAYBOOK,
                               "expected": ""})
        self.assertIn("more.yaml", self.saved()[ref]["files"])
        self.ok("project.starter", {"id": model["id"], "starter": False})
        self.assertEqual(self.saved(), {})
        self.assertEqual(self.code("project.create", {"name": "x", "kind": "managed",
                                                      "starter": ref}), "not_found")

    def test_an_inventory_kept_as_a_starter_gives_its_format_and_path(self):
        model = self.managed_inventory()
        self.ok("inventory.starter", {"id": model["id"], "starter": True})
        offered = self.saved()[f"saved/{model['id']}"]
        self.assertEqual((offered["kind"], offered["format"], offered["path"], offered["files"]),
                         ("inventory", "ini", "hosts", ["hosts"]))
        made = self.ok("inventory.create", {"name": "lab", "kind": "managed",
                                            "starter": f"saved/{model['id']}"})
        self.assertEqual((made["format"], made["path"], made["starter"]), ("ini", "hosts", False))
        self.assertEqual(self.ok("inventory.get", {"id": made["id"]})["content"], "[web]\na\n")

    def test_no_run_uses_one(self):
        model = self.project()
        inventory = self.managed_inventory()
        self.ok("project.starter", {"id": model["id"], "starter": True})
        self.ok("inventory.starter", {"id": inventory["id"], "starter": True})
        usable = self.managed_inventory("usable")
        other = self.project("other")
        template = {"name": "t", "playbook": "site.yaml"}
        self.assertEqual(self.code("template.create", {**template, "project_id": model["id"],
                                                       "inventory_id": usable["id"]}), "conflict")
        self.assertEqual(self.code("template.create", {**template, "project_id": other["id"],
                                                       "inventory_id": inventory["id"]}),
                         "conflict")
        self.assertEqual(self.code("inventory.create", {"name": "f", "kind": "file",
                                                        "project_id": model["id"],
                                                        "path": "hosts"}), "conflict")
        self.assertEqual(self.code("run.adhoc", {"inventory_id": inventory["id"],
                                                 "module": "ansible.builtin.command",
                                                 "free_form": "uptime", "limit": "all"}),
                         "conflict")
        self.assertEqual(self.code("check.create", {"name": "d", "kind": "disk",
                                                    "inventory_id": inventory["id"]}), "conflict")
        self.assertEqual(self.code("maintenance.create", {"inventory_id": inventory["id"],
                                                          "group": "web",
                                                          "ends_at": int(self.clock + 60)}),
                         "conflict")
        # At launch too, whatever the database says of the template.
        made = self.ok("template.create", {**template, "project_id": other["id"],
                                           "inventory_id": usable["id"]})
        self.ok("project.starter", {"id": other["id"], "starter": False})
        with self.ctx().conn as conn:
            conn.execute("UPDATE projects SET starter = 1 WHERE id = ?", (other["id"],))
        self.assertEqual(self.code("run.start", {"template_id": made["id"]}), "conflict")
        # Its files are still checked and read.
        self.ok("file.check", {"id": model["id"], "path": "site.yaml"})
        self.ok("inventory.parse", {"id": inventory["id"]})

    def test_one_in_use_is_not_kept_as_a_starter(self):
        project = self.project()
        inventory = self.managed_inventory()
        self.ok("template.create", {"name": "deploy", "playbook": "site.yaml",
                                    "project_id": project["id"], "inventory_id": inventory["id"]})
        self.assertEqual(self.code("project.starter", {"id": project["id"], "starter": True}),
                         "conflict")
        self.assertEqual(self.code("inventory.starter", {"id": inventory["id"], "starter": True}),
                         "conflict")
        local = self.local_project()
        self.assertEqual(self.code("project.starter", {"id": local["id"], "starter": True}),
                         "bad_request")
        self.assertEqual(self.code("project.starter", {"id": project["id"], "starter": True},
                                   role="operator"), "forbidden")

    def test_the_hosts_of_a_starter_are_no_nodes(self):
        inventory = self.inventory()
        self.assertTrue(self.ok("host.list")["hosts"])
        self.ok("inventory.starter", {"id": inventory["id"], "starter": True})
        self.assertEqual(self.ok("host.list")["hosts"], [])
        self.assertEqual(self.ok("hostkey.list")["untrusted"], [])
        # Read again, they stay out; back in the list once it is usable again.
        self.read(inventory)
        self.assertEqual(self.ok("host.list")["hosts"], [])
        self.ok("inventory.starter", {"id": inventory["id"], "starter": False})
        queued = [run for run in self.ok("run.list")["runs"]
                  if run["kind"] == "inventory" and run["status"] in ("queued", "running")]
        self.assertEqual(len(queued), 1)
        self.read(inventory, queued[0]["id"])
        self.assertTrue(self.ok("host.list")["hosts"])

    def read(self, inventory, run_id=None):
        run_id = run_id or self.ok("inventory.parse", {"id": inventory["id"]})["run_id"]
        (paths.RUNS_DIR / run_id / "inventory.json").write_text(json.dumps(LISTING))
        self.finish(run_id)
        self.ok("run.list")  # reconcile

    def test_export_and_import_keep_the_flag(self):
        project = self.project()
        inventory = self.managed_inventory()
        self.ok("project.starter", {"id": project["id"], "starter": True})
        self.ok("inventory.starter", {"id": inventory["id"], "starter": True})
        document = self.ok("config.export")
        self.assertTrue(document["projects"][0]["starter"])
        self.assertTrue(document["inventories"][0]["starter"])
        with self.ctx().conn as conn:
            conn.execute("DELETE FROM inventory_variable_sets")
            conn.execute("DELETE FROM inventories")
            conn.execute("DELETE FROM projects")
        report = self.ok("config.import", {"document": document})
        self.assertEqual(report["failed"], 0, report["report"])
        self.assertTrue(self.ok("project.list")["projects"][0]["starter"])
        self.assertTrue(self.ok("inventory.list")["inventories"][0]["starter"])


if __name__ == "__main__":
    unittest.main()
