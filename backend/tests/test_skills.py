"""Skills of the projects, of their collections and of the controller:
found where they are, read without following a link, listed for the
instructions of the assistant."""

import os
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import jobs, skills

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
