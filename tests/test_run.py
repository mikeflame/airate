import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from helpers import FIXTURES, ROOT, fixture
import run
import validate

FAKE_AGENT = r'''#!/usr/bin/env python3
import json, pathlib, sys, time
prompt = pathlib.Path(sys.argv[1]).read_text()
cwd = pathlib.Path(sys.argv[2])
if "FAIL" not in prompt:
    (cwd / "done.txt").write_text("ok")
if "TAMPER" in prompt:
    (cwd / "seed.txt").write_text("the grader is now whatever I want")
print("some agent chatter")
print(json.dumps({"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01, "duration_ms": 5, "turns": 2, "model": "fake-1"}))
'''


class ParserTests(unittest.TestCase):
    def test_claude_result_object(self):
        m = run.parse_claude_result((FIXTURES / "claude_result.json").read_text())
        self.assertEqual(m["input_tokens"], 10)
        self.assertEqual(m["output_tokens"], 35)
        self.assertEqual(m["cache_write_tokens"], 30762)
        self.assertEqual(m["cost_usd"], 0.061709)
        self.assertEqual(m["turns"], 1)
        self.assertEqual(m["model"], "claude-haiku-4-5-20251001")
        self.assertFalse(m["error"])

    def test_claude_result_array(self):
        result = json.loads((FIXTURES / "claude_result.json").read_text())
        m = run.parse_claude_result(json.dumps([{"type": "system"}, result]))
        self.assertEqual(m["duration_ms"], 1185)

    def test_custom_last_line(self):
        m = run.parse_custom_result('noise\n{"cost_usd": 0.5, "error": false}\n')
        self.assertEqual(m["cost_usd"], 0.5)
        self.assertIsNone(m["turns"])


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.task = self.dir / "task"
        (self.task / "workspace").mkdir(parents=True)
        (self.task / "workspace" / "seed.txt").write_text("seed")
        self.spec = {
            "id": "fake-task", "prompt": "Create done.txt", "check": "test -f done.txt && grep -q seed seed.txt",
            "timeout_s": 30, "protected": ["seed.txt"],
            "arms": {"with": {"setup": "echo setup > setup.txt", "prompt_suffix": "use the subject", "verify": "test -f setup.txt"},
                     "baseline": {"setup": "", "prompt_suffix": "FAIL without it", "verify": "! test -f setup.txt"}},
        }
        (self.task / "task.json").write_text(json.dumps(self.spec))
        agent = self.dir / "agent.py"
        agent.write_text(FAKE_AGENT)
        agent.chmod(0o755)
        self.agent = agent

    def tearDown(self):
        self.tmp.cleanup()

    def test_end_to_end_custom_agent(self):
        out = self.dir / "runs"
        code = run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                         "--agent-cmd", f"python3 {self.agent} {{prompt_file}} {{cwd}}", "--agent-name", "fake",
                         "--trials", "2", "--task-source", "custom", "--out", str(out)])
        self.assertEqual(code, 0)
        report_path = next(out.rglob("report.json"))
        report = json.loads(report_path.read_text())
        self.assertEqual(validate.validate_report(report), [])
        self.assertEqual(report["agent"], {"name": "fake", "model": "fake-1"})
        with_trials = report["bench"]["arms"]["with"]["trials"]
        base_trials = report["bench"]["arms"]["baseline"]["trials"]
        self.assertEqual([t["success"] for t in with_trials], [True, True])
        self.assertEqual([t["success"] for t in base_trials], [False, False])
        self.assertEqual(with_trials[0]["cost_usd"], 0.01)
        self.assertEqual((with_trials[0]["grader_intact"], with_trials[0]["arm_ok"]), (True, True))
        self.assertEqual(report["bench"]["task"]["sha256"], run.sha256_dir(self.task))
        run_dir = report_path.parent
        self.assertTrue((run_dir / "with" / "1" / "setup.log").read_text().startswith("$ echo setup"))
        self.assertTrue((run_dir / "with" / "1" / "workspace" / "setup.txt").exists())
        self.assertIn("[exit 1]", (run_dir / "baseline" / "1" / "check.log").read_text())

    def test_agent_that_edits_the_grader_fails(self):
        self.spec["arms"]["with"]["prompt_suffix"] = "TAMPER with the tests"
        (self.task / "task.json").write_text(json.dumps(self.spec))
        out = self.dir / "runs"
        run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                  "--agent-cmd", f"python3 {self.agent} {{prompt_file}} {{cwd}}", "--trials", "1", "--arms", "with",
                  "--task-source", "custom", "--out", str(out)])
        trial = json.loads(next(out.rglob("trial.json")).read_text())
        self.assertEqual(trial["check_rc"], 0, "the check ran against the restored grader")
        self.assertFalse(trial["grader_intact"])
        self.assertFalse(trial["success"])
        self.assertEqual((next(out.rglob("workspace")) / "seed.txt").read_text(), "seed", "protected file restored")

    def test_arm_condition_is_verified(self):
        self.spec["arms"]["with"]["verify"] = "test -f the-subject-was-not-used"
        (self.task / "task.json").write_text(json.dumps(self.spec))
        out = self.dir / "runs"
        run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                  "--agent-cmd", f"python3 {self.agent} {{prompt_file}} {{cwd}}", "--trials", "1", "--arms", "with",
                  "--task-source", "custom", "--out", str(out)])
        trial = json.loads(next(out.rglob("trial.json")).read_text())
        self.assertEqual(trial["check_rc"], 0)
        self.assertFalse(trial["arm_ok"])
        self.assertFalse(trial["success"])

    def test_agent_failure_is_recorded_not_fatal(self):
        out = self.dir / "runs"
        code = run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                         "--agent-cmd", "false", "--trials", "1", "--arms", "with", "--task-source", "custom",
                         "--out", str(out)])
        self.assertEqual(code, 1, "a report with only one arm is invalid and must not be submitted")
        trial = json.loads(next(out.rglob("trial.json")).read_text())
        self.assertTrue(trial["agent_error"])
        self.assertFalse(trial["success"])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DateTaskTests(unittest.TestCase):
    """The shipped task must be solvable with Node alone and must fail on an empty workspace."""

    def run_check(self, with_solution: bool) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            ws = pathlib.Path(tmp) / "ws"
            shutil.copytree(ROOT / "tasks" / "js-date-parse" / "workspace", ws)
            if with_solution:
                shutil.copy(FIXTURES / "reference_parse.mjs", ws / "parse.mjs")
            task = json.loads((ROOT / "tasks" / "js-date-parse" / "task.json").read_text())
            return subprocess.run(["bash", "-c", task["check"]], cwd=ws, capture_output=True).returncode

    def test_reference_solution_passes(self):
        self.assertEqual(self.run_check(True), 0)

    def test_empty_workspace_fails(self):
        self.assertNotEqual(self.run_check(False), 0)


if __name__ == "__main__":
    unittest.main()
