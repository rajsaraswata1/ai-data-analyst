# Evaluation framework

`python evaluation/evaluator.py` runs 26 questions (6 sessions, including multi-turn follow-ups) through the **real
orchestrator** and scores each answer on:

| Dimension | What is checked |
|---|---|
| intent | classified intent equals the expected intent (e.g. `RANKING`, `FORECAST`) |
| tools | the expected tools were actually executed (nested calls included) |
| chart | the chart type matches the selection rules (line for time, hbar for ranking, pie on request, ...) |
| numeric | values are compared with **independently computed pandas** results from the raw CSVs (`Truth` class) |
| sql | the returned SQL passes the read-only validator **and executes** |
| relevance | the answer is non-empty and mentions the expected entities (e.g. `North`) |

* `questions.json` – the question set (edit/extend freely; each entry names a `check` implemented in `evaluator.py`).
* `evaluator.py` – harness; exits non-zero if any question fails or the overall score is below `--min-score` (default 0.9).
* `results.json` – written on every run (per-question pass/fail + notes).

The expected values are *not* hard-coded: they are recomputed from the CSVs on each run, so the harness also works if the
sample data is regenerated. Scope: it evaluates the deterministic engine. It does not measure LLM answer quality (no LLM
API was available during development).
