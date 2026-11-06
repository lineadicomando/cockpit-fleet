import tempfile
import unittest
from pathlib import Path

from cockpit_fleet.config import Config, ConfigError, load, parse


class ConfigTest(unittest.TestCase):
    def test_empty_file_gives_defaults(self):
        self.assertEqual(parse(""), Config())

    def test_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load(Path(tmp) / "config.toml"), Config())

    def test_all_values(self):
        config = parse("""
            [runs]
            max_concurrent = 8
            default_forks = 10
            max_forks = 100
            [projects]
            local_roots = ["/srv/ansible", "/opt/playbooks/"]
            [network]
            proxy = "http://proxy.example:3128"
        """)
        self.assertEqual(config.max_concurrent_runs, 8)
        self.assertEqual((config.default_forks, config.max_forks), (10, 100))
        self.assertEqual(config.local_project_roots, ("/srv/ansible", "/opt/playbooks"))
        self.assertEqual(config.proxy, "http://proxy.example:3128")

    def test_profiles_of_the_assistant(self):
        config = parse("")
        self.assertEqual((config.chat_profiles, config.chat_default_profile),
                         (("observer", "assisted"), "assisted"))
        config = parse('[chat]\nprofiles = ["diagnostic", "administrator"]')
        self.assertEqual(config.chat_default_profile, "diagnostic")
        config = parse('[chat]\nprofiles = ["observer", "administrator"]\n'
                       'default_profile = "administrator"')
        self.assertEqual(config.chat_default_profile, "administrator")
        for bad in ('profiles = []', 'profiles = ["root"]', 'profiles = "observer"',
                    'profiles = ["observer"]\ndefault_profile = "assisted"',
                    'default_profile = "administrator"'):
            with self.assertRaises(ConfigError, msg=bad):
                parse("[chat]\n" + bad)

    def test_administrators_may_approve_their_own_unless_told_otherwise(self):
        self.assertTrue(parse("").approvals_admins_exempt)
        self.assertFalse(parse("[approvals]\nadmins_exempt = false").approvals_admins_exempt)
        with self.assertRaises(ConfigError):
            parse('[approvals]\nadmins_exempt = "no"')

    def test_limits_of_autonomy(self):
        config = parse("")
        self.assertEqual((config.chat_autonomous_runs, config.chat_autonomous_dry_run),
                         (10, False))
        config = parse("[chat]\nautonomous_runs = 3\nautonomous_dry_run = true")
        self.assertEqual((config.chat_autonomous_runs, config.chat_autonomous_dry_run),
                         (3, True))
        for bad in ("autonomous_runs = 0", "autonomous_runs = 1001", 'autonomous_runs = "3"',
                    "autonomous_dry_run = 1"):
            with self.assertRaises(ConfigError, msg=bad):
                parse("[chat]\n" + bad)

    def test_diagnostic_modules_are_among_those_of_ad_hoc_commands(self):
        self.assertIn("ansible.builtin.setup", parse("").mcp_adhoc_modules)
        self.assertNotIn("ansible.builtin.shell", parse("").mcp_adhoc_modules)
        config = parse('[adhoc]\nmodules = ["ansible.builtin.ping", "ansible.builtin.shell"]')
        self.assertEqual(config.mcp_adhoc_modules, ("ansible.builtin.ping",))
        self.assertEqual(parse("[adhoc]\nmodules = []").mcp_adhoc_modules, ())
        config = parse('[mcp]\nadhoc_modules = ["ansible.builtin.ping"]')
        self.assertEqual(config.mcp_adhoc_modules, ("ansible.builtin.ping",))
        for bad in ('[mcp]\nadhoc_modules = ["ansible.builtin.nope"]',
                    '[adhoc]\nmodules = []\n[mcp]\nadhoc_modules = ["ansible.builtin.ping"]',
                    '[mcp]\nadhoc_modules = "ansible.builtin.ping"'):
            with self.assertRaises(ConfigError, msg=bad):
                parse(bad)

    def test_inventory_scripts_are_off_unless_asked(self):
        self.assertFalse(parse("").inventory_scripts)
        self.assertTrue(parse("[inventories]\nscripts = true").inventory_scripts)

    def test_metrics_are_kept_raw_then_hourly_then_daily(self):
        config = parse("[metrics]\nraw_days = 7\nhourly_days = 30")
        self.assertEqual((config.metrics_raw_days, config.metrics_hourly_days,
                          config.metrics_daily_days), (7, 30, 730))

    def test_runs_keep_their_output_90_days_unless_told(self):
        self.assertEqual(parse("").runs_keep_days, 90)
        self.assertEqual(parse("[runs]\nkeep_days = 0").runs_keep_days, 0)

    def test_check_cycles_take_half_the_memory_unless_told(self):
        self.assertEqual(parse("").checks_memory_mb, 0)
        self.assertEqual(parse("[checks]\nmemory_mb = 1024").checks_memory_mb, 1024)
        with self.assertRaises(ConfigError):
            parse("[checks]\nmemory_mb = -1")

    def test_default_forks_follow_a_lower_maximum(self):
        self.assertEqual(parse("[runs]\nmax_forks = 2").default_forks, 2)

    def test_errors_name_the_problem(self):
        cases = {
            "[runs]\nmax_concurent = 2": "unknown key in \\[runs\\]: max_concurent",
            "[run]\nx = 1": "unknown section: run",
            "[runs]\nmax_concurrent = 0": "runs.max_concurrent",
            "[runs]\nmax_concurrent = true": "runs.max_concurrent",
            "[runs]\ndefault_forks = 60": "cannot exceed",
            "[projects]\nlocal_roots = \"/srv\"": "must be a list",
            "[projects]\nlocal_roots = [\"srv\"]": "absolute paths",
            "[projects]\nlocal_roots = [\"/srv/../etc\"]": "not usable",
            "[projects]\nlocal_roots = [\"/\"]": "not usable",
            "[projects]\nmax_upload_mb = 51": "projects.max_upload_mb",
            "[projects]\nmax_upload_mb = 0": "projects.max_upload_mb",
            "[network]\nproxy = \"proxy:3128\"": "network.proxy",
            "[network]\nproxy = \"http://u:p@proxy\"": "network.proxy",
            "runs = 3": "\\[runs\\] must be a table",
            "[inventories]\nscripts = 1": "inventories.scripts",
            "[inventories]\nscript = true": "unknown key in \\[inventories\\]",
            "[runs]\nkeep_days = -1": "runs.keep_days",
            "[metrics]\nraw_days = 0": "metrics.raw_days",
            "[metrics]\nraw_days = 100": "must not decrease",
            "[runs": "not valid TOML",
        }
        for text, message in cases.items():
            with self.assertRaisesRegex(ConfigError, message, msg=text):
                parse(text)

    def test_load_reports_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[oops]")
            with self.assertRaisesRegex(ConfigError, str(path)):
                load(path)


if __name__ == "__main__":
    unittest.main()
