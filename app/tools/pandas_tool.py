"""Controlled pandas analysis layer: a fixed allowlist of operations over *result frames* produced by SQL.
No user-supplied code is ever evaluated."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ..core.errors import AppError
from .base import Tool, ToolContext

OPERATIONS = ["share_of_total", "underperformers", "trend_stats", "compare_periods", "describe_column"]


def share_of_total(frame: pd.DataFrame, label: str, value: str) -> dict[str, Any]:
    df = frame[[label, value]].copy()
    total = float(df[value].sum())
    df["share_pct"] = (df[value] / total * 100).round(2) if total else 0.0
    ranked = df.sort_values(value, ascending=False).reset_index(drop=True)
    top, bottom = ranked.iloc[0], ranked.iloc[-1]
    second = ranked.iloc[1] if len(ranked) > 1 else None
    return {"frame": ranked, "total": total, "n": len(ranked), "top_label": str(top[label]), "top_value": float(top[value]),
            "top_share_pct": float(top["share_pct"]), "bottom_label": str(bottom[label]), "bottom_value": float(bottom[value]),
            "second_label": str(second[label]) if second is not None else None,
            "second_value": float(second[value]) if second is not None else None,
            "top_vs_bottom_ratio": float(top[value] / bottom[value]) if bottom[value] else None,
            "top_vs_second_pct": float((top[value] / second[value] - 1) * 100) if second is not None and second[value] else None}


def underperformers(frame: pd.DataFrame, label: str, value: str) -> dict[str, Any]:
    """Underperforming = value at or below the first quartile of the group values (and below the median)."""
    df = frame[[label, value]].copy()
    q1, med, total = float(df[value].quantile(0.25)), float(df[value].median()), float(df[value].sum())
    flagged = df[df[value] <= q1].sort_values(value).copy()
    flagged["vs_median_pct"] = ((flagged[value] / med - 1) * 100).round(1) if med else np.nan
    flagged["share_pct"] = (flagged[value] / total * 100).round(2) if total else 0.0
    return {"frame": flagged.reset_index(drop=True), "q1": q1, "median": med, "total": total, "n_total": len(df),
            "n_flagged": len(flagged), "flagged_share_pct": float(flagged[value].sum() / total * 100) if total else 0.0}


def trend_stats(frame: pd.DataFrame, period: str, value: str) -> dict[str, Any]:
    df = frame[[period, value]].copy().sort_values(period).reset_index(drop=True)
    v = df[value].astype(float)
    df["change_pct"] = (v.pct_change() * 100).round(2)
    out: dict[str, Any] = {"frame": df, "n": len(df), "total": float(v.sum()), "mean": float(v.mean()),
                           "first_period": str(df[period].iloc[0]), "first_value": float(v.iloc[0]),
                           "last_period": str(df[period].iloc[-1]), "last_value": float(v.iloc[-1]),
                           "peak_period": str(df[period].iloc[int(v.idxmax())]), "peak_value": float(v.max()),
                           "trough_period": str(df[period].iloc[int(v.idxmin())]), "trough_value": float(v.min())}
    out["last_change_pct"] = float(df["change_pct"].iloc[-1]) if len(df) > 1 and pd.notna(df["change_pct"].iloc[-1]) else None
    if len(df) >= 4:
        res = stats.linregress(np.arange(len(v)), v.to_numpy())
        out.update(slope=float(res.slope), r_squared=float(res.rvalue ** 2), p_value=float(res.pvalue),
                   direction="upward" if res.slope > 0 and res.pvalue < 0.05 else "downward" if res.slope < 0 and res.pvalue < 0.05 else "flat/no significant trend")
    if len(df) >= 6:
        out["recent_vs_prior_pct"] = float((v.iloc[-3:].mean() / v.iloc[-6:-3].mean() - 1) * 100) if v.iloc[-6:-3].mean() else None
    return out


def compare_periods(frame: pd.DataFrame, dim: str, period: str, value: str, prev: str, cur: str) -> dict[str, Any]:
    pv = frame.pivot_table(index=dim, columns=period, values=value, aggfunc="sum", fill_value=0.0)
    for p in (prev, cur):
        if p not in pv.columns:
            pv[p] = 0.0
    pv = pv[[prev, cur]]
    pv["delta"] = pv[cur] - pv[prev]
    total_delta = float(pv["delta"].sum())
    pv["pct_change"] = np.where(pv[prev] != 0, pv["delta"] / pv[prev] * 100, np.nan)
    pv["share_of_change_pct"] = pv["delta"] / total_delta * 100 if total_delta else np.nan
    pv = pv.sort_values("delta").reset_index()
    return {"frame": pv, "total_delta": total_delta, "prev_total": float(pv[prev].sum()), "cur_total": float(pv[cur].sum())}


def describe_column(df: pd.DataFrame, column: str) -> dict[str, Any]:
    s = df[column].dropna().astype(float)
    return {"count": int(s.count()), "mean": float(s.mean()), "median": float(s.median()), "std": float(s.std()),
            "min": float(s.min()), "max": float(s.max()), "q1": float(s.quantile(.25)), "q3": float(s.quantile(.75))}


class PandasAnalysisTool(Tool):
    name = "run_pandas_analysis"
    description = f"Run one of a fixed set of safe pandas analyses {OPERATIONS} over a result frame."
    input_schema = {"properties": {
        "operation": {"type": "string", "enum": OPERATIONS},
        "frame": {"type": "object", "internal": True},
        "label": {"type": "string"}, "value": {"type": "string"}, "period": {"type": "string"},
        "dim": {"type": "string"}, "prev": {"type": "string"}, "cur": {"type": "string"},
        "table": {"type": "string"}, "column": {"type": "string"}}, "required": ["operation"]}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        op, f = args["operation"], args.get("frame")
        if op == "describe_column":
            table, col = args.get("table"), args.get("column")
            if table not in ctx.ws.tables or col not in ctx.ws.tables[table].columns:
                raise AppError("Unknown table/column.", 404, "not_found")
            return describe_column(ctx.ws.tables[table], col)
        if f is None or len(f) == 0:
            raise AppError("There is no data to analyze for this question.", 200, "empty_result")
        try:
            if op == "share_of_total":
                return share_of_total(f, args["label"], args["value"])
            if op == "underperformers":
                return underperformers(f, args["label"], args["value"])
            if op == "trend_stats":
                return trend_stats(f, args["period"], args["value"])
            return compare_periods(f, args["dim"], args["period"], args["value"], args["prev"], args["cur"])
        except KeyError as exc:
            raise AppError("Analysis arguments referenced a missing column.", 400, "tool_args", repr(exc))
