import pathlib
import re
import subprocess
import tempfile
import unittest

from helpers import ROOT


def gate_diff_command() -> str:
    """The exact diff command the gate workflow runs, so the test breaks if someone weakens it."""
    text = (ROOT / ".github" / "workflows" / "gate.yml").read_text()
    line = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith("changed=$(git diff")), None)
    assert line, "gate.yml no longer lists changed files with git diff"
    return line[len("changed=$("):-1]


class GateTests(unittest.TestCase):
    def test_moving_a_report_out_of_reports_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            report = pathlib.Path(tmp) / "reports" / "o" / "r" / "x.json"
            report.parent.mkdir(parents=True)
            report.write_text('{"kind": "install"}\n')
            git("add", ".")
            git("commit", "-q", "-m", "base")
            base = git("rev-parse", "HEAD")
            (pathlib.Path(tmp) / "archive").mkdir()
            git("mv", "reports/o/r/x.json", "archive/x.json")
            git("commit", "-q", "-m", "move")
            head = git("rev-parse", "HEAD")
            cmd = gate_diff_command().replace('"$BASE"', base).replace('"$HEAD"', head)
            changed = subprocess.run(["bash", "-c", cmd], cwd=tmp, capture_output=True, text=True, check=True).stdout.split()
            self.assertIn("reports/o/r/x.json", changed, "the deleted path must be listed even though git sees a rename")
            self.assertIn("archive/x.json", changed)

    def test_commits_that_land_on_main_after_branching_are_not_blamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            pathlib.Path(tmp, "README.md").write_text("x\n")
            git("add", "."); git("commit", "-q", "-m", "base")
            git("checkout", "-q", "-b", "feature")
            pathlib.Path(tmp, "tools.txt").write_text("change\n")
            git("add", "."); git("commit", "-q", "-m", "feature work")
            head = git("rev-parse", "HEAD")
            git("checkout", "-q", "main")
            report = pathlib.Path(tmp, "reports", "o", "r", "x.json"); report.parent.mkdir(parents=True); report.write_text("{}\n")
            git("add", "."); git("commit", "-q", "-m", "intake commit after branching")
            base = git("rev-parse", "HEAD")
            cmd = gate_diff_command().replace('"$BASE"', base).replace('"$HEAD"', head)
            changed = subprocess.run(["bash", "-c", cmd], cwd=tmp, capture_output=True, text=True, check=True).stdout.split()
            self.assertEqual(changed, ["tools.txt"], "only the pull request's own changes are listed")


if __name__ == "__main__":
    unittest.main()
