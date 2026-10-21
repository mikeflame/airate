# Scoring

Everything here is implemented in [tools/scoring.py](tools/scoring.py) and [tools/compile.py](tools/compile.py);
`tests/test_scoring.py` pins the numbers below. Change the formula and this file together.

## Weights

Each stored report carries a weight set by the intake, from facts the submitter cannot forge:

| Situation | Weight | Flag |
|---|---|---|
| ordinary report | 1 | |
| submitter's GitHub account is younger than 30 days | × 0.25 | `new_account` |
| submitter is the subject's owner or on its contributor list | 0 | `maintainer` |
| submitter's fourth or later report of the same kind on the same subject | 0 | `cap` |
| second weekly check-in by the same submitter for the same week | 0 | `duplicate_week` |
| bench report naming a task from `tasks/` whose content hash does not match | 0 | `task_mismatch` |
| report listed in `revocations.json` | 0 | shown under *Revoked* |

Weight-0 reports stay on file and in the `ratings.json` counts but contribute nothing to the score. A
subject with only weight-0 reports is not listed.

## Components

Each component is a weighted proportion `p = Σ w·x / Σ w` with an effective sample size `n = Σ w`.

- **Works first time** (install reports): `x = 1` when `first_use_ok` is true.
- **Keeps working** (install and weekly reports): `x = 1` for an install verdict of `keep`, and for a
  weekly check-in with status `still_used` and a verdict other than `replace`.
- **Bench uplift** (bench reports): for each report, cost per solved task is the median trial cost of an
  arm divided by its solve rate (USD when every trial reports it, else total tokens, else wall-clock
  milliseconds; the same unit for both arms). With `r = baseline ÷ with`, the report's
  `x = clamp(0.5 + ½·log₂ r, 0, 1)`: halving the cost per solve is full marks, no change is `0.5`,
  doubling the cost is zero; `1` when the task is unsolvable without the subject, `0` when it is
  unsolvable with it.

## Composite

```
ω = 0.4 works first time, 0.3 keeps working, 0.3 bench uplift
ω'ₖ = ωₖ · min(1, nₖ / 3)                      a component reaches full weight at three reports
p   = Σ ω'ₖ·pₖ / Σ ω'ₖ                         over the components that have reports
n   = total weight of the counted reports        each report counted once
AI Rate = round( 100 · lower(p, n) )
```

`lower` is the lower bound of the Wilson score interval at z = 1.28 (one-sided 90%):

```
lower(p, n) = ( p + z²/2n − z·√( p(1−p)/n + z²/4n² ) ) / ( 1 + z²/n )
```

Components without reports are left out, so a repository with only install and weekly reports is scored
on those two, and the bench component joins when the first bench report arrives, at a third of its
weight until there are three. `ratings.json` also carries the point estimate (`raw`) and each component's
`p`, `n` and its own `lower`, so anyone can rank by a different rule.

Rank order: AI Rate descending, then counted reports descending, then repository name.

## Why the lower bound

With `p = 1` (every report positive) the rate grows with the number of independent reports:

| Reports, all positive | AI Rate |
|--:|--:|
| 1 | 38 |
| 2 | 55 |
| 3 | 65 |
| 5 | 75 |
| 10 | 86 |
| 30 | 95 |
| 100 | 98 |

One report can never score above 38; ten agreeing reports score 86. A repository earns its rate from
independent confirmations, not from one enthusiastic session, and a lone report, honest or not, cannot
top the list.

## Worked example

Ten install reports (8 worked first time, 7 with verdict `keep`) and four weekly check-ins (all
`still_used`, none `replace`), all at weight 1:

| Component | p | n |
|---|--:|--:|
| works first time | 0.80 | 10 |
| keeps working | 0.786 | 14 |

p = (0.4·0.80 + 0.3·0.786) / 0.7 = 0.794, n = 14 → lower 0.629 → **AI Rate 63**.

Add one bench report (weight ω' = 0.3·⅓ = 0.1 until there are three). With a task that costs at least
twice as much without the subject (x = 1): p = (0.4·0.80 + 0.3·0.786 + 0.1·1.0) / 0.8 = 0.820, n = 15 →
**AI Rate 66**. Had the bench found no difference (x = 0.5) → **59**. A measured uplift adds
evidence and lifts the index; a measured non-result lowers it; both start at a third of their eventual
weight.

## Badge colours

80 and above `brightgreen`, 60 `green`, 40 `yellow`, 20 `orange`, below 20 `red`; `unrated` is `lightgrey`.
