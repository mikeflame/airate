# Scoring

Everything here is implemented in [tools/scoring.py](tools/scoring.py) and [tools/compile.py](tools/compile.py);
`tests/test_scoring.py` pins the numbers below. Change the formula and this file together.

**AI Rate is an evidence-weighted index on a 0–100 scale. It is a ranking heuristic, not a probability
and not a confidence interval.** Each column of the table is a measured proportion with its own
uncertainty; the index adds them up with fixed weights and counts missing or thin evidence as zero.
The observations in `reports/` and the per-column numbers in `ratings.json` are the data; the index
is one way to order them.

## Weights of a report

Each stored report carries a weight set by the intake, from facts the submitter cannot forge:

| Situation | Weight | Flag |
|---|---|---|
| ordinary report | 1 | |
| submitter's GitHub account is younger than 30 days | × 0.25 | `new_account` |
| submitter is the subject's owner or on its contributor list | 0 | `maintainer` |
| submitter's k-th counted report on the same subject, any kind | ÷ k | `repeat` |
| submitter's 21st or later report on the same subject | 0 | `cap` |
| bench report in which neither arm solved the task | unchanged, but adds no uplift evidence | `inconclusive` |
| second weekly check-in by the same submitter for the same week | 0 | `duplicate_week` |
| bench report naming a task from `tasks/` whose content hash does not match | 0 | `task_mismatch` |
| bench report running a task from `tasks/` against a repository the task does not declare | 0 | `subject_mismatch` |
| report listed in `revocations.json` | 0 | shown under *Revoked* |

The `÷ k` rule means one account's total influence on a subject grows only logarithmically: nine
perfect reports from one account (three installs, three bench runs, three weekly check-ins) produce an
index of **15**. Weight-0 reports stay on file and in the `ratings.json` counts but contribute
nothing. A subject with only weight-0 reports is not listed, and its badge reads *unrated*.

## Columns

Each column is a weighted proportion `p = Σ w·x / Σ w` with effective sample size `n = Σ w` and its own
Wilson lower bound at z = 1.28 (one-sided 90%):

```
lower(p, n) = ( p + z²/2n − z·√( p(1−p)/n + z²/4n² ) ) / ( 1 + z²/n )
```

| Column | Reports | x = 1 when |
|---|---|---|
| **Works** | install | `first_use_ok`: the package did its job within the session, however many attempts it took |
| **First attempt** | install | `first_use_ok` and `attempts_to_first_success` is 1 |
| **Retained** | weekly | one unit per reporting agent, from that agent's **latest** check-in: status `still_used` and verdict not `replace`. The unit is the agent's trust weight (1, or ¼ for a new account), not the decayed repeat weight, so an agent that stops using the subject flips its own earlier check-ins even after its reports are capped |
| **Bench uplift** | bench | see below; `x` is graded, not binary |

Bench: cost per solved task is everything an arm spent, failed trials included, divided by its number
of solved trials (USD when every trial reports it, else total tokens, else wall-clock milliseconds; the
same unit for both arms). That is the budget question: what it costs to get one solved task, so three
trials costing 1, 1 and 1000 are worse than three costing 2. With `r = baseline ÷ with`,
`x = clamp(0.5 + ½·log₂ r, 0, 1)`: halving the cost per solve is full marks, no change is 0.5, doubling
the cost is zero; `1` when the task is unsolvable without the subject, `0` when it is unsolvable with it.
A report in which neither arm solved anything is **inconclusive**: it compares nothing, is stored with
the `inconclusive` flag and contributes to no column. A trial succeeds when the task's check passes; an
agent that crashed, timed out or hit its turn limit after writing a passing solution still solved it, and
its missing metrics only move the comparison to a unit every trial has.

## The index

```
ω = 0.25 works, 0.15 first attempt, 0.30 retained, 0.30 bench uplift
AI Rate = round( 100 · Σ ωₖ · min(1, nₖ / 3) · lower(pₖ, nₖ) )
```

Three properties follow:

- **Every column keeps its own uncertainty.** Three installs plus a hundred weekly check-ins give
  **55**: retention evidence cannot stand in for installation evidence, and the *Works* column
  still says n = 3.
- **More positive evidence never lowers the index.** A column with no reports contributes zero; a
  column with one report contributes a third of its weight; from three reports it contributes in full.
- **The top needs everything.** Install reports alone reach at most 40; retention lifts the ceiling to
  70; only benchmarks reach 100.

| Evidence, all positive | AI Rate |
|---|--:|
| 1 install (first try) | 5 |
| 3 installs | 26 |
| 10 installs | 34 |
| 10 installs + 10 weekly check-ins | 60 |
| 10 installs + 10 weeklies + 3 bench runs (≥ 2× cheaper) | 80 |
| 10 + 10 + 10 | 86 |
| 30 + 30 + 30 | 95 |

A mixed subject (10 installs: 80% worked, 60% on the first attempt; 8 weekly check-ins at 90% retained;
2 bench runs averaging x = 0.75) scores **48**. `ratings.json` carries every column's `p`, `n` and
`lower` plus the point-estimate index (`raw`), so anyone can rank by a different rule.

Rank order: AI Rate descending, then counted reports descending, then repository name.

## Badge colours

80 and above `brightgreen`, 60 `green`, 40 `yellow`, 20 `orange`, below 20 `red`; `unrated` is `lightgrey`.
