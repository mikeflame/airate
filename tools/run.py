#!/usr/bin/env python3
"""The AI Rate bench harness: run one task with and without the subject, driving an agent CLI headlessly.

Metrics come from the agent CLI's own machine-readable output, never from the agent's opinion of itself, and
success comes from the task's check command. The harness writes a bench report plus a bundle of raw outputs.

Usage:
  python3 tools/run.py --task tasks/js-date-parse --subject iamkun/dayjs --package npm:dayjs \
      --agent claude --model haiku --trials 3
  python3 tools/run.py --task tasks/js-date-parse --subject iamkun/dayjs \
      --agent custom --agent-cmd 'my-agent --prompt-file {prompt_file} --cwd {cwd}'

A custom agent command must print, as its last stdout line, a JSON object with any of: input_tokens,
output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, duration_ms, turns, model, error (boolean).

A task's `protected` files (its grader) are snapshotted after the arm's setup and restored before the check;
a trial whose agent changed them fails. Each arm's `verify` command must pass for the trial to count, so an
agent that ignores the subject in the with arm, or installs it in the baseline, fails that trial.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import validate  # noqa: E402

HARNESS = "airate-run 1"
DEFAULT_ALLOWED_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(npm:*),Bash(npx:*),Bash(node:*),Bash(python3:*),Bash(python:*),Bash(pytest:*),Bash(pip:*),Bash(ls:*),Bash(cat:*)"
SETUP_TIMEOUT_S = 600


def sha256_dir(folder: pathlib.Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "node_modules" not in p.parts):
        h.update(path.relative_to(folder).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def run_shell(cmd: str, cwd: pathlib.Path, timeout: int, log: pathlib.Path) -> int:
    if not cmd.strip():
        log.write_text("")
        return 0
    try:
        proc = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout)
        log.write_text(f"$ {cmd}\n[exit {proc.returncode}]\n{proc.stdout}{proc.stderr}")
        return proc.returncode
    except subprocess.TimeoutExpired:
        log.write_text(f"$ {cmd}\n[timeout after {timeout}s]\n")
        return 124


def parse_claude_result(stdout: str) -> dict:
    """Metrics from `claude -p --output-format json` (a result object, or a message array in older versions)."""
    data = json.loads(stdout)
    if isinstance(data, list):
        data = next((m for m in data if m.get("type") == "result"), data[-1])
    usage = data.get("usage") or {}
    models = list((data.get("modelUsage") or {}).keys())
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "cost_usd": data.get("total_cost_usd"),
        "duration_ms": data.get("duration_ms"),
        "turns": data.get("num_turns"),
        "model": models[0] if models else None,
        "error": bool(data.get("is_error")),
    }


def parse_custom_result(stdout: str) -> dict:
    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    data = json.loads(lines[-1]) if lines else {}
    return {k: data.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                                     "cost_usd", "duration_ms", "turns", "model")} | {"error": bool(data.get("error"))}


def agent_command(args: argparse.Namespace, prompt: str, prompt_file: pathlib.Path, cwd: pathlib.Path) -> list[str]:
    if args.agent == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--max-turns", str(args.max_turns),
               "--permission-mode", "acceptEdits", "--allowedTools", args.allowed_tools]
        if args.model:
            cmd += ["--model", args.model]
        return cmd
    template = args.agent_cmd.replace("{prompt_file}", shlex.quote(str(prompt_file))).replace("{cwd}", shlex.quote(str(cwd)))
    return shlex.split(template)


def run_trial(args: argparse.Namespace, task: dict, task_dir: pathlib.Path, arm: str, index: int, out: pathlib.Path) -> dict:
    trial_dir = out / arm / str(index)
    workspace = trial_dir / "workspace"
    src = task_dir / task.get("workspace", "workspace")
    if src.exists():
        shutil.copytree(src, workspace)
    else:
        workspace.mkdir(parents=True)
    arm_spec = task["arms"][arm]
    setup_rc = run_shell(arm_spec.get("setup", ""), workspace, SETUP_TIMEOUT_S, trial_dir / "setup.log")
    protected = {name: (workspace / name).read_bytes() for name in task.get("protected", []) if (workspace / name).exists()}
    prompt = task["prompt"].rstrip() + "\n\n" + arm_spec.get("prompt_suffix", "")
    prompt_file = trial_dir / "prompt.md"
    prompt_file.write_text(prompt)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    started = time.monotonic()
    metrics: dict = {"error": True}
    stdout = ""
    try:
        proc = subprocess.run(agent_command(args, prompt, prompt_file, workspace), cwd=workspace, env=env,
                              capture_output=True, text=True, timeout=task.get("timeout_s", 900))
        stdout = proc.stdout
        (trial_dir / "agent.stderr").write_text(proc.stderr)
        parser = parse_claude_result if args.agent == "claude" else parse_custom_result
        metrics = parser(stdout) if proc.returncode == 0 and stdout.strip() else {"error": True}
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        (trial_dir / "agent.stderr").write_text(f"timeout after {task.get('timeout_s', 900)}s\n")
    except (ValueError, OSError) as exc:
        (trial_dir / "agent.stderr").write_text(f"{type(exc).__name__}: {exc}\n")
    wall_ms = int((time.monotonic() - started) * 1000)
    (trial_dir / "agent.out").write_text(stdout)
    grader_intact = all((workspace / name).exists() and (workspace / name).read_bytes() == content for name, content in protected.items())
    for name, content in protected.items():  # the check always runs against the task's own grader
        (workspace / name).write_bytes(content)
    check_rc = run_shell(task["check"], workspace, task.get("check_timeout_s", 300), trial_dir / "check.log")
    verify = arm_spec.get("verify", "")
    arm_ok = run_shell(verify, workspace, task.get("check_timeout_s", 300), trial_dir / "verify.log") == 0
    trial = {
        "success": check_rc == 0 and setup_rc == 0 and grader_intact and arm_ok,
        "duration_ms": int(metrics.get("duration_ms") or wall_ms),
        "turns": metrics.get("turns"),
        "input_tokens": metrics.get("input_tokens"),
        "output_tokens": metrics.get("output_tokens"),
        "cache_read_tokens": metrics.get("cache_read_tokens"),
        "cache_write_tokens": metrics.get("cache_write_tokens"),
        "cost_usd": metrics.get("cost_usd"),
        "agent_error": bool(metrics.get("error")),
        "grader_intact": grader_intact,
        "arm_ok": arm_ok,
    }
    (trial_dir / "trial.json").write_text(json.dumps(trial | {"model": metrics.get("model"), "setup_rc": setup_rc, "check_rc": check_rc}, indent=2) + "\n")
    print(f"  {arm} #{index}: {'PASS' if trial['success'] else 'FAIL'}"
          + ("" if grader_intact else " (grader modified)") + ("" if arm_ok else " (arm condition not met)") + f" in {trial['duration_ms']} ms"
          + (f", ${trial['cost_usd']:.4f}" if trial["cost_usd"] is not None else "")
          + (f", {trial['turns']} turns" if trial["turns"] is not None else ""), flush=True)
    return trial, metrics.get("model")


def build_report(args: argparse.Namespace, task: dict, task_dir: pathlib.Path, arms: dict, model: str | None, out: pathlib.Path) -> dict:
    agent = {"name": "claude-code" if args.agent == "claude" else args.agent_name, "model": model or args.model or "unknown"}
    if args.agent == "claude":
        version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
        if version:
            agent["version"] = version.split()[0]
    subject = {"repo": args.subject, "type": args.subject_type}
    if args.package:
        subject["package"] = args.package
    if args.version:
        subject["version"] = args.version
    report = {
        "protocol": 1,
        "kind": "bench",
        "subject": subject,
        "agent": agent,
        "environment": {"os": platform.system().lower(), "runtime": f"python {platform.python_version()}"},
        "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attestation": validate.attestation_text(),
        "bench": {
            "task": {"id": task["id"], "sha256": sha256_dir(task_dir), "source": args.task_source},
            "harness": HARNESS,
            "arms": arms,
            "bundle_sha256": sha256_dir(out),
        },
    }
    if args.task_url:
        report["bench"]["task"]["url"] = args.task_url
    if args.bundle_url:
        report["bench"]["bundle_url"] = args.bundle_url
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", required=True, help="task directory containing task.json")
    parser.add_argument("--subject", required=True, help="GitHub owner/name being rated")
    parser.add_argument("--subject-type", default="library", choices=["library", "cli", "skill", "mcp", "action", "other"])
    parser.add_argument("--package", help="ecosystem:name, e.g. npm:dayjs")
    parser.add_argument("--version", help="subject version under test")
    parser.add_argument("--agent", default="claude", choices=["claude", "custom"])
    parser.add_argument("--agent-cmd", help="custom agent command template with {prompt_file} and {cwd}")
    parser.add_argument("--agent-name", default="custom", help="agent product name for custom commands")
    parser.add_argument("--model", help="model alias or id passed to the agent")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--arms", default="with,baseline")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--allowed-tools", default=DEFAULT_ALLOWED_TOOLS)
    parser.add_argument("--task-source", default="airate", choices=["airate", "subject", "custom"])
    parser.add_argument("--task-url")
    parser.add_argument("--bundle-url")
    parser.add_argument("--out", default="runs")
    args = parser.parse_args(argv)
    if args.agent == "custom" and not args.agent_cmd:
        parser.error("--agent custom needs --agent-cmd")
    task_dir = pathlib.Path(args.task).resolve()
    task = json.loads((task_dir / "task.json").read_text())
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + task["id"]
    out = pathlib.Path(args.out).resolve() / run_id
    out.mkdir(parents=True)
    print(f"run {run_id}: {args.subject} on task {task['id']} with {args.agent}, {args.trials} trials per arm → {out}")
    arms, model = {}, None
    for arm in args.arms.split(","):
        trials = []
        for i in range(1, args.trials + 1):
            trial, seen_model = run_trial(args, task, task_dir, arm, i, out)
            trials.append(trial)
            model = model or seen_model
        arms[arm] = {"trials": trials}
    report = build_report(args, task, task_dir, arms, model, out)
    errors = validate.validate_report(report)
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if errors:
        print("report is INVALID:\n  " + "\n  ".join(errors))
        return 1
    print(f"report written: {report_path}\n"
          f"submit it:\n  gh issue create -R mikeflame/airate -t 'report: {args.subject}' -F {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
