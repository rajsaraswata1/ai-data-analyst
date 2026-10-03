"""Conversation memory: compact structured state (not the raw transcript) used to resolve follow-ups."""
from __future__ import annotations

from typing import Any

MAX_HISTORY = 8


class ConversationMemory:
    def __init__(self, state: dict[str, Any] | None = None):
        self.state: dict[str, Any] = state or {"last": None, "history": []}

    @property
    def last(self) -> dict[str, Any] | None:
        return self.state.get("last")

    def update(self, plan: dict[str, Any], sql: str | None, top_entity: dict[str, str] | None,
               entities: list[str], summary: str) -> None:
        keep = {k: plan.get(k) for k in ("mode", "intent", "metric", "dimension", "time", "filters", "order", "limit", "year", "table")}
        self.state["last"] = {**keep, "question": plan.get("question"), "sql": sql, "top_entity": top_entity,
                              "entities": entities[:10], "summary": summary[:300]}
        hist = self.state.setdefault("history", [])
        hist.append({"q": plan.get("question"), "mode": plan.get("mode"), "summary": summary[:160]})
        del hist[:-MAX_HISTORY]

    def for_llm(self) -> dict[str, Any]:
        last = self.last or {}
        return {k: last.get(k) for k in ("mode", "metric", "dimension", "filters", "top_entity")} | {"recent": self.state.get("history", [])[-3:]}
