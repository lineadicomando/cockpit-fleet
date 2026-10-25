import json
import re
import unittest
from pathlib import Path

import cockpit_fleet

ROOT = Path(__file__).resolve().parents[2]


class VersionTest(unittest.TestCase):
    def test_package_json_matches(self):
        package = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(package["version"], cockpit_fleet.__version__)

    def test_debian_changelog_matches(self):
        first_line = (ROOT / "debian" / "changelog").read_text().splitlines()[0]
        match = re.match(r"cockpit-fleet \(([^)]+)\)", first_line)
        self.assertIsNotNone(match, first_line)
        self.assertEqual(match.group(1), cockpit_fleet.__version__)

    def test_collection_matches(self):
        galaxy = (ROOT / "collection" / "ansible_collections" / "lineadicomando" / "fleet"
                  / "galaxy.yml").read_text()
        match = re.search(r"^version:\s*(\S+)$", galaxy, re.MULTILINE)
        self.assertIsNotNone(match, "no version in galaxy.yml")
        self.assertEqual(match.group(1), cockpit_fleet.__version__)

    def test_rpm_spec_matches(self):
        spec = (ROOT / "packaging" / "cockpit-fleet.spec").read_text()
        match = re.search(r"^Version:\s*(\S+)$", spec, re.MULTILINE)
        self.assertIsNotNone(match, "no Version in the spec")
        self.assertEqual(match.group(1), cockpit_fleet.__version__)


if __name__ == "__main__":
    unittest.main()
