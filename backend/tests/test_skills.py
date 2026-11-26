"""Skills of the projects, of their collections and of the controller:
found where they are, read without following a link, listed for the
instructions of the assistant."""

import dataclasses
import os
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import jobs, paths, skills

SKILL = "---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nDo it this way.\n"


def write_skill(root, name, description="When to use it.", text=None, files=None):
    directory = root / ".fleet" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(text if text is not None
                                         else SKILL.format(name=name, description=description))
    for file, content in (files or {}).items():
        (directory / file).write_text(content)
    return directory


class SkillTest(Base):
    def setUp(self):
        super().setUp()
        self.controller = self.root / "controller-skills"
        patch = mock.patch.object(skills, "CONTROLLER_DIR", self.controller)
        patch.start()
        self.addCleanup(patch.stop)

    def project_tree(self):
        project = self.local_project()
        return project, self.root / "src" / "site"

    def test_skills_of_a_project_its_collections_and_the_controller(self):
        project, tree = self.project_tree()
        write_skill(tree, "lab-maintenance", "Maintaining a lab: wake, update, shut down.",
                    files={"steps.md": "1. wol\n", "run.sh": "echo no\n"})
        (tree / "collections").mkdir()
        (tree / "collections" / "requirements.yml").write_text("collections: []\n")
        digest = jobs.requirements(tree)[1]
        collection = jobs.collections_home(project["id"]) / digest / "ansible_collections" \
            / "acme" / "tools"
        write_skill(collection, "task-strings", "The task strings of acme.tools.")
        self.controller.mkdir()
        write_skill(self.controller.parent / "x", "unused")  # not a source
        (self.controller / "site").mkdir()
        (self.controller / "site" / "SKILL.md").write_text(
            "---\nname: site\ndescription: >-\n  The labs of this school\n  and who to ask.\n---\n")
        found = {(one["source"], one["name"]): one
                 for one in self.ok("skill.list", role="viewer")["skills"]}
        self.assertEqual(sorted(found), sorted([
            ("controller", "site"), (f"project:{project['id']}", "lab-maintenance"),
            (f"collection:{project['id']}:acme.tools", "task-strings")]))
        self.assertEqual(found[("controller", "site")]["description"],
                         "The labs of this school and who to ask.")
        mine = found[(f"project:{project['id']}", "lab-maintenance")]
        self.assertEqual((mine["files"], mine["from"]), (["steps.md"], "the project site"))
        text = self.ok("skill.read", {"source": f"project:{project['id']}",
                                      "name": "lab-maintenance"}, role="viewer")["text"]
        self.assertIn("Do it this way.", text)
        self.assertEqual(self.ok("skill.read", {"source": f"project:{project['id']}",
                                                "name": "lab-maintenance", "file": "steps.md"},
                                 role="viewer")["text"], "1. wol\n")
        for bad, code in (({"file": "run.sh"}, "bad_request"),
                          ({"file": "../SKILL.md"}, "bad_request"),
                          ({"name": "../x"}, "bad_request"),
                          ({"name": "nope"}, "not_found"),
                          ({"source": "project:pnope"}, "not_found")):
            params = {"source": f"project:{project['id']}", "name": "lab-maintenance", **bad}
            self.assertEqual(self.code("skill.read", params), code, bad)
        index = skills.index(self.ctx())
        self.assertIn("- task-strings (source collection:", index)
        self.assertNotIn("Do it this way", index)

    def test_what_is_not_a_skill_is_left_out(self):
        project, tree = self.project_tree()
        write_skill(tree, "no-description", text="---\nname: no-description\n---\nbody\n")
        write_skill(tree, "other-name", text="---\nname: another\ndescription: x\n---\n")
        write_skill(tree, "no-frontmatter", text="# just text\n")
        write_skill(tree, "Upper", "x")
        big = write_skill(tree, "too-big", "x")
        (big / "SKILL.md").write_text(SKILL.format(name="too-big", description="x")
                                      + "y" * skills.MAX_FILE)
        write_skill(tree, "fine", "'Quoted: yes'")
        self.assertEqual([(one["name"], one["description"])
                          for one in self.ok("skill.list")["skills"]], [("fine", "Quoted: yes")])

    def test_a_link_is_never_followed(self):
        project, tree = self.project_tree()
        secret = self.root / "secret.txt"
        secret.write_text("the database")
        directory = write_skill(tree, "linked", "x", files={"notes.md": "ok"})
        (directory / "SKILL.md").unlink()
        (directory / "SKILL.md").symlink_to(secret)
        (directory / "other.md").symlink_to(secret)
        elsewhere = self.root / "elsewhere"
        write_skill(elsewhere, "far", "far away")
        (tree / ".fleet" / "skills" / "far").symlink_to(elsewhere / ".fleet" / "skills" / "far")
        self.assertEqual(self.ok("skill.list")["skills"], [])
        # A SKILL.md there makes it a skill; its other file is still a link.
        (directory / "SKILL.md").unlink()
        (directory / "SKILL.md").write_text(SKILL.format(name="linked", description="x"))
        source = f"project:{project['id']}"
        self.assertEqual(self.code("skill.read", {"source": source, "name": "linked",
                                                  "file": "other.md"}), "not_found")
        self.assertEqual(self.code("skill.read", {"source": source, "name": "far"}), "not_found")
        # A link on the way to the skills is not followed either.
        os.rename(tree / ".fleet", self.root / "moved")
        (tree / ".fleet").symlink_to(self.root / "moved")
        self.assertEqual(self.ok("skill.list")["skills"], [])

    def test_the_index_is_bounded_and_says_where_the_rest_is(self):
        project, tree = self.project_tree()
        for at in range(40):
            write_skill(tree, f"skill-{at:02d}", "d" * 900)
        index = skills.index(self.ctx())
        self.assertLessEqual(len(index), skills.MAX_INDEX + 200)
        self.assertIn("more: skill_list lists them all.", index)
        self.assertEqual(len(self.ok("skill.list")["skills"]), 40)

    def test_frontmatter(self):
        self.assertEqual(skills.frontmatter("---\nname: a\ndescription: |\n  one\n  two\n---\n"),
                         {"name": "a", "description": "one\ntwo"})
        self.assertEqual(skills.frontmatter("no\n---\nname: a\n---"), {})
        self.assertEqual(skills.frontmatter('---\ndescription: "x: y"\n---'), {"description": "x: y"})
        self.assertEqual(skills.frontmatter("---\nname: a\ndescription: Managing\n  workstations,\n"
                                            "  and more.\nother: b\n---\n"),
                         {"name": "a", "description": "Managing workstations, and more.",
                          "other": "b"})


