"""Inventories written in the interface: a tree of files with its repository."""

import base64
import json
import os
import subprocess
import unittest

from test_phase2 import Base

from cockpit_fleet import checks, paths, transfer  # noqa: F401  (their commands)

SCHOOL = [
    {"path": "hosts.yaml", "content": "all:\n  children:\n    students:\n      hosts:\n"
                                      "        pc01:\n"},
    {"path": "group_vars/students/vars.yaml", "content": "ansible_user: student\n"},
    {"path": "host_vars/pc01.yaml", "content": "ansible_host: 192.0.2.21\n"},
    {"path": "group_vars/all/vault.yaml.example", "content": "secret: x\n"},
]


@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class InventoryTreeTest(Base):
    def home(self, inventory_id):
        return paths.INVENTORIES_DIR / inventory_id

    def git(self, inventory_id, *args):
        home = self.home(inventory_id)
        return subprocess.run(["/usr/bin/git", f"--git-dir={home / 'repo.git'}",
                               f"--work-tree={home / 'tree'}", *args],
                              capture_output=True, text=True, check=True).stdout

    def spec_of(self, run_id):
        return json.loads((paths.RUNS_DIR / run_id / "spec.json").read_text())

    def test_the_text_of_an_inventory_of_before_becomes_its_first_file(self):
        ctx = self.ctx()
        ctx.conn.execute(
            "INSERT INTO inventories (id, name, kind, format, content, created_at)"
            " VALUES ('iold', 'old', 'managed', 'ini', '[web]\nweb01\n', 0)")
        ctx.conn.commit()
        got = self.ok("inventory.get", {"id": "iold"})
        self.assertEqual(got["content"], "[web]\nweb01\n")
        self.assertEqual((self.home("iold") / "tree" / "hosts").read_text(), "[web]\nweb01\n")
        self.assertEqual(self.git("iold", "log", "--format=%an %s").strip(),
                         "cockpit-fleet Take the inventory written before as its first file")
        row = self.ctx().conn.execute("SELECT content, revision FROM inventories"
                                      " WHERE id = 'iold'").fetchone()
        self.assertEqual(row["content"], "")
        self.assertEqual(len(row["revision"]), 40)
        # Once only.
        self.ok("inventory.get", {"id": "iold"})
        self.assertEqual(len(self.git("iold", "log", "--format=%H").split()), 1)

    def test_created_with_its_main_file_and_changed_as_commits(self):
        made = self.ok("inventory.create", {"name": "web", "kind": "managed",
                                            "content": "all: {}\n"})
        tree = self.home(made["id"]) / "tree"
        self.assertEqual(os.listdir(tree), ["hosts.yml"])
        self.ok("inventory.update", {"id": made["id"], "content": "all:\n  hosts: {}\n"})
        self.assertEqual((tree / "hosts.yml").read_text(), "all:\n  hosts: {}\n")
        # Another format: the main file of the other one goes.
        self.ok("inventory.update", {"id": made["id"], "format": "ini", "content": "web01\n"})
        self.assertEqual(os.listdir(tree), ["hosts"])
        self.assertEqual(self.git(made["id"], "log", "--format=%an").split(), ["anna"] * 3)
        # Only the name: no commit.
        self.ok("inventory.update", {"id": made["id"], "name": "web2"})
        self.assertEqual(len(self.git(made["id"], "log", "--format=%H").split()), 3)
        self.assertEqual(self.ok("inventory.get", {"id": made["id"]})["content"], "web01\n")
        self.assertEqual(self.ok("inventory.get", {"id": made["id"]}, role="viewer")["content"], "")

    def test_a_tree_like_a_school_is_written_read_by_a_job_and_copied_for_it(self):
        made = self.ok("inventory.create", {"name": "school", "kind": "managed",
                                            "files": SCHOOL})
        listed = self.ok("file.list", {"id": made["id"]})
        self.assertEqual(sorted(entry["path"] for entry in listed["files"]),
                         sorted(item["path"] for item in SCHOOL))
        self.assertTrue(listed["editable"])
        # A change in the editor reads the inventory again.
        current = self.ok("file.read", {"id": made["id"], "path": "host_vars/pc01.yaml"})
        done = self.ok("file.change", {"id": made["id"], "operations": [
            {"op": "write", "path": "host_vars/pc01.yaml", "content": "ansible_host: 192.0.2.22\n",
             "expected": current["sha256"]},
            {"op": "copy", "path": "group_vars/students", "to": "group_vars/teachers"}]})
        self.assertEqual(len(done["inventory_run_ids"]), 1)
        run_id = done["inventory_run_ids"][0]
        run = self.ok("run.get", {"id": run_id})
        self.assertEqual((run["kind"], run["inventory_id"]), ("inventory", made["id"]))
        self.assertEqual(self.spec_of(run_id)["inventory"], ".")
        copy = paths.RUNS_DIR / run_id / "inventory"
        self.assertEqual((copy / "host_vars/pc01.yaml").read_text(), "ansible_host: 192.0.2.22\n")
        self.assertTrue((copy / "group_vars/teachers/vars.yaml").is_file())
        # A file or a directory of the tree as the source.
        self.ok("inventory.update", {"id": made["id"], "path": "hosts.yaml"})
        run_id = self.ok("inventory.parse", {"id": made["id"]})["run_id"]
        self.assertEqual(self.spec_of(run_id)["inventory"], "hosts.yaml")
        for path in ("../x", "/etc", ".git/config"):
            self.assertEqual(self.code("inventory.update", {"id": made["id"], "path": path}),
                             "bad_request", path)
        # History and restore work on it as on a project.
        commits = self.ok("file.history", {"id": made["id"]})["commits"]
        self.assertEqual(len(commits), 2)
        self.ok("file.restore", {"id": made["id"], "path": "host_vars",
                                 "revision": commits[-1]["revision"]})
        self.assertEqual(self.ok("file.read", {"id": made["id"], "path": "host_vars/pc01.yaml"})
                         ["content"], "ansible_host: 192.0.2.21\n")

    def test_what_is_only_for_projects_or_only_for_trees(self):
        made = self.ok("inventory.create", {"name": "web", "kind": "managed", "content": ""})
        self.assertEqual(self.code("file.check", {"id": made["id"], "path": "hosts.yml"}),
                         "conflict")
        project = self.ok("project.create", {"name": "site", "kind": "managed"})
        self.ok("file.write", {"id": project["id"], "path": "hosts.yml", "content": "all: {}\n",
                               "expected": ""})
        on_file = self.ok("inventory.create", {"name": "f", "kind": "file",
                                               "project_id": project["id"], "path": "hosts.yml"})
        self.assertEqual(self.code("file.list", {"id": on_file["id"]}), "conflict")
        self.assertEqual(self.code("file.list", {"id": made["id"]}, role="operator"), "forbidden")
        self.assertEqual(self.code("file.list", {"id": "i0000"}), "not_found")
        self.assertEqual(self.code("inventory.create", {"name": "bad", "kind": "managed",
                                                        "files": [{"path": "../x",
                                                                   "content": ""}]}),
                         "bad_request")
        self.assertEqual(self.code("inventory.create", {"name": "bad", "kind": "managed",
                                                        "files": [{"op": "delete",
                                                                   "path": "x"}]}),
                         "bad_request")
        self.assertNotIn("bad", [i["name"] for i in self.ok("inventory.list")["inventories"]])

    def test_deleted_with_its_files(self):
        made = self.ok("inventory.create", {"name": "web", "kind": "managed", "content": "x"})
        self.assertTrue(self.home(made["id"]).is_dir())
        self.ok("inventory.delete", {"id": made["id"]})
        self.assertFalse(self.home(made["id"]).exists())

    def test_exported_and_imported_with_its_files(self):
        blob = base64.b64encode(b"\xff\x00binary").decode()
        self.ok("inventory.create", {"name": "school", "kind": "managed", "path": "hosts.yaml",
                                     "files": [*SCHOOL, {"path": "files/key.bin",
                                                         "data": blob}]})
        document = self.ok("config.export")
        item = document["inventories"][0]
        self.assertEqual(len(item["files"]), 5)
        self.assertIn({"path": "files/key.bin", "data": blob}, item["files"])
        self.ok("inventory.delete", {"id": self.ok("inventory.list")["inventories"][0]["id"]})
        self.ok("config.import", {"document": document})
        again = self.ok("inventory.list")["inventories"][0]
        self.assertEqual(again["path"], "hosts.yaml")
        self.assertEqual(self.ok("file.read", {"id": again["id"], "path": "host_vars/pc01.yaml"})
                         ["content"], SCHOOL[2]["content"])
        # A document of before trees: its text becomes the main file.
        old = {**document, "inventories": [{"name": "old", "kind": "managed", "format": "yaml",
                                            "content": "all: {}\n"}]}
        self.ok("config.import", {"document": old})
        made = next(i for i in self.ok("inventory.list")["inventories"] if i["name"] == "old")
        self.assertEqual(self.ok("inventory.get", {"id": made["id"]})["content"], "all: {}\n")
        # The audit trail has the paths of the files, not what they hold.
        entries = self.ok("audit.list")["entries"]
        created = [entry for entry in entries if entry["command"] == "inventory.create"]
        self.assertTrue(any("host_vars/pc01.yaml" in entry["params"] for entry in created))
        self.assertFalse(any("192.0.2.21" in entry["params"] for entry in entries))


