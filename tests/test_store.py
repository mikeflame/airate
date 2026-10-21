import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from helpers import FIXTURES, ROOT

STORE = ROOT / "tools" / "store.sh"
PRODUCER_WRITE = ["python3", "-c", "import pathlib; p=pathlib.Path('reports/o/r/x.json'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}\\n')"]
PRODUCER_NOOP = ["true"]
PRODUCER_REJECT = ["bash", "-c", "echo rejected; exit 1"]
PRODUCER_WRITE_OUTSIDE = ["bash", "-c", "mkdir -p archive && echo '{}' > archive/x.json && echo archive/x.json >> \"$STORE_MANIFEST\" && mkdir -p badges && echo '{}' > badges/b.json && echo badges/b.json >> \"$STORE_MANIFEST\""]
PRODUCER_WRITE_UNREADABLE = ["bash", "-c", "mkdir -p reports/o/r && echo '{}' > reports/o/r/x.json && echo reports/o/r/x.json >> \"$STORE_MANIFEST\" && mkdir -p badges && echo '{}' > badges/b.json && echo badges/b.json >> \"$STORE_MANIFEST\" && chmod 000 reports/o"]


class StoreWithRealIntakeTests(unittest.TestCase):
    """The sequence the data path must survive: push failure → retry → duplicate detection → compilation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = base / "seed"
        seed.mkdir()
        for name in ("tools", "schema", "tasks"):
            shutil.copytree(ROOT / name, seed / name, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        shutil.copy(ROOT / ".gitignore", seed / ".gitignore")
        (seed / "README.md").write_text("# x\n\n<!-- ratings:start -->\nold\n<!-- ratings:end -->\n")
        (seed / "revocations.json").write_text("[]\n")
        self.git(["init", "-q", "-b", "main"], cwd=seed)
        self.git(["add", "."], cwd=seed)
        self.git(["commit", "-q", "-m", "seed"], cwd=seed)
        self.git(["push", "-q", str(self.remote), "main"], cwd=seed)
        self.work = base / "work"
        self.git(["clone", "-q", str(self.remote), str(self.work)], cwd=base)
        self.comment = base / "comment.md"
        self.ctx = base / "ctx.json"
        body = json.loads((FIXTURES / "install.json").read_text())
        body["subject"]["repo"] = "nellore/runs"   # a subject whose path once matched a development ignore rule
        self.ctx.write_text(json.dumps({"issue": 1, "title": "report: nellore/runs", "body": json.dumps(body), "login": "octocat",
                                        "created_at": "2019-01-01T00:00:00Z", "now": "2026-10-21T12:00:00Z",
                                        "subject_exists": True, "contributors": []}))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, args, cwd):
        return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def store(self):
        env = os.environ | {"STORE_BACKOFF": "0", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_EMAIL": "t@example.com"}
        producer = ["python3", "tools/intake.py", "--context", str(self.ctx), "--comment", str(self.comment), "--root", "."]
        proc = subprocess.run([str(STORE), "--message", "report: nellore/runs (#1)", "--paths", "reports badges ratings.json RATINGS.md README.md",
                               "--comment", str(self.comment), "--", *producer], cwd=self.work, env=env, capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr

    def remote_file(self, path: str) -> str | None:
        proc = subprocess.run(["git", "--git-dir", str(self.remote), "show", f"main:{path}"], capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else None

    def test_failure_retry_duplicate_compilation(self):
        marker = pathlib.Path(self.tmp.name) / "failed-once"
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text(f"#!/bin/sh\nif [ ! -e {marker} ]; then touch {marker}; exit 1; fi\nexit 0\n")
        hook.chmod(0o755)
        code, out = self.store()
        self.assertEqual(code, 0, out)
        self.assertIn("attempt 1: push rejected", out, "the first push failed")
        self.assertIn("stored", out, "the retry stored it")
        report = self.remote_file("reports/nellore/runs/20261020-install-1.json")
        self.assertIsNotNone(report, "the report is on main even though its path contains 'runs'")
        self.assertEqual(json.loads(report)["intake"]["issue"], 1)
        self.assertIn("nellore/runs", self.remote_file("RATINGS.md"))
        self.assertIn('"message": "5"', self.remote_file("badges/nellore/runs.json"))
        commits_after_first = self.git(["rev-list", "--count", "origin/main"], cwd=self.work)
        code, out = self.store()
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to store", out)
        self.assertIn("already accepted", self.comment.read_text())
        self.git(["fetch", "-q", "origin"], cwd=self.work)
        self.assertEqual(self.git(["rev-list", "--count", "origin/main"], cwd=self.work), commits_after_first, "a duplicate adds no commit")

    def test_an_ignore_rule_that_swallows_the_report_is_never_a_success(self):
        (self.work / ".gitignore").write_text("runs/\n")
        self.git(["commit", "-q", "-am", "bad ignore rule"], cwd=self.work)
        self.git(["push", "-q", "origin", "main"], cwd=self.work)
        code, out = self.store()
        self.assertEqual(code, 2, out)
        self.assertIn("ignored by .gitignore", self.comment.read_text())
        self.assertIsNone(self.remote_file("reports/nellore/runs/20261020-install-1.json"))
        self.assertIsNone(self.remote_file("ratings.json"), "no half-stored compilation either")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = base / "seed"
        self.git(["init", "-q", "-b", "main", str(seed)], cwd=base)
        (seed / "README.md").write_text("seed\n")
        self.git(["add", "."], cwd=seed)
        self.git(["commit", "-q", "-m", "seed"], cwd=seed)
        self.git(["push", "-q", str(self.remote), "main"], cwd=seed)
        self.work = base / "work"
        self.git(["clone", "-q", str(self.remote), str(self.work)], cwd=base)
        self.comment = base / "comment.md"
        self.comment.write_text("Accepted as reports/o/r/x.json\n")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, args, cwd):
        return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def store(self, producer, remote="origin", paths="reports badges"):
        env = os.environ | {"STORE_BACKOFF": "0", "STORE_REMOTE": remote, "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_EMAIL": "t@example.com"}
        proc = subprocess.run([str(STORE), "--message", "report: o/r (#1)", "--paths", paths, "--comment", str(self.comment), "--", *producer],
                              cwd=self.work, env=env, capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr

    def remote_has(self, path: str) -> bool:
        return subprocess.run(["git", "--git-dir", str(self.remote), "cat-file", "-e", f"main:{path}"], capture_output=True).returncode == 0

    def remote_has_report(self) -> bool:
        proc = subprocess.run(["git", "--git-dir", str(self.remote), "cat-file", "-e", "main:reports/o/r/x.json"], capture_output=True)
        return proc.returncode == 0

    def install_hook(self, script: str):
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\n" + script)
        hook.chmod(0o755)

    def test_stored_and_verified_on_main(self):
        code, out = self.store(PRODUCER_WRITE)
        self.assertEqual(code, 0, out)
        self.assertIn("stored", out)
        self.assertTrue(self.remote_has_report())

    def test_nothing_to_store_is_success(self):
        code, out = self.store(PRODUCER_NOOP)
        self.assertEqual((code, "nothing to store" in out), (0, True))

    def test_producer_rejection_commits_nothing(self):
        code, _ = self.store(PRODUCER_REJECT)
        self.assertEqual(code, 1)
        self.assertFalse(self.remote_has_report())
        self.assertEqual(self.git(["status", "--porcelain"], cwd=self.work), "")

    def test_permanent_push_rejection_is_not_success(self):
        self.install_hook("echo 'no pushes today' >&2; exit 1\n")
        code, out = self.store(PRODUCER_WRITE)
        self.assertEqual(code, 2, out)
        self.assertFalse(self.remote_has_report())
        self.assertIn("could not be stored", self.comment.read_text())
        self.assertNotIn("Accepted", self.comment.read_text())

    def test_transient_push_rejection_recovers(self):
        marker = pathlib.Path(self.tmp.name) / "failed-once"
        self.install_hook(f"if [ ! -e {marker} ]; then touch {marker}; exit 1; fi\nexit 0\n")
        code, out = self.store(PRODUCER_WRITE)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.remote_has_report())

    def test_unreachable_remote_never_runs_the_producer(self):
        code, out = self.store(PRODUCER_WRITE, remote="nowhere")
        self.assertEqual(code, 2)
        self.assertFalse((self.work / "reports" / "o" / "r" / "x.json").exists(), "no sync, no producer, no local commit to mistake for success")
        self.assertIn("could not sync", self.comment.read_text())

    def test_a_commit_missing_a_written_file_is_not_pushed(self):
        code, out = self.store(PRODUCER_WRITE_OUTSIDE)
        self.assertEqual(code, 2, out)
        self.assertFalse(self.remote_has("badges/b.json"), "the half commit (badge without its report) must not reach main")
        self.assertIn("could not be stored", self.comment.read_text())

    def test_unstageable_report_blocks_the_push(self):
        try:
            code, out = self.store(PRODUCER_WRITE_UNREADABLE)
        finally:
            subprocess.run(["chmod", "-R", "u+rwx", str(self.work / "reports")], check=False)
        # git add fails loudly on the unreadable directory, so the first attempt stages nothing and pushes nothing;
        # the retry's producer then fails in the damaged workspace (its own exit code, 1). Either way: no push.
        self.assertIn(code, (1, 2), out)
        self.assertIn("could not open directory", out)
        self.assertFalse(self.remote_has("badges/b.json"))
        self.assertFalse(self.remote_has("reports/o/r/x.json"))

    def test_surviving_local_commit_is_discarded_before_the_producer_runs(self):
        # A previous attempt's unpushed commit must not make the producer believe the report is already on main.
        (self.work / "reports" / "o" / "r").mkdir(parents=True)
        (self.work / "reports" / "o" / "r" / "x.json").write_text("{}\n")
        self.git(["add", "."], cwd=self.work)
        self.git(["commit", "-q", "-m", "stale local commit"], cwd=self.work)
        code, out = self.store(PRODUCER_WRITE)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.remote_has_report())
        self.assertEqual(self.git(["log", "--oneline", "origin/main"], cwd=self.work).count("\n"), 1, "exactly seed + the stored commit")


if __name__ == "__main__":
    unittest.main()
