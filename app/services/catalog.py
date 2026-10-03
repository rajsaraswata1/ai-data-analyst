"""Helpers that interpret a Workspace: which table is the 'fact' table, primary metric, usable dimensions."""
from __future__ import annotations

from typing import Any

from .profiling import label_column
from .registry import Workspace

PRIMARY_METRIC_ORDER = ["revenue", "sales_amount", "sales", "amount", "total", "income", "profit", "price", "cost"]
MAX_DIM_CARDINALITY = 30


def col_info(ws: Workspace, table: str, column: str) -> dict[str, Any] | None:
    return next((c for c in ws.schemas.get(table, []) if c["name"] == column), None)


def date_columns(ws: Workspace, table: str) -> list[str]:
    return [c["name"] for c in ws.schemas[table] if c["role"] == "date"]


def measure_columns(ws: Workspace, table: str) -> list[dict[str, Any]]:
    return [c for c in ws.schemas[table] if c["role"] == "measure" and c["non_null"] > 0]


def primary_metric(ws: Workspace, table: str) -> str | None:
    measures = {c["name"]: c for c in measure_columns(ws, table)}
    for name in PRIMARY_METRIC_ORDER:
        if name in measures:
            return name
    for c in measures.values():
        if c["is_money"]:
            return c["name"]
    return next(iter(measures), None)


def fact_table(ws: Workspace) -> str | None:
    """Heuristic: the table with a date, a money-like measure, and the most rows."""
    best, best_score = None, -1.0
    for t in ws.tables:
        score = (3 if date_columns(ws, t) else 0) + (3 if primary_metric(ws, t) else 0) + len(measure_columns(ws, t)) * 0.2
        score += min(len(ws.tables[t]) / 1e6, 1)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 3 else (next(iter(ws.tables), None))


def label_for_table(ws: Workspace, table: str) -> str | None:
    return label_column({"schema": ws.schemas[table]})


def dimension_options(ws: Workspace, fact: str) -> list[dict[str, Any]]:
    """Dimensions usable with the fact table: its own low-cardinality columns plus those of directly joined tables."""
    from .relationships import join_path
    out: list[dict[str, Any]] = []
    for t in ws.tables:
        if t != fact and join_path(ws.relationships, fact, t) is None:
            continue
        for c in ws.schemas[t]:
            if c["role"] == "dimension" and c["kind"] != "boolean" and 1 < c["unique"] <= MAX_DIM_CARDINALITY:
                out.append({"table": t, "column": c["name"], "unique": c["unique"]})
    return out


def entity_value_index(ws: Workspace) -> dict[str, list[tuple[str, str]]]:
    """lower-cased categorical value -> [(table, column)], used to resolve 'North', 'Consumer', 'Electronics' in questions."""
    idx: dict[str, list[tuple[str, str]]] = {}
    for t, df in ws.tables.items():
        for c in ws.schemas[t]:
            if c["role"] == "dimension" and c["kind"] == "text" and 1 < c["unique"] <= 200:
                for v in df[c["name"]].dropna().astype(str).unique():
                    if len(v) >= 2 and not v.replace(".", "").isdigit():
                        idx.setdefault(v.lower(), []).append((t, c["name"]))
    return idx
