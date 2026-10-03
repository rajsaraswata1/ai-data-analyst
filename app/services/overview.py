"""Business overview used by both the dashboard and the 'executive summary' question. All numbers via SQL/pandas."""
from __future__ import annotations

from typing import Any

import pandas as pd

from ..core.cache import make_key
from ..tools.base import ToolContext
from . import catalog
from .formatting import money, num, pct
from .query_builder import build_sql

PREFERRED_DIMS = ["region", "category", "segment", "channel", "country", "city", "department", "type"]


def _run(ctx: ToolContext, plan: dict[str, Any]) -> pd.DataFrame:
    return ctx.router.call("run_sql", {"sql": build_sql(plan, ctx.ws.relationships)}, ctx).data["frame"]


def _find_id(ws, table: str, token: str) -> str | None:
    return next((c["name"] for c in ws.schemas[table] if c["role"] == "id" and token in c["name"]), None)


def compute_overview(ctx: ToolContext) -> dict[str, Any]:
    ws, cfg = ctx.ws, ctx.cfg
    fact = catalog.fact_table(ws)
    if not fact:
        return {"empty": True}
    metric = catalog.primary_metric(ws, fact)
    info = catalog.col_info(ws, fact, metric) if metric else None
    cur = cfg.currency if info and info["is_money"] else ""
    dates = catalog.date_columns(ws, fact)
    out: dict[str, Any] = {"fact_table": fact, "metric": metric, "kpis": [], "charts": [], "insights": [], "empty": False}
    if not metric:
        return {**out, "note": "No numeric metric found in the datasets."}
    base = {"metric": {"table": fact, "column": metric, "agg": "sum"}}
    total = float(_run(ctx, base).iloc[0, 0] or 0)
    out["total"], out["currency"] = total, cur
    out["kpis"].append({"label": f"Total {metric.replace('_', ' ').title()}", "value": total, "display": money(total, cur)})

    order_col = _find_id(ws, fact, "order")
    n_orders = float(_run(ctx, {"metric": {"table": fact, "column": order_col, "agg": "count_distinct"}} if order_col
                         else {"metric": {"table": fact, "column": None, "agg": "count"}}).iloc[0, 0])
    out["kpis"].append({"label": "Total Orders" if order_col else "Total Records", "value": n_orders, "display": num(n_orders)})
    if n_orders:
        out["kpis"].append({"label": f"Avg {'Order Value' if order_col else 'Value per Record'}", "value": total / n_orders,
                            "display": money(total / n_orders, cur)})
    cust_col = _find_id(ws, fact, "customer")
    if cust_col:
        n_c = float(_run(ctx, {"metric": {"table": fact, "column": cust_col, "agg": "count_distinct"}}).iloc[0, 0])
        out["kpis"].append({"label": "Unique Customers", "value": n_c, "display": num(n_c)})

    if dates:
        tplan = {**base, "time": {"table": fact, "column": dates[0], "grain": "month"}}
        tr = _run(ctx, tplan)
        if len(tr) >= 2:
            alias = tr.columns[-1]
            last_date = ws.tables[fact][dates[0]].max()
            import calendar
            partial = last_date.day < 0.8 * calendar.monthrange(last_date.year, last_date.month)[1]
            usable = tr.iloc[:-1] if partial and len(tr) > 2 else tr
            if len(usable) >= 2:
                a, b = float(usable[alias].iloc[-2]), float(usable[alias].iloc[-1])
                g = (b / a - 1) * 100 if a else None
                out["growth_pct"] = g
                out["kpis"].append({"label": f"Growth ({usable['period'].iloc[-1]} vs prior month)", "value": g, "display": pct(g, True),
                                    "positive": g is not None and g >= 0})
            out["charts"].append({"type": "line", "title": f"Monthly {metric.replace('_', ' ')}", "x_label": "Month",
                                  "y_label": metric.replace("_", " "), "format": "money" if cur else "number", "currency": cur,
                                  "labels": tr["period"].tolist(), "series": [{"name": metric, "values": [round(float(v), 2) for v in tr[alias]]}]})
            out["monthly"] = tr
    dims = catalog.dimension_options(ws, fact)
    ranked = sorted(dims, key=lambda d: (PREFERRED_DIMS.index(d["column"]) if d["column"] in PREFERRED_DIMS else 99, d["unique"]))
    for d in ranked[:2]:
        df = _run(ctx, {**base, "dimension": {"table": d["table"], "column": d["column"]}, "order": "desc"})
        if df.empty:
            continue
        alias = df.columns[-1]
        ctype = "pie" if len(df) <= 5 and d["column"] == "region" else "bar"
        out["charts"].append({"type": ctype, "title": f"{metric.replace('_', ' ').title()} by {d['column'].replace('_', ' ')}",
                              "x_label": d["column"], "y_label": metric, "format": "money" if cur else "number", "currency": cur,
                              "labels": df[d["column"]].astype(str).tolist(), "series": [{"name": metric, "values": [round(float(v), 2) for v in df[alias]]}]})
        top = df.iloc[0]
        out["insights"].append(f"{top[d['column']]} is the top {d['column'].replace('_', ' ')} with {money(float(top[alias]), cur)} "
                               f"({float(top[alias]) / total * 100:.1f}% of total {metric.replace('_', ' ')}).")
        out.setdefault("dimension_tops", []).append({"dimension": d["column"], "top": str(top[d["column"]]), "value": float(top[alias])})
    an = ctx.router.call("detect_anomalies", {"table": fact, "metric": metric}, ctx).data
    out["anomaly_summary"] = {"total": an.get("total", 0), "method": an.get("method"), "rule": an.get("rule"),
                              "top": an.get("anomalies", [])[:3], "metric": metric, "rows_scanned": an.get("rows_scanned")}
    q = ctx.router.call("get_data_quality", {"table": fact}, ctx).data["reports"][fact]
    out["quality"] = {"score": q["score"], "components": q["components"], "issue_count": q["summary"]["issue_count"]}
    out["kpis"].append({"label": "Data Quality Score", "value": q["score"], "display": f"{q['score']:.1f}/100"})
    if dates and "growth_pct" in out and out["growth_pct"] is not None:
        out["insights"].insert(0, f"{metric.replace('_', ' ').title()} changed {pct(out['growth_pct'], True)} in the latest complete month versus the month before.")
    out["insights"].append(f"{out['anomaly_summary']['total']} anomalous {metric.replace('_', ' ')} values were flagged; data quality score is {q['score']:.1f}/100.")
    return out


class InsightTool:
    """Thin Tool wrapper so overview/insight generation goes through the same router as every other capability."""


def make_insight_tool():
    from .. tools.base import Tool

    class GenerateInsightsTool(Tool):
        name = "generate_insights"
        description = "Compute headline KPIs, breakdowns, anomaly summary and quality score for the fact table (deterministic)."
        input_schema = {"properties": {}, "required": []}

        def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
            key = make_key("overview", ctx.ws.fingerprint)
            res, cached = ctx.cache.get_or_compute(key, lambda: compute_overview(ctx))
            return {**res, "cached": cached}

    return GenerateInsightsTool()
