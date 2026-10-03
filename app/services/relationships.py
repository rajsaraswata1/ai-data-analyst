"""Automatic join/relationship detection between uploaded tables.

A relationship is proposed when two columns have the same (or id-compatible) name and the child's values are
largely contained in the parent's key column (high containment, parent key ~unique).
"""
from __future__ import annotations

from collections import deque
from typing import Any

import pandas as pd

MIN_CONTAINMENT = 0.8
MIN_PARENT_UNIQUE = 0.95


def detect_relationships(tables: dict[str, pd.DataFrame], schemas: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rels: list[dict[str, Any]] = []
    names = list(tables)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for ca in schemas[a]:
                for cb in schemas[b]:
                    if ca["name"] != cb["name"] or ca["role"] in ("measure", "date", "empty") or cb["role"] in ("measure", "date", "empty"):
                        continue
                    if ca["role"] == "text" and cb["role"] == "text":
                        continue
                    rel = _score(a, ca["name"], tables[a], b, cb["name"], tables[b])
                    if rel:
                        rels.append(rel)
    rels.sort(key=lambda r: -r["confidence"])
    return rels


def _score(a: str, col_a: str, df_a: pd.DataFrame, b: str, col_b: str, df_b: pd.DataFrame) -> dict[str, Any] | None:
    sa, sb = df_a[col_a].dropna().astype(str), df_b[col_b].dropna().astype(str)
    if sa.empty or sb.empty:
        return None
    ua, ub = sa.nunique() / len(sa), sb.nunique() / len(sb)
    # parent = side whose key is (almost) unique
    if ub >= MIN_PARENT_UNIQUE and ua <= ub:
        child, child_col, parent, parent_col, child_vals, parent_vals = a, col_a, b, col_b, sa, sb
    elif ua >= MIN_PARENT_UNIQUE:
        child, child_col, parent, parent_col, child_vals, parent_vals = b, col_b, a, col_a, sb, sa
    else:
        return None
    containment = float(child_vals.isin(set(parent_vals)).mean())
    if containment < MIN_CONTAINMENT:
        return None
    return {"child_table": child, "child_column": child_col, "parent_table": parent, "parent_column": parent_col,
            "cardinality": "many-to-one", "containment": round(containment, 4), "confidence": round(0.5 + 0.5 * containment, 3),
            "description": f"{child}.{child_col} → {parent}.{parent_col} ({containment:.1%} of {child} values match)"}


def join_path(rels: list[dict[str, Any]], start: str, goal: str) -> list[dict[str, Any]] | None:
    """Shortest chain of relationships connecting two tables (BFS, undirected). [] if same table, None if unreachable."""
    if start == goal:
        return []
    graph: dict[str, list[tuple[str, dict]]] = {}
    for r in rels:
        graph.setdefault(r["child_table"], []).append((r["parent_table"], r))
        graph.setdefault(r["parent_table"], []).append((r["child_table"], r))
    seen, queue = {start}, deque([(start, [])])
    while queue:
        node, path = queue.popleft()
        for nxt, rel in graph.get(node, []):
            if nxt in seen:
                continue
            if nxt == goal:
                return path + [rel]
            seen.add(nxt)
            queue.append((nxt, path + [rel]))
    return None
