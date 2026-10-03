# Implementation Plan

## Current architecture
Greenfield. The workspace contained only the assignment PDF — no code, no git repo.

## Environment constraints (discovered, drive the stack)
- No network: `pip`/`npm` cannot install anything. No Docker binary.
- Available: Python 3.12, Flask, pandas, NumPy, SciPy, scikit-learn, matplotlib, SQLite 3.45 (window functions), werkzeug (scrypt hashing).
- Missing: FastAPI, DuckDB, statsmodels, pytest, React toolchain, any LLM SDK, Chart.js.

## Target architecture
UI (HTML/CSS/JS, custom SVG charts) → Flask API → Orchestrator → Planner (rules, or LLM-produced
JSON plan that is validated) → Tool Router → tools → Result validation → Narrator (LLM optional,
number-guarded) → UI. See `docs/architecture.md`.

Swap points: `AnalyticsEngine` (SQLite now → DuckDB later), `LLMProvider` (none / OpenAI-compatible),
Flask blueprint (→ FastAPI router), static UI (→ React).

## Problems / risks found
- No existing code to reuse. No installable test runner beyond stdlib `unittest`.
- Docker cannot be executed here → files written, **not verified**.
- No LLM key/network → LLM path is tested only against a local stub HTTP server.

## Phases (each ends with a run + tests)
1 core infra · 2 ingestion/profiling/quality · 3 SQL engine + relationships · 4 planner/NL routing ·
5 charts/dashboard · 6 anomalies · 7 forecasting · 8 conversation memory · 9 reports ·
10 auth/security · 11 cache/logging/eval · 12 tests + manual QA.

## Testing strategy
`python -m unittest discover -s tests` (unit + API integration via Flask test client),
`python evaluation/evaluator.py` (question set vs. ground truth computed independently with pandas),
manual QA script `scripts/manual_qa.py`.

## Deployment
`python run.py` locally; `Dockerfile` + `docker-compose.yml` provided (untested here).
