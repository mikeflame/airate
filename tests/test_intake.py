import json
import pathlib
import tempfile
import unittest

from helpers import fixture, make_root, store
import intake


def ctx(root, issue=1, title="report: iamkun/dayjs", body=None, login="octocat", created_at="2019-01-01T00:00:00Z",
        subject_exists=True, contributors=None, now="2026-10-20T20:00:00Z"):
    data = {"issue": issue, "title": title, "body": body if body is not None else json.dumps(fixture("install.json")),
            "login": login, "created_at": created_at, "now": now, "subject_exists": subject_exists,
            "contributors": contributors or []}
    path = root / f"ctx-{issue}.json"
    path.write_text(json.dumps(data))
    return path


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(pathlib.Path(self.tmp.name))
        self.comment = self.root / "comment.md"

    def tearDown(self):
        self.tmp.cleanup()

    def run_intake(self, ctx_path):
        code = intake.main(["--context", str(ctx_path), "--comment", str(self.comment), "--root", str(self.root)])
        return code, self.comment.read_text()

    def stored(self):
        return sorted((self.root / "reports").rglob("*.json"))

    def test_accept_writes_report_and_compiles(self):
        code, msg = self.run_intake(ctx(self.root))
        self.assertEqual(code, 0, msg)
        files = self.stored()
        self.assertEqual([p.name for p in files], ["20261020-install-1.json"])
        self.assertEqual(files[0].parent, self.root / "reports" / "iamkun" / "dayjs")
        report = json.loads(files[0].read_text())
        self.assertEqual(report["intake"]["submitter"], "octocat")
        self.assertEqual(report["intake"]["weight"], 1.0)
        self.assertEqual(report["intake"]["flags"], [])
        self.assertTrue((self.root / "ratings.json").exists())
        self.assertTrue((self.root / "badges" / "iamkun" / "dayjs.json").exists())
        self.assertIn("AI Rate of iamkun/dayjs is now **38**", msg)

    def test_fenced_body_from_issue_form(self):
        body = "### Report JSON\n\n```json\n" + json.dumps(fixture("install.json"), indent=2) + "\n```\n"
        code, _ = self.run_intake(ctx(self.root, body=body))
        self.assertEqual(code, 0)

    def test_invalid_json_rejected(self):
        code, msg = self.run_intake(ctx(self.root, body="{not json"))
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", msg)
        self.assertEqual(self.stored(), [])

    def test_schema_violation_rejected_with_paths(self):
        bad = fixture("install.json")
        bad["install"]["verdict"] = "love it"
        code, msg = self.run_intake(ctx(self.root, body=json.dumps(bad)))
        self.assertEqual(code, 1)
        self.assertIn("install/verdict", msg)

    def test_bad_title_rejected(self):
        code, msg = self.run_intake(ctx(self.root, title="dayjs is great"))
        self.assertEqual(code, 1)
        self.assertIn("report:", msg)

    def test_missing_subject_rejected(self):
        code, msg = self.run_intake(ctx(self.root, subject_exists=False))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", msg)

    def test_duplicate_issue_is_a_noop(self):
        self.run_intake(ctx(self.root, issue=7))
        code, msg = self.run_intake(ctx(self.root, issue=7))
        self.assertEqual(code, 0)
        self.assertIn("already accepted", msg)
        self.assertEqual(len(self.stored()), 1)

    def test_new_account_weight(self):
        code, _ = self.run_intake(ctx(self.root, created_at="2026-10-01T00:00:00Z"))
        self.assertEqual(code, 0)
        report = json.loads(self.stored()[0].read_text())
        self.assertEqual((report["intake"]["weight"], report["intake"]["flags"]), (0.25, ["new_account"]))

    def test_maintainer_weight_zero(self):
        self.run_intake(ctx(self.root, issue=1, login="iamkun"))
        self.run_intake(ctx(self.root, issue=2, login="Someone", contributors=["someone"]))
        for p in self.stored():
            report = json.loads(p.read_text())
            self.assertEqual((report["intake"]["weight"], report["intake"]["flags"]), (0.0, ["maintainer"]))
        self.assertIn("unrated", self.comment.read_text())

    def test_cap_per_submitter(self):
        for i in range(1, 5):
            self.run_intake(ctx(self.root, issue=i))
        weights = [json.loads(p.read_text())["intake"]["weight"] for p in self.stored()]
        self.assertEqual(weights, [1.0, 1.0, 1.0, 0.0])
        self.assertEqual(json.loads(self.stored()[-1].read_text())["intake"]["flags"], ["cap"])

    def test_weekly_duplicate_week(self):
        body = json.dumps(fixture("weekly.json"))
        self.run_intake(ctx(self.root, issue=1, body=body))
        self.run_intake(ctx(self.root, issue=2, body=body))
        flags = [json.loads(p.read_text())["intake"]["flags"] for p in self.stored()]
        self.assertEqual(flags, [[], ["duplicate_week"]])

    def test_bench_task_hash_checked(self):
        bench = fixture("bench.json")
        bench["bench"]["task"]["source"] = "airate"
        self.run_intake(ctx(self.root, issue=1, body=json.dumps(bench)))
        bench["bench"]["task"]["sha256"] = intake.task_sha256(self.root / "tasks" / "js-date-parse")
        self.run_intake(ctx(self.root, issue=2, body=json.dumps(bench)))
        flags = [json.loads(p.read_text())["intake"]["flags"] for p in self.stored()]
        self.assertEqual(flags, [["task_mismatch"], []])

    def test_register(self):
        code, msg = self.run_intake(ctx(self.root, title="register: tj/commander.js"))
        self.assertEqual(code, 0)
        self.assertIn("Registered", msg)
        badge = json.loads((self.root / "badges" / "tj" / "commander.js.json").read_text())
        self.assertEqual(badge["message"], "unrated")
        code, msg = self.run_intake(ctx(self.root, issue=2, title="register: not a repo"))
        self.assertEqual(code, 1)

    def test_stored_reports_validate(self):
        import validate
        self.run_intake(ctx(self.root))
        for p in self.stored():
            self.assertEqual(validate.validate_report(json.loads(p.read_text()), stored=True), [])


if __name__ == "__main__":
    unittest.main()