class DocumentSkillTest(Base):
    """Skills of the documents, which the assistant may write and a person approves."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(skills, "CONTROLLER_DIR", self.root / "controller-skills")
        patch.start()
        self.addCleanup(patch.stop)
        self.config = dataclasses.replace(self.config, mcp_enabled=True, mcp_max_role="operator")

    def write(self, text, by_agent=False, expected="", role="operator"):
        params = {"path": "skills/patching/SKILL.md", "content": text, "expected": expected}
        if by_agent:
            params["via"] = "mcp:test"
        answer = self.call("doc.write", params, role)
        self.assertTrue(answer["ok"], answer)
        return answer["data"]

    def listed(self):
        data = self.ok("skill.list", role="viewer")
        return ([one["name"] for one in data["skills"] if one["source"] == "documents"],
                data["waiting"])

    def test_a_skill_the_agent_wrote_waits_for_a_person(self):
        first = self.write(SKILL.format(name="patching", description="Patching a lab."),
                           by_agent=True)
        commit = self.ok("doc.history", role="viewer")["commits"][0]
        self.assertEqual(commit["author"], "anna")
        self.assertTrue(commit["agent"].startswith("mcp:test"), commit)
        names, waiting = self.listed()
        self.assertEqual(names, [])
        self.assertEqual([(one["name"], one["revision"], one["approved_revision"])
                          for one in waiting], [("patching", first["revision"], None)])
        self.assertEqual(self.code("skill.read", {"source": "documents", "name": "patching"},
                                   role="viewer"), "conflict")
        self.assertNotIn("patching", skills.index(self.ctx()))

        # Never by an agent, nor by a viewer, nor for a version not looked at.
        self.assertEqual(self.code("skill.approve", {"name": "patching", "revision":
                                   first["revision"], "via": "mcp:test"}, role="operator"),
                         "forbidden")
        self.assertEqual(self.code("skill.approve", {"name": "patching",
                                                     "revision": first["revision"]},
                                   role="viewer"), "forbidden")
        self.assertEqual(self.code("skill.approve", {"name": "patching", "revision": "0" * 40},
                                   role="operator"), "conflict")
        approved = self.ok("skill.approve", {"name": "patching", "revision": first["revision"]},
                           role="operator")
        self.assertEqual(self.listed(), (["patching"], []))
        read = self.ok("skill.read", {"source": "documents", "name": "patching"}, role="viewer")
        self.assertIn("Do it this way.", read["text"])
        self.assertIn("patching (source documents", skills.index(self.ctx()))
        commit = self.ok("doc.history", role="viewer")["commits"][0]
        self.assertEqual((commit["revision"], commit["subject"], commit["author"],
                          commit["approves"], commit["agent"], commit["paths"]),
                         (approved["revision"], "Approve the skill patching", "anna",
                          "patching", "", []))
        self.assertEqual(self.code("skill.approve", {"name": "patching",
                                                     "revision": first["revision"]},
                                   role="operator"), "conflict")  # nothing waits
        own = self.ok("doc.history", {"path": "skills/patching/SKILL.md"}, role="viewer")
        self.assertEqual([(one["subject"], one["approves"]) for one in own["commits"]],
                         [("Approve the skill patching", "patching"),
                          ("Create skills/patching/SKILL.md", "")])

        # Changed again by the agent, it waits again; the last approved is the first.
        second = self.write(SKILL.format(name="patching", description="Patching a lab, safely."),
                            by_agent=True, expected=read_digest(self))
        names, waiting = self.listed()
        self.assertEqual(names, [])
        self.assertEqual((waiting[0]["revision"], waiting[0]["approved_revision"]),
                         (second["revision"], first["revision"]))
        diff = self.ok("doc.diff", {"revision": second["revision"],
                                    "against": first["revision"],
                                    "path": "skills/patching"}, role="viewer")["text"]
        self.assertIn("+description: Patching a lab, safely.", diff)

        # A person's own save approves it.
        self.write(SKILL.format(name="patching", description="Patching a lab."),
                   expected=read_digest(self))
        self.assertEqual(self.listed(), (["patching"], []))

    def test_a_skill_a_person_wrote_is_a_skill_at_once_with_its_texts_only(self):
        self.write(SKILL.format(name="patching", description="Patching a lab."))
        (paths.DOCUMENTS_DIR / "skills" / "patching" / "notes.txt").write_text("not a text\n")
        self.ok("doc.write", {"path": "skills/patching/steps.md", "content": "1. wol\n"},
                role="operator")
        found = [one for one in self.ok("skill.list", role="viewer")["skills"]
                 if one["source"] == "documents"]
        self.assertEqual([(one["name"], one["files"]) for one in found],
                         [("patching", ["steps.md"])])
        self.assertEqual(self.code("skill.read", {"source": "documents", "name": "patching",
                                                  "file": "notes.txt"}, role="viewer"),
                         "bad_request")
        self.assertEqual(self.ok("skill.read", {"source": "documents", "name": "patching",
                                                "file": "steps.md"}, role="viewer")["text"],
                         "1. wol\n")
        self.assertEqual(self.code("skill.approve", {"name": "nothing", "revision": "0" * 40},
                                   role="operator"), "not_found")


def read_digest(test):
    return test.ok("doc.read", {"path": "skills/patching/SKILL.md"}, role="viewer")["sha256"]
