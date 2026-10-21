import subprocess
import unittest

from helpers import ROOT


class IgnoreRuleTests(unittest.TestCase):
    """A report path must never match a development ignore rule, whatever the subject is called."""

    def ignored(self, path: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", "--", path], cwd=ROOT, capture_output=True).returncode == 0

    def test_report_paths_are_never_ignored(self):
        for path in ("reports/nellore/runs/20261021-install-1.json", "reports/acme/node_modules/x.json",
                     "reports/x/__pycache__/y.json", "badges/nellore/runs.json"):
            with self.subTest(path):
                self.assertFalse(self.ignored(path))

    def test_development_paths_are_ignored(self):
        for path in ("runs/x/report.json", "runs-bench2.log", "tasks/js-date-parse/workspace/node_modules/dayjs/index.js"):
            with self.subTest(path):
                self.assertTrue(self.ignored(path))


if __name__ == "__main__":
    unittest.main()
