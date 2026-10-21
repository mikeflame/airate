import json
import pathlib
import tempfile
import unittest

from helpers import fixture, make_root, store
import compile as compiler
import scoring


class CompileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(pathlib.Path(self.tmp.name), with_tasks=False)

    def tearDown(self):
        self.tmp.cleanup()

    def _other(self, report, repo):
        r = json.loads(json.dumps(report))
        r["subject"]["repo"] = repo
        return r

    def test_ranking_badges_and_markdown(self):
        install = fixture("install.json")
        for i in range(1, 4):
            store(self.root, install, issue=i, submitter=f"user{i}")
        store(self.root, self._other(install, "tj/commander.js"), issue=10, submitter="solo")
        store(self.root, self._other(install, "ghost/ignored"), issue=20, submitter="owner", weight=0.0, flags=["maintainer"])
        ratings = compiler.compile_ratings(self.root)
        repos = [s["repo"] for s in ratings["subjects"]]
        self.assertEqual(repos, ["iamkun/dayjs", "tj/commander.js"])  # weight-0-only subjects are not listed
        top, second = ratings["subjects"]
        self.assertEqual((top["rank"], top["reports"], top["submitters"]), (1, 3, 3))
        self.assertEqual(top["rate"], 26, msg="three first-try installs: 0.4 × Wilson(1, 3)")
        self.assertEqual(second["rate"], 5)
        self.assertEqual(top["components"]["bench"], None)
        self.assertEqual(top["components"]["retained"], None, "install verdicts are not retention")
        changed = compiler.write_all(self.root, ratings)
        self.assertTrue((self.root / "badges" / "iamkun" / "dayjs.json").exists())
        badge = json.loads((self.root / "badges" / "iamkun" / "dayjs.json").read_text())
        self.assertEqual(badge, {"schemaVersion": 1, "label": "AI Rate", "message": str(top["rate"]), "color": "orange"})
        md = (self.root / "RATINGS.md").read_text()
        self.assertIn("| 1 | [iamkun/dayjs](https://github.com/iamkun/dayjs) | library | **26** | 100% (n=3) | 100% (n=3) | – | – | 3 | 3 | claude-code |", md)
        readme = (self.root / "README.md").read_text()
        self.assertIn("<!-- ratings:start -->\n| # | Repository | AI Rate |", readme)
        self.assertNotIn("old", readme)
        self.assertTrue(readme.endswith("<!-- ratings:end -->\n\ntail\n"))
        self.assertEqual(set(p.name for p in changed), {"ratings.json", "RATINGS.md", "README.md", "dayjs.json", "commander.js.json"})
        self.assertEqual(compiler.write_all(self.root, compiler.compile_ratings(self.root)), [], "second run must be a no-op")
        self.assertEqual(ratings["generated_at"], "2026-10-20T20:00:00Z", "generated_at is the newest acceptance, not the wall clock")

    def test_weekly_and_bench_components(self):
        store(self.root, fixture("install.json"), issue=1, submitter="a")
        store(self.root, fixture("weekly.json"), issue=2, submitter="a")
        store(self.root, fixture("bench.json"), issue=3, submitter="b")
        s = compiler.compile_ratings(self.root)["subjects"][0]
        self.assertEqual(s["kinds"], {"install": 1, "weekly": 1, "bench": 1, "bench_inconclusive": 0})
        self.assertEqual(s["components"]["retained"], {"p": 1.0, "n": 1, "lower": round(scoring.wilson_lower(1, 1), 4)})
        self.assertAlmostEqual(s["components"]["bench"]["ratio"], 5.08, places=2)
        self.assertEqual(s["components"]["bench"]["p"], 1.0)
        self.assertGreater(s["rate"], 5)

    def test_removed_subject_lowers_keep(self):
        store(self.root, fixture("install.json"), issue=1, submitter="a")
        wk = fixture("weekly.json")
        wk["weekly"].update({"status": "removed", "verdict": "replace"})
        store(self.root, wk, issue=2, submitter="a")
        s = compiler.compile_ratings(self.root)["subjects"][0]
        self.assertEqual(s["components"]["retained"]["p"], 0.0)

    def test_inconclusive_benches_add_nothing(self):
        for i in range(1, 4):
            store(self.root, fixture("install.json"), issue=i, submitter=f"u{i}")
        before = compiler.compile_ratings(self.root)["subjects"][0]["rate"]
        dead = fixture("bench.json")
        for arm in dead["bench"]["arms"].values():
            for t in arm["trials"]:
                t["success"] = False
        for i in range(100, 200):
            store(self.root, dead, issue=i, submitter=f"b{i}")
        s = compiler.compile_ratings(self.root)["subjects"][0]
        self.assertEqual(s["rate"], before, "a hundred benchmarks that solved nothing must not move the rate")
        self.assertIsNone(s["components"]["bench"])
        self.assertEqual(s["kinds"]["bench_inconclusive"], 100)

    def test_retained_is_each_agents_latest_status(self):
        store(self.root, fixture("install.json"), issue=1, submitter="a")
        wk = fixture("weekly.json")
        for i in range(2, 21):
            wk["created_at"] = f"2026-10-{min(26 + (i - 2) // 10, 31):02d}T{i:02d}:00:00Z"
            store(self.root, wk, issue=i, submitter="a", weight=1 / i, flags=["repeat"])
        gone = fixture("weekly.json")
        gone["created_at"] = "2026-11-30T09:00:00Z"
        gone["weekly"].update({"status": "removed", "verdict": "replace"})
        store(self.root, gone, issue=21, submitter="a", weight=0.0, flags=["cap"])
        c = compiler.compile_ratings(self.root)["subjects"][0]["components"]
        self.assertEqual((c["retained"]["p"], c["retained"]["n"]), (0.0, 1), "a's latest check-in says removed, whatever the cap")
        store(self.root, fixture("weekly.json"), issue=30, submitter="b")
        c = compiler.compile_ratings(self.root)["subjects"][0]["components"]
        self.assertEqual((c["retained"]["p"], c["retained"]["n"]), (0.5, 2))
        dup = fixture("weekly.json")
        dup["created_at"] = "2026-12-01T00:00:00Z"
        dup["weekly"].update({"status": "removed", "verdict": "replace"})
        store(self.root, dup, issue=31, submitter="b", weight=0.0, flags=["duplicate_week"])
        c = compiler.compile_ratings(self.root)["subjects"][0]["components"]
        self.assertEqual(c["retained"]["p"], 0.5, "a duplicate-week report never speaks for an agent")

    def test_second_attempt_is_not_first_try(self):
        r = fixture("install.json")
        r["install"]["attempts_to_first_success"] = 2
        store(self.root, r, issue=1, submitter="a")
        c = compiler.compile_ratings(self.root)["subjects"][0]["components"]
        self.assertEqual((c["works"]["p"], c["first_try"]["p"]), (1.0, 0.0))

    def test_revoked_subject_badge_resets(self):
        p1 = store(self.root, fixture("install.json"), issue=1, submitter="a")
        compiler.write_all(self.root, compiler.compile_ratings(self.root))
        badge = self.root / "badges" / "iamkun" / "dayjs.json"
        self.assertIn('"message": "5"', badge.read_text())
        (self.root / "revocations.json").write_text(json.dumps([{"report": p1.relative_to(self.root).as_posix(), "reason": "test", "by": "x", "date": "2026-10-21"}]))
        compiler.write_all(self.root, compiler.compile_ratings(self.root))
        self.assertIn('"message": "unrated"', badge.read_text())
        self.assertEqual(compiler.main(["--root", str(self.root), "--check"]), 0)

    def test_revocation_excludes_report(self):
        p1 = store(self.root, fixture("install.json"), issue=1, submitter="a")
        store(self.root, fixture("install.json"), issue=2, submitter="b")
        (self.root / "revocations.json").write_text(json.dumps([{"report": p1.relative_to(self.root).as_posix(), "reason": "fabricated: issue #1 author admitted it", "by": "mikeflame", "date": "2026-10-21"}]))
        ratings = compiler.compile_ratings(self.root)
        self.assertEqual(ratings["subjects"][0]["reports"], 1)
        self.assertEqual(ratings["revoked"][0]["report"], p1.relative_to(self.root).as_posix())
        self.assertIn("## Revoked reports", compiler.render_ratings_md(ratings))

    def test_empty(self):
        ratings = compiler.compile_ratings(self.root)
        self.assertEqual(ratings["subjects"], [])
        self.assertIn("_No reports yet._", compiler.render_ratings_md(ratings))
        self.assertIsNone(ratings["generated_at"])
        self.assertIn("_No reports yet._", compiler.update_readme(self.root, ratings))

    def test_check_mode(self):
        store(self.root, fixture("install.json"), issue=1)
        self.assertEqual(compiler.main(["--root", str(self.root), "--check"]), 1)
        self.assertEqual(compiler.main(["--root", str(self.root)]), 0)
        self.assertEqual(compiler.main(["--root", str(self.root), "--check"]), 0)

    def test_badge_colours(self):
        self.assertEqual(compiler.badge_for(None)["message"], "unrated")
        self.assertEqual(compiler.badge_for(80)["color"], "brightgreen")
        self.assertEqual(compiler.badge_for(5)["color"], "red")


if __name__ == "__main__":
    unittest.main()
