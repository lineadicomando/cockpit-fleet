"""The survey of a template: its definition, the answers, and how they reach Ansible."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_phase2 import Base

from cockpit_fleet import paths, runner, survey
from cockpit_fleet.errors import BadRequest

TICKET = "a-" + "0123456789abcdef" * 2

FIELDS = [
    {"name": "version", "label": "Version", "type": "text", "required": True, "max_length": 20},
    {"name": "replicas", "type": "number", "min": 1, "max": 10, "default": 2},
    {"name": "channel", "type": "choice", "choices": ["stable", "beta"], "default": "stable"},
    {"name": "restart", "type": "boolean"},
]


class DefinitionTest(unittest.TestCase):
    def test_fields_are_normalised(self):
        fields = survey.check_survey(FIELDS)
        self.assertEqual(fields[0], {"name": "version", "label": "Version", "help": "",
                                     "type": "text", "required": True, "min_length": 0,
                                     "max_length": 20})
        self.assertEqual(fields[1]["default"], 2)
        self.assertNotIn("default", fields[3])

    def test_invalid_definitions_are_refused(self):
        for bad in (
            "text", [1], [{"name": "a"}], [{"name": "a", "type": "password"}],
            [{"name": "a", "type": "secret", "default": "hunter2"}],
            [{"name": "a", "type": "secret", "max_length": 5}],
            [{"name": "1a", "type": "text"}], [{"name": "a-b", "type": "text"}],
            [{"name": "ansible_user", "type": "text"}],
            [{"name": "Ansible_ssh_common_args", "type": "text"}],
            [{"name": "a", "type": "text"}, {"name": "a", "type": "boolean"}],
            [{"name": "a", "type": "text", "choices": ["x"]}],
            [{"name": "a", "type": "text", "min_length": 5, "max_length": 2}],
            [{"name": "a", "type": "text", "max_length": 100000}],
            [{"name": "a", "type": "text", "required": "yes"}],
            [{"name": "a", "type": "text", "label": "two\nlines"}],
            [{"name": "a", "type": "number", "min": "1"}],
            [{"name": "a", "type": "number", "min": 5, "max": 1}],
            [{"name": "a", "type": "number", "default": 11, "max": 10}],
            [{"name": "a", "type": "number", "default": True}],
            [{"name": "a", "type": "choice"}], [{"name": "a", "type": "choice", "choices": []}],
            [{"name": "a", "type": "choice", "choices": ["x", "x"]}],
            [{"name": "a", "type": "choice", "choices": ["x"], "default": "y"}],
            [{"name": "a", "type": "boolean", "default": "true"}],
            [{"name": f"v{n}", "type": "boolean"} for n in range(31)],
        ):
            with self.assertRaises(BadRequest, msg=bad):
                survey.check_survey(bad)


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self.fields = survey.check_survey(FIELDS)

    def test_defaults_fill_what_is_left_empty(self):
        self.assertEqual(survey.answer(self.fields, {"version": "1.2"}),
                         ({"version": "1.2", "replicas": 2, "channel": "stable"}, {}))
        self.assertEqual(
            survey.answer(self.fields, {"version": "1.2", "replicas": 3.5, "channel": "beta",
                                        "restart": False})[0],
            {"version": "1.2", "replicas": 3.5, "channel": "beta", "restart": False})

    def test_invalid_answers_are_refused(self):
        for bad in ({}, {"version": ""}, {"version": None}, {"version": 12},
                    {"version": "x" * 21}, {"version": "a\nb"}, {"version": "a\x00b"},
                    {"version": "1", "replicas": 0}, {"version": "1", "replicas": 11},
                    {"version": "1", "replicas": "3"}, {"version": "1", "replicas": True},
                    {"version": "1", "replicas": float("nan")},
                    {"version": "1", "channel": "nightly"}, {"version": "1", "channel": ["beta"]},
                    {"version": "1", "restart": "yes"}, {"version": "1", "other": "x"}):
            with self.assertRaises(BadRequest, msg=bad):
                survey.answer(self.fields, bad)

    def test_template_text_in_an_answer_is_just_text(self):
        value = "{{ lookup('pipe', 'id') }}"
        self.assertEqual(survey.answer(self.fields, {"version": value[:20]})[0]["version"],
                         value[:20])

    def test_a_secret_answer_is_a_ticket_never_a_value(self):
        fields = survey.check_survey([{"name": "token", "type": "secret", "required": True},
                                      {"name": "extra", "type": "secret"}])
        self.assertEqual(survey.answer(fields, {"token": TICKET}), ({}, {"token": TICKET}))
        for bad in ({}, {"token": "hunter2"}, {"token": "a-" + "g" * 32}, {"token": TICKET + "/x"},
                    {"token": "../" + TICKET}, {"token": 1}, {"token": TICKET, "extra": "x"}):
            with self.assertRaises(BadRequest, msg=bad):
                survey.answer(fields, bad)


class VarsFileTest(unittest.TestCase):
    def test_strings_are_tagged_unsafe_and_escaped(self):
        text = runner.survey_vars_file({
            "a": "{{ lookup('pipe', 'id') }}", "b": 'q"\\ #: x', "c": "caffè € 😀",
            "n": 3, "f": 1.5, "big": 1e16, "t": True, "no": False,
        })
        self.assertEqual(text.splitlines(), [
            "a: !unsafe \"{{ lookup('pipe', 'id') }}\"",
            'b: !unsafe "q\\"\\\\ #: x"',
            'c: !unsafe "caff\\xE8 \\u20AC \\U0001F600"',
            "n: 3", "f: 1.5", "big: 1.0e+16", "t: true", "no: false",
        ])

    def test_a_string_cannot_break_out_of_its_line(self):
        text = runner.survey_vars_file({"a": 'x"\nansible_connection: local\n'})
        self.assertEqual(text, 'a: !unsafe "x\\"\\x0Aansible_connection: local\\x0A"\n')

    def test_names_and_values_are_checked_again(self):
        for bad in ([], {"ansible_user": "x"}, {"a b": "x"}, {"a: 1\nb": "x"}, {"a": None},
                    {"a": [1]}, {"a": {"b": 1}}, {"a": float("inf")}):
            with self.assertRaises(BadRequest, msg=bad):
                runner.survey_vars_file(bad)

    def test_answers_come_after_the_template_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, runtime = Path(tmp) / "run", Path(tmp) / "runtime"
            (run / "project").mkdir(parents=True)
            runtime.mkdir()
            (run / "project" / "site.yml").write_text("")
            (run / "project" / "hosts").write_text("")
            argv = runner.build_argv({"playbook": "site.yml", "inventory": "hosts",
                                      "extra_vars": {"version": "0"},
                                      "survey_vars": {"version": "s3cret"}}, run, runtime, None)
            files = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--extra-vars"]
            self.assertEqual(files, [f"@{runtime / 'extra_vars.json'}",
                                     f"@{runtime / 'survey_vars.yml'}"])
            self.assertNotIn("s3cret", " ".join(argv))
            self.assertEqual((runtime / "survey_vars.yml").read_text(),
                             'version: !unsafe "s3cret"\n')


class SecretVarsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.run, self.runtime, self.creds = root / "run", root / "runtime", root / "creds"
        for directory in (self.run / "project", self.runtime, self.creds):
            directory.mkdir(parents=True)
        (self.run / "project" / "site.yml").write_text("")
        (self.run / "project" / "hosts").write_text("")
        self.spec = {"playbook": "site.yml", "inventory": "hosts",
                     "survey_secrets": {"token": "survey_0"}}

    def test_a_secret_answer_reaches_ansible_in_a_private_file_only(self):
        (self.creds / "fleet_survey_0").write_text("hunter2 {{ x }}")
        argv = runner.build_argv({**self.spec, "survey_vars": {"n": 1}}, self.run, self.runtime,
                                 self.creds)
        file = self.runtime / "survey_vars.yml"
        self.assertEqual(argv[argv.index("--extra-vars") + 1], f"@{file}")
        self.assertNotIn("hunter2", " ".join(argv))
        self.assertEqual(file.read_text(), 'n: 1\ntoken: !unsafe "hunter2 {{ x }}"\n')
        self.assertEqual(oct(file.stat().st_mode & 0o077), "0o0")

    def test_a_run_does_not_go_on_without_a_promised_secret(self):
        for creds in (self.creds, None):
            with self.assertRaises(BadRequest):
                runner.build_argv(self.spec, self.run, self.runtime, creds)
        for bad in ([], {"token": "../private_key"}, {"token": "private_key"}, {"token": 1},
                    {"ansible_user": "survey_0"}):
            (self.creds / "fleet_survey_0").write_text("x")
            with self.assertRaises(BadRequest, msg=bad):
                runner.build_argv({**self.spec, "survey_secrets": bad}, self.run, self.runtime,
                                  self.creds)


class SurveyRunTest(Base):
    def setUp(self):
        super().setUp()
        (self.root / "answers").mkdir()
        patch = mock.patch.object(paths, "ANSWERS_DIR", self.root / "answers")
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_secret_answer_serves_one_run_and_leaves_no_trace(self):
        template = self.template(survey=[{"name": "token", "type": "secret", "required": True}])
        waiting = paths.ANSWERS_DIR / f"{TICKET}.cred"
        waiting.write_bytes(b"CIPHERTEXT")
        launch = {"template_id": template["id"], "answers": {"token": TICKET}}
        run = self.ok("run.start", launch, role="operator")
        directory = paths.RUNS_DIR / run["id"]
        spec = json.loads((directory / "spec.json").read_text())

        self.assertEqual((directory / "creds" / "survey_0").read_bytes(), b"CIPHERTEXT")
        self.assertFalse(waiting.exists())
        self.assertEqual(spec["survey_secrets"], {"token": "survey_0"})
        self.assertEqual(spec["survey_vars"], {})
        self.assertEqual(run["options"]["answers"], {})
        self.assertEqual(run["options"]["secret_answers"], ["token"])
        # The ticket is spent: a second launch needs the secret typed again.
        self.assertEqual(self.code("run.start", launch, role="operator"), "bad_request")
        self.assertEqual(len(list(paths.RUNS_DIR.iterdir())), 1)
        self.assertEqual(self.code("run.start", {"template_id": template["id"]}, role="operator"),
                         "bad_request")

    def test_template_keeps_its_survey_and_only_an_admin_defines_it(self):
        template = self.template(survey=FIELDS)
        self.assertEqual([field["name"] for field in template["survey"]],
                         ["version", "replicas", "channel", "restart"])
        listed = self.ok("template.list", role="operator")["templates"][0]
        self.assertEqual(listed["survey"], template["survey"])
        base = {"id": template["id"], "name": "deploy", "project_id": template["project_id"],
                "playbook": "site.yml", "inventory_id": template["inventory_id"]}
        self.assertEqual(self.code("template.update", {**base, "survey": FIELDS}, role="operator"),
                         "forbidden")
        self.assertEqual(self.code("template.update", {**base, "survey": [{"name": "a"}]}),
                         "bad_request")
        self.assertEqual(self.code("template.update", {**base, "survey": ["a"]}), "bad_request")
        self.assertEqual(self.ok("template.update", base)["survey"], [])

    def test_operator_answers_become_variables_of_the_run(self):
        template = self.template(survey=FIELDS, extra_vars={"version": "0", "fixed": "{{ x }}"})
        run = self.ok("run.start", {"template_id": template["id"],
                                    "answers": {"version": "{{ evil }}", "restart": True}},
                      role="operator")
        spec = json.loads((paths.RUNS_DIR / run["id"] / "spec.json").read_text())
        expected = {"version": "{{ evil }}", "replicas": 2, "channel": "stable", "restart": True}
        self.assertEqual(spec["survey_vars"], expected)
        self.assertEqual(spec["extra_vars"], {"version": "0", "fixed": "{{ x }}"})
        self.assertEqual(run["options"]["answers"], expected)

    def test_a_refused_answer_starts_nothing(self):
        template = self.template(survey=FIELDS)
        for answers in ({}, {"version": "1", "replicas": 99}, {"version": "1", "fixed": "x"},
                        ["version"]):
            self.assertEqual(self.code("run.start", {"template_id": template["id"],
                                                     "answers": answers}, role="operator"),
                             "bad_request", answers)
        self.assertEqual(self.started, [])
        self.assertEqual(list(paths.RUNS_DIR.iterdir()), [])

    def test_a_template_without_survey_takes_no_answers(self):
        template = self.template()
        self.assertEqual(self.code("run.start", {"template_id": template["id"],
                                                 "answers": {"version": "1"}}, role="operator"),
                         "bad_request")
        run = self.ok("run.start", {"template_id": template["id"]}, role="operator")
        self.assertEqual(run["options"]["answers"], {})


if __name__ == "__main__":
    unittest.main()
