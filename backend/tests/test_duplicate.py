"""Duplicating a managed project or inventory: its files, not its history."""

import base64
import os
import unittest

from test_phase2 import Base

from cockpit_fleet import paths

PLAYBOOK = "- hosts: all\n  tasks: []\n"


@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class DuplicateTest(Base):
    def subjects(self, container_id):
        return [one["subject"] for one in self.ok("file.history", {"id": container_id})["commits"]]

    def syntax_runs(self):
        return [run for run in self.ok("run.list")["runs"] if run["kind"] == "syntax"]

    def test_a_project_is_copied_as_main_has_it_in_one_commit(self):
        source = self.ok("project.create", {"name": "web", "kind": "managed"})["id"]
        self.ok("file.change", {"id": source, "operations": [
            {"op": "write", "path": "site.yaml", "content": PLAYBOOK},
            {"op": "write", "path": "files/blob.bin",
             "data": base64.b64encode(b"\xff\x00").decode()},
            {"op": "mkdir", "path": "roles/web/templates"}]})
        self.ok("file.write", {"id": source, "path": "site.yaml", "content": PLAYBOOK + "# 2\n",
                               "expected": self.ok("file.read", {"id": source,
                                                                 "path": "site.yaml"})["sha256"]})
        before = len(self.syntax_runs())
        copy = self.ok("project.duplicate", {"id": source, "name": "web 2"})
        self.assertEqual((copy["name"], copy["kind"]), ("web 2", "managed"))
        self.assertNotEqual(copy["id"], source)
        self.assertEqual(self.subjects(copy["id"]), ["Copy of web"])
        listed = self.ok("file.list", {"id": copy["id"]})
        self.assertEqual(sorted(one["path"] for one in listed["files"]),
                         ["files/blob.bin", "site.yaml"])
        self.assertIn("roles/web/templates", listed["directories"])
        tree = paths.PROJECTS_DIR / copy["id"] / "tree"
        self.assertEqual((tree / "site.yaml").read_text(), PLAYBOOK + "# 2\n")
        self.assertEqual((tree / "files/blob.bin").read_bytes(), b"\xff\x00")
        self.assertEqual(len(self.syntax_runs()), before)
        # The two go their own ways.
        self.ok("file.delete", {"id": copy["id"], "path": "site.yaml"})
        self.assertTrue((paths.PROJECTS_DIR / source / "tree" / "site.yaml").is_file())

    def test_an_empty_project_is_copied_empty(self):
        source = self.ok("project.create", {"name": "empty", "kind": "managed"})["id"]
        copy = self.ok("project.duplicate", {"id": source, "name": "empty 2"})
        self.assertEqual(self.ok("file.list", {"id": copy["id"]})["files"], [])

    def test_only_a_managed_project_by_an_administrator_under_a_new_name(self):
        local = self.ok("project.create", {"name": "here", "kind": "local",
                                           "path": str(self.root / "src")})["id"]
        self.assertEqual(self.code("project.duplicate", {"id": local, "name": "x"}),
                         "bad_request")
        managed = self.ok("project.create", {"name": "web", "kind": "managed"})["id"]
        self.assertEqual(self.code("project.duplicate", {"id": managed, "name": "web"}),
                         "conflict")
        self.assertEqual(self.code("project.duplicate", {"id": managed, "name": "x"},
                                   role="operator"), "forbidden")
        self.assertEqual(self.code("project.duplicate", {"id": "p999", "name": "x"}),
                         "not_found")
        self.assertEqual(len(self.ok("project.list")["projects"]), 2)
        self.assertEqual(sorted(os.listdir(paths.PROJECTS_DIR)), [managed])

    def test_an_inventory_keeps_its_files_settings_and_sets_not_its_users(self):
        vault = self.ok("credential.create", {"name": "v", "kind": "vault"})["id"]
        varset = self.ok("varset.create", {"name": "lab", "variables": {"a": 1}})["id"]
        source = self.ok("inventory.create", {
            "name": "pcs", "kind": "managed", "format": "ini", "content": "[web]\na\n",
            "files": [{"path": "group_vars/web.yaml", "content": "x: 1\n"}],
            "vault_credential_id": vault, "variable_set_ids": [varset]})["id"]
        copy = self.ok("inventory.duplicate", {"id": source, "name": "pcs 2"})
        self.assertEqual((copy["kind"], copy["format"], copy["vault_credential_id"],
                          copy["variable_set_ids"]), ("managed", "ini", vault, [varset]))
        self.assertEqual(self.subjects(copy["id"]), ["Copy of pcs"])
        self.assertEqual(self.ok("inventory.get", {"id": copy["id"]})["content"], "[web]\na\n")
        tree = paths.INVENTORIES_DIR / copy["id"] / "tree"
        self.assertEqual((tree / "group_vars/web.yaml").read_text(), "x: 1\n")
        # Read once made, as a change of its files would read it.
        self.assertIn("pcs 2", [run["title"] for run in self.ok("run.list")["runs"]
                                if run["kind"] == "inventory"])

    def test_an_inventory_of_a_project_is_not_duplicated(self):
        project = self.ok("project.create", {"name": "web", "kind": "managed"})["id"]
        inventory = self.ok("inventory.create", {"name": "f", "kind": "file",
                                                 "project_id": project, "path": "hosts"})["id"]
        self.assertEqual(self.code("inventory.duplicate", {"id": inventory, "name": "g"}),
                         "bad_request")


if __name__ == "__main__":
    unittest.main()
