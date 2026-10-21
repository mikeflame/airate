#!/usr/bin/env python3
"""Turn one GitHub issue into a stored report (or a badge registration). Run by .github/workflows/intake.yml.

Usage: python3 tools/intake.py --context ctx.json --comment comment.md [--root DIR]
ctx.json: {"issue": 12, "title": "report: owner/name", "body": "...", "login": "octocat",
           "created_at": "2020-01-01T00:00:00Z", "now": "2026-10-20T00:00:00Z",
           "subject_exists": true, "contributors": ["octocat"]}
Exit 0 = accepted, registered or duplicate (commit whatever changed); 1 = rejected (comment explains); 2 = internal error.
Humans never write to reports/: this script is the only writer, and the gate workflow fails any pull request that touches it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compile as compiler  # noqa: E402
import scoring  # noqa: E402
import validate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)
NEW_ACCOUNT_DAYS = 30
NEW_ACCOUNT_WEIGHT = scoring.NEW_ACCOUNT_WEIGHT
MAX_REPORTS_PER_SUBMITTER = 20


class Rejected(Exception):
    pass


def parse_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_json(body: str) -> dict:
    text = body.strip()
    match = FENCE_RE.search(text)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Rejected(f"The issue body is not valid JSON ({exc}). Paste the report JSON, optionally inside a ```json fence.")


def git_ignores(root: pathlib.Path, path: pathlib.Path) -> bool:
    """True when git would silently drop the file: a report must never match an ignore rule."""
    if not shutil.which("git") or not (root / ".git").exists():
        return False
    proc = subprocess.run(["git", "check-ignore", "-q", "--", str(path.relative_to(root))], cwd=root, capture_output=True)
    return proc.returncode == 0


def existing_reports(root: pathlib.Path, repo: str) -> list[dict]:
    folder = root / "reports" / repo.lower()
    out = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        out.append(json.loads(path.read_text()))
    return out


def all_reports(root: pathlib.Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in (root / "reports").rglob("*.json")]


def task_sha256(task_dir: pathlib.Path) -> str:
    import hashlib
    h = hashlib.sha256()
    for path in sorted(p for p in task_dir.rglob("*") if p.is_file() and "node_modules" not in p.parts):
        h.update(path.relative_to(task_dir).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def weigh(report: dict, ctx: dict, root: pathlib.Path) -> tuple[float, list[str]]:
    """Weight in [0, 1] and the flags that explain it. Weight 0 keeps the report on file but out of the score."""
    weight, flags = 1.0, []
    login = ctx["login"].lower()
    owner = report["subject"]["repo"].split("/")[0].lower()
    if login == owner or login in {c.lower() for c in ctx.get("contributors", [])}:
        flags.append("maintainer")
        weight = 0.0
    created = parse_timestamp(ctx["created_at"])
    now = parse_timestamp(ctx["now"])
    if (now - created).days < NEW_ACCOUNT_DAYS:
        flags.append("new_account")
        weight *= NEW_ACCOUNT_WEIGHT
    previous = [r for r in existing_reports(root, report["subject"]["repo"]) if r["intake"]["submitter"].lower() == login]
    if report["kind"] == "weekly" and any(r["kind"] == "weekly" and r["weekly"]["week_of"] == report["weekly"]["week_of"] for r in previous):
        flags.append("duplicate_week")
        weight = 0.0
    elif len(previous) >= MAX_REPORTS_PER_SUBMITTER:
        flags.append("cap")
        weight = 0.0
    else:
        counted_before = sum(1 for r in previous if r["intake"]["weight"] > 0)
        if counted_before:
            flags.append("repeat")
        weight = scoring.repeat_weight(weight, counted_before)
    if report["kind"] == "bench" and scoring.is_inconclusive(report["bench"]):
        flags.append("inconclusive")  # stored and counted, but it compares nothing and adds no uplift evidence
    if report["kind"] == "bench" and report["bench"]["task"]["source"] == "airate":
        task_dir = root / "tasks" / report["bench"]["task"]["id"]
        if not task_dir.exists() or task_sha256(task_dir) != report["bench"]["task"]["sha256"]:
            flags.append("task_mismatch")
            weight = 0.0
        else:
            declared = [sub.lower() for sub in json.loads((task_dir / "task.json").read_text()).get("subjects", [])]
            if declared and report["subject"]["repo"].lower() not in declared:
                flags.append("subject_mismatch")
                weight = 0.0
    return weight, flags


def register(ctx: dict, root: pathlib.Path) -> str:
    repo = ctx["title"].split(":", 1)[1].strip()
    if not REPO_RE.match(repo):
        raise Rejected(f"`{repo}` is not an owner/name GitHub repository.")
    if ctx.get("subject_exists") is False:
        raise Rejected(f"github.com/{repo} does not exist or is not public.")
    badge = root / "badges" / f"{repo.lower()}.json"
    if not badge.exists():
        badge.parent.mkdir(parents=True, exist_ok=True)
        badge.write_text(json.dumps(compiler.badge_for(None)) + "\n")
        compiler.record_written(root, [badge])
    return (f"Registered **{repo}**. Badge endpoint: `{badge.relative_to(root).as_posix()}` (shows *unrated* until the first "
            f"accepted report). See [FOR-MAINTAINERS.md](https://github.com/mikeflame/airate/blob/main/FOR-MAINTAINERS.md) for the README block.")


def accept(ctx: dict, root: pathlib.Path) -> str:
    issue = int(ctx["issue"])
    for r in all_reports(root):
        if r.get("intake", {}).get("issue") == issue:
            return f"Issue #{issue} was already accepted as a report; edits after acceptance are ignored."
    report = extract_json(ctx["body"])
    errors = validate.validate_report(report)
    if errors:
        raise Rejected("The report does not follow the protocol:\n\n" + "\n".join(f"- `{e}`" for e in errors)
                       + "\n\nFix the JSON and edit this issue; the intake runs again on every edit.")
    repo = report["subject"]["repo"]
    if ctx.get("subject_exists") is False:
        raise Rejected(f"github.com/{repo} does not exist or is not public.")
    weight, flags = weigh(report, ctx, root)
    report["intake"] = {
        "issue": issue,
        "submitter": ctx["login"],
        "submitter_created_at": ctx["created_at"],
        "accepted_at": ctx["now"],
        "weight": weight,
        "flags": flags,
    }
    day = parse_timestamp(report["created_at"]).astimezone(dt.timezone.utc).strftime("%Y%m%d")
    path = root / "reports" / repo.lower() / f"{day}-{report['kind']}-{issue}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    if git_ignores(root, path):
        path.unlink()
        raise RuntimeError(f"{path.relative_to(root).as_posix()} would be ignored by .gitignore; the repository's ignore rules must be fixed before this report can be stored")
    ratings = compiler.compile_ratings(root)
    compiler.record_written(root, [path] + compiler.write_all(root, ratings))
    entry = next((s for s in ratings["subjects"] if s["repo"].lower() == repo.lower()), None)
    rate = f"**{entry['rate']}** (rank {entry['rank']} of {len(ratings['subjects'])}, {entry['reports']} reports)" if entry else "unrated (this report carries no weight)"
    lines = [f"Accepted as `{path.relative_to(root).as_posix()}` with weight {weight:g}" + (f" — flags: {', '.join(flags)}" if flags else "") + ".",
             f"AI Rate of {repo} is now {rate}.",
             "Thank you. Weekly check-ins keep it honest: see PROTOCOL.md § Weekly check-in."]
    return "\n\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--context", required=True)
    parser.add_argument("--comment", required=True)
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    ctx = json.loads(pathlib.Path(args.context).read_text())
    root = pathlib.Path(args.root)
    title = ctx["title"].strip()
    if ctx.get("lookup_failed"):
        message = ("Intake could not verify the facts it needs from the GitHub API (" + "; ".join(ctx.get("lookup_errors", [])) +
                   "). Nothing was stored. Edit the issue to retry.")
        pathlib.Path(args.comment).write_text(message + "\n")
        print(message)
        return 2
    try:
        if title.lower().startswith("register:"):
            message = register(ctx, root)
        elif title.lower().startswith("report:"):
            message = accept(ctx, root)
        else:
            raise Rejected("The title must start with `report:` or `register:`.")
        code = 0
    except Rejected as exc:
        message, code = f"Rejected: {exc}", 1
    except Exception as exc:  # noqa: BLE001 - surfaced on the issue, never silently swallowed
        message, code = f"Intake error ({type(exc).__name__}: {exc}). A maintainer will look at it.", 2
    pathlib.Path(args.comment).write_text(message + "\n")
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
