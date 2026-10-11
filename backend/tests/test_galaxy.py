"""The collections a project asks for in its requirements file: the job that
installs them, where they are kept and how a run gets them."""

import fcntl
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import jobs, paths, runner
from cockpit_fleet.errors import BadRequest, Conflict

REQUIREMENTS = "collections:\n  - name: acme.tools\n"
DIGEST = hashlib.sha256(REQUIREMENTS.encode()).hexdigest()


class RequirementsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name) / "tree"
        (self.tree / "collections").mkdir(parents=True)

    def test_the_first_known_name_counts_and_its_content_is_digested(self):
        self.assertIsNone(jobs.requirements(self.tree))
        (self.tree / "requirements.yaml").write_text(REQUIREMENTS)
        self.assertEqual(jobs.requirements(self.tree), ("requirements.yaml", DIGEST))
        (self.tree / "collections" / "requirements.yml").write_text("collections: []\n")
        self.assertEqual(jobs.requirements(self.tree)[0], "collections/requirements.yml")

    def test_a_link_is_not_followed(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "requirements.yml").write_text(REQUIREMENTS)
        os.symlink(outside / "requirements.yml", self.tree / "requirements.yml")
        (self.tree / "collections").rmdir()
        os.symlink(outside, self.tree / "collections")
        self.assertIsNone(jobs.requirements(self.tree))

    def test_a_fifo_or_a_huge_file_is_not_read(self):
        os.mkfifo(self.tree / "requirements.yml")
        self.assertIsNone(jobs.requirements(self.tree))
        (self.tree / "collections" / "requirements.yml").write_bytes(
            b"#" * (jobs.MAX_REQUIREMENTS + 1))
        with self.assertRaises(Conflict):
            jobs.requirements(self.tree)


