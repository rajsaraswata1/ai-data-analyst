from __future__ import annotations

import time
from typing import Any

from ..core.cache import make_key
from ..core.logging_setup import log_event
from ..services.sql_engine import validate_sql
from .base import Tool, ToolContext


class RunSQLTool(Tool):
    name = "run_sql"
    description = "Validate and execute a read-only SQL SELECT over the uploaded tables. Returns rows."
    input_schema = {"properties": {"sql": {"type": "string"}}, "required": ["sql"]}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        sql = validate_sql(args["sql"])           # raises UnsafeSQLError
        key = make_key("sql", ctx.ws.fingerprint, " ".join(sql.split()))
        t0 = time.perf_counter()
        frame, cached = ctx.cache.get_or_compute(key, lambda: ctx.ws.engine.execute(sql, ctx.cfg.max_result_rows))
        ms = (time.perf_counter() - t0) * 1000
        log_event("sql_executed", request_id=ctx.request_id, sql_ms=round(ms, 2), rows=len(frame), cached=cached)
        return {"sql": sql, "frame": frame.copy(), "row_count": len(frame), "truncated": bool(frame.attrs.get("truncated")),
                "cached": cached, "sql_ms": round(ms, 2)}
