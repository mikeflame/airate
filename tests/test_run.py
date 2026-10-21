import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
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
if "CRASH" in prompt:
    sys.exit(1)
if "WRONG" in prompt:
    (cwd / "done.txt").write_text("wrong answer")
if "LINGER" in prompt:
    import subprocess
    subprocess.Popen(["bash", "-c", "sleep 2; echo late > done.txt"])
    time.sleep(30)
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
            "id": "fake-task", "prompt": "Create done.txt", "check": "test -f done.txt && grep -q ok done.txt && grep -q seed seed.txt",
            "timeout_s": 30, "protected": ["seed.txt"],
            "arms": {"with": {"setup": "echo setup > setup.txt", "prompt_suffix": "use the subject",
                              "policy": "test -f setup.txt", "necessity": "grep -q ok done.txt"},
                     "baseline": {"setup": "", "prompt_suffix": "FAIL without it", "policy": "! test -f setup.txt"}},
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

    def run_one(self, arm: str) -> dict:
        (self.task / "task.json").write_text(json.dumps(self.spec))
        out = self.dir / "runs"
        run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                  "--agent-cmd", f"python3 {self.agent} {{prompt_file}} {{cwd}}", "--trials", "1", "--arms", arm,
                  "--task-source", "custom", "--out", str(out)])
        return json.loads(next(out.rglob("trial.json")).read_text())

    def test_subject_not_necessary_after_a_passing_check_is_void(self):
        self.spec["arms"]["with"]["necessity"] = "test -f the-subject-was-not-used"
        trial = self.run_one("with")
        self.assertEqual(trial["check_rc"], 0)
        self.assertEqual((trial["arm_ok"], trial["success"]), (False, False))

    def test_broken_policy_is_void_even_when_the_check_passes(self):
        self.spec["arms"]["baseline"]["prompt_suffix"] = "solve it anyway"   # the agent solves it...
        self.spec["arms"]["baseline"]["setup"] = "echo setup > setup.txt"  # ...but the baseline policy is broken
        trial = self.run_one("baseline")
        self.assertEqual(trial["check_rc"], 0)
        self.assertEqual((trial["arm_ok"], trial["success"]), (False, False))

    def test_wrong_answer_that_kept_the_policy_is_a_failed_attempt(self):
        self.spec["arms"]["baseline"]["prompt_suffix"] = "FAIL, give a WRONG answer"
        trial = self.run_one("baseline")
        self.assertNotEqual(trial["check_rc"], 0)
        self.assertEqual((trial["arm_ok"], trial["success"]), (True, False), "not void: its cost counts")
        self.assertEqual(trial["cost_usd"], 0.01)

    def test_failed_check_in_the_with_arm_is_a_failed_attempt(self):
        self.spec["arms"]["with"]["prompt_suffix"] = "FAIL, give a WRONG answer"
        trial = self.run_one("with")
        self.assertNotEqual(trial["check_rc"], 0)
        self.assertEqual((trial["arm_ok"], trial["success"]), (True, False), "necessity is not judged on a failing solution")

    def test_agent_error_after_a_passing_solution_is_still_a_solve(self):
        self.spec["arms"]["with"]["prompt_suffix"] = "then CRASH"
        self.spec["arms"]["baseline"]["prompt_suffix"] = "FAIL without it"
        (self.task / "task.json").write_text(json.dumps(self.spec))
        out = self.dir / "runs"
        code = run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                         "--agent-cmd", f"python3 {self.agent} {{prompt_file}} {{cwd}}", "--trials", "1",
                         "--task-source", "custom", "--out", str(out)])
        self.assertEqual(code, 0, "the harness and the validator agree: the report is fileable")
        report = json.loads(next(out.rglob("report.json")).read_text())
        trial = report["bench"]["arms"]["with"]["trials"][0]
        self.assertEqual((trial["success"], trial["agent_error"], trial["cost_usd"]), (True, True, None))
        self.assertEqual(validate.validate_report(report), [])

    def test_nothing_solved_is_inconclusive_but_fileable(self):
        out = self.dir / "runs"
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                             "--agent-cmd", "false", "--trials", "1", "--task-source", "custom", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("inconclusive", buf.getvalue())

    def test_timeout_kills_the_whole_process_tree(self):
        self.spec["timeout_s"] = 1
        self.spec["arms"]["with"]["prompt_suffix"] = "LINGER, FAIL to write anything yourself"
        (self.task / "task.json").write_text(json.dumps(self.spec))
        out = self.dir / "runs"
        run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                  "--agent-cmd", f"python3 {self.agent} {{prompt_file}} {{cwd}}", "--trials", "1", "--arms", "with",
                  "--task-source", "custom", "--out", str(out)])
        trial = json.loads(next(out.rglob("trial.json")).read_text())
        self.assertTrue(trial["agent_error"])
        self.assertFalse(trial["success"], "work finished after the deadline must not count")
        self.assertLess(trial["duration_ms"], 1900)
        time.sleep(2.5)
        self.assertFalse((next(out.rglob("workspace")) / "done.txt").exists(), "the agent's child was killed with it")

    def test_agent_failure_is_recorded_not_fatal(self):
        out = self.dir / "runs"
        code = run.main(["--task", str(self.task), "--subject", "acme/thing", "--agent", "custom",
                         "--agent-cmd", "false", "--trials", "1", "--arms", "with", "--task-source", "custom",
                         "--out", str(out)])
        self.assertEqual(code, 1, "a report with only one arm is invalid and must not be submitted")
        trial = json.loads(next(out.rglob("trial.json")).read_text())
        self.assertTrue(trial["agent_error"])
        self.assertFalse(trial["success"])


