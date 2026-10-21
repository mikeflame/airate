import os
import pathlib
import subprocess
import tempfile
import unittest

from helpers import ROOT

STORE = ROOT / "tools" / "store.sh"
PRODUCER_WRITE = ["python3", "-c", "import pathlib; p=pathlib.Path('reports/o/r/x.json'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}\\n')"]
PRODUCER_NOOP = ["true"]
PRODUCER_REJECT = ["bash", "-c", "echo rejected; exit 1"]


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

    def store(self, producer, remote="origin"):
        env = os.environ | {"STORE_BACKOFF": "0", "STORE_REMOTE": remote, "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_EMAIL": "t@example.com"}
        proc = subprocess.run([str(STORE), "--message", "report: o/r (#1)", "--paths", "reports", "--comment", str(self.comment), "--", *producer],
                              cwd=self.work, env=env, capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr

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