class GalaxyTest(Base):
    def project(self):
        project = self.local_project()
        (self.root / "src" / "site" / "requirements.yml").write_text(REQUIREMENTS)
        return project

    def install(self, project, files=("ansible_collections/acme/tools/MANIFEST.json",)):
        """A galaxy job, with what ansible-galaxy would leave."""
        run_id = self.ok("project.collections", {"id": project["id"]})["run_id"]
        for name in files:
            target = paths.RUNS_DIR / run_id / "galaxy" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}")
        self.finish(run_id)
        self.assertEqual(self.ok("run.get", {"id": run_id})["status"], "successful")
        return run_id

    def listed(self):
        return self.ok("project.list")["projects"][0]["collections"]

    def test_a_project_says_whether_it_asks_for_collections(self):
        project = self.local_project()
        self.assertEqual(self.listed(), {"file": "", "state": "none", "installed_at": None,
                                         "error": ""})
        self.assertEqual(self.code("project.collections", {"id": project["id"]}), "conflict")
        self.assertEqual(os.listdir(paths.RUNS_DIR), [])
        (self.root / "src" / "site" / "requirements.yml").write_text(REQUIREMENTS)
        self.assertEqual((self.listed()["file"], self.listed()["state"]),
                         ("requirements.yml", "missing"))
        self.assertEqual(self.code("project.collections", {"id": project["id"]}, role="operator"),
                         "forbidden")

    def test_the_job_is_described_by_its_spec_and_its_digest(self):
        self.config = type(self.config)(local_project_roots=self.config.local_project_roots,
                                        proxy="http://proxy.example.org:3128")
        self.ok("hostkey.add", {"host": "git.example.org", "key_type": "ssh-ed25519", "key": "AAAA"})
        project = self.project()
        run_id = self.ok("project.collections", {"id": project["id"]})["run_id"]
        directory = paths.RUNS_DIR / run_id
        self.assertEqual(json.loads((directory / "spec.json").read_text()),
                         {"kind": "galaxy", "requirements": "requirements.yml",
                          "proxy": "http://proxy.example.org:3128"})
        run = self.ok("run.get", {"id": run_id})
        self.assertEqual((run["kind"], run["options"]),
                         ("galaxy", {"path": "requirements.yml", "sha256": DIGEST}))
        self.assertEqual(os.listdir(directory / "creds"), [])
        self.assertIn("git.example.org", (directory / "known_hosts").read_text())

    def test_what_the_job_installed_is_kept_under_the_digest(self):
        project = self.project()
        run_id = self.install(project)
        home = paths.PROJECTS_DIR / project["id"] / "collections"
        self.assertTrue((home / DIGEST / "ansible_collections/acme/tools/MANIFEST.json").is_file())
        state = self.listed()
        self.assertEqual((state["state"], state["error"]), ("installed", ""))
        self.assertIsNotNone(state["installed_at"])
        self.assertEqual(sorted(os.listdir(paths.RUNS_DIR / run_id)),
                         ["known_hosts", "result.json", "spec.json"])

        # Other requirements: what was installed for the old ones is of no use.
        changed = REQUIREMENTS + "  - name: acme.more\n"
        (self.root / "src" / "site" / "requirements.yml").write_text(changed)
        self.assertEqual(self.listed()["state"], "missing")
        self.install(project)
        self.assertEqual(os.listdir(home), [hashlib.sha256(changed.encode()).hexdigest()])

    def test_nothing_to_install_is_an_installation_too(self):
        project = self.project()
        self.install(project, files=())
        self.assertEqual(self.listed()["state"], "installed")
        self.assertEqual(os.listdir(paths.PROJECTS_DIR / project["id"] / "collections" / DIGEST), [])

    def test_a_failed_job_keeps_what_was_there_and_shows_why(self):
        project = self.project()
        self.install(project)
        run_id = self.ok("project.collections", {"id": project["id"]})["run_id"]
        (paths.RUNS_DIR / run_id / "stdout.log").write_text("ERROR! no such collection\n")
        (paths.RUNS_DIR / run_id / "galaxy").mkdir()
        self.finish(run_id, rc=1)
        state = self.listed()
        self.assertEqual((state["state"], state["error"]), ("installed", "ERROR! no such collection\n"))
        self.assertFalse((paths.RUNS_DIR / run_id / "galaxy").exists())

    def test_what_a_job_left_is_not_trusted(self):
        project = self.project()
        run_id = self.ok("project.collections", {"id": project["id"]})["run_id"]
        os.symlink("/etc", paths.RUNS_DIR / run_id / "galaxy")
        self.finish(run_id)
        self.assertEqual(self.ok("run.get", {"id": run_id})["status"], "error")
        self.assertEqual(self.listed()["state"], "missing")
        self.assertIn("directory", self.listed()["error"])

    def test_a_playbook_waits_for_its_collections_and_then_gets_a_copy(self):
        project = self.project()
        inventory = self.ok("inventory.create", {"name": "inv", "kind": "managed",
                                                 "content": "all:\n"})
        template = self.ok("template.create", {"name": "deploy", "project_id": project["id"],
                                               "playbook": "site.yml",
                                               "inventory_id": inventory["id"]})
        self.assertEqual(self.code("run.start", {"template_id": template["id"]}), "conflict")
        self.assertEqual(os.listdir(paths.RUNS_DIR), [])

        self.install(project)
        run = self.ok("run.start", {"template_id": template["id"]})
        directory = paths.RUNS_DIR / run["id"]
        copy = directory / "collections/ansible_collections/acme/tools/MANIFEST.json"
        self.assertTrue(copy.is_file())
        kept = paths.PROJECTS_DIR / project["id"] / "collections" / DIGEST
        self.assertNotEqual(copy.stat().st_ino,
                            (kept / "ansible_collections/acme/tools/MANIFEST.json").stat().st_ino)
        (directory / "runtime").mkdir()
        env = runner.build_env(directory, directory / "runtime")
        self.assertTrue(env["ANSIBLE_COLLECTIONS_PATH"].endswith(f":{directory / 'collections'}"))
        self.finish(run["id"])
        self.ok("run.get", {"id": run["id"]})
        self.assertFalse((directory / "collections").exists())

    def test_new_files_that_ask_for_collections_get_them_installed(self):
        project = self.ok("project.create", {"name": "g", "kind": "git",
                                             "url": "https://example.org/a.git"})
        sync = self.ok("project.sync", {"id": project["id"]})["run_id"]
        checkout = paths.RUNS_DIR / sync / "checkout"
        checkout.mkdir()
        (checkout / "requirements.yml").write_text(REQUIREMENTS)
        self.finish(sync, revision="abc123")
        runs = self.ok("run.list", role="viewer")["runs"]
        self.assertEqual([(run["kind"], run["status"], run["created_by"]) for run in runs],
                         [("galaxy", "running", "anna"), ("sync", "successful", "anna")])
        # One at a time: reading the list again does not queue another.
        self.assertEqual(len(self.ok("run.list")["runs"]), 2)

        (paths.RUNS_DIR / runs[0]["id"] / "galaxy").mkdir()
        self.finish(runs[0]["id"])
        self.assertEqual(self.listed()["state"], "installed")
        # The same files again: nothing to install.
        sync = self.ok("project.sync", {"id": project["id"]})["run_id"]
        checkout = paths.RUNS_DIR / sync / "checkout"
        checkout.mkdir()
        (checkout / "requirements.yml").write_text(REQUIREMENTS)
        self.finish(sync, revision="def456")
        self.assertEqual(len(self.ok("run.list")["runs"]), 3)

    def test_a_run_is_collected_once_when_two_callers_get_there_together(self):
        project = self.project()
        self.install(project)
        run_id = self.ok("project.collections", {"id": project["id"]})["run_id"]
        (paths.RUNS_DIR / run_id / "galaxy").mkdir()
        self.finish(run_id)
        first, second = self.ctx(), self.ctx()
        collect = jobs._collect

        def both(ctx, run):
            # The other caller arrives while this one is at work: it must wait.
            holder = os.open(paths.RUNS_DIR, os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(holder)
            collect(ctx, run)

        with mock.patch.object(jobs, "_collect", both):
            jobs.reconcile(first)
        jobs.reconcile(second)
        self.assertEqual(self.ok("run.get", {"id": run_id})["status"], "successful")
        self.assertEqual(self.listed()["state"], "installed")


class BuildGalaxyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir, self.runtime = Path(self.tmp.name) / "run", Path(self.tmp.name) / "runtime"
        (self.run_dir / "project").mkdir(parents=True)
        self.runtime.mkdir()
        (self.run_dir / "project" / "requirements.yml").write_text(REQUIREMENTS)

    def test_command_line_and_environment(self):
        spec = {"kind": "galaxy", "requirements": "requirements.yml",
                "proxy": "http://proxy.example.org:3128"}
        argv, env = runner.build_galaxy(spec, self.run_dir, self.runtime)
        self.assertEqual(argv, ["/usr/bin/ansible-galaxy", "collection", "install",
                                "--requirements-file",
                                str((self.run_dir / "project" / "requirements.yml").resolve()),
                                "--collections-path", str(self.run_dir / "galaxy")])
        self.assertTrue(env["ANSIBLE_COLLECTIONS_PATH"].endswith(f":{self.run_dir / 'galaxy'}"))
        self.assertEqual(env["https_proxy"], "http://proxy.example.org:3128")
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "https:ssh")
        self.assertEqual((env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]),
                         ("core.hooksPath", "/dev/null"))
        self.assertIn("StrictHostKeyChecking=yes", env["GIT_SSH_COMMAND"])
        self.assertNotIn("ANSIBLE_CALLBACKS_ENABLED", env)
        self.assertNotIn("https_proxy", runner.build_galaxy({**spec, "proxy": ""}, self.run_dir,
                                                            self.runtime)[1])

    def test_the_requirements_file_stays_inside_the_project(self):
        (Path(self.tmp.name) / "outside.yml").write_text(REQUIREMENTS)
        os.symlink("../../outside.yml", self.run_dir / "project" / "linked.yml")
        for path in ("../../outside.yml", "linked.yml", "missing.yml", "--force", None):
            with self.assertRaises(BadRequest):
                runner.build_galaxy({"requirements": path}, self.run_dir, self.runtime)


if __name__ == "__main__":
    unittest.main()
