import pathlib
import unittest

import yaml

from helpers import ROOT


class WorkflowFileTests(unittest.TestCase):
    """GitHub fails a workflow at parse time without running anything, so every YAML file must parse."""

    def test_workflows_and_issue_forms_parse(self):
        files = sorted((ROOT / ".github" / "workflows").glob("*.yml")) + sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
        self.assertGreaterEqual(len(files), 6)
        for path in files:
            with self.subTest(path.name):
                data = yaml.safe_load(path.read_text())
                self.assertIsInstance(data, dict)
                if path.parent.name == "workflows":
                    self.assertIn("jobs", data)
                    for job in data["jobs"].values():
                        for step in job["steps"]:
                            self.assertTrue("run" in step or "uses" in step)

    def test_store_script_is_what_the_workflows_call(self):
        for name in ("intake.yml", "compile.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text()
            self.assertIn("tools/store.sh --message", text, name)


if __name__ == "__main__":
    unittest.main()
