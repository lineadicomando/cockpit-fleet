"""Projects written in the interface: files, commits and syntax checks."""

import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import db, files, paths, runner
from cockpit_fleet.errors import BadRequest
from cockpit_fleet.validators import check_file_path

PLAYBOOK = "- hosts: all\n  tasks: []\n"


class Managed(Base):
    """A project written in the interface, and how its repository is looked at."""

    def setUp(self):
        super().setUp()
        self.project = self.ok("project.create", {"name": "edited", "kind": "managed"})
        self.id = self.project["id"]
        self.tree = paths.PROJECTS_DIR / self.id / "tree"

    def write(self, path, content, expected="", **extra):
        return self.ok("file.write", {"id": self.id, "path": path, "content": content,
                                      "expected": expected, **extra})

    def git(self, *args):
        home = paths.PROJECTS_DIR / self.id
        argv = ["/usr/bin/git", f"--git-dir={home / 'repo.git'}", f"--work-tree={self.tree}", *args]
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout



@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class ManagedProjectTest(Managed):
    def test_a_gitignore_in_the_tree_keeps_nothing_out_of_the_history(self):
        self.write(".gitignore", "evil.yml\n")
        self.write("evil.yml", PLAYBOOK)
        self.assertIn("evil.yml", self.git("ls-files").split())
        commits = self.ok("file.history", {"id": self.id, "path": "evil.yml"})["commits"]
        self.assertEqual(len(commits), 1)

    def test_a_new_project_is_an_empty_tree_with_its_repository_beside_it(self):
        self.assertEqual((self.project["kind"], self.project["revision"]), ("managed", ""))
        self.assertEqual(list(self.tree.iterdir()), [])
        self.assertTrue((paths.PROJECTS_DIR / self.id / "repo.git" / "HEAD").is_file())
        listed = self.ok("file.list", {"id": self.id})
        self.assertEqual((listed["files"], listed["editable"]), ([], True))
        self.assertEqual(self.ok("file.history", {"id": self.id})["commits"], [])

    def test_each_write_is_a_commit_by_the_caller(self):
        first = self.write("roles/web/tasks/main.yml", "- debug:\n")
        self.assertEqual(len(first["revision"]), 40)
        self.assertIsNone(first["check_run_id"])
        second = self.write("roles/web/tasks/main.yml", "- debug:\n    msg: hi\n",
                            first["sha256"], message="Say hi")
        self.assertEqual((self.tree / "roles/web/tasks/main.yml").read_text(),
                         "- debug:\n    msg: hi\n")
        self.assertEqual(self.ok("project.list")["projects"][0]["revision"], second["revision"])

        commits = self.ok("file.history", {"id": self.id})["commits"]
        self.assertEqual([c["subject"] for c in commits],
                         ["Say hi", "Add roles/web/tasks/main.yml"])
        self.assertEqual({c["author"] for c in commits}, {"anna"})
        self.assertEqual(self.git("log", "-1", "--format=%cn").strip(), "cockpit-fleet")
        diff = self.ok("file.diff", {"id": self.id, "revision": second["revision"]})
        self.assertIn("+    msg: hi", diff["text"])
        self.assertFalse(diff["cut"])
        # The content is not kept in the audit trail.
        audit = self.ok("audit.list")["entries"][0]
        self.assertEqual(audit["command"], "file.write")
        self.assertNotIn("msg: hi", audit["params"])

    def test_writing_the_same_content_makes_no_commit(self):
        first = self.write("a.yml", "x: 1\n")
        again = self.write("a.yml", "x: 1\n", first["sha256"])
        self.assertEqual(again["revision"], first["revision"])
        self.assertEqual(len(self.ok("file.history", {"id": self.id})["commits"]), 1)

    def test_a_file_changed_meanwhile_is_not_overwritten(self):
        first = self.write("a.yml", "x: 1\n")
        self.write("a.yml", "x: 2\n", first["sha256"])
        stale = {"id": self.id, "path": "a.yml", "content": "x: 3\n", "expected": first["sha256"]}
        self.assertEqual(self.code("file.write", stale), "conflict")
        self.assertEqual(self.code("file.write", {**stale, "expected": ""}), "conflict")
        self.assertEqual(self.code("file.write", {**stale, "path": "gone.yml"}), "conflict")
        self.assertEqual((self.tree / "a.yml").read_text(), "x: 2\n")

    def test_read_returns_the_text_and_what_a_write_must_quote(self):
        written = self.write("vars/all.yml", "città: Roma\n")
        got = self.ok("file.read", {"id": self.id, "path": "vars/all.yml"})
        self.assertEqual((got["content"], got["sha256"]), ("città: Roma\n", written["sha256"]))
        self.assertEqual(self.code("file.read", {"id": self.id, "path": "nope.yml"}), "not_found")
        # A file that is not text is said to be one, without its content.
        (self.tree / "blob.bin").write_bytes(b"\xff\xfe\x00")
        blob = self.ok("file.read", {"id": self.id, "path": "blob.bin"})
        self.assertEqual((blob["binary"], blob["content"], blob["size"]), (True, "", 3))
        self.assertFalse(got["binary"])
        (self.tree / "big.txt").write_bytes(b"x" * (files.MAX_FILE + 1))
        self.assertEqual(self.code("file.read", {"id": self.id, "path": "big.txt"}), "conflict")

    def test_paths_cannot_leave_the_tree_or_reach_the_repository(self):
        for path in ("../x", "/etc/passwd", "a/../../x", ".git/config", "a/.GIT/hooks/pre-commit",
                     "-rf", "a\nb", "a\\b", " a", "", "."):
            self.assertEqual(self.code("file.write", {"id": self.id, "path": path, "content": "",
                                                      "expected": ""}), "bad_request", path)
            self.assertEqual(self.code("file.read", {"id": self.id, "path": path}),
                             "bad_request", path)
        self.assertEqual(list(self.tree.iterdir()), [])

    def test_symbolic_links_are_never_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_text("s3cret")
        os.symlink(outside, self.tree / "dir")
        os.symlink(outside / "secret", self.tree / "link")
        for path in ("dir/secret", "link"):
            self.assertEqual(self.code("file.read", {"id": self.id, "path": path}), "conflict")
            self.assertEqual(self.code("file.write", {"id": self.id, "path": path, "content": "x",
                                                      "expected": ""}), "conflict")
        self.assertEqual(self.code("file.write", {"id": self.id, "path": "dir/new", "content": "x",
                                                  "expected": ""}), "conflict")
        self.assertEqual((outside / "secret").read_text(), "s3cret")
        self.assertEqual(sorted(os.listdir(outside)), ["secret"])
        self.assertEqual(self.ok("file.list", {"id": self.id})["files"], [])

    def test_delete_and_move_are_commits_and_leave_no_empty_directory(self):
        self.write("roles/web/tasks/main.yml", "a\n")
        self.write("site.yml", "b\n")
        self.ok("file.move", {"id": self.id, "path": "roles/web/tasks/main.yml",
                              "to": "roles/db/tasks/main.yml"})
        self.assertFalse((self.tree / "roles" / "web").exists())
        self.assertEqual([f["path"] for f in self.ok("file.list", {"id": self.id})["files"]],
                         ["site.yml", "roles/db/tasks/main.yml"])
        self.assertEqual(self.code("file.move", {"id": self.id, "path": "site.yml",
                                                 "to": "roles/db/tasks/main.yml"}), "conflict")
        self.assertEqual(self.code("file.move", {"id": self.id, "path": "nope", "to": "x"}),
                         "not_found")
        self.ok("file.delete", {"id": self.id, "path": "roles/db/tasks/main.yml"})
        self.assertFalse((self.tree / "roles").exists())
        self.assertEqual(self.code("file.delete", {"id": self.id, "path": "site.yml/x"}),
                         "conflict")
        self.assertEqual(self.code("file.delete", {"id": self.id, "path": "nope"}), "not_found")
        subjects = [c["subject"] for c in self.ok("file.history", {"id": self.id})["commits"]]
        self.assertEqual(subjects[:2], ["Delete roles/db/tasks/main.yml",
                                        "Rename roles/web/tasks/main.yml to roles/db/tasks/main.yml"])
        only = self.ok("file.history", {"id": self.id, "path": "site.yml"})["commits"]
        self.assertEqual([c["subject"] for c in only], ["Add site.yml"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_a_name_is_never_a_pattern(self):
        self.write("a.yml", "a\n")
        self.write("*.yml", "star\n")
        only = self.ok("file.history", {"id": self.id, "path": "*.yml"})["commits"]
        self.assertEqual([c["subject"] for c in only], ["Add *.yml"])

    def test_only_administrators_and_only_managed_projects(self):
        self.write("a.yml", "a\n")
        for name, params in (("file.list", {}), ("file.read", {"path": "a.yml"}),
                             ("file.write", {"path": "a.yml", "content": "", "expected": ""}),
                             ("file.delete", {"path": "a.yml"}), ("file.history", {}),
                             ("file.check", {"path": "a.yml"})):
            self.assertEqual(self.code(name, {"id": self.id, **params}, role="operator"),
                             "forbidden", name)
        local = self.local_project()
        self.assertEqual([f["path"] for f in self.ok("file.list", {"id": local["id"]})["files"]],
                         ["site.yml", "inventories/hosts.yml"])
        self.assertFalse(self.ok("file.list", {"id": local["id"]})["editable"])
        self.assertEqual(self.ok("file.read", {"id": local["id"], "path": "site.yml"})["content"],
                         "- hosts: all\n")
        for name, params in (("file.write", {"path": "x", "content": "", "expected": ""}),
                             ("file.delete", {"path": "site.yml"}),
                             ("file.move", {"path": "site.yml", "to": "x"}), ("file.history", {})):
            self.assertEqual(self.code(name, {"id": local["id"], **params}), "conflict", name)
        self.assertTrue((self.root / "src" / "site" / "site.yml").is_file())

    def test_saving_a_playbook_queues_a_check_of_its_syntax(self):
        saved = self.write("site.yml", PLAYBOOK)
        run_id = saved["check_run_id"]
        directory = paths.RUNS_DIR / run_id
        spec = json.loads((directory / "spec.json").read_text())
        self.assertEqual(spec, {"kind": "syntax", "playbook": "site.yml"})
        self.assertEqual((directory / "project" / "site.yml").read_text(), PLAYBOOK)
        self.assertEqual(runner.build_syntax_argv(spec, directory)[1:4],
                         ["--syntax-check", "--inventory", "localhost,"])
        run = self.ok("run.get", {"id": run_id})
        self.assertEqual((run["kind"], run["title"], run["revision"], run["options"]),
                         ("syntax", "edited: site.yml", saved["revision"], {"path": "site.yml"}))
        self.finish(run_id, rc=0, lint_rc=2)
        done = self.ok("run.get", {"id": run_id})
        self.assertEqual((done["status"], done["options"]),
                         ("successful", {"path": "site.yml", "lint": "findings"}))

        asked = self.ok("file.check", {"id": self.id, "path": "site.yml"})["run_id"]
        self.finish(asked, rc=4)
        failed = self.ok("run.get", {"id": asked})
        self.assertEqual((failed["status"], failed["options"]), ("failed", {"path": "site.yml"}))
        self.assertEqual(self.code("file.check", {"id": self.id, "path": "nope.yml"}), "not_found")

    def test_a_run_gets_the_files_and_the_revision_they_are_at(self):
        saved = self.write("site.yml", PLAYBOOK)
        self.finish(saved["check_run_id"])
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed", "content": "x"})
        template = self.ok("template.create", {"name": "t", "project_id": self.id,
                                               "playbook": "site.yml",
                                               "inventory_id": inventory["id"]})
        run = self.ok("run.start", {"template_id": template["id"]})
        self.assertEqual(run["revision"], saved["revision"])
        self.assertEqual(sorted(os.listdir(paths.RUNS_DIR / run["id"] / "project")), ["site.yml"])
        self.assertEqual(self.ok("project.files", {"id": self.id})["files"], ["site.yml"])

    def test_project_files_lists_the_files_to_choose_and_the_directories(self):
        for path in ("site.yml", "inventories/prod/hosts.yml", "inventories/prod/group_vars/all.yml",
                     "roles/web/tasks/main.yml", "notes.txt"):
            (self.tree / path).parent.mkdir(parents=True, exist_ok=True)
            (self.tree / path).write_text("x\n")
        listed = self.ok("project.files", {"id": self.id})
        self.assertEqual(listed["files"], ["site.yml", "inventories/prod/hosts.yml",
                                           "inventories/prod/group_vars/all.yml"])
        self.assertEqual(listed["directories"], ["inventories", "inventories/prod",
                                                 "inventories/prod/group_vars"])

    def test_rename_and_delete_of_the_project(self):
        renamed = self.ok("project.update", {"id": self.id, "name": "renamed"})
        self.assertEqual((renamed["name"], renamed["kind"]), ("renamed", "managed"))
        self.assertEqual(self.code("project.sync", {"id": self.id}), "bad_request")
        self.ok("project.delete", {"id": self.id})
        self.assertFalse((paths.PROJECTS_DIR / self.id).exists())

    def test_a_project_that_cannot_get_its_repository_is_not_created(self):
        with mock.patch.object(files, "init_repository", side_effect=OSError("disk full")):
            self.assertEqual(self.code("project.create", {"name": "x", "kind": "managed"}),
                             "internal")
        self.assertEqual(len(self.ok("project.list")["projects"]), 1)


@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class ChangeTest(Managed):
    """Several changes at once, directories, binary files, restore and download."""

    def change(self, *operations, **extra):
        return self.ok("file.change", {"id": self.id, "operations": list(operations), **extra})

    def listed(self):
        return [entry["path"] for entry in self.ok("file.list", {"id": self.id})["files"]]

    def lab(self):
        """A tree like the inventory of a school: hosts, group_vars, host_vars."""
        return self.change(
            {"op": "write", "path": "inventories/school/hosts.yaml", "content": "all: {}\n"},
            {"op": "write", "path": "inventories/school/group_vars/all/vars.yaml",
             "content": "a: 1\n"},
            {"op": "write", "path": "inventories/school/host_vars/dc01.yaml", "content": "b: 2\n"},
            message="A school")

    def test_many_changes_are_one_commit(self):
        done = self.lab()
        self.assertEqual(len(done["files"]), 3)
        self.assertEqual(self.ok("file.history", {"id": self.id})["commits"][0]["subject"],
                         "A school")
        listed = self.ok("file.list", {"id": self.id})
        self.assertEqual(listed["revision"], done["revision"])
        self.assertIn("inventories/school/group_vars/all", listed["directories"])
        self.assertEqual(len(self.git("log", "--format=%H").split()), 1)

    def test_a_directory_is_copied_moved_and_deleted_whole(self):
        self.lab()
        self.change({"op": "copy", "path": "inventories/school", "to": "inventories/lab2"})
        self.assertIn("inventories/lab2/group_vars/all/vars.yaml", self.listed())
        self.assertIn("inventories/school/hosts.yaml", self.listed())
        self.change({"op": "move", "path": "inventories/lab2", "to": "labs/two"})
        self.assertIn("labs/two/host_vars/dc01.yaml", self.listed())
        self.assertFalse((self.tree / "inventories/lab2").exists())
        self.ok("file.delete", {"id": self.id, "path": "labs"})
        self.assertFalse((self.tree / "labs").exists())
        self.assertEqual(len(self.listed()), 3)
        # The commands of one file move a directory too.
        self.ok("file.move", {"id": self.id, "path": "inventories", "to": "inv"})
        self.assertEqual(sorted(self.listed())[0], "inv/school/group_vars/all/vars.yaml")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_nothing_is_made_when_one_change_is_refused(self):
        self.lab()
        before = self.ok("file.list", {"id": self.id})
        for refused, code in (
                ({"op": "move", "path": "nope", "to": "x"}, "not_found"),
                ({"op": "copy", "path": "inventories", "to": "inventories/again"}, "bad_request"),
                ({"op": "write", "path": "inventories/school/hosts.yaml", "content": "x"},
                 "conflict"),
                ({"op": "move", "path": "inventories", "to": "/".join(["d"] * 9)}, "bad_request"),
                ({"op": "chmod", "path": "x"}, "bad_request"),
                ({"op": "write", "path": "x", "content": "a", "data": "YQ=="}, "bad_request"),
                ({"op": "write", "path": "x", "data": "not base64!"}, "bad_request"),
                ({"op": "delete", "path": "x", "to": "y"}, "bad_request")):
            answer = self.call("file.change", {"id": self.id, "operations": [
                {"op": "delete", "path": "inventories/school/host_vars"},
                {"op": "write", "path": "new.yml", "content": "x"}, refused]})
            self.assertEqual(answer["error"]["code"], code, refused)
            self.assertEqual(self.ok("file.list", {"id": self.id}), before, refused)
            self.assertEqual(self.git("status", "--porcelain"), "", refused)
        self.assertEqual(self.code("file.change", {"id": self.id, "operations": []}),
                         "bad_request")

    def test_only_playbooks_still_there_are_checked(self):
        done = self.change({"op": "write", "path": "site.yml", "content": PLAYBOOK},
                           {"op": "write", "path": "gone.yml", "content": PLAYBOOK},
                           {"op": "move", "path": "gone.yml", "to": "notes.txt"})
        self.assertEqual(len(done["check_run_ids"]), 1)
        self.assertEqual(self.ok("run.get", {"id": done["check_run_ids"][0]})["options"]["path"],
                         "site.yml")

    def test_a_revision_that_changed_meanwhile_is_refused(self):
        first = self.lab()
        self.write("other.yml", "x: 1\n")
        self.assertEqual(self.code("file.change", {
            "id": self.id, "revision": first["revision"],
            "operations": [{"op": "delete", "path": "inventories"}]}), "conflict")
        self.assertIn("inventories/school/hosts.yaml", self.listed())

    def test_binary_files_are_uploaded_read_and_kept_up_to_a_limit(self):
        import base64
        blob = bytes(range(256)) * 16
        done = self.change({"op": "write", "path": "files/setup.msi",
                            "data": base64.b64encode(blob).decode()})
        self.assertEqual((self.tree / "files/setup.msi").read_bytes(), blob)
        read = self.ok("file.read", {"id": self.id, "path": "files/setup.msi"})
        self.assertEqual((read["binary"], read["size"], read["sha256"]),
                         (True, len(blob), done["files"][0]["sha256"]))
        self.assertEqual(done["check_run_ids"], [])
        big = base64.b64encode(b"\0" * (files.MAX_FILE + 1)).decode()
        self.assertEqual(self.code("file.change", {"id": self.id, "operations": [
            {"op": "write", "path": "files/big.iso", "data": big}]}), "bad_request")
        # The content never reaches the audit trail; the paths do.
        entry = self.ok("audit.list")["entries"][1]
        self.assertEqual(entry["command"], "file.change")
        self.assertIn("files/setup.msi", entry["params"])
        self.assertNotIn(base64.b64encode(blob).decode()[:40], entry["params"])

    def test_a_path_is_restored_as_it_was(self):
        first = self.lab()
        self.change({"op": "write", "path": "inventories/school/host_vars/pc01.yaml",
                     "content": "c: 3\n"},
                    {"op": "delete", "path": "inventories/school/group_vars"})
        done = self.ok("file.restore", {"id": self.id, "path": "inventories/school",
                                        "revision": first["revision"]})
        self.assertEqual(sorted(self.listed()), [
            "inventories/school/group_vars/all/vars.yaml",
            "inventories/school/host_vars/dc01.yaml", "inventories/school/hosts.yaml"])
        self.assertEqual(self.ok("file.history", {"id": self.id})["commits"][0]["subject"],
                         f"Restore inventories/school as of {first['revision'][:10]}")
        self.assertNotEqual(done["revision"], first["revision"])
        for params, code in (({"path": "nope", "revision": first["revision"]}, "not_found"),
                             ({"path": "inventories", "revision": "f" * 40}, "not_found"),
                             ({"path": "inventories", "revision": "HEAD"}, "bad_request")):
            self.assertEqual(self.code("file.restore", {"id": self.id, **params}), code, params)

    def test_a_file_or_a_directory_is_downloaded(self):
        import base64
        import tarfile
        self.lab()
        one = self.ok("file.download", {"id": self.id, "path": "inventories/school/hosts.yaml"})
        self.assertEqual((one["name"], one["archive"], base64.b64decode(one["data"])),
                         ("hosts.yaml", False, b"all: {}\n"))
        packed = self.ok("file.download", {"id": self.id, "path": "inventories/school"})
        self.assertEqual((packed["name"], packed["archive"]), ("school.tar.gz", True))
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(packed["data"]))) as archive:
            names = archive.getnames()
            self.assertEqual(archive.extractfile("school/host_vars/dc01.yaml").read(), b"b: 2\n")
        self.assertIn("school/group_vars/all/vars.yaml", names)
        whole = self.ok("file.download", {"id": self.id})
        self.assertEqual(whole["name"], "edited.tar.gz")
        # A link is left out, never followed.
        os.symlink("/etc/passwd", self.tree / "inventories/school/passwd")
        packed = self.ok("file.download", {"id": self.id, "path": "inventories"})
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(packed["data"]))) as archive:
            self.assertNotIn("inventories/school/passwd", archive.getnames())
        self.assertEqual(self.ok("audit.list")["entries"][0]["command"], "file.download")

    def test_inventories_of_the_project_are_read_again_when_their_files_change(self):
        self.lab()
        whole = self.ok("inventory.create", {"name": "school", "kind": "file",
                                             "project_id": self.id,
                                             "path": "inventories/school"})
        top = self.ok("inventory.create", {"name": "top", "kind": "file",
                                           "project_id": self.id, "path": "hosts.yaml"})
        cases = (
            ("inventories/school/group_vars/all/vars.yaml", {whole["id"]}),
            ("roles/web/tasks/main.yml", set()),
            ("group_vars/all.yml", {top["id"]}),
            ("hosts.yaml", {top["id"]}),
        )
        for path, wanted in cases:
            current = self.ok("file.read", {"id": self.id, "path": path}) \
                if (self.tree / path).exists() else {"sha256": ""}
            done = self.change({"op": "write", "path": path, "content": f"# {path}\n",
                                "expected": current["sha256"]})
            read = {self.ok("run.get", {"id": run_id})["inventory_id"]
                    for run_id in done["inventory_run_ids"]}
            self.assertEqual(read, wanted, path)
        # Moving a directory an inventory is in reads it again too.
        done = self.change({"op": "move", "path": "inventories", "to": "old"})
        self.assertEqual(len(done["inventory_run_ids"]), 1)


