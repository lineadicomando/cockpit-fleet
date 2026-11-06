"""Drafts: what an agent writes in a managed project or inventory goes to a
branch of its session, which a person reviews and takes into main."""

import json
import os
import subprocess
import unittest
from dataclasses import replace

from test_phase2 import Base

from cockpit_fleet import cli, paths

PLAYBOOK = "- hosts: all\n  tasks: []\n"
SESSION = "0123456789abcdef"
OTHER = "fedcba9876543210"


@unittest.skipUnless(os.path.exists("/usr/bin/git"), "needs git")
class DraftTest(Base):
    def setUp(self):
        super().setUp()
        cli.load_commands()
        self.config = replace(self.config, mcp_enabled=True, mcp_max_role="admin",
                              chat_profiles=("assisted", "administrator"),
                              chat_default_profile="assisted")
        self.id = self.ok("project.create", {"name": "site", "kind": "managed"})["id"]
        self.main = paths.PROJECTS_DIR / self.id / "tree"

    def agent(self, name, params=None, session=SESSION):
        extra = {"session": session} if session else {}
        return self.call(name, {**(params or {}), "via": "mcp:test",
                                "profile": "administrator", **extra})

    def agent_ok(self, name, params=None, session=SESSION):
        answer = self.agent(name, params, session)
        self.assertTrue(answer["ok"], answer)
        return answer["data"]

    def write(self, path, content, expected="", who=None, **extra):
        params = {"id": extra.pop("id", self.id), "path": path, "content": content,
                  "expected": expected, **extra}
        return self.agent_ok("file.write", params) if who == "agent" \
            else self.ok("file.write", params)

    def git(self, *args):
        home = paths.PROJECTS_DIR / self.id
        argv = ["/usr/bin/git", f"--git-dir={home / 'repo.git'}", *args]
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout

    def diff(self, draft=SESSION, container=None):
        return self.ok("draft.diff", {"id": container or self.id, "draft": draft})

    def merge(self, paths_=(), draft=SESSION, container=None, **extra):
        revision = self.diff(draft, container)["revision"]
        return self.ok("draft.merge", {"id": container or self.id, "draft": draft,
                                       "revision": revision, "paths": list(paths_), **extra})

    def test_an_agent_writes_to_its_draft_and_main_is_untouched(self):
        first = self.write("site.yml", PLAYBOOK)
        written = self.write("site.yml", PLAYBOOK + "# agent\n", first["sha256"], who="agent")
        self.assertEqual(written["draft"], SESSION)
        self.assertEqual((self.main / "site.yml").read_text(), PLAYBOOK)
        project = self.ok("project.list")["projects"][0]
        self.assertEqual(project["revision"], first["revision"])
        self.assertEqual(self.git("rev-parse", f"drafts/{SESSION}").strip(), written["revision"])
        self.assertEqual(self.git("rev-parse", "main").strip(), first["revision"])
        # The agent reads its draft; a person reads main, or the draft by name.
        self.assertIn("# agent", self.agent_ok("file.read", {"id": self.id,
                                                             "path": "site.yml"})["content"])
        self.assertEqual(self.ok("file.read", {"id": self.id, "path": "site.yml"})["content"],
                         PLAYBOOK)
        read = self.ok("file.read", {"id": self.id, "path": "site.yml", "draft": SESSION})
        self.assertIn("# agent", read["content"])
        listed = self.agent_ok("file.list", {"id": self.id})
        self.assertEqual((listed["revision"], listed["draft"]), (written["revision"], SESSION))
        self.assertIsNone(self.ok("file.list", {"id": self.id})["draft"])
        history = self.agent_ok("file.history", {"id": self.id})["commits"]
        self.assertEqual(len(history), 2)
        self.assertEqual(len(self.ok("file.history", {"id": self.id})["commits"]), 1)
        # The author is the person the agent acts for, as for any of its calls.
        self.assertEqual(history[0]["author"], "anna")
        audit = self.ok("audit.list")["entries"]
        self.assertEqual((audit[0]["command"], audit[0]["via"]), ("file.write", "mcp:test"))

    def test_runs_read_main_and_the_draft_is_beside_it(self):
        self.write("site.yml", PLAYBOOK)
        self.write("new.yml", PLAYBOOK, who="agent")
        self.assertFalse((self.main / "new.yml").exists())
        draft = paths.PROJECTS_DIR / self.id / "drafts" / SESSION / "tree"
        self.assertEqual((draft / "new.yml").read_text(), PLAYBOOK)
        self.assertEqual(self.code("file.check", {"id": self.id, "path": "new.yml"}),
                         "not_found")
        self.assertNotIn("new.yml", self.git("ls-tree", "-r", "--name-only", "main"))

    def test_an_agent_writes_only_to_the_draft_of_its_own_session(self):
        self.assertEqual(self.agent("file.write", {"id": self.id, "path": "a.yml",
                                                   "content": "x", "expected": ""},
                                    session=None)["error"]["code"], "forbidden")
        answer = self.agent("file.write", {"id": self.id, "path": "a.yml", "content": "x",
                                           "expected": "", "draft": OTHER})
        self.assertEqual(answer["error"]["code"], "forbidden")
        self.assertEqual(self.code("file.write", {"id": self.id, "path": "a.yml",
                                                  "content": "x", "expected": "",
                                                  "draft": "nope"}), "bad_request")
        # A person names a draft that exists.
        self.assertEqual(self.code("file.write", {"id": self.id, "path": "a.yml",
                                                  "content": "x", "expected": "",
                                                  "draft": OTHER}), "not_found")
        self.assertEqual(self.ok("draft.list")["drafts"], [])

    def test_a_write_that_fails_leaves_no_draft(self):
        self.write("a.yml", "x: 1\n")
        answer = self.agent("file.write", {"id": self.id, "path": "a.yml", "content": "x: 2\n",
                                           "expected": ""})
        self.assertEqual(answer["error"]["code"], "conflict")
        self.assertEqual(self.ok("draft.list")["drafts"], [])
        self.assertFalse((paths.PROJECTS_DIR / self.id / "drafts" / SESSION).exists())

    def test_a_draft_begins_where_main_is_when_it_first_changes_something(self):
        first = self.write("a.yml", "a: 1\n")
        # Written as it was: no draft yet.
        self.write("a.yml", "a: 1\n", first["sha256"], who="agent")
        self.assertEqual(self.ok("draft.list")["drafts"], [])
        second = self.write("b.yml", "b: 1\n")
        self.write("c.yml", "c: 1\n", who="agent")
        self.assertEqual(self.agent_ok("file.read", {"id": self.id, "path": "b.yml"})["content"],
                         "b: 1\n")
        self.assertEqual([c["path"] for c in self.diff()["changes"]], ["c.yml"])
        self.assertEqual(self.git("merge-base", "main", f"drafts/{SESSION}").strip(),
                         second["revision"])

    def test_a_playbook_of_the_draft_is_checked_in_the_draft(self):
        written = self.write("site.yml", PLAYBOOK, who="agent")
        run_id = written["check_run_id"]
        self.assertIsNotNone(run_id)
        run = self.ok("run.get", {"id": run_id})
        self.assertEqual((run["kind"], run["revision"], run["title"]),
                         ("syntax", written["revision"], "site: site.yml (draft)"))
        self.assertEqual((paths.RUNS_DIR / run_id / "project/site.yml").read_text(), PLAYBOOK)
        self.finish(run_id, rc=1)
        self.ok("run.get", {"id": run_id})
        checks = self.diff()["checks"]
        self.assertEqual([(c["path"], c["status"], c["current"]) for c in checks],
                         [("site.yml", "failed", True)])
        # Asked again, by a person, of the draft.
        again = self.ok("file.check", {"id": self.id, "path": "site.yml", "draft": SESSION})
        self.assertEqual(self.ok("run.get", {"id": again["run_id"]})["revision"],
                         written["revision"])
        # Collections and inventories of main are not touched by a draft.
        self.assertIsNone(self.write("requirements.yml", "collections: []\n",
                                     who="agent")["collections_run_id"])

    def test_listed_and_shown_then_taken_whole_into_main(self):
        self.write("site.yml", PLAYBOOK)
        current = self.ok("file.read", {"id": self.id, "path": "site.yml"})["sha256"]
        self.agent_ok("file.change", {"id": self.id, "operations": [
            {"op": "write", "path": "site.yml", "content": PLAYBOOK + "# two\n",
             "expected": current},
            {"op": "write", "path": "roles/web/tasks/main.yml", "content": "- debug:\n",
             "expected": ""},
            {"op": "mkdir", "path": "files"}]})
        drafts = self.ok("draft.list")["drafts"]
        self.assertEqual([(d["id"], d["kind"], d["draft"], d["author"], d["changes"],
                           d["conflicts"]) for d in drafts],
                         [(self.id, "project", SESSION, "anna", 3, 0)])
        shown = self.diff()
        self.assertEqual([(c["path"], c["status"], c["directory"]) for c in shown["changes"]],
                         [("files", "added", True), ("roles/web/tasks/main.yml", "added", False),
                          ("site.yml", "changed", False)])
        self.assertIn("+# two", shown["text"])
        self.assertIn("+- debug:", shown["text"])
        # Agents do not take or discard drafts: those are for a person.
        for name in ("draft.merge", "draft.discard"):
            answer = self.agent(name, {"id": self.id, "draft": SESSION,
                                       **({"revision": shown["revision"]}
                                          if name == "draft.merge" else {})})
            self.assertEqual(answer["error"]["code"], "forbidden", name)

        taken = self.merge(message="Take the web role")
        self.assertEqual((taken["left"], taken["discarded"]), (0, True))
        self.assertEqual((self.main / "site.yml").read_text(), PLAYBOOK + "# two\n")
        self.assertEqual((self.main / "roles/web/tasks/main.yml").read_text(), "- debug:\n")
        self.assertTrue((self.main / "files").is_dir())
        self.assertEqual(self.git("log", "-1", "--format=%an %s", "main").strip(),
                         "anna Take the web role")
        self.assertEqual(len(taken["check_run_ids"]), 1)  # site.yml, as any change of main
        self.assertEqual(self.ok("draft.list")["drafts"], [])
        self.assertFalse((paths.PROJECTS_DIR / self.id / "drafts" / SESSION).exists())
        self.assertEqual(self.code("draft.diff", {"id": self.id, "draft": SESSION}),
                         "not_found")
        audit = self.ok("audit.list")["entries"][0]
        self.assertEqual((audit["command"], audit["outcome"]), ("draft.merge", "ok"))

    def test_taken_in_part_and_the_rest_waits(self):
        self.write("a.yml", "a: 1\n", who="agent")
        self.write("b.yml", "b: 1\n", who="agent")
        taken = self.merge(["a.yml"])
        self.assertEqual((taken["left"], taken["discarded"]), (1, False))
        self.assertTrue((self.main / "a.yml").is_file())
        self.assertFalse((self.main / "b.yml").exists())
        self.assertEqual([c["path"] for c in self.diff()["changes"]], ["b.yml"])
        self.assertEqual(self.code("draft.merge", {"id": self.id, "draft": SESSION,
                                                   "revision": self.diff()["revision"],
                                                   "paths": ["a.yml"]}), "bad_request")
        # The agent goes on in the same draft.
        current = self.agent_ok("file.read", {"id": self.id, "path": "b.yml"})["sha256"]
        self.write("b.yml", "b: 2\n", current, who="agent")
        self.assertEqual(self.merge()["discarded"], True)
        self.assertEqual((self.main / "b.yml").read_text(), "b: 2\n")

    def test_what_changed_in_main_too_is_a_conflict(self):
        first = self.write("a.yml", "a: 1\n")
        self.write("b.yml", "b: 1\n")
        agent_a = self.write("a.yml", "a: agent\n", first["sha256"], who="agent")
        self.write("c.yml", "c: 1\n", who="agent")
        self.write("a.yml", "a: person\n", first["sha256"])
        shown = self.diff()
        self.assertEqual([(c["path"], c["conflict"]) for c in shown["changes"]],
                         [("a.yml", True), ("c.yml", False)])
        self.assertEqual(self.ok("draft.list")["drafts"][0]["conflicts"], 1)
        self.assertEqual(self.code("draft.merge", {"id": self.id, "draft": SESSION,
                                                   "revision": shown["revision"]}), "conflict")
        self.assertFalse((self.main / "c.yml").exists())
        self.merge(["c.yml"])
        # Settled by editing the draft, as a person: to what main has, say.
        main_text = self.ok("file.read", {"id": self.id, "path": "a.yml"})["content"]
        self.write("a.yml", main_text, agent_a["sha256"], draft=SESSION)
        self.assertEqual(self.ok("draft.list")["drafts"], [{**self.ok("draft.list")["drafts"][0],
                                                            "changes": 0, "conflicts": 0}])
        self.assertEqual(self.code("draft.merge", {"id": self.id, "draft": SESSION,
                                                   "revision": self.diff()["revision"]}),
                         "conflict")
        self.ok("draft.discard", {"id": self.id, "draft": SESSION})
        self.assertEqual(self.ok("draft.list")["drafts"], [])
        self.assertEqual((self.main / "a.yml").read_text(), "a: person\n")

    def test_a_draft_that_changed_since_it_was_reviewed_is_not_taken(self):
        self.write("a.yml", "a: 1\n", who="agent")
        reviewed = self.diff()["revision"]
        current = self.agent_ok("file.read", {"id": self.id, "path": "a.yml"})["sha256"]
        self.write("a.yml", "a: 2\n", current, who="agent")
        self.assertEqual(self.code("draft.merge", {"id": self.id, "draft": SESSION,
                                                   "revision": reviewed}), "conflict")
        self.assertFalse((self.main / "a.yml").exists())

    def test_deletions_and_moves_are_taken_and_empty_directories_go(self):
        self.ok("file.change", {"id": self.id, "operations": [
            {"op": "write", "path": "old/one.yml", "content": "1\n", "expected": ""},
            {"op": "write", "path": "old/deep/two.yml", "content": "2\n", "expected": ""},
            {"op": "write", "path": "keep.yml", "content": "k\n", "expected": ""}]})
        self.agent_ok("file.move", {"id": self.id, "path": "old", "to": "new"})
        self.agent_ok("file.delete", {"id": self.id, "path": "keep.yml"})
        statuses = {c["path"]: c["status"] for c in self.diff()["changes"]}
        self.assertEqual(statuses, {"keep.yml": "deleted", "new/deep/two.yml": "added",
                                    "new/one.yml": "added", "old/deep/two.yml": "deleted",
                                    "old/one.yml": "deleted"})
        self.merge()
        listed = self.ok("file.list", {"id": self.id})
        self.assertEqual(sorted(f["path"] for f in listed["files"]),
                         ["new/deep/two.yml", "new/one.yml"])
        self.assertEqual(sorted(listed["directories"]), ["new", "new/deep"])

    def test_a_draft_of_a_project_with_nothing_in_main(self):
        self.write("site.yml", PLAYBOOK, who="agent")
        self.assertEqual(self.ok("project.list")["projects"][0]["revision"], "")
        self.merge()
        self.assertEqual((self.main / "site.yml").read_text(), PLAYBOOK)
        self.assertNotEqual(self.ok("project.list")["projects"][0]["revision"], "")

    def test_an_agent_restores_in_its_draft(self):
        first = self.write("a.yml", "a: 1\n")
        self.write("a.yml", "a: 2\n", first["sha256"])
        restored = self.agent_ok("file.restore", {"id": self.id, "path": "a.yml",
                                                  "revision": first["revision"]})
        self.assertEqual(restored["draft"], SESSION)
        self.assertEqual((self.main / "a.yml").read_text(), "a: 2\n")
        self.assertEqual(self.agent_ok("file.read", {"id": self.id, "path": "a.yml"})["content"],
                         "a: 1\n")
        # A draft is only made by what changes something.
        self.assertEqual(self.agent("file.restore", {"id": self.id, "path": "a.yml",
                                                     "revision": "0" * 40},
                                    session=OTHER)["error"]["code"], "not_found")
        self.assertFalse((paths.PROJECTS_DIR / self.id / "drafts" / OTHER).exists())

    def test_two_sessions_have_two_drafts(self):
        self.write("a.yml", "a\n", who="agent")
        self.agent_ok("file.write", {"id": self.id, "path": "b.yml", "content": "b\n",
                                     "expected": ""}, session=OTHER)
        drafts = self.ok("draft.list", {"id": self.id})["drafts"]
        self.assertEqual(sorted(d["draft"] for d in drafts), sorted([SESSION, OTHER]))
        self.assertEqual(self.agent_ok("file.list", {"id": self.id})["files"],
                         [{"path": "a.yml", "size": 2}])
        # Another session's draft can be read by naming it, not written.
        self.assertEqual(self.agent_ok("file.read", {"id": self.id, "path": "b.yml",
                                                     "draft": OTHER})["content"], "b\n")

    def test_drafts_of_a_managed_inventory(self):
        made = self.ok("inventory.create", {"name": "web", "kind": "managed",
                                            "content": "all:\n  hosts:\n    a:\n"})
        current = self.ok("file.read", {"id": made["id"], "path": "hosts.yml"})["sha256"]
        written = self.write("hosts.yml", "all:\n  hosts:\n    b:\n", current, who="agent",
                             id=made["id"])
        self.assertEqual(self.ok("file.read", {"id": made["id"], "path": "hosts.yml"})["content"],
                         "all:\n  hosts:\n    a:\n")
        run = self.ok("run.get", {"id": written["check_run_id"]})
        self.assertEqual((run["kind"], run["title"]), ("syntax", "web (draft)"))
        self.assertEqual(json.loads((paths.RUNS_DIR / run["id"] / "spec.json").read_text())
                         ["kind"], "syntax")
        self.assertEqual((paths.RUNS_DIR / run["id"] / "inventory/hosts.yml").read_text(),
                         "all:\n  hosts:\n    b:\n")
        self.assertEqual([d["kind"] for d in self.ok("draft.list")["drafts"]], ["inventory"])
        taken = self.merge(container=made["id"])
        self.assertEqual(len(taken["inventory_run_ids"]), 1)
        self.assertEqual(self.ok("file.read", {"id": made["id"], "path": "hosts.yml"})["content"],
                         "all:\n  hosts:\n    b:\n")

    def test_projects_not_written_here_have_no_drafts(self):
        project = self.local_project("local")
        self.assertEqual(self.code("file.read", {"id": project["id"], "path": "site.yml",
                                                 "draft": SESSION}), "conflict")
        # An agent reads them as they are.
        self.assertEqual(self.agent_ok("file.read", {"id": project["id"],
                                                     "path": "site.yml"})["content"],
                         "- hosts: all\n")


if __name__ == "__main__":
    unittest.main()
