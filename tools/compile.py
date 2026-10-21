#!/usr/bin/env python3
"""Compile reports/ into ratings.json, RATINGS.md, badges/ and the table in README.md.

Usage: python3 tools/compile.py [--root DIR] [--check]
--check exits 1 when the generated files on disk differ from what the reports produce.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoring  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
README_START = "<!-- ratings:start -->"
README_END = "<!-- ratings:end -->"
README_ROWS = 10
BADGE_LABEL = "AI Rate"
BADGE_COLORS = ((80, "brightgreen"), (60, "green"), (40, "yellow"), (20, "orange"), (0, "red"))


def parse_timestamp(value: str) -> dt.datetime:
    """An aware datetime, so that 23:00+14:00 sorts before 10:00Z as it should."""
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_reports(root: pathlib.Path) -> list[dict]:
    reports = []
    for path in sorted((root / "reports").rglob("*.json")):
        report = json.loads(path.read_text())
        report["_path"] = path.relative_to(root).as_posix()
        reports.append(report)
    return reports


def load_revocations(root: pathlib.Path) -> dict[str, dict]:
    path = root / "revocations.json"
    if not path.exists():
        return {}
    return {r["report"]: r for r in json.loads(path.read_text())}


def badge_for(rate: int | None) -> dict:
    if rate is None:
        return {"schemaVersion": 1, "label": BADGE_LABEL, "message": "unrated", "color": "lightgrey"}
    color = next(c for threshold, c in BADGE_COLORS if rate >= threshold)
    return {"schemaVersion": 1, "label": BADGE_LABEL, "message": str(rate), "color": color}


def score_subject(reports: list[dict]) -> dict:
    """Aggregate every stored (non-revoked) report of one subject into columns and the index. See SCORING.md.

    Works, first attempt and bench use counted reports (weight > 0). Retained uses each agent's latest weekly
    check-in at its trust weight, so an agent that stops using the subject flips its own earlier check-ins
    even after its reports are capped or decayed. Inconclusive bench reports (no solve in either arm) add
    no uplift evidence.
    """
    sums = {k: [0.0, 0.0] for k in ("works", "first_try", "retained", "bench")}
    ratios: list[float] = []
    inconclusive = 0

    def add(name: str, w: float, x: float) -> None:
        sums[name][0] += w * x
        sums[name][1] += w

    latest_weekly: dict[str, dict] = {}
    for r in reports:
        w = r["_weight"]
        if r["kind"] == "install" and w > 0:
            inst = r["install"]
            add("works", w, inst["first_use_ok"])
            add("first_try", w, inst["first_use_ok"] and inst.get("attempts_to_first_success") == 1)
        elif r["kind"] == "weekly":
            flags = set(r["intake"]["flags"])
            if flags & {"maintainer", "duplicate_week"}:
                continue
            key = r["intake"]["submitter"].lower()
            stamp = (parse_timestamp(r["created_at"]), r["intake"]["issue"])
            if key not in latest_weekly or stamp > (parse_timestamp(latest_weekly[key]["created_at"]), latest_weekly[key]["intake"]["issue"]):
                latest_weekly[key] = r
        elif r["kind"] == "bench" and w > 0:
            if scoring.is_inconclusive(r["bench"]):
                inconclusive += 1
                continue
            up = scoring.bench_uplift(r["bench"])
            add("bench", w, up["u"])
            ratios.append(up["ratio"])
    for r in latest_weekly.values():
        wk = r["weekly"]
        add("retained", scoring.trust_weight(r["intake"]), wk["status"] == "still_used" and wk["verdict"] != "replace")
    components = {name: scoring.proportion_component(num, den) for name, (num, den) in sums.items()}
    if components["bench"] is not None:
        components["bench"]["ratio"] = statistics.median(ratios)
    idx = scoring.index(components)
    return {"components": components, "raw": idx["raw"], "lower": idx["lower"], "rate": round(100 * idx["lower"]),
            "bench_inconclusive": inconclusive}


def compile_ratings(root: pathlib.Path) -> dict:
    """A pure function of reports/ and revocations.json: the same inputs always produce the same output."""
    revocations = load_revocations(root)
    reports = load_reports(root)
    accepted = [r["intake"]["accepted_at"] for r in reports if "intake" in r]
    by_subject: dict[str, list[dict]] = {}
    revoked = []
    for r in reports:
        path = r["_path"]
        if path in revocations:
            revoked.append({"report": path, "reason": revocations[path]["reason"]})
            continue
        r["_weight"] = float(r.get("intake", {}).get("weight", 0.0))
        by_subject.setdefault(r["subject"]["repo"].lower(), []).append(r)

    subjects = []
    for key, items in by_subject.items():
        counted = [r for r in items if r["_weight"] > 0]
        scored = score_subject(items)
        if all(c is None for c in scored["components"].values()):
            continue
        latest = max(parse_timestamp(r["created_at"]) for r in (counted or items)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        subjects.append({
            "repo": items[-1]["subject"]["repo"],
            "type": items[-1]["subject"]["type"],
            "rate": scored["rate"],
            "raw": round(scored["raw"], 4),
            "lower": round(scored["lower"], 4),
            "components": _round_components(scored["components"]),
            "reports": len(counted),
            "reports_total": len(items),
            "submitters": len({r["intake"]["submitter"] for r in (counted or items)}),
            "agents": sorted({r["agent"]["name"] for r in (counted or items)}),
            "models": sorted({r["agent"]["model"] for r in (counted or items)}),
            "kinds": {k: sum(1 for r in counted if r["kind"] == k) for k in ("install", "weekly", "bench")} | {"bench_inconclusive": scored["bench_inconclusive"]},
            "last_report": latest,
            "badge": f"badges/{key}.json",
        })
    subjects.sort(key=lambda s: (-s["rate"], -s["reports"], s["repo"].lower()))
    for i, s in enumerate(subjects, 1):
        s["rank"] = i
    return {
        "generated_at": max(accepted) if accepted else None,
        "protocol": 1,
        "reports": len(reports),
        "subjects": subjects,
        "revoked": revoked,
    }


def _round_components(components: dict) -> dict:
    out = {}
    for name, c in components.items():
        if c is None:
            out[name] = None
            continue
        item = {"p": round(c["p"], 4), "n": round(c["n"], 2), "lower": round(c["lower"], 4)}
        if "ratio" in c:
            item["ratio"] = None if math.isinf(c["ratio"]) else round(c["ratio"], 2)
        out[name] = item
    return out


def _pct(c: dict | None) -> str:
    return "–" if c is None else f"{round(100 * c['p'])}% (n={c['n']:g})"


def _ratio(c: dict | None) -> str:
    if c is None:
        return "–"
    r = c.get("ratio")
    return f"{'∞' if r is None else f'{r:.1f}'}× (n={c['n']:g})"


def render_rows(subjects: list[dict], full: bool) -> list[str]:
    if full:
        head = "| # | Repository | Type | AI Rate | Works | First attempt | Retained | Bench uplift | Reports | Submitters | Agents | Last report |"
        sep = "|--:|---|---|--:|---|---|---|---|--:|--:|---|---|"
    else:
        head = "| # | Repository | AI Rate | Works | First attempt | Retained | Bench uplift | Reports |"
        sep = "|--:|---|--:|---|---|---|---|--:|"
    rows = [head, sep]
    for s in subjects:
        repo = f"[{s['repo']}](https://github.com/{s['repo']})"
        c = s["components"]
        cells = f"{_pct(c['works'])} | {_pct(c['first_try'])} | {_pct(c['retained'])} | {_ratio(c['bench'])}"
        if full:
            rows.append(f"| {s['rank']} | {repo} | {s['type']} | **{s['rate']}** | {cells} | {s['reports']} | {s['submitters']} | {', '.join(s['agents'])} | {s['last_report'][:10]} |")
        else:
            rows.append(f"| {s['rank']} | {repo} | **{s['rate']}** | {cells} | {s['reports']} |")
    return rows


def render_ratings_md(ratings: dict) -> str:
    lines = [
        "# AI Rate ratings",
        "",
        f"Compiled from {ratings['reports']} reports, the last accepted {ratings['generated_at'] or 'never'}. Generated file: do not edit; see [SCORING.md](SCORING.md) for the formula and [PROTOCOL.md](PROTOCOL.md) to add a report.",
        "",
        "**AI Rate** is an evidence-weighted index (0–100), not a probability: each column's Wilson lower bound, scaled by its weight and by how much evidence it has; missing evidence counts as zero. Columns are weighted point estimates with the effective number of reports in brackets. *Works* is first-use success within the session, *First attempt* success on the first try, *Retained* is the share of reporting agents whose latest weekly check-in still uses the repository, *Bench uplift* is total spend per solved task without the repository divided by the same with it; benchmarks in which neither arm solved the task are inconclusive and add nothing.",
        "",
    ]
    if ratings["subjects"]:
        lines += render_rows(ratings["subjects"], full=True)
    else:
        lines.append("_No reports yet._")
    if ratings["revoked"]:
        lines += ["", "## Revoked reports", ""]
        lines += [f"- `{r['report']}`: {r['reason']}" for r in ratings["revoked"]]
    return "\n".join(lines) + "\n"


def update_readme(root: pathlib.Path, ratings: dict) -> str:
    path = root / "README.md"
    text = path.read_text()
    start, end = text.find(README_START), text.find(README_END)
    if start == -1 or end == -1:
        return text
    top = ratings["subjects"][:README_ROWS]
    body = "\n".join(render_rows(top, full=False)) if top else "_No reports yet._"
    updated = ratings["generated_at"][:10] if ratings["generated_at"] else "never"
    body += f"\n\nUpdated {updated} · {ratings['reports']} reports · [full list](RATINGS.md)\n"
    return text[: start + len(README_START)] + "\n" + body + text[end:]


def generated_files(root: pathlib.Path, ratings: dict) -> dict[pathlib.Path, str]:
    files = {
        root / "ratings.json": json.dumps(ratings, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        root / "RATINGS.md": render_ratings_md(ratings),
    }
    if (root / "README.md").exists():
        files[root / "README.md"] = update_readme(root, ratings)
    listed = {s["badge"] for s in ratings["subjects"]}
    for s in ratings["subjects"]:
        files[root / s["badge"]] = json.dumps(badge_for(s["rate"])) + "\n"
    for path in (root / "badges").rglob("*.json") if (root / "badges").exists() else []:
        if path.relative_to(root).as_posix() not in listed:
            files[path] = json.dumps(badge_for(None)) + "\n"  # the subject lost its counted evidence
    return files


def record_written(root: pathlib.Path, paths: list[pathlib.Path]) -> None:
    """Tell tools/store.sh which files this run wrote, so it can refuse a commit that lacks any of them."""
    manifest = os.environ.get("STORE_MANIFEST")
    if manifest and paths:
        with open(manifest, "a") as fh:
            fh.writelines(p.relative_to(root).as_posix() + "\n" for p in paths)


def write_all(root: pathlib.Path, ratings: dict) -> list[pathlib.Path]:
    changed = []
    for path, content in generated_files(root, ratings).items():
        if path.exists() and path.read_text() == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        changed.append(path)
    return changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.root)
    ratings = compile_ratings(root)
    if args.check:
        stale = [p for p, c in generated_files(root, ratings).items() if not p.exists() or p.read_text() != c]
        if stale:
            print("stale generated files:\n  " + "\n  ".join(str(p.relative_to(root)) for p in stale))
            return 1
        print("generated files are up to date")
        return 0
    changed = write_all(root, ratings)
    record_written(root, changed)
    print(f"{len(ratings['subjects'])} subjects from {ratings['reports']} reports; "
          f"{len(changed)} files written" + (": " + ", ".join(p.relative_to(root).as_posix() for p in changed) if changed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
