"""Starters: what a new managed project, inventory or playbook begins with."""

import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import paths, starters

PACKAGED = Path(__file__).resolve().parents[2] / "starters"


@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class StarterTest(Base):
    def setUp(self):
        super().setUp()
        self.local = self.root / "local-starters"
        for patch in (mock.patch.object(paths, "STARTERS_DIR", PACKAGED),
                      mock.patch.object(paths, "LOCAL_STARTERS_DIR", self.local)):
            patch.start()
            self.addCleanup(patch.stop)

    def listed(self, kind="all", role="admin"):
        return {(one["kind"], one["ref"]): one
                for one in self.ok("starter.list", {"kind": kind}, role)["starters"]}

    def local_project(self, name, files):
        home = self.local / "projects" / name
        for path, text in files.items():
            (home / path).parent.mkdir(parents=True, exist_ok=True)
            (home / path).write_text(text)
        return home

    def git_files(self, home):
        argv = ["/usr/bin/git", f"--git-dir={home / 'repo.git'}", "ls-files"]
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.split()

    def test_the_package_offers_linux_and_windows_of_each_kind(self):
        listed = self.listed()
        self.assertEqual(sorted(listed), [(kind, f"package/{name}")
                                          for kind in ("inventory", "playbook", "project")
                                          for name in ("linux", "windows")])
        self.assertTrue(all(not one["error"] and one["title"] != one["name"]
                            for one in listed.values()))
        windows = listed[("inventory", "package/windows")]
        self.assertEqual((windows["format"], windows["files"]),
                         ("yaml", ["group_vars/windows.yml", "hosts.yml"]))
        self.assertIn("requirements.yml", listed[("project", "package/windows")]["files"])
        self.assertIn("win_ping", listed[("playbook", "package/windows")]["content"])
        self.assertNotIn("starter.toml", listed[("project", "package/linux")]["files"])
        self.assertEqual(set(self.listed("playbook")), {("playbook", "package/linux"),
                                                        ("playbook", "package/windows")})

    def test_only_an_administrator_lists_them(self):
        self.assertEqual(self.code("starter.list", {}, role="operator"), "forbidden")

    def test_a_local_starter_of_the_same_name_is_offered_beside_the_package_one(self):
        self.local_project("linux", {"starter.toml": 'title = "Ours"\n', "site.yml": "- hosts: all\n"})
        listed = self.listed("project")
        self.assertEqual(listed[("project", "local/linux")]["title"], "Ours")
        self.assertEqual(listed[("project", "local/linux")]["files"], ["site.yml"])
        self.assertIn(("project", "package/linux"), listed)

    def test_a_project_starts_with_the_files_of_its_starter_in_one_commit(self):
        project = self.ok("project.create", {"name": "web", "kind": "managed",
                                             "starter": "package/linux"})
        home = paths.PROJECTS_DIR / project["id"]
        self.assertEqual(sorted(self.git_files(home)), [
            "README.md", "group_vars/all.yml", "ping.yml", "roles/common/defaults/main.yml",
            "roles/common/tasks/main.yml", "site.yml"])
        self.assertEqual((home / "tree" / "site.yml").read_bytes(),
                         (PACKAGED / "projects/linux/site.yml").read_bytes())
        commits = self.ok("file.history", {"id": project["id"]})["commits"]
        self.assertEqual([one["subject"] for one in commits], ["Start from Linux project"])
        self.assertTrue(self.ok("project.list")["projects"][0]["revision"])

    def test_an_inventory_starts_with_its_starter_in_its_format(self):
        inventory = self.ok("inventory.create", {"name": "pcs", "kind": "managed",
                                                 "starter": "package/windows"})
        self.assertEqual(inventory["format"], "yaml")
        tree = paths.INVENTORIES_DIR / inventory["id"] / "tree"
        self.assertIn("ansible_shell_type: powershell",
                      (tree / "group_vars" / "windows.yml").read_text())
        self.assertIn("windows:", self.ok("inventory.get", {"id": inventory["id"]})["content"])
        ini = self.local / "inventories" / "flat"
        ini.mkdir(parents=True)
        (ini / "hosts").write_text("[web]\na\n")
        self.assertEqual(self.ok("inventory.create", {"name": "flat", "kind": "managed",
                                                      "starter": "local/flat"})["format"], "ini")
        self.assertEqual(self.code("inventory.create", {"name": "x", "kind": "managed",
                                                        "format": "ini",
                                                        "starter": "package/linux"}),
                         "bad_request")

    def test_a_starter_is_refused_where_it_does_not_apply(self):
        for params in ({"kind": "managed", "starter": "package/linux", "content": "all:\n"},
                       {"kind": "managed", "starter": "package/linux",
                        "files": [{"path": "a", "content": ""}]},
                       {"kind": "file", "starter": "package/linux", "project_id": "p1",
                        "path": "hosts"}):
            self.assertEqual(self.code("inventory.create", {"name": "x", **params}),
                             "bad_request", params)
        self.assertEqual(self.code("project.create", {"name": "x", "kind": "archive",
                                                      "starter": "package/linux"}), "bad_request")
        self.assertEqual(self.ok("inventory.list")["inventories"], [])
        self.assertEqual(self.ok("project.list")["projects"], [])

    def test_unknown_and_malformed_starters(self):
        self.assertEqual(self.code("project.create", {"name": "x", "kind": "managed",
                                                      "starter": "package/nothing"}), "not_found")
        for ref in ("linux", "other/linux", "package/../linux", "package/.hidden"):
            self.assertEqual(self.code("project.create", {"name": "x", "kind": "managed",
                                                          "starter": ref}), "bad_request", ref)
        # A playbook starter is not a project starter.
        self.local.joinpath("playbooks").mkdir(parents=True)
        (self.local / "playbooks" / "only.yml").write_text("- hosts: all\n")
        self.assertEqual(self.code("project.create", {"name": "x", "kind": "managed",
                                                      "starter": "local/only"}), "not_found")

    def test_a_starter_with_a_link_or_a_large_file_is_listed_with_why_and_not_used(self):
        home = self.local_project("linked", {"site.yml": "- hosts: all\n"})
        (home / "secret").symlink_to(self.root / "fleet.db")
        big = self.local_project("big", {"site.yml": "- hosts: all\n"})
        (big / "blob").write_bytes(b"x" * (starters.MAX_FILE + 1))
        listed = self.listed("project")
        self.assertIn("secret", listed[("project", "local/linked")]["error"])
        self.assertIn("blob", listed[("project", "local/big")]["error"])
        self.assertEqual(listed[("project", "local/big")]["files"], [])
        self.assertEqual(self.code("project.create", {"name": "x", "kind": "managed",
                                                      "starter": "local/linked"}), "bad_request")
        self.assertEqual(self.ok("project.list")["projects"], [])

    def test_a_starter_whose_directory_is_a_link_is_not_followed(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "site.yml").write_text("- hosts: all\n")
        home = self.local_project("outer", {"site.yml": "- hosts: all\n"})
        (home / "roles").symlink_to(target)
        self.assertIn("roles", self.listed("project")[("project", "local/outer")]["error"])

    def test_bad_metadata_is_an_error_of_the_starter(self):
        self.local_project("meta", {"starter.toml": "title = [1]\n", "site.yml": ""})
        self.assertIn("title", self.listed("project")[("project", "local/meta")]["error"])


if __name__ == "__main__":
    unittest.main()