class SubjectMembershipTests(unittest.TestCase):
    def test_task_refuses_an_undeclared_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                run.main(["--task", str(ROOT / "tasks" / "js-date-parse"), "--subject", "python-jsonschema/jsonschema",
                          "--agent", "custom", "--agent-cmd", "true", "--out", tmp])
            self.assertEqual(ctx.exception.code, 2)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DateTaskTests(unittest.TestCase):
    """The shipped task must be solvable with Node alone, fail on an empty workspace, and its arm checks must
    test necessity: text that merely mentions the subject does not pass the with arm."""

    task = json.loads((ROOT / "tasks" / "js-date-parse" / "task.json").read_text())

    def workspace(self, tmp, solution: str | None, fake_dayjs: bool = False) -> pathlib.Path:
        ws = pathlib.Path(tmp) / "ws"
        shutil.copytree(ROOT / "tasks" / "js-date-parse" / "workspace", ws)
        if solution is not None:
            (ws / "parse.mjs").write_text(solution)
        if fake_dayjs:
            pkg = ws / "node_modules" / "dayjs"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text('{"name": "dayjs", "version": "0.0.0-fake", "main": "index.js"}')
            (pkg / "index.js").write_text("module.exports = {};\n")
        return ws

    def sh(self, cmd: str, ws: pathlib.Path) -> int:
        return subprocess.run(["bash", "-c", cmd], cwd=ws, capture_output=True).returncode

    def test_reference_solution_passes_and_empty_fails(self):
        reference = (FIXTURES / "reference_parse.mjs").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.sh(self.task["check"], self.workspace(tmp, reference)), 0)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotEqual(self.sh(self.task["check"], self.workspace(tmp, None)), 0)

    def test_baseline_policy_is_about_packages_not_correctness(self):
        reference = (FIXTURES / "reference_parse.mjs").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.sh(self.task["arms"]["baseline"]["policy"], self.workspace(tmp, reference)), 0)
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.workspace(tmp, "export function parseDate() { return null; }\n")
            self.assertNotEqual(self.sh(self.task["check"], ws), 0, "a wrong answer")
            self.assertEqual(self.sh(self.task["arms"]["baseline"]["policy"], ws), 0, "still a baseline attempt: its cost must count")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotEqual(self.sh(self.task["arms"]["baseline"]["policy"], self.workspace(tmp, reference, fake_dayjs=True)), 0, "a package was installed")

    def test_with_necessity_rejects_a_comment_that_only_mentions_the_subject(self):
        reference = (FIXTURES / "reference_parse.mjs").read_text() + "\n// from 'dayjs'\n"
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.workspace(tmp, reference, fake_dayjs=True)
            self.assertEqual(self.sh(self.task["check"], ws), 0, "the tests pass")
            self.assertEqual(self.sh(self.task["arms"]["with"]["policy"], ws), 0, "the subject is installed")
            self.assertNotEqual(self.sh(self.task["arms"]["with"]["necessity"], ws), 0, "but the subject was not necessary")
            self.assertTrue((ws / "node_modules" / "dayjs").is_dir(), "necessity restores what it hid")

    def test_with_necessity_requires_the_subject_to_be_loaded(self):
        reference = 'import "dayjs";\n' + (FIXTURES / "reference_parse.mjs").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.workspace(tmp, reference, fake_dayjs=True)
            self.assertEqual(self.sh(self.task["check"], ws), 0)
            self.assertEqual(self.sh(self.task["arms"]["with"]["necessity"], ws), 0, "tests fail once dayjs is hidden")
            self.assertNotEqual(self.sh(self.task["arms"]["baseline"]["policy"], ws), 0, "and the baseline policy fails for the same workspace")


class ConfigTaskTests(unittest.TestCase):
    """The Python task must be solvable with the standard library, fail on an empty workspace, and its baseline
    arm check must accept a dependency-free solution in a plain venv."""

    task = json.loads((ROOT / "tasks" / "py-config-validate" / "task.json").read_text())

    def workspace(self, tmp, solution: bool) -> pathlib.Path:
        ws = pathlib.Path(tmp) / "ws"
        shutil.copytree(ROOT / "tasks" / "py-config-validate" / "workspace", ws)
        if solution:
            shutil.copy(FIXTURES / "reference_validate_config.py", ws / "validate_config.py")
        subprocess.run(["bash", "-c", self.task["arms"]["baseline"]["setup"]], cwd=ws, check=True, capture_output=True)
        return ws

    def sh(self, cmd: str, ws: pathlib.Path) -> int:
        return subprocess.run(["bash", "-c", cmd], cwd=ws, capture_output=True).returncode

    def test_reference_solution_passes_and_baseline_policy_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.workspace(tmp, solution=True)
            self.assertEqual(self.sh(self.task["check"], ws), 0)
            self.assertEqual(self.sh(self.task["arms"]["baseline"]["policy"], ws), 0)
            self.assertNotEqual(self.sh(self.task["arms"]["with"]["policy"], ws), 0, "no jsonschema in the venv: not a with-arm workspace")

    def test_grader_rejects_a_validator_that_raises_on_invalid_types(self):
        crashing = (FIXTURES / "reference_validate_config.py").read_text().replace(
            'if not isinstance(mode, str) or mode not in MODES:', 'if mode not in MODES:')
        self.assertNotEqual(crashing, (FIXTURES / "reference_validate_config.py").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            ws = self.workspace(tmp, solution=False)
            (ws / "validate_config.py").write_text(crashing)
            self.assertNotEqual(self.sh(self.task["check"], ws), 0, "mode: [] must produce an error, not a TypeError")

    def test_empty_workspace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotEqual(self.sh(self.task["check"], self.workspace(tmp, solution=False)), 0)


if __name__ == "__main__":
    unittest.main()
