"""End-to-end QA against a RUNNING server over real HTTP. Writes docs/QA_REPORT.md with input/expected/actual/PASS/evidence.

Usage: python scripts/manual_qa.py --url http://127.0.0.1:5077 [--restart-cmd 'sh /tmp/qa/start.sh' --pid-file /tmp/qa/pid]
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "data" / "samples"
H = {"X-Requested-With": "XMLHttpRequest"}
rows: list[dict] = []


def record(n, title, inp, expected, actual, ok, evidence):
    rows.append(dict(n=n, title=title, inp=inp, expected=expected, actual=actual, ok=ok, evidence=evidence))
    print(f"TEST {n} {'PASS' if ok else 'FAIL'} – {title}")


def wait(url, tries=40):
    for _ in range(tries):
        try:
            if requests.get(url + "/api/health", timeout=2).ok:
                return True
        except requests.RequestException:
            time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5000")
    ap.add_argument("--restart-cmd")
    ap.add_argument("--pid-file")
    a = ap.parse_args()
    u = a.url
    s = requests.Session()
    s.headers.update(H)
    sales = pd.read_csv(S / "sales.csv", parse_dates=["order_date"])
    cust = pd.read_csv(S / "customers.csv")
    user = f"qa_{int(time.time())}"
    assert s.post(f"{u}/api/auth/register", json={"username": user, "password": "qa-password-1"}).status_code == 201

    def up(name):
        with open(S / f"{name}.csv", "rb") as f:
            return s.post(f"{u}/api/datasets", files={"files": (f"{name}.csv", f, "text/csv")})

    def ask(cid, q):
        r = s.post(f"{u}/api/conversations/{cid}/ask", json={"question": q})
        r.raise_for_status()
        return r.json()

    # ---- TEST 1
    r = up("sales")
    cid = s.post(f"{u}/api/conversations").json()["id"]
    q = "Which region generated the highest revenue?"
    d = ask(cid, q)["result"]
    by = sales.groupby("region").revenue.sum().sort_values(ascending=False)
    ok = r.status_code == 200 and d["table"]["rows"][0][0] == by.index[0] and abs(d["table"]["rows"][0][1] - by.iloc[0]) < 0.01
    record(1, "Upload sales.csv and ask highest-revenue region", f"upload sales.csv; “{q}”", f"{by.index[0]} with {by.iloc[0]:,.2f} (pandas)",
           d["answer"], ok, f"table rows: {d['table']['rows']}; validation: {d['validation'][0]}")
    # ---- TEST 2
    q = "Show monthly sales trends."
    d = ask(cid, q)["result"]
    exp = sales.assign(p=sales.order_date.dt.strftime("%Y-%m")).groupby("p").revenue.sum()
    ch = d["charts"][0]
    ok = ch["type"] == "line" and ch["labels"] == list(exp.index) and abs(sum(ch["series"][0]["values"]) - exp.sum()) < 0.5
    record(2, "Monthly sales trends -> line chart", q, f"line chart, {len(exp)} monthly points summing to {exp.sum():,.2f}",
           d["answer"], ok, f"chart type={ch['type']}, points={len(ch['labels'])}, sum={sum(ch['series'][0]['values']):,.2f}; insights: {d['insights'][0]}")
    # ---- TEST 3
    q = "Which products are underperforming?"
    d = ask(cid, q)["result"]
    t = sales.groupby("product_id").revenue.sum()
    exp_set = set(t[t <= t.quantile(0.25)].index)
    got = {r_[0] for r_ in d["table"]["rows"] if r_[-1] is True}
    ok = got == exp_set and d["table"]["rows"][0][0] == t.idxmin()
    record(3, "Underperforming products ranking", q, f"{len(exp_set)} products at/below the 25th percentile, weakest first ({t.idxmin()})",
           d["answer"], ok, f"flagged={sorted(got)[:6]}...; assumptions: {d['assumptions'][:2]}")
    # ---- TEST 4
    q = "What are the top five customers?"
    d4 = ask(cid, q)["result"]
    t = sales.groupby("customer_id").revenue.sum().round(2).sort_values(ascending=False).head(5)
    ok = [r_[0] for r_ in d4["table"]["rows"]] == list(t.index) and all(abs(r_[1] - v) < 0.01 for r_, v in zip(d4["table"]["rows"], t.values))
    record(4, "Top five customers", q, f"{list(t.index)} (grouped by customer_id: customers.csv not uploaded yet)", d4["answer"], ok,
           f"rows: {d4['table']['rows']}")
    # ---- TEST 5
    q = "Generate SQL for this analysis."
    d = ask(cid, q)["result"]
    import re
    sql = d["sql"]
    blocked = re.search(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|ATTACH|COPY|PRAGMA)\b", sql, re.I)
    ok = sql == d4["sql"] and sql.lstrip().upper().startswith("SELECT") and not blocked and len(d["table"]["rows"]) == 5
    record(5, "Generate SQL for this analysis (read-only)", q, "the previous analysis' SELECT, re-executed read-only, 5 rows", d["answer"], ok, sql.replace("\n", " ⏎ "))
    # ---- TEST 6
    q = "Detect anomalies in the dataset."
    d = ask(cid, q)["result"]
    q1, q3 = sales.revenue.quantile([.25, .75])
    hi = q3 + 3 * (q3 - q1)
    n_out = int((sales.revenue > hi).sum())
    a0 = d["anomalies"][0]
    ok = a0["actual"] == sales.revenue.max() and int(d["answer"].split()[0]) >= n_out and "unusually high" in a0["reason"]
    record(6, "Detect anomalies (real statistics)", q, f"IQR(k=3) upper bound {hi:,.2f}; ≥{n_out} rows above it; most extreme = {sales.revenue.max():,.2f}",
           d["answer"], ok, f"top: id={a0['id']} actual={a0['actual']} expected≤{a0['expected_high']}; reason: {a0['reason'][:160]}…")
    # ---- TEST 7
    q = "Forecast sales for the next 6 months."
    d = ask(cid, q)["result"]
    f = d["forecast"]
    ok = f["forecast"]["periods"] == [f"2026-0{i}" for i in range(1, 7)] and all(lo <= p <= hi_ for lo, p, hi_ in zip(f["forecast"]["lower"], f["forecast"]["point"], f["forecast"]["upper"]))
    record(7, "Forecast next 6 months", q, "6 monthly estimates from 2026-01 with lower ≤ point ≤ upper, labelled as estimates", d["answer"], ok,
           f"model={f['model']}; point={f['forecast']['point']}; backtest={f['backtest'][f['model']]}")
    # ---- TEST 8
    r = up("customers")
    ds = s.get(f"{u}/api/datasets").json()
    q = "Which customer segment generated the highest revenue?"
    d8 = ask(cid, q)["result"]
    t = sales.merge(cust, on="customer_id").groupby("segment").revenue.sum().round(2).sort_values(ascending=False)
    ok = len(ds["datasets"]) == 2 and len(ds["relationships"]) == 1 and d8["table"]["rows"][0][0] == t.index[0] and abs(d8["table"]["rows"][0][1] - t.iloc[0]) < 0.01 and "JOIN" in d8["sql"]
    record(8, "Two datasets + cross-dataset question (join)", f"upload customers.csv (now 2 datasets); “{q}”",
           f"relationship sales.customer_id→customers.customer_id; {t.index[0]} = {t.iloc[0]:,.2f}", d8["answer"], ok,
           f"detected: {ds['relationships'][0]['description']}; SQL: {d8['sql'].splitlines()[2]} / {d8['sql'].splitlines()[3]}; {d8['validation'][0]}")
    # ---- TEST 9
    mid = ask(cid, q)["message_id"]
    out = ROOT / "docs" / "sample_exports"
    out.mkdir(exist_ok=True)
    got = {}
    for fmt in ("md", "html", "csv"):
        resp = s.get(f"{u}/api/reports/message/{mid}.{fmt}")
        (out / f"qa_report.{fmt}").write_bytes(resp.content)
        got[fmt] = (resp.status_code, len(resp.content))
    md = (out / "qa_report.md").read_text()
    html = (out / "qa_report.html").read_text()
    sess = s.get(f"{u}/api/reports/conversation/{cid}.md")
    needles = ["Key findings", "### SQL", "Analysis method", "Assumptions", "Datasets used", "sales.csv", "customers.csv"]
    ok = all(c == 200 for c, _ in got.values()) and all(n in md for n in needles) and "data:image/png;base64," in html and sess.status_code == 200 and sess.text.count("## ") >= 8
    record(9, "Export report (MD / HTML / CSV / session)", "export the segment analysis", "200 responses; MD has findings, SQL, method, assumptions, sources; HTML embeds chart PNG",
           f"statuses/sizes: {got}; session report {len(sess.text)} bytes", ok, f"files written to docs/sample_exports/; markdown sections present: {[n for n in needles if n in md]}")
    # ---- TEST 10 (context + restart)
    cid2 = s.post(f"{u}/api/conversations").json()["id"]
    d_a = ask(cid2, "Which region has the highest revenue?")["result"]
    top = d_a["table"]["rows"][0][0]
    d_b = ask(cid2, "Show me its monthly trend")["result"]
    exp_total = sales[sales.region == top].revenue.sum()
    ok1 = d_b["mode"] == "trend" and abs(sum(d_b["charts"][0]["series"][0]["values"]) - exp_total) < 0.5
    restarted, ok2, d_c = False, True, None
    if a.restart_cmd and a.pid_file:
        os.kill(int(Path(a.pid_file).read_text()), signal.SIGTERM)
        time.sleep(1)
        subprocess.run(a.restart_cmd, shell=True, check=True)
        restarted = wait(u)
        s2 = requests.Session()
        s2.headers.update(H)
        login = s2.post(f"{u}/api/auth/login", json={"username": user, "password": "qa-password-1"})
        ds2 = s2.get(f"{u}/api/datasets").json()
        r_c = s2.post(f"{u}/api/conversations/{cid2}/ask", json={"question": "what about South"})
        d_c = r_c.json()["result"] if r_c.ok else {}
        ok2 = restarted and login.ok and len(ds2["datasets"]) == 2 and d_c.get("mode") == "trend" and \
            abs(sum(d_c["charts"][0]["series"][0]["values"]) - sales[sales.region == "South"].revenue.sum()) < 0.5
    record(10, "Conversation context (+ server restart persistence)", "“Which region has the highest revenue?” → “Show me its monthly trend” → [restart server] → “what about South”",
           f"‘its’ resolves to {top}: trend total = {exp_total:,.2f}; after restart datasets (2) and context persist and ‘South’ replaces the filter",
           f"{d_b['answer']} | after restart: {d_c['answer'] if d_c else 'n/a'}", ok1 and ok2,
           f"assumptions: {d_b['assumptions']}; restarted={restarted}; follow-up after restart used context: {bool(d_c)}")
    # ---- report
    lines = ["# Manual QA report", "", f"Run against a live server at `{u}` over HTTP (`scripts/manual_qa.py`). Expected values are computed independently with pandas from the CSVs.", "",
             "| # | Test | Result |", "|---|---|---|"] + [f"| {r_['n']} | {r_['title']} | **{'PASS' if r_['ok'] else 'FAIL'}** |" for r_ in rows] + [""]
    for r_ in rows:
        lines += [f"## TEST {r_['n']} – {r_['title']}: {'PASS' if r_['ok'] else 'FAIL'}", f"- **Input:** {r_['inp']}", f"- **Expected:** {r_['expected']}", f"- **Actual:** {r_['actual']}", f"- **Evidence:** {r_['evidence']}", ""]
    (ROOT / "docs" / "QA_REPORT.md").write_text("\n".join(lines))
    print(f"\n{sum(r_['ok'] for r_ in rows)}/{len(rows)} passed -> docs/QA_REPORT.md")
    return 0 if all(r_["ok"] for r_ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
