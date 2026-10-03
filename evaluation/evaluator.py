"""Evaluation harness. Runs every question in questions.json through the real orchestrator and scores:
intent, tool selection, chart type, numerical correctness (vs. independently computed pandas), SQL validity, relevance.

Usage:  python evaluation/evaluator.py [--min-score 0.9] [--json evaluation/results.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.llm import NullProvider  # noqa: E402
from app.agents.orchestrator import Orchestrator  # noqa: E402
from app.config import Config  # noqa: E402
from app.core.cache import TTLCache  # noqa: E402
from app.core.db import AppDB  # noqa: E402
from app.services.registry import DatasetRegistry  # noqa: E402
from app.services.sql_engine import split_statements, validate_sql  # noqa: E402
from app.tools.router_factory import build_router  # noqa: E402

SAMPLES = ROOT / "data" / "samples"


class Truth:
    """Ground truth computed directly from the CSVs with plain pandas (independent of the app's code paths)."""

    def __init__(self) -> None:
        self.s = pd.read_csv(SAMPLES / "sales.csv", parse_dates=["order_date"])
        self.c = pd.read_csv(SAMPLES / "customers.csv")
        self.p = pd.read_csv(SAMPLES / "products.csv")
        self.sc = self.s.merge(self.c, on="customer_id")
        self.sp = self.s.merge(self.p, on="product_id")


def rows(res):
    return res["table"]["rows"] if res.get("table") else []


def close(a, b, tol=0.02):
    return abs(float(a) - float(b)) <= tol


def checks(T: Truth):
    month = lambda df: df.assign(p=df.order_date.dt.strftime("%Y-%m")).groupby("p").revenue.sum()

    def region_top(r, ctx):
        by = T.s.groupby("region").revenue.sum().sort_values(ascending=False)
        return rows(r)[0][0] == by.index[0] and close(rows(r)[0][1], by.iloc[0]), f"expected {by.index[0]}={by.iloc[0]:.2f}"

    def north_trend_followup(r, ctx):
        exp = T.s[T.s.region == "North"].revenue.sum()
        return close(sum(r["charts"][0]["series"][0]["values"]), exp, 0.5), f"expected North total {exp:.2f}"

    def sql_recall_matches_previous(r, ctx):
        return r["sql"] == ctx["prev"]["sql"], "SQL must equal the previous analysis' SQL"

    def monthly_total(r, ctx):
        e = month(T.s)
        return r["charts"][0]["labels"] == list(e.index) and close(sum(r["charts"][0]["series"][0]["values"]), e.sum(), 0.5), "36 monthly totals"

    def quarterly_total(r, ctx):
        e = T.s.assign(p=T.s.order_date.dt.year.astype(str) + "-Q" + T.s.order_date.dt.quarter.astype(str)).groupby("p").revenue.sum()
        return r["charts"][0]["labels"] == list(e.index) and close(sum(r["charts"][0]["series"][0]["values"]), e.sum(), 0.5), "12 quarterly totals"

    def north_march_drop(r, ctx):
        n = T.s[T.s.region == "North"]
        f = n[(n.order_date.dt.year == 2025) & (n.order_date.dt.month == 2)].revenue.sum()
        m = n[(n.order_date.dt.year == 2025) & (n.order_date.dt.month == 3)].revenue.sum()
        return f"{abs((m / f - 1) * 100):.1f}%" in r["answer"], f"expected {abs((m / f - 1) * 100):.1f}% change"

    def underperformers(r, ctx):
        t = T.sp.groupby("product_name").revenue.sum()
        exp = set(t[t <= t.quantile(0.25)].index)
        return {x[0] for x in rows(r) if x[-1] is True} == exp, f"expected {len(exp)} flagged"

    def top5_customers(r, ctx):
        t = T.sc.groupby("customer_name").revenue.sum().sort_values(ascending=False).head(5)
        return [x[0] for x in rows(r)] == list(t.index) and all(close(x[1], v) for x, v in zip(rows(r), t.values)), "top 5 names+values"

    def segment_top(r, ctx):
        t = T.sc.groupby("segment").revenue.sum().sort_values(ascending=False)
        return rows(r)[0][0] == t.index[0] and close(rows(r)[0][1], t.iloc[0]), f"expected {t.index[0]}"

    def category_top(r, ctx):
        t = T.sp.groupby("category").revenue.sum().sort_values(ascending=False)
        return rows(r)[0][0] == t.index[0] and close(rows(r)[0][1], t.iloc[0]), f"expected {t.index[0]}"

    def region_lowest(r, ctx):
        t = T.s.groupby("region").revenue.sum().sort_values()
        return rows(r)[0][0] == t.index[0], f"expected {t.index[0]} first"

    def north_top3_products(r, ctx):
        t = T.sp[T.sp.region == "North"].groupby("product_name").revenue.sum().sort_values(ascending=False).head(3)
        return [x[0] for x in rows(r)] == list(t.index), "top 3 within North"

    def region_totals(r, ctx):
        t = T.s.groupby("region").revenue.sum()
        return len(rows(r)) == 4 and all(close(v, t[k]) for k, v, *_ in rows(r)), "4 regions"

    def avg_unit_price(r, ctx):
        return close(rows(r)[0][0], T.s.unit_price.mean()), f"expected {T.s.unit_price.mean():.2f}"

    def orders_2024(r, ctx):
        return rows(r)[0][0] == T.s[T.s.order_date.dt.year == 2024].order_id.nunique(), "distinct 2024 orders"

    def category_totals(r, ctx):
        t = T.sp.groupby("category").revenue.sum()
        return all(close(v, t[k]) for k, v, *_ in rows(r)), "category totals"

    def anomalies(r, ctx):
        q1, q3 = T.s.revenue.quantile([.25, .75])
        hi = q3 + 3 * (q3 - q1)
        n = int((T.s.revenue > hi).sum())
        return r["anomalies"][0]["actual"] == T.s.revenue.max() and int(r["answer"].split()[0]) >= n, f"max revenue first; >= {n} flagged"

    def forecast6(r, ctx):
        f = r["forecast"]
        return f["horizon"] == 6 and f["forecast"]["periods"][0] == "2026-01" and f["history"]["periods"] == list(month(T.s).index), "6 months from 2026-01"

    def forecast3(r, ctx):
        return r["forecast"]["horizon"] == 3, "next quarter = 3 months"

    def quality(r, ctx):
        return any(i["type"] == "duplicate_rows" and i["affected_rows"] == int(T.s.duplicated().sum()) for i in (r.get("table") or {}).get("rows", []) and [dict(zip(r["table"]["columns"], x)) | {"type": x[r["table"]["columns"].index("type")], "affected_rows": x[r["table"]["columns"].index("affected_rows")]} for x in r["table"]["rows"]]), "duplicate row count matches pandas"

    def summary(r, ctx):
        return f"{T.s.revenue.sum():,.2f}" in r["answer"], "total revenue present"

    def unsupported(r, ctx):
        return bool(r.get("unsupported")) and bool(r.get("follow_ups")), "graceful help response"

    def data_intact(r, ctx):
        n = int(ctx["engine"].execute("SELECT COUNT(*) FROM sales").iloc[0, 0])
        return n == len(T.s), "sales table untouched"

    def schema(r, ctx):
        return "3 dataset(s)" in r["answer"], "3 datasets listed"

    return {k: v for k, v in locals().items() if callable(v) and k not in ("month",)}


def new_system():
    cfg = Config.from_env(data_dir=tempfile.mkdtemp(), log_dir=tempfile.mkdtemp(), testing=True)
    cache = TTLCache(True, 900)
    reg = DatasetRegistry(cfg, AppDB(cfg.db_path), cache)
    for n in ("sales", "customers", "products"):
        reg.add("eval", f"{n}.csv", (SAMPLES / f"{n}.csv").read_bytes())
    ws = reg.workspace("eval")
    return ws, Orchestrator(cfg, cache, build_router(), NullProvider())


def ask(orch, ws, state, q):
    out = None
    for ev in orch.run(ws, q, state, "eval", "eval"):
        if ev["type"] == "result":
            out, state = ev["result"], ev["state"]
    return out, state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=float, default=0.9)
    ap.add_argument("--json", default=str(ROOT / "evaluation" / "results.json"))
    args = ap.parse_args()
    spec = json.loads((ROOT / "evaluation" / "questions.json").read_text())
    T = Truth()
    CH = checks(T)
    results, dims = [], {k: [0, 0] for k in ("intent", "tools", "chart", "numeric", "sql", "relevance")}
    for sess in spec["sessions"]:
        ws, orch = new_system()
        state, prev = None, None
        for q in sess["questions"]:
            res, state = ask(orch, ws, state, q["q"])
            ctx = {"prev": prev, "engine": ws.engine}
            row: dict = {"id": q["id"], "question": q["q"], "scores": {}, "notes": []}

            def score(dim, ok, note=""):
                dims[dim][1] += 1
                dims[dim][0] += int(bool(ok))
                row["scores"][dim] = bool(ok)
                if not ok:
                    row["notes"].append(f"{dim}: {note}")

            if q["intent"]:
                score("intent", res.get("intent") == q["intent"], f"got {res.get('intent')}, want {q['intent']}")
            score("tools", set(q["tools"]) <= set(res.get("tools_used", [])), f"used {res.get('tools_used')}, want ⊇ {q['tools']}")
            if q["chart"] is not None or q["id"] in ("Q24", "Q25", "Q26"):
                got = res["charts"][0]["type"] if res.get("charts") else None
                score("chart", got == q["chart"], f"got {got}, want {q['chart']}")
            if q["check"]:
                try:
                    ok, detail = CH[q["check"]](res, ctx)
                except Exception as exc:
                    ok, detail = False, f"check crashed: {exc!r}"
                score("numeric", ok, detail)
            if q["sql"]:
                try:
                    stmts = split_statements(res["sql"])
                    assert stmts, "no SQL returned"
                    for s in stmts:
                        validate_sql(s)
                        ws.engine.execute(s)
                    ok = True
                except Exception as exc:
                    ok = False
                    row["notes"].append(f"sql: {exc}")
                score("sql", ok)
            score("relevance", bool(res.get("answer")) and all(m.lower() in res["answer"].lower() for m in q.get("mentions", [])), f"answer missing {q.get('mentions')}")
            row["passed"] = all(row["scores"].values())
            results.append(row)
            prev = res
    total_ok = sum(r["passed"] for r in results)
    print(f"\n{'ID':5} {'RESULT':6} QUESTION")
    for r in results:
        print(f"{r['id']:5} {'PASS' if r['passed'] else 'FAIL':6} {r['question'][:70]}" + ("" if r["passed"] else "  <- " + "; ".join(r["notes"])))
    print("\nDimension scores:")
    for k, (ok, n) in dims.items():
        print(f"  {k:10} {ok}/{n}  ({(ok / n if n else 1):.0%})")
    overall = sum(o for o, _ in dims.values()) / max(sum(n for _, n in dims.values()), 1)
    print(f"\nQuestions fully passing: {total_ok}/{len(results)} | overall check score: {overall:.1%}")
    Path(args.json).write_text(json.dumps({"questions": results, "dimensions": {k: {"passed": o, "total": n} for k, (o, n) in dims.items()},
                                           "overall": overall, "fully_passing": f"{total_ok}/{len(results)}"}, indent=2))
    return 0 if overall >= args.min_score and total_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
