"""Scoring math for AI Rate. Pure functions, no I/O. See SCORING.md for the prose version."""
from __future__ import annotations

import math
import statistics

Z = 1.28  # one-sided 90% Wilson bound per component; the index built from them is a heuristic, not a probability
COMPONENT_WEIGHTS = {"works": 0.25, "first_try": 0.15, "retained": 0.3, "bench": 0.3}
EVIDENCE_FOR_FULL_WEIGHT = 3.0
NEUTRAL_UPLIFT = 0.5
NEW_ACCOUNT_WEIGHT = 0.25


def wilson_lower(p: float, n: float, z: float = Z) -> float:
    """Lower bound of the Wilson score interval for proportion p observed over n (possibly fractional) trials."""
    if n <= 0:
        return 0.0
    p = min(1.0, max(0.0, p))
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - margin) / denom)


def total_tokens(trial: dict) -> int | None:
    parts = [trial.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")]
    if trial.get("input_tokens") is None or trial.get("output_tokens") is None:
        return None
    return sum(p for p in parts if p is not None)


def _trial_value(trial: dict, unit: str):
    if unit == "usd":
        return trial.get("cost_usd")
    if unit == "tokens":
        return total_tokens(trial)
    return trial.get("duration_ms")


def pick_unit(with_trials: list[dict], base_trials: list[dict]) -> str:
    """Cheapest-to-compare unit present on every trial of both arms: usd, then tokens, then duration_ms."""
    for unit in ("usd", "tokens", "duration_ms"):
        if all(_trial_value(t, unit) is not None for t in with_trials + base_trials):
            return unit
    return "duration_ms"


def is_void(trial: dict) -> bool:
    """A trial whose arm condition was not met never measured the arm: the agent broke the arm's policy (a
    third-party package in the baseline, the subject removed from the with arm), or its passing solution
    turned out not to need the subject. It counts for nothing, not as a failure. A wrong answer that kept
    the policy is a failed attempt and keeps its cost."""
    return trial.get("arm_ok") is False


def arm_summary(trials: list[dict], unit: str) -> dict:
    """Solve rate and cost per solved task for one arm over its attempted (non-void) trials: everything spent
    on them, failed trials included, divided by the number of solved trials. The budget question, not the
    typical trial."""
    attempted = [t for t in trials if not is_void(t)]
    solved = sum(1 for t in attempted if t["success"])
    total = float(sum(_trial_value(t, unit) for t in attempted))
    return {"trials": len(trials), "void": len(trials) - len(attempted), "attempted": len(attempted), "solved": solved,
            "solve_rate": solved / len(attempted) if attempted else 0.0, "total_cost": total,
            "unit": unit, "cost_per_solve": total / solved if solved else math.inf}


def uplift_score(ratio: float) -> float:
    """Map cost-per-solve ratio (baseline ÷ with) to x in [0, 1] on a log2 scale: halving the cost is full
    marks, no change is 0.5, doubling the cost is zero."""
    if ratio <= 0:
        return 0.0
    if math.isinf(ratio):
        return 1.0
    return min(1.0, max(0.0, NEUTRAL_UPLIFT + 0.5 * math.log2(ratio)))


def bench_uplift(bench: dict) -> dict:
    """Solve rate and cost per solved task of both arms, their ratio and the report's uplift score.

    A report in which neither arm solved anything is inconclusive: it compares nothing and carries no
    uplift evidence (u is reported as neutral but conclusive is False)."""
    with_trials = bench["arms"]["with"]["trials"]
    base_trials = bench["arms"]["baseline"]["trials"]
    unit = pick_unit([t for t in with_trials if not is_void(t)], [t for t in base_trials if not is_void(t)])
    w = arm_summary(with_trials, unit)
    b = arm_summary(base_trials, unit)
    cw, cb = w["cost_per_solve"], b["cost_per_solve"]
    conclusive = w["attempted"] > 0 and b["attempted"] > 0 and (w["solved"] > 0 or b["solved"] > 0)
    if not conclusive:
        ratio = 1.0
    elif math.isinf(cw):
        ratio = 0.0
    elif math.isinf(cb) or cw == 0:
        ratio = 1.0 if cb == cw else math.inf
    else:
        ratio = cb / cw
    return {"u": uplift_score(ratio), "ratio": ratio, "unit": unit, "conclusive": conclusive, "with": w, "baseline": b}


def is_inconclusive(bench: dict) -> bool:
    """No comparison is possible: an arm has no attempted trial, or nothing was solved anywhere."""
    arms = bench["arms"].values()
    if any(all(is_void(t) for t in arm["trials"]) for arm in arms):
        return True
    return not any(t["success"] for arm in arms for t in arm["trials"])


def proportion_component(num: float, den: float) -> dict | None:
    """Weighted proportion with its effective sample size and its own Wilson lower bound."""
    if den <= 0:
        return None
    p = num / den
    return {"p": p, "n": den, "lower": wilson_lower(p, den)}


def index(components: dict) -> dict:
    """The AI Rate index: each component's lower bound, scaled by its weight and by min(1, n/3), summed.

    Missing or thin evidence counts as zero, so more positive evidence never lowers the index and the top
    of the scale needs every component. This is a ranking heuristic, not a probability.
    """
    raw = lower = 0.0
    for name, c in components.items():
        if c is None:
            continue
        scale = COMPONENT_WEIGHTS[name] * min(1.0, c["n"] / EVIDENCE_FOR_FULL_WEIGHT)
        raw += scale * c["p"]
        lower += scale * c["lower"]
    return {"raw": raw, "lower": lower}


def trust_weight(intake: dict) -> float:
    """A report's weight before the repeat decay: 0 for maintainers, a quarter for new accounts, else 1.
    Retention uses this, so a capped or decayed check-in still carries its agent's latest status."""
    flags = intake.get("flags", [])
    if "maintainer" in flags:
        return 0.0
    return NEW_ACCOUNT_WEIGHT if "new_account" in flags else 1.0


def repeat_weight(base: float, counted_before: int) -> float:
    """A submitter's k-th counted report on the same subject weighs base / k: one account's influence grows
    only logarithmically with the number of reports it files."""
    return base / (counted_before + 1)