class PlaybookGuessTest(unittest.TestCase):
    def test_a_playbook_is_a_list_with_a_play(self):
        yes = ("- hosts: all\n", "---\n# site\n- name: Web\n  hosts: web\n  roles: [a]\n",
               "- import_playbook: other.yml\n",
               "- name: x\n  ansible.builtin.import_playbook: other.yml\n")
        no = ("all:\n  hosts:\n    a:\n", "- name: task\n  debug:\n", "x: 1\n", "",
              "- name: t\n  add_host:\n    hosts: x\n")
        for text in yes:
            self.assertTrue(files.looks_like_playbook("site.yml", text), text)
        for text in no:
            self.assertFalse(files.looks_like_playbook("site.yml", text), text)
        self.assertFalse(files.looks_like_playbook("notes.txt", yes[0]))

    def test_file_names(self):
        self.assertEqual(str(check_file_path("roles/./web//main.yml")), "roles/web/main.yml")
        self.assertEqual(str(check_file_path("città/è.yml")), "città/è.yml")
        for bad in ("a/" * 13 + "x", "x" * 256, "a/.git", "\ud800"):
            with self.assertRaises(BadRequest):
                check_file_path(bad)


class RunnerSyntaxTest(unittest.TestCase):
    def test_commands_take_only_a_playbook_inside_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "project").mkdir()
            (run_dir / "project" / "site.yml").write_text(PLAYBOOK)
            (run_dir / "outside.yml").write_text(PLAYBOOK)
            os.symlink(run_dir / "outside.yml", run_dir / "project" / "link.yml")
            playbook = str((run_dir / "project" / "site.yml").resolve())
            self.assertEqual(runner.build_syntax_argv({"playbook": "site.yml"}, run_dir),
                             ["/usr/bin/ansible-playbook", "--syntax-check", "--inventory",
                              "localhost,", playbook])
            self.assertEqual(runner.build_lint_argv({"playbook": "site.yml"}, run_dir),
                             ["/usr/bin/ansible-lint", "--offline", playbook])
            for bad in ("../outside.yml", "link.yml", "--version", "missing.yml", None):
                with self.assertRaises(BadRequest):
                    runner.build_syntax_argv({"playbook": bad}, run_dir)
                with self.assertRaises(BadRequest):
                    runner.build_lint_argv({"playbook": bad}, run_dir)

    def test_a_check_writes_no_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "project").mkdir()
            (run_dir / "rt").mkdir()
            env = runner.build_env(run_dir, run_dir / "rt", events=False)
            self.assertNotIn("ANSIBLE_CALLBACKS_ENABLED", env)
            self.assertNotIn("FLEET_EVENTS_FILE", env)
            self.assertIn("FLEET_EVENTS_FILE", runner.build_env(run_dir, run_dir / "rt"))


