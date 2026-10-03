from __future__ import annotations

from typing import Any

from ..core.errors import AppError
from ..services import reporting
from .base import Tool, ToolContext


class ReportTool(Tool):
    name = "generate_report"
    description = "Render stored analysis results as a Markdown, HTML or CSV report."
    input_schema = {"properties": {"results": {"type": "array"}, "format": {"type": "string", "enum": ["markdown", "html", "csv"]},
                                   "title": {"type": "string"}}, "required": ["results", "format"]}

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        results, fmt, title = args["results"], args["format"], args.get("title") or "AI Data Analyst Report"
        if not results:
            raise AppError("There is nothing to export yet. Ask a question first.", 400, "empty_report")
        if fmt == "markdown":
            return {"content": reporting.to_markdown(results, title), "mimetype": "text/markdown", "ext": "md"}
        if fmt == "html":
            return {"content": reporting.to_html(results, title), "mimetype": "text/html", "ext": "html"}
        return {"content": reporting.to_csv(results[-1]), "mimetype": "text/csv", "ext": "csv"}
