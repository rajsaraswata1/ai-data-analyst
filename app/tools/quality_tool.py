from __future__ import annotations

from typing import Any

from ..core.errors import AppError
from .base import Tool, ToolContext


class DataQualityTool(Tool):
    name = "get_data_quality"
    description = "Return the data-quality score and detailed issues (missing, duplicates, outliers, type problems) per table."
    input_schema = {"properties": {"table": {"type": "string"}}, "required": []}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        ws = ctx.ws
        if args.get("table") and args["table"] not in ws.quality:
            raise AppError(f"Unknown table '{args['table']}'.", 404, "not_found")
        tables = [args["table"]] if args.get("table") else list(ws.quality)
        return {"reports": {t: ws.quality[t] for t in tables}}
