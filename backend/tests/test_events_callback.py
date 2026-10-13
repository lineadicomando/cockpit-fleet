import importlib.util
import unittest
from pathlib import Path

PLUGIN = (Path(__file__).resolve().parents[2] / "collection/ansible_collections/lineadicomando/fleet"
          / "plugins/callback/events.py")

# The plugin imports Ansible, which the tests do not require.
try:
    _spec = importlib.util.spec_from_file_location("fleet_events_callback", PLUGIN)
    events = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(events)
except ImportError:
    events = None


@unittest.skipIf(events is None, "ansible is not installed")
class MaskTest(unittest.TestCase):
    def test_secrets_are_masked_in_values_at_any_depth_not_in_field_names(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "fleet_become_password").write_text("hunter2\n")
            (Path(tmp) / "fleet_survey_0").write_text('host\nwith "quotes"\nabc\n')
            (Path(tmp) / "other").write_text("not-a-secret\n")
            secrets = events.read_secrets(tmp)
        self.assertEqual(secrets, ['with "quotes"', "hunter2", "host"])
        event = {"host": "web01", "result": {"msg": 'pw hunter2 with "quotes"', "n": 3,
                                             "list": ["hunter2", {"hunter2": "abc not-a-secret"}]}}
        self.assertEqual({key: events.mask(value, secrets) for key, value in event.items()}, {
            "host": "web01", "result": {"msg": "pw ******** ********", "n": 3,
                                        "list": ["********", {"********": "abc not-a-secret"}]}})
        self.assertEqual(events.read_secrets(None), [])


@unittest.skipIf(events is None, "ansible is not installed")
class UnifiedDiffTest(unittest.TestCase):
    def test_before_and_after_become_a_unified_diff(self):
        text = events.unified_diff({"before": "a\nb\n", "after": "a\nc", "before_header": "/etc/x",
                                    "after_header": "/etc/x"})
        self.assertEqual(text, "--- before: /etc/x\n+++ after: /etc/x\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
                               "\\ No newline at end of file\n")

    def test_nothing_changed_is_no_diff(self):
        self.assertEqual(events.unified_diff({"before": "a\n", "after": "a\n"}), "")
        self.assertEqual(events.unified_diff([]), "")
        self.assertEqual(events.unified_diff(["not a diff", 3]), "")

    def test_structures_are_compared_as_json(self):
        text = events.unified_diff([{"before": {"state": "absent"}, "after": {"state": "present"}}])
        self.assertIn('-    "state": "absent"\n+    "state": "present"\n', text)
        self.assertTrue(text.startswith("--- before\n+++ after\n"))

    def test_a_file_that_did_not_exist(self):
        text = events.unified_diff({"before": None, "after": "new\n"})
        self.assertIn("@@ -0,0 +1 @@\n+new\n", text)

    def test_prepared_diffs_and_skipped_ones(self):
        text = events.unified_diff([{"prepared": "-x\n+y"}, {"dst_larger": 104448}, {"src_binary": True}])
        self.assertEqual(text, "-x\n+y\ndiff skipped: destination file size is greater than 104448\n"
                               "diff skipped: source file appears to be binary\n")

    def test_the_whole_text_is_compared_before_any_cut(self):
        before = "".join(f"line {n}\n" for n in range(5000))
        text = events.unified_diff({"before": before, "after": before.replace("line 4990\n", "changed\n")})
        self.assertIn("-line 4990\n+changed\n", text)
        self.assertLess(len(text), 400)
