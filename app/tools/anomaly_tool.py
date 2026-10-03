from __future__ import annotations

from typing import Any

from ..core.cache import make_key
from ..core.errors import AppError
from ..services import catalog
from ..services.anomaly import detect_anomalies
from .base import Tool, ToolContext


class AnomalyTool(Tool):
    name = "detect_anomalies"
    description = "Detect anomalous rows in a numeric metric (IQR/z-score auto-selected, plus Isolation Forest) with explanations."
    input_schema = {"properties": {"table": {"type": "string"}, "metric": {"type": "string"}}, "required": []}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        ws = ctx.ws
        table = args.get("table") or catalog.fact_table(ws)
        if table not in ws.tables:
            raise AppError("There is no dataset to analyze.", 400, "no_data")
        metric = args.get("metric") or catalog.primary_metric(ws, table)
        if not metric or metric not in ws.tables[table].columns:
            raise AppError(f"'{table}' has no numeric column to check for anomalies.", 400, "no_metric")
        measures = [c["name"] for c in catalog.measure_columns(ws, table)]
        id_col = next((c["name"] for c in ws.schemas[table] if c["role"] == "id" and c["unique_ratio"] >= 0.99), None)
        dates = catalog.date_columns(ws, table)
        key = make_key("anomaly", ws.fingerprint, table, metric)
        res, cached = ctx.cache.get_or_compute(key, lambda: detect_anomalies(ws.tables[table], metric, measures, id_col, dates[0] if dates else None))
        return {**res, "table": table, "cached": cached}
