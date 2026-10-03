from __future__ import annotations

from typing import Any

import pandas as pd

from ..core.cache import make_key
from ..core.errors import AppError
from ..services import catalog
from ..services.forecasting import forecast_monthly
from ..services.query_builder import build_sql
from .base import Tool, ToolContext


class ForecastTool(Tool):
    name = "forecast_metric"
    description = "Forecast a monthly metric for the next N months (model auto-selected by backtest; intervals are approximate)."
    input_schema = {"properties": {"plan": {"type": "object"}, "horizon": {"type": "integer"}}, "required": ["plan"]}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        plan = {**args["plan"], "dimension": None, "order": None, "limit": None}
        tm = plan.get("time")
        if not tm:
            raise AppError("Forecasting needs a date column and a numeric metric; none was found.", 400, "no_date")
        plan["time"] = {**tm, "grain": "month"}
        sql = build_sql(plan, ctx.ws.relationships)
        res = ctx.router.call("run_sql", {"sql": sql}, ctx)
        frame = res.data["frame"]
        if frame.empty:
            raise AppError("No data matched this forecast request.", 200, "empty_result")
        alias = frame.columns[-1]
        series = pd.Series(frame[alias].astype(float).values, index=frame["period"].values)
        last = ctx.ws.tables[tm["table"]][tm["column"]].max()
        key = make_key("forecast", ctx.ws.fingerprint, sql, int(args.get("horizon", 6)))
        out, cached = ctx.cache.get_or_compute(key, lambda: forecast_monthly(series, int(args.get("horizon", 6)), last))
        return {**out, "sql": sql, "value_column": alias, "cached": cached and res.cached}
