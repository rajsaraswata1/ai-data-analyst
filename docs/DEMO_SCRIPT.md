# 10–30 second demo script

> The demo video itself must be recorded by you (no screen recorder or browser was available in the build environment).
> Start the app (`python run.py`), open http://127.0.0.1:5000, register `demo` / `demo-password`, and keep the window at ~1280px wide.

| Time | Action | What the viewer sees |
|---|---|---|
| 0–4 s | **Datasets** → *Load sample datasets* (or drop `data/samples/*.csv`) | 3 files validated, quality scores, 2 detected joins |
| 4–8 s | **Dashboard** | KPI cards, trend / category / region charts, anomaly + quality cards |
| 8–15 s | **Analysis** → click *Which customer segment generated the highest revenue?* | live status steps (“Generating SQL… Running SQL… Validating…”), answer, chart, SQL, method |
| 15–20 s | Click the follow-up chip **Show its monthly trend** | context carried: line chart filtered to the top segment |
| 20–26 s | Ask `Detect anomalies in the dataset.` | flagged orders with expected range + reasons |
| 26–30 s | Ask `Forecast sales for the next 6 months.` then click **HTML** export | forecast band labelled “estimate”, report downloads |

Tip: pre-load the data before recording so the first 4 s show the Datasets page already populated.
