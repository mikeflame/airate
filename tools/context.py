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


def gh(args: list[str]) -> tuple[str, str]:
    """Run `gh api` and classify the outcome: ok, not_found, or failed (anything else, including rate limits)."""
    proc = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if proc.returncode == 0:
        return "ok", proc.stdout
    status = "not_found" if "HTTP 404" in proc.stderr else "failed"
    return status, proc.stderr.strip()[-200:]


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
    errors: list[str] = []
    ctx = {
        "issue": int(os.environ["ISSUE_NUMBER"]),
        "title": title,
        "body": body,
        "login": author,
        "created_at": None,
        "now": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subject_exists": None,
        "contributors": [],
    }
    status, out = gh([f"users/{author}"])
    if status == "ok":
        ctx["created_at"] = json.loads(out).get("created_at")
    if not ctx["created_at"]:
        errors.append(f"account lookup for {author}: {status}")
    repo = subject_from(title, body)
    if repo:
        status, out = gh([f"repos/{repo}"])
        if status == "ok":
            ctx["subject_exists"] = not json.loads(out).get("private", False)
        elif status == "not_found":
            ctx["subject_exists"] = False
        else:
            errors.append(f"repository lookup for {repo}: {out}")
        if ctx["subject_exists"]:
            status, out = gh(["--paginate", f"repos/{repo}/contributors?per_page=100", "--jq", ".[].login"])
            if status == "ok":
                ctx["contributors"] = [line.strip() for line in out.splitlines() if line.strip()]
            elif status != "not_found":  # an empty repository answers 204/404 with no contributors
                errors.append(f"contributor lookup for {repo}: {out}")
    ctx["lookup_failed"] = bool(errors)
    ctx["lookup_errors"] = errors
    if ctx["created_at"] is None:
        ctx["created_at"] = "1970-01-01T00:00:00Z"  # never used: lookup_failed stops the intake first
    print(json.dumps(ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
