from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext


class DatasetSchemaTool(Tool):
    name = "get_dataset_schema"
    description = "Return table(s), column types, roles, and detected relationships for the uploaded datasets."
    input_schema = {"properties": {"table": {"type": "string", "description": "Optional table name"}}, "required": []}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        ws = ctx.ws
        tables = [args["table"]] if args.get("table") in ws.tables else list(ws.tables)
        out = {t: {"rows": ws.meta[t]["rows"], "filename": ws.meta[t]["filename"],
                   "columns": [{k: c[k] for k in ("name", "kind", "role", "unique", "missing_pct")} for c in ws.schemas[t]]}
               for t in tables}
        return {"tables": out, "relationships": ws.relationships}


class SearchMetadataTool(Tool):
    name = "search_metadata"
    description = "Semantic search over table/column metadata and business terms (e.g. 'sales performance' -> revenue)."
    input_schema = {"properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        hits = ctx.ws.semantic.search(args["query"], int(args.get("top_k", 5))) if ctx.ws.semantic else []
        return {"matches": hits}
