"""Scoring math for AI Rate. Pure functions, no I/O. See SCORING.md for the prose version."""
from __future__ import annotations

import math
import statistics

Z = 1.28  # one-sided 90% Wilson bound per component; the index built from them is a heuristic, not a probability
COMPONENT_WEIGHTS = {"works": 0.25, "first_try": 0.15, "retained": 0.3, "bench": 0.3}
EVIDENCE_FOR_FULL_WEIGHT = 3.0
NEUTRAL_UPLIFT = 0.5


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


def arm_summary(trials: list[dict], unit: str) -> dict:
    """Solve rate and median cost per solved task for one arm."""
    rate = sum(1 for t in trials if t["success"]) / len(trials)
    median = float(statistics.median(_trial_value(t, unit) for t in trials))
    cost_per_solve = median / rate if rate > 0 else math.inf
    return {"trials": len(trials), "solve_rate": rate, "median_cost": median, "unit": unit, "cost_per_solve": cost_per_solve}


def uplift_score(ratio: float) -> float:
    """Map cost-per-solve ratio (baseline ÷ with) to x in [0, 1] on a log2 scale: halving the cost is full
    marks, no change is 0.5, doubling the cost is zero."""
    if ratio <= 0:
        return 0.0
    if math.isinf(ratio):
        return 1.0
    return min(1.0, max(0.0, NEUTRAL_UPLIFT + 0.5 * math.log2(ratio)))


def bench_uplift(bench: dict) -> dict:
    """Solve rate and cost per solved task of both arms, their ratio and the report's uplift score."""
    with_trials = bench["arms"]["with"]["trials"]
    base_trials = bench["arms"]["baseline"]["trials"]
    unit = pick_unit(with_trials, base_trials)
    w = arm_summary(with_trials, unit)
    b = arm_summary(base_trials, unit)
    cw, cb = w["cost_per_solve"], b["cost_per_solve"]
    if math.isinf(cw) and math.isinf(cb):
        ratio = 1.0
    elif math.isinf(cw):
        ratio = 0.0
    elif math.isinf(cb) or cw == 0:
        ratio = 1.0 if cb == cw else math.inf
    else:
        ratio = cb / cw
    return {"u": uplift_score(ratio), "ratio": ratio, "unit": unit, "with": w, "baseline": b}


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


def repeat_weight(base: float, counted_before: int) -> float:
    """A submitter's k-th counted report on the same subject weighs base / k: one account's influence grows
    only logarithmically with the number of reports it files."""
    return base / (counted_before + 1)
