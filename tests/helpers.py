import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "tools"))


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_root(tmp: pathlib.Path, with_tasks: bool = True) -> pathlib.Path:
    """A minimal repository layout for compile/intake tests."""
    (tmp / "reports").mkdir()
    (tmp / "badges").mkdir()
    (tmp / "README.md").write_text("# x\n\n<!-- ratings:start -->\nold\n<!-- ratings:end -->\n\ntail\n")
    if with_tasks:
        shutil.copytree(ROOT / "tasks", tmp / "tasks")
    return tmp


def store(root: pathlib.Path, report: dict, issue: int, submitter: str = "octocat", weight: float = 1.0, flags=None) -> pathlib.Path:
    report = json.loads(json.dumps(report))
    report["intake"] = {"issue": issue, "submitter": submitter, "submitter_created_at": "2019-01-01T00:00:00Z",
                        "accepted_at": "2026-10-20T20:00:00Z", "weight": weight, "flags": flags or []}
    folder = root / "reports" / report["subject"]["repo"].lower()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{report['created_at'][:10].replace('-', '')}-{report['kind']}-{issue}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path