class OtherProjectTest(Base):
    """A file inventory of one project used by templates of another."""

    def setUp(self):
        super().setUp()
        self.playbooks = self.local_project("playbooks")
        tree = self.root / "src" / "labs"
        (tree / "inventories" / "school" / "host_vars").mkdir(parents=True)
        (tree / "inventories" / "school" / "hosts.yaml").write_text("all:\n  hosts:\n    pc01:\n")
        (tree / "inventories" / "school" / "host_vars" / "pc01.yaml").write_text("a: 1\n")
        (tree / "inventories" / "other.yml").write_text("all: {}\n")
        (tree / "hosts").write_text("pc02\n")
        (tree / "site.yml").write_text("- hosts: all\n")
        self.tree = tree
        self.labs = self.ok("project.create", {"name": "labs", "kind": "local", "path": str(tree)})

    def inventory(self, name, path):
        return self.ok("inventory.create", {"name": name, "kind": "file",
                                            "project_id": self.labs["id"], "path": path})

    def launch(self, inventory):
        template = self.ok("template.create", {"name": inventory["name"],
                                               "project_id": self.playbooks["id"],
                                               "playbook": "site.yml",
                                               "inventory_id": inventory["id"]})
        run = self.ok("run.start", {"template_id": template["id"]})
        directory = paths.RUNS_DIR / run["id"]
        return directory, json.loads((directory / "spec.json").read_text())

    def copied(self, directory):
        return sorted(str(path.relative_to(directory / "inventory"))
                      for path in (directory / "inventory").rglob("*") if path.is_file())

    def test_what_the_inventory_reads_is_copied_apart(self):
        for path, wanted in (
                ("inventories/school", ["inventories/school/host_vars/pc01.yaml",
                                        "inventories/school/hosts.yaml"]),
                ("inventories/school/hosts.yaml", ["inventories/school/host_vars/pc01.yaml",
                                                   "inventories/school/hosts.yaml"]),
                ("hosts", ["hosts", "inventories/other.yml", "inventories/school/host_vars/pc01.yaml",
                           "inventories/school/hosts.yaml", "site.yml"])):
            directory, spec = self.launch(self.inventory(path.replace("/", "-"), path))
            self.assertEqual((spec["inventory"], spec["inventory_source"]), (path, "copy"), path)
            self.assertEqual(self.copied(directory), wanted, path)
            # The project of the template is the one copied as the project.
            self.assertTrue((directory / "project" / "inventories" / "hosts.yml").is_file())

    def test_a_link_on_the_way_is_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "hosts.yaml").write_text("secret\n")
        os.symlink(outside, self.tree / "linked")
        inventory = self.inventory("linked", "linked/hosts.yaml")
        template = self.ok("template.create", {"name": "linked",
                                               "project_id": self.playbooks["id"],
                                               "playbook": "site.yml",
                                               "inventory_id": inventory["id"]})
        self.assertEqual(self.code("run.start", {"template_id": template["id"]}), "conflict")

    def test_a_check_of_the_user_and_an_ad_hoc_command_may_use_it_too(self):
        inventory = self.inventory("school", "inventories/school")
        self.ok("check.create", {"name": "queue", "kind": "playbook",
                                 "inventory_id": inventory["id"],
                                 "project_id": self.playbooks["id"], "playbook": "site.yml"})
        run = self.ok("run.adhoc", {"inventory_id": inventory["id"],
                                    "project_id": self.playbooks["id"], "limit": "all",
                                    "module": "ansible.builtin.ping"})
        spec = json.loads((paths.RUNS_DIR / run["id"] / "spec.json").read_text())
        self.assertEqual(spec["inventory_source"], "copy")
