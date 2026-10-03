"""Tool abstraction: each tool declares an input schema, validates arguments, and returns a typed ToolResult.
The ToolRouter adds uniform logging, timing, error handling and metrics around every call."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..core.cache import TTLCache
from ..core.errors import AppError
from ..core.logging_setup import log_event
from ..services.registry import Workspace

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


@dataclass
class ToolContext:
    ws: Workspace
    cache: TTLCache
    cfg: Config
    user_id: str = ""
    request_id: str = ""
    router: "ToolRouter | None" = None
    calls: list = field(default_factory=list)   # names of every tool executed for this request, incl. nested calls


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    cached: bool = False


class Tool:
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {"properties": {}, "required": []}

    def validate(self, args: dict[str, Any]) -> None:
        props, required = self.input_schema.get("properties", {}), self.input_schema.get("required", [])
        for key in required:
            if key not in args or args[key] is None:
                raise AppError(f"Tool '{self.name}' is missing required argument '{key}'.", 400, "tool_args")
        for key, val in args.items():
            spec = props.get(key)
            if spec is None:
                raise AppError(f"Tool '{self.name}' got unexpected argument '{key}'.", 400, "tool_args")
            expected = _TYPES.get(spec.get("type", ""), object)
            if val is not None and spec.get("type") != "any" and not isinstance(val, expected):
                if not (spec.get("type") == "object" and hasattr(val, "columns")):  # DataFrames pass as 'object'
                    raise AppError(f"Argument '{key}' of '{self.name}' has the wrong type.", 400, "tool_args")
            if "enum" in spec and val not in spec["enum"]:
                raise AppError(f"Argument '{key}' must be one of {spec['enum']}.", 400, "tool_args")

    def run(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def spec(self) -> dict[str, Any]:
        """OpenAI-style function spec (for LLM tool calling / documentation)."""
        props = {k: {kk: vv for kk, vv in v.items() if kk != "internal"} for k, v in self.input_schema["properties"].items()
                 if not v.get("internal")}
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": props, "required": [r for r in self.input_schema.get("required", []) if r in props]}}}


class ToolRouter:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.metrics: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "errors": 0, "total_ms": 0.0})
        self._lock = threading.Lock()

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise AppError(f"Unknown tool '{name}'.", 400, "unknown_tool")
        ctx.router = self
        start = time.perf_counter()
        try:
            tool.validate(args)
            data = tool.run(ctx, args)
            res = ToolResult(True, data, cached=bool(data.get("cached", False)))
        except AppError as exc:
            res = ToolResult(False, {}, error=exc.message)
            log_event("tool_error", logging.WARNING, tool=name, request_id=ctx.request_id, code=exc.code, detail=exc.detail[:300])
            res.data = {"code": exc.code}
            self._record(name, start, error=True, res=res)
            raise
        except Exception as exc:  # unexpected: log details, surface a generic error
            log_event("tool_crash", logging.ERROR, tool=name, request_id=ctx.request_id, error=repr(exc))
            self._record(name, start, error=True, res=ToolResult(False))
            raise AppError(f"The '{name}' step failed unexpectedly.", 500, "tool_failure", repr(exc))
        self._record(name, start, error=False, res=res)
        ctx.calls.append(name)
        log_event("tool_call", tool=name, request_id=ctx.request_id, user_id=ctx.user_id, dataset_fp=ctx.ws.fingerprint,
                  duration_ms=round(res.duration_ms, 2), cached=res.cached)
        return res

    def _record(self, name: str, start: float, error: bool, res: ToolResult) -> None:
        res.duration_ms = (time.perf_counter() - start) * 1000
        with self._lock:
            m = self.metrics[name]
            m["calls"] += 1
            m["total_ms"] += res.duration_ms
            m["errors"] += int(error)
