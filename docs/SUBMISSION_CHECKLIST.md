# Submission checklist

Status legend: ✅ done & verified here · ⚠️ done but not verifiable here · ⬜ you must do it

## Repository
- ✅ Complete source code (`app/`, `tests/`, `evaluation/`, `scripts/`)
- ✅ README with setup, architecture diagram (Mermaid), assumptions, limitations
- ✅ Sample datasets (`data/samples/*.csv`, regenerate with `scripts/generate_samples.py`)
- ⚠️ Docker support: `Dockerfile`, `docker-compose.yml`, `.dockerignore` written but **never built or run** (no Docker in the dev sandbox)
- ✅ `.env.example`; no secrets committed (`.gitignore` excludes `.env`, `data/app.db`, uploads, logs, secret key)

## Verified in the build environment
- ✅ `python -m unittest discover -s tests -t .` (see README for the latest count)
- ✅ `python evaluation/evaluator.py` → 26/26 questions
- ✅ `python scripts/manual_qa.py` against a live server → 10/10 (report: `docs/QA_REPORT.md`)
- ✅ JS renderers executed in Node against real results (`scripts/ui_smoke.mjs`) – *not* a visual browser test

## Deliverables you must produce yourself
- ⬜ **UI screenshots** (no browser was available). Suggested: Dashboard, Analysis answer with chart + SQL, Anomalies, Forecast. Save to `docs/screenshots/ui_*.png` and add to the README “Screenshots” section.
- ⬜ **10–30 s demo video** following `docs/DEMO_SCRIPT.md`; link it in the README.
- ⬜ Run `docker compose up --build` once and confirm it works before claiming Docker support in your submission.
- ⬜ Open the UI in a real browser and click through every page once (the author could not).
- ⬜ Put your name in the README “Author” section.

## GitHub
```bash
git init          # if not already a repository
git add .
git status        # confirm: no .env, no data/app.db, no data/uploads, no logs/
git commit -m "Build advanced AI data analyst"
git remote add origin <your-repo-url>
git push -u origin main
```
