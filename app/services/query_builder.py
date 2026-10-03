"""Deterministic SQL builder. The plan (metric, dimension, filters, time grain) is turned into read-only SQL
with automatic LEFT JOINs along detected relationships. Identifiers are quoted; literals escaped."""
from __future__ import annotations

from typing import Any

from ..core.errors import AppError
from .relationships import join_path

AGG_SQL = {"sum": "SUM({c})", "avg": "AVG({c})", "min": "MIN({c})", "max": "MAX({c})",
           "count": "COUNT({c})", "count_distinct": "COUNT(DISTINCT {c})"}
AGG_ALIAS = {"sum": "total_{c}", "avg": "avg_{c}", "min": "min_{c}", "max": "max_{c}", "count": "count_{c}",
             "count_distinct": "unique_{c}"}


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def lit(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ref(table: str, col: str) -> str:
    return f"{q(table)}.{q(col)}"


def time_expr(table: str, col: str, grain: str) -> str:
    c = ref(table, col)
    return {"day": f"strftime('%Y-%m-%d', {c})",
            "week": f"strftime('%Y-W%W', {c})",
            "month": f"strftime('%Y-%m', {c})",
            "quarter": f"(strftime('%Y', {c}) || '-Q' || ((CAST(strftime('%m', {c}) AS INTEGER) + 2) / 3))",
            "year": f"strftime('%Y', {c})"}[grain]


def metric_alias(metric: dict[str, Any]) -> str:
    if metric.get("column") is None:
        return f"count_{metric['table']}"
    return AGG_ALIAS[metric["agg"]].format(c=metric["column"])


def _joins(fact: str, needed: set[str], rels: list[dict[str, Any]]) -> list[str]:
    clauses, joined = [], {fact}
    for target in sorted(needed - {fact}):
        path = join_path(rels, fact, target)
        if path is None:
            raise AppError(f"No relationship was found between '{fact}' and '{target}', so they cannot be combined.",
                           400, "no_join")
        cur = fact
        for r in path:
            nxt = r["parent_table"] if cur == r["child_table"] else r["child_table"]
            cur_col = r["child_column"] if cur == r["child_table"] else r["parent_column"]
            nxt_col = r["parent_column"] if cur == r["child_table"] else r["child_column"]
            if nxt not in joined:
                clauses.append(f"LEFT JOIN {q(nxt)} ON {ref(cur, cur_col)} = {ref(nxt, nxt_col)}")
                joined.add(nxt)
            cur = nxt
    return clauses


def build_sql(plan: dict[str, Any], rels: list[dict[str, Any]], *, grouped: bool = True) -> str:
    """plan keys: metric{table,column,agg}, dimension{table,column}|None, time{table,column,grain}|None,
    filters[{table,column,value}], order('desc'|'asc'|None), limit, periods(list[str] restricting the time bucket)."""
    metric, dim, tm = plan["metric"], plan.get("dimension"), plan.get("time")
    fact = metric["table"]
    expr_col = "*" if metric.get("column") is None else ref(fact, metric["column"])
    agg = AGG_SQL[metric["agg"]].format(c=expr_col)
    if metric["agg"] in ("sum", "avg"):
        agg = f"ROUND({agg}, 2)"
    alias = metric_alias(metric)

    select, group, where = [], [], []
    needed = {fact}
    if grouped and tm:
        select.append(f"{time_expr(tm['table'], tm['column'], tm['grain'])} AS period")
        group.append(len(select))
        needed.add(tm["table"])
        where.append(f"{ref(tm['table'], tm['column'])} IS NOT NULL")
    if grouped and dim:
        select.append(f"{ref(dim['table'], dim['column'])} AS {q(dim['column'])}")
        group.append(len(select))
        needed.add(dim["table"])
        where.append(f"{ref(dim['table'], dim['column'])} IS NOT NULL")
    select.append(f"{agg} AS {alias}")

    for f in plan.get("filters", []):
        needed.add(f["table"])
        if f.get("values"):
            where.append(f"{ref(f['table'], f['column'])} IN ({', '.join(lit(v) for v in f['values'])})")
        else:
            where.append(f"{ref(f['table'], f['column'])} = {lit(f['value'])}")
    yc = plan.get("year_col") or tm
    if plan.get("year") and yc:
        where.append(f"strftime('%Y', {ref(yc['table'], yc['column'])}) = {lit(plan['year'])}")
        needed.add(yc["table"])
    if plan.get("periods") and tm:
        inner = ", ".join(lit(p) for p in plan["periods"])
        where.append(f"{time_expr(tm['table'], tm['column'], tm['grain'])} IN ({inner})")

    lines = ["SELECT " + ",\n       ".join(select), f"FROM {q(fact)}"]
    lines += _joins(fact, needed, rels)
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    if group:
        lines.append("GROUP BY " + ", ".join(str(g) for g in group))
        if plan.get("order") and not (tm and grouped):
            lines.append(f"ORDER BY {alias} {plan['order'].upper()}")
        elif tm:
            lines.append("ORDER BY " + ", ".join(str(g) for g in group))
        if plan.get("limit"):
            lines.append(f"LIMIT {int(plan['limit'])}")
    return "\n".join(lines)
