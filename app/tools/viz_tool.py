"""Chart specs are built *from analytical result frames only* (never from LLM text)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..core.errors import AppError
from .base import Tool, ToolContext

CHART_TYPES = ["bar", "hbar", "line", "pie", "scatter", "histogram", "kpi"]
MAX_PIE_SLICES = 8
MAX_POINTS = 2000


def select_chart_type(mode: str, n_categories: int, override: str | None = None, share_question: bool = False) -> str:
    """Rules: time→line, ranking→horizontal bar, part-to-whole (few slices)→pie, categorical→bar, distribution→histogram."""
    if override in CHART_TYPES:
        return override
    if mode in ("trend", "forecast"):
        return "line"
    if mode == "distribution":
        return "histogram"
    if mode == "scatter":
        return "scatter"
    if mode == "rank":
        return "hbar"
    if share_question and 2 <= n_categories <= MAX_PIE_SLICES:
        return "pie"
    return "bar"


def _clean(vals) -> list[Any]:
    return [None if (isinstance(v, float) and not np.isfinite(v)) else (round(float(v), 4) if isinstance(v, (int, float, np.integer, np.floating)) else v)
            for v in vals]


class ChartTool(Tool):
    name = "create_chart"
    description = "Build a chart spec (bar, hbar, line, pie, scatter, histogram, kpi) from an analytical result frame."
    input_schema = {"properties": {
        "chart_type": {"type": "string", "enum": CHART_TYPES}, "frame": {"type": "object", "internal": True},
        "x": {"type": "string"}, "y": {"type": "string"}, "series_col": {"type": "string"},
        "title": {"type": "string"}, "x_label": {"type": "string"}, "y_label": {"type": "string"},
        "format": {"type": "string", "enum": ["money", "number", "percent"]}, "bins": {"type": "integer"},
        "reference": {"type": "number"}, "reference_label": {"type": "string"}}, "required": ["chart_type", "frame"]}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        t, f = args["chart_type"], args["frame"]
        if f is None or len(f) == 0:
            raise AppError("There is no data to chart.", 200, "empty_result")
        base = {"type": t, "title": args.get("title", ""), "x_label": args.get("x_label", args.get("x", "")),
                "y_label": args.get("y_label", args.get("y", "")), "format": args.get("format", "number"),
                "currency": ctx.cfg.currency if args.get("format") == "money" else ""}
        if args.get("reference") is not None:
            base["reference"] = {"value": float(args["reference"]), "label": args.get("reference_label", "")}
        try:
            if t == "kpi":
                return {"chart": {**base, "value": float(f[args["y"]].iloc[0])}}
            if t == "histogram":
                vals = f[args["x"]].dropna().astype(float).to_numpy()
                counts, edges = np.histogram(vals, bins=args.get("bins") or min(30, max(8, int(np.sqrt(len(vals))))))
                return {"chart": {**base, "bins": [{"x0": float(edges[i]), "x1": float(edges[i + 1]), "count": int(counts[i])}
                                                   for i in range(len(counts))], "y_label": "Count"}}
            if t == "scatter":
                d = f[[args["x"], args["y"]]].dropna().head(MAX_POINTS)
                return {"chart": {**base, "points": [{"x": round(float(a), 4), "y": round(float(b), 4)} for a, b in zip(d[args["x"]], d[args["y"]])]}}
            if args.get("series_col"):
                wide = f.pivot_table(index=args["x"], columns=args["series_col"], values=args["y"], aggfunc="sum").sort_index()
                return {"chart": {**base, "labels": [str(i) for i in wide.index],
                                  "series": [{"name": str(c), "values": _clean(wide[c].tolist())} for c in wide.columns]}}
            if t == "pie":
                f = f.sort_values(args["y"], ascending=False)
                if len(f) > MAX_PIE_SLICES:
                    head, tail = f.head(MAX_PIE_SLICES - 1), f.iloc[MAX_PIE_SLICES - 1:]
                    f = pd.concat([head, pd.DataFrame({args["x"]: ["Other"], args["y"]: [tail[args["y"]].sum()]})])
            return {"chart": {**base, "labels": [str(v) for v in f[args["x"]]],
                              "series": [{"name": args.get("y_label") or args["y"], "values": _clean(f[args["y"]].tolist())}]}}
        except KeyError as exc:
            raise AppError("Chart could not be built: a result column was missing.", 400, "chart_error", repr(exc))
