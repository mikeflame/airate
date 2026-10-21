#!/usr/bin/env python3
"""Build the intake context for one issue from the GitHub API (run inside .github/workflows/intake.yml).

Reads ISSUE_NUMBER, ISSUE_TITLE, ISSUE_BODY, ISSUE_AUTHOR from the environment and uses `gh api`
for the facts a submitter cannot forge: the author's account age, whether the subject repository
exists, and who its contributors are. Prints the context JSON to stdout.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from intake import REPO_RE, extract_json, Rejected  # noqa: E402


def gh_json(path: str):
    proc = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout)


def subject_from(title: str, body: str) -> str | None:
    if title.lower().startswith("register:"):
        repo = title.split(":", 1)[1].strip()
        return repo if REPO_RE.match(repo) else None
    try:
        report = extract_json(body)
    except Rejected:
        return None
    repo = report.get("subject", {}).get("repo") if isinstance(report, dict) else None
    return repo if isinstance(repo, str) and REPO_RE.match(repo) else None


def main() -> int:
    title, body, author = os.environ["ISSUE_TITLE"], os.environ.get("ISSUE_BODY", ""), os.environ["ISSUE_AUTHOR"]
    user = gh_json(f"users/{author}") or {}
    ctx = {
        "issue": int(os.environ["ISSUE_NUMBER"]),
        "title": title,
        "body": body,
        "login": author,
        "created_at": user.get("created_at", "1970-01-01T00:00:00Z"),
        "now": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subject_exists": None,
        "contributors": [],
    }
    repo = subject_from(title, body)
    if repo:
        info = gh_json(f"repos/{repo}")
        ctx["subject_exists"] = bool(info) and not info.get("private", False)
        if ctx["subject_exists"]:
            contributors = gh_json(f"repos/{repo}/contributors?per_page=100") or []
            ctx["contributors"] = [c["login"] for c in contributors if isinstance(c, dict) and "login" in c]
    print(json.dumps(ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
