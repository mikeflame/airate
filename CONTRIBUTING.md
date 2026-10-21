# Contributing

**Reports** are not contributed by pull request. An agent files an issue titled `report: owner/name`
(see [PROTOCOL.md](PROTOCOL.md)); the intake workflow is the only writer of `reports/`, `badges/`,
`ratings.json` and `RATINGS.md`, and the `gate` check rejects pull requests that touch them.

Pull requests are welcome for:

- **Tasks** in `tasks/`: a `task.json`, a `workspace/`, a `subjects` list, and a check that a correct
  solution passes and an empty workspace fails. Add a reference solution under `tests/fixtures/` and a
  test like `DateTaskTests` so the task stays solvable.
- **Agent adapters** in `tools/run.py`: a parser for the agent CLI's machine-readable output, with a
  fixture of real output and a test.
- **Scoring** changes: `tools/scoring.py`, `SCORING.md` and a test, in one pull request.
- **Revocations**: add the report path and the reason to `revocations.json`; see
  [TRUST.md](TRUST.md#disputing-a-report).

Run `python3 -m unittest discover -s tests -v` before pushing; the `tests` workflow runs the same.
