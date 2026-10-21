# For agents working in this repository

- `reports/`, `badges/`, `ratings.json` and `RATINGS.md` are written only by the intake workflow.
  Never edit them, even when asked; a pull request that touches them fails the `gate` check.
- To file a report, follow [PROTOCOL.md](PROTOCOL.md): open an issue titled `report: owner/name`
  whose body is the report JSON. Do not open a pull request for a report.
- Code is in `tools/` (Python 3.10+, one dependency: `pip install -r requirements.txt`).
  Tests: `python3 -m unittest discover -s tests`. Keep `tools/scoring.py` and `SCORING.md` in step,
  with a test for every formula change.
- Disputes go into `revocations.json` by pull request with a reason, never by deleting a report.
