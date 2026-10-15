"""Module names and parameters for the editor, with ansible-doc replaced by a fake."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import moduledocs, paths

COPY = {"ansible.builtin.copy": {"doc": {
    "short_description": "Copy files to remote locations",
    "options": {
        "dest": {"description": ["Remote absolute path, see O(src).", "More."], "type": "path",
                 "required": True},
        "backup": {"description": "Keep a backup.", "type": "bool", "default": False},
        "mode": {"description": ["x" * 1000], "type": "raw", "aliases": ["perm"]},
        "validate": {"description": [], "choices": ["a", 1]},
        "broken": "not a mapping",
    }}}}


class ModuleDocsTest(Base):
    def setUp(self):
        super().setUp()
        self.calls: list[tuple] = []
        self.listing = {"ansible.builtin.copy": "Copy files", "ansible.builtin.ping": ["Try C(x)"],
                        "--evil": "x", "Bad.Name": "y"}

        def ansible_doc(*args):
            self.calls.append(args)
            return self.listing if args == ("--list",) else COPY if "copy" in args[0] else {}

        self.status = self.root / "status"
        self.status.write_text("1")
        for patch in (mock.patch.object(paths, "CACHE_DIR", self.root / "cache"),
                      mock.patch.object(moduledocs, "_ansible_doc", ansible_doc),
                      mock.patch.object(moduledocs, "_PACKAGE_DBS", (self.status,)),
                      mock.patch.object(moduledocs, "_COLLECTION_ROOTS", (self.root / "none",))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_list_is_kept_until_something_is_installed(self):
        first = self.ok("module.list")["modules"]
        self.assertEqual(first, {"ansible.builtin.copy": "Copy files",
                                 "ansible.builtin.ping": "Try x"})
        self.listing["ansible.builtin.file"] = "Manage files"
        self.assertEqual(self.ok("module.list")["modules"], first)
        self.assertEqual(self.calls, [("--list",)])
        os.utime(self.status, ns=(1, 1))
        self.assertIn("ansible.builtin.file", self.ok("module.list")["modules"])
        self.assertEqual(len(self.calls), 2)

    def test_an_unreadable_cache_is_rebuilt(self):
        (self.root / "cache").mkdir()
        (self.root / "cache" / "modules.json").write_text("[not json")
        self.assertEqual(len(self.ok("module.list")["modules"]), 2)

    def test_parameters_of_a_module(self):
        doc = self.ok("module.doc", {"name": "ansible.builtin.copy"})
        self.assertEqual(doc["description"], "Copy files to remote locations")
        options = {option["name"]: option for option in doc["options"]}
        self.assertEqual(sorted(options), ["backup", "dest", "mode", "validate"])
        self.assertEqual(options["dest"], {
            "name": "dest", "type": "path", "required": True, "default": None, "choices": [],
            "aliases": [], "description": "Remote absolute path, see src."})
        self.assertEqual((options["backup"]["type"], options["backup"]["default"]), ("bool", False))
        self.assertEqual(options["mode"]["aliases"], ["perm"])
        self.assertEqual(len(options["mode"]["description"]), 401)
        self.assertEqual((options["validate"]["type"], options["validate"]["choices"]),
                         ("str", ["a", "1"]))

    def test_help_has_the_whole_documentation(self):
        entry = COPY["ansible.builtin.copy"]
        full = {"ansible.builtin.copy": {
            "doc": {**entry["doc"], "description": ["Copies a file.",
                                    "See M(ansible.builtin.fetch) and L(the guide, https://x.example/y)."],
                    "notes": ["Works in check mode."], "collection": "ansible.builtin",
                    "options": {"dest": {"description": ["Path, see O(src).", "More."],
                                         "type": "path", "required": True,
                                         "suboptions": {"deep": {"description": "Inside."}}}}},
            "examples": "\n- name: Copy\n  ansible.builtin.copy:\n    dest: /srv/x\n",
            "return": {"dest": {"description": "Where it went.", "type": "str",
                                "returned": "success", "sample": "/srv/x",
                                "contains": {"part": {"description": ["A part."]}}},
                       "broken": 3},
        }}
        with mock.patch.dict(COPY, full):
            doc = self.ok("module.help", {"name": "ansible.builtin.copy"})
        self.assertEqual(doc["text"], [
            "Copies a file.", "See ansible.builtin.fetch and the guide (https://x.example/y)."])
        self.assertEqual((doc["notes"], doc["collection"]), (["Works in check mode."],
                                                             "ansible.builtin"))
        self.assertEqual(doc["options"][0]["text"], ["Path, see src.", "More."])
        self.assertEqual(doc["options"][0]["suboptions"][0]["name"], "deep")
        self.assertTrue(doc["examples"].startswith("- name: Copy"))
        self.assertEqual(doc["returns"], [{
            "name": "dest", "type": "str", "returned": "success", "text": ["Where it went."],
            "sample": "/srv/x", "contains": [{"name": "part", "type": "", "returned": "",
                                              "text": ["A part."], "sample": None,
                                              "contains": []}]}])
        self.assertEqual(self.code("module.help", {"name": "--list"}), "bad_request")
        self.assertEqual(self.code("module.help", {"name": "ansible.builtin.copy"},
                                   role="operator"), "forbidden")

    def test_names_are_checked_and_roles_enforced(self):
        self.assertEqual(self.code("module.doc", {"name": "ansible.builtin.nope"}), "not_found")
        for name in ("--list", "-h", "a/b", "../x", "a b", "", "A.b.c"):
            self.assertEqual(self.code("module.doc", {"name": name}), "bad_request", name)
        self.assertEqual([call for call in self.calls if call != ("ansible.builtin.nope",)], [])
        self.assertEqual(self.code("module.list", role="operator"), "forbidden")
        self.assertEqual(self.code("module.doc", {"name": "copy"}, role="operator"), "forbidden")


class AnsibleDocCommandTest(unittest.TestCase):
    def test_it_runs_closed_away_from_any_project(self):
        seen = {}

        def run(argv, **options):
            seen.update(argv=argv, **options)
            return mock.Mock(returncode=0, stdout='{"a.b.c": "d"}', stderr="")

        with mock.patch.object(moduledocs.subprocess, "run", run):
            self.assertEqual(moduledocs._ansible_doc("--list"), {"a.b.c": "d"})
        self.assertEqual(seen["argv"], ["/usr/bin/ansible-doc", "--type", "module", "--json",
                                        "--list"])
        self.assertEqual(seen["cwd"], "/")
        self.assertNotIn("ANSIBLE_CONFIG", seen["env"])
        self.assertTrue(seen["env"]["HOME"].startswith(tempfile.gettempdir()))
        self.assertFalse(Path(seen["env"]["HOME"]).exists())
        self.assertEqual(seen["env"]["ANSIBLE_COLLECTIONS_PATH"], "/usr/share/ansible/collections")


if __name__ == "__main__":
    unittest.main()
