#!/usr/bin/env python3
"""Validate AI Rate reports: JSON Schema (schema/report.schema.json) plus the semantic rules of PROTOCOL.md.

Usage: python3 tools/validate.py [--stored] report.json [more.json ...]
--stored validates files under reports/ (which carry the intake block) instead of submissions.
Exit 0 when every file is valid, 1 otherwise. Errors are printed one per line as path: message.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / "report.schema.json"

_schema_cache: dict | None = None


def load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(SCHEMA_PATH.read_text())
    return _schema_cache


def attestation_text() -> str:
    return load_schema()["properties"]["attestation"]["const"]


def schema_errors(report: dict) -> list[str]:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover
        sys.exit("jsonschema is required: pip install -r requirements.txt")
    validator = jsonschema.Draft202012Validator(load_schema())
    errors = sorted(validator.iter_errors(report), key=lambda e: [str(p) for p in e.path])
    return [f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errors]


def semantic_errors(report: dict, stored: bool = False) -> list[str]:
    """Rules the schema cannot express. Assumes the report already passed the schema.

    stored=False validates a submission (no intake block allowed); stored=True validates a file under reports/.
    """
    errors: list[str] = []
    kind = report["kind"]
    for other in ("install", "weekly", "bench"):
        if other != kind and other in report:
            errors.append(f"{other}: not allowed in a {kind} report")
    if "intake" in report and not stored:
        errors.append("intake: is added by the intake workflow and must not be submitted")
    if "intake" not in report and stored:
        errors.append("intake: missing on a stored report")
    if kind == "install":
        inst = report["install"]
        if not inst["install_ok"] and inst["first_use_ok"]:
            errors.append("install/first_use_ok: cannot be true when install_ok is false")
        if not inst["first_use_ok"]:
            for key in ("attempts_to_first_success", "minutes_to_first_success"):
                if inst.get(key) is not None:
                    errors.append(f"install/{key}: must be null when first_use_ok is false")
        tests = inst.get("tests")
        if tests and tests.get("ran") and "passed" not in tests:
            errors.append("install/tests/passed: required when tests ran")
    if kind == "weekly":
        wk = report["weekly"]
        try:
            if dt.date.fromisoformat(wk["week_of"]).weekday() != 0:
                errors.append("weekly/week_of: must be the Monday of the week")
        except ValueError:
            errors.append("weekly/week_of: not a calendar date")
        if wk["status"] == "replaced" and not wk.get("replaced_by"):
            errors.append("weekly/replaced_by: required when status is replaced")
        if wk["status"] in ("removed", "replaced") and wk["verdict"] == "keep":
            errors.append("weekly/verdict: cannot be keep when the subject was removed or replaced")
    if kind == "bench":
        for arm_name, arm in report["bench"]["arms"].items():
            for i, trial in enumerate(arm["trials"]):
                if trial.get("agent_error") and trial["success"]:
                    errors.append(f"bench/arms/{arm_name}/trials/{i}: agent_error and success cannot both be true")
    return errors


def validate_report(report, stored: bool = False) -> list[str]:
    if not isinstance(report, dict):
        return ["$: report must be a JSON object"]
    errors = schema_errors(report)
    if errors:
        return errors
    return semantic_errors(report, stored=stored)


def main(argv: list[str]) -> int:
    stored = "--stored" in argv
    argv = [a for a in argv if a != "--stored"]
    if not argv:
        print(__doc__)
        return 2
    failed = 0
    for path in argv:
        try:
            report = json.loads(pathlib.Path(path).read_text())
        except (OSError, ValueError) as exc:
            print(f"{path}: cannot read JSON: {exc}")
            failed += 1
            continue
        errors = validate_report(report, stored=stored)
        if errors:
            failed += 1
            print(f"{path}: INVALID")
            for err in errors:
                print(f"  {err}")
        else:
            print(f"{path}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