class RebuildMigrationTest(unittest.TestCase):
    def test_new_kinds_are_accepted_and_existing_rows_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fleet.db"
            with mock.patch.object(db, "MIGRATIONS", db.MIGRATIONS[:4]):
                conn = db.connect(path)
                conn.execute("INSERT INTO projects (id, name, kind, path, created_at)"
                             " VALUES ('p1', 'site', 'local', '/srv/site', 1)")
                conn.execute("INSERT INTO inventories (id, name, kind, project_id, path, created_at)"
                             " VALUES ('i1', 'inv', 'file', 'p1', 'hosts', 1)")
                conn.execute("INSERT INTO templates (id, name, project_id, playbook, inventory_id,"
                             " created_at, updated_at) VALUES ('t1', 't', 'p1', 's.yml', 'i1', 1, 1)")
                for run_id in ("r1", "r2", "r3"):
                    conn.execute("INSERT INTO runs (id, kind, status, title, template_id,"
                                 " project_id, created_by, created_at) VALUES"
                                 " (?, 'playbook', 'successful', 't', 't1', 'p1', 'u', 1)", (run_id,))
                conn.execute("DELETE FROM runs WHERE id = 'r2'")
                conn.close()

            conn = db.connect(path)
            self.addCleanup(conn.close)
            self.assertEqual(db.schema_version(conn), len(db.MIGRATIONS))
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            rows = conn.execute("SELECT rowid, id, project_id, template_id FROM runs").fetchall()
            self.assertEqual([tuple(row) for row in rows],
                             [(1, "r1", "p1", "t1"), (3, "r3", "p1", "t1")])
            conn.execute("INSERT INTO projects (id, name, kind, created_at)"
                         " VALUES ('p2', 'edited', 'managed', 1)")
            conn.execute("INSERT INTO runs (id, kind, status, title, project_id, created_by,"
                         " created_at) VALUES ('r4', 'syntax', 'queued', 't', 'p2', 'u', 1)")
            # The references still hold: a project in use is not deleted, and a
            # deleted one leaves its runs behind.
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM projects WHERE id = 'p1'")
            conn.execute("DELETE FROM projects WHERE id = 'p2'")
            self.assertIsNone(conn.execute("SELECT project_id FROM runs WHERE id = 'r4'")
                              .fetchone()[0])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE runs SET status = 'maybe' WHERE id = 'r1'")
            indexes = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'runs'")}
            self.assertLessEqual({"runs_created", "runs_status"}, indexes)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class TerminalCloneTest(Managed):
    """fleet project clone, pull and push, through the real dispatch and git."""

    def setUp(self):
        super().setUp()
        self.write("site.yml", PLAYBOOK)
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clone = str(self.work / "edited")
        env = mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "anna", "GIT_AUTHOR_EMAIL": "a@x",
                                           "GIT_COMMITTER_NAME": "anna",
                                           "GIT_COMMITTER_EMAIL": "a@x",
                                           "GIT_CONFIG_GLOBAL": "/dev/null",
                                           "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)

    def fleet(self, *words):
        from cockpit_fleet import cli, terminal

        def call(name, params):
            return cli.dispatch(name, json.dumps(params).encode(), self.ctx("admin"))
        term = terminal.Terminal(call=call, out=io.StringIO(), err=io.StringIO(),
                                 stdin=io.StringIO())
        code = terminal.run(term, cli.load_commands(), list(words))
        return code, term.out.getvalue()

    def commit(self, path, content, message="change"):
        target = Path(self.clone) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        subprocess.run(["/usr/bin/git", "-C", self.clone, "add", "--all"], check=True)
        subprocess.run(["/usr/bin/git", "-C", self.clone, "commit", "--quiet", "-m", message], check=True)

    def test_an_inventory_written_here_is_cloned_and_pushed_too(self):
        made = self.ok("inventory.create", {"name": "school", "kind": "managed",
                                            "content": "all: {}\n"})
        clone = str(self.work / "school")
        self.assertEqual(self.fleet("inventory", "clone", "school", clone)[0], 0)
        self.assertEqual((Path(clone) / "hosts.yml").read_text(), "all: {}\n")
        target = Path(clone) / "group_vars" / "all.yml"
        target.parent.mkdir()
        target.write_text("a: 1\n")
        subprocess.run(["/usr/bin/git", "-C", clone, "add", "--all"], check=True)
        subprocess.run(["/usr/bin/git", "-C", clone, "commit", "--quiet", "-m", "vars"], check=True)
        code, said = self.fleet("inventory", "push", clone)
        self.assertEqual(code, 0, said)
        self.assertIn("inventory read again", said)
        self.assertEqual(self.ok("file.read", {"id": made["id"], "path": "group_vars/all.yml"})
                         ["content"], "a: 1\n")
        # A clone of an inventory is not pushed as a project's, nor the other way.
        from cockpit_fleet.terminal import Usage
        with self.assertRaises(Usage):
            self.fleet("project", "push", clone)
        self.assertEqual(self.fleet("project", "clone", "edited", self.clone)[0], 0)
        with self.assertRaises(Usage):
            self.fleet("inventory", "pull", self.clone)

    def test_clone_commit_push_as_if_written_in_the_interface(self):
        self.assertEqual(self.fleet("project", "clone", "edited", self.clone)[0], 0)
        self.assertEqual((Path(self.clone) / "site.yml").read_text(), PLAYBOOK)
        self.commit("roles/web/tasks/main.yml", "- debug: msg=hi\n")
        self.commit("deploy.yml", PLAYBOOK, "add deploy")
        code, said = self.fleet("project", "push", self.clone)
        self.assertEqual(code, 0, said)
        self.assertIn("2 files changed", said)
        head = subprocess.run(["/usr/bin/git", "-C", self.clone, "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        self.assertEqual(self.ok("project.list")["projects"][0]["revision"], head)
        self.assertEqual((self.tree / "deploy.yml").read_text(), PLAYBOOK)
        self.assertIn("add deploy", self.git("log", "--format=%s"))
        checks = [run for run in self.ok("run.list")["runs"] if run["kind"] == "syntax"]
        self.assertEqual(sorted(run["options"]["path"] for run in checks), ["deploy.yml", "site.yml"])
        row = self.ctx().conn.execute("SELECT params FROM audit WHERE command = 'file.push'").fetchone()
        self.assertEqual(json.loads(row["params"])["bundle"], "***")
        self.assertIn("nothing to push", self.fleet("project", "push", self.clone)[1])

    def test_a_project_that_changed_meanwhile_is_pulled_first(self):
        self.fleet("project", "clone", "edited", self.clone)
        self.commit("a.yml", "a: 1\n")
        self.write("b.yml", "b: 1\n")
        with self.assertRaisesRegex(terminal_failure(), "changed since your clone"):
            self.fleet("project", "push", self.clone)
        with self.assertRaisesRegex(terminal_failure(), "git rebase fleet/main"):
            self.fleet("project", "pull", self.clone)
        subprocess.run(["/usr/bin/git", "-C", self.clone, "rebase", "--quiet", "fleet/main"], check=True)
        self.assertEqual(self.fleet("project", "push", self.clone)[0], 0)
        self.assertEqual(sorted(path.name for path in self.tree.iterdir()),
                         ["a.yml", "b.yml", "site.yml"])

    def test_what_the_editor_would_not_write_is_refused_whole(self):
        self.fleet("project", "clone", "edited", self.clone)
        before = self.ok("project.list")["projects"][0]["revision"]
        os.symlink("/etc/shadow", Path(self.clone) / "shadow")
        subprocess.run(["/usr/bin/git", "-C", self.clone, "add", "--all"], check=True)
        subprocess.run(["/usr/bin/git", "-C", self.clone, "commit", "--quiet", "-m", "link"], check=True)
        with self.assertRaisesRegex(terminal_failure(), "only regular files"):
            self.fleet("project", "push", self.clone)
        # What the refused push left in the repository cannot be restored from.
        refused = subprocess.run(["/usr/bin/git", "-C", self.clone, "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(self.code("file.restore", {"id": self.id, "path": "shadow",
                                                    "revision": refused}), "not_found")
        subprocess.run(["/usr/bin/git", "-C", self.clone, "reset", "--quiet", "--hard", "HEAD~1"], check=True)
        self.commit("big.bin", "x" * (files.MAX_FILE + 1))
        with self.assertRaisesRegex(terminal_failure(), "larger than"):
            self.fleet("project", "push", self.clone)
        self.assertEqual(self.ok("project.list")["projects"][0]["revision"], before)
        self.assertFalse((self.tree / "shadow").exists())
        self.assertEqual(self.git("for-each-ref", "refs/fleet"), "")

    def test_only_a_clone_and_only_a_managed_project(self):
        from cockpit_fleet.terminal import Usage
        with self.assertRaises(Usage):
            self.fleet("project", "push", str(self.work))
        self.local_project()
        with self.assertRaisesRegex(Usage, "not written in the interface"):
            self.fleet("project", "clone", "site", str(self.work / "x"))
        self.assertEqual(self.code("file.bundle", {"id": self.id}, role="operator"), "forbidden")


def terminal_failure():
    from cockpit_fleet.terminal import Failure
    return Failure
