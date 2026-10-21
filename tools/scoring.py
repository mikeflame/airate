"""Scoring math for AI Rate. Pure functions, no I/O. See SCORING.md for the prose version."""
from __future__ import annotations

import math
import statistics

Z = 1.28  # one-sided 90% confidence
COMPONENT_WEIGHTS = {"first_use": 0.4, "keep": 0.3, "bench": 0.3}
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


def bench_uplift(bench: dict) -> dict:
    """Map a bench report to u in [0, 1]: 0.5 = no difference, 1 = the subject makes the task free, 0 = it makes it unsolvable."""
    with_trials = bench["arms"]["with"]["trials"]
    base_trials = bench["arms"]["baseline"]["trials"]
    unit = pick_unit(with_trials, base_trials)
    w = arm_summary(with_trials, unit)
    b = arm_summary(base_trials, unit)
    cw, cb = w["cost_per_solve"], b["cost_per_solve"]
    if math.isinf(cw) and math.isinf(cb):
        u, ratio = NEUTRAL_UPLIFT, 1.0
    elif math.isinf(cw):
        u, ratio = 0.0, 0.0
    elif math.isinf(cb) or cw == 0:
        u, ratio = (NEUTRAL_UPLIFT, 1.0) if cb == cw else (1.0, math.inf)
    else:
        ratio = cb / cw
        u = ratio / (1.0 + ratio)
    return {"u": u, "ratio": ratio, "unit": unit, "with": w, "baseline": b}


EVIDENCE_FOR_FULL_WEIGHT = 3.0


def composite(components: dict, n_total: float) -> dict:
    """One index from the available components, then one Wilson lower bound over all counted reports.

    A component's weight scales with min(1, n/3): it reaches full weight at three reports, so a lone
    report of a new kind nudges the composite instead of swinging it. n_total counts each report once.
    """
    present = {k: v for k, v in components.items() if v is not None}
    if not present or n_total <= 0:
        return {"raw": 0.0, "lower": 0.0, "n": n_total}
    eff = {k: COMPONENT_WEIGHTS[k] * min(1.0, v["n"] / EVIDENCE_FOR_FULL_WEIGHT) for k, v in present.items()}
    total = sum(eff.values())
    raw = sum(eff[k] * present[k]["p"] for k in present) / total
    return {"raw": raw, "lower": wilson_lower(raw, n_total), "n": n_total}


def proportion_component(num: float, den: float) -> dict | None:
    if den <= 0:
        return None
    p = num / den
    return {"p": p, "n": den, "lower": wilson_lower(p, den)}
