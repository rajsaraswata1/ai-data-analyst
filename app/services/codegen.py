"""Generate an *illustrative, equivalent* pandas snippet for a plan (template-based; never executed by the app)."""
from __future__ import annotations

from typing import Any

from .query_builder import metric_alias
from .relationships import join_path

PD_AGG = {"sum": "sum", "avg": "mean", "min": "min", "max": "max", "count": "count", "count_distinct": "nunique"}
GRAIN = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}


def pandas_snippet(plan: dict[str, Any], rels: list[dict[str, Any]], meta: dict[str, dict[str, Any]], schemas: dict[str, Any]) -> str:
    metric, dim, tm = plan["metric"], plan.get("dimension"), plan.get("time")
    fact = metric["table"]
    needed = {fact} | ({dim["table"]} if dim else set()) | ({tm["table"]} if tm else set()) | ({plan["year_col"]["table"]} if plan.get("year_col") and plan.get("year") else set()) | {f["table"] for f in plan.get("filters", [])}
    lines = ["import pandas as pd", ""]
    for t in sorted(needed):
        dates = [c["name"] for c in schemas[t] if c["role"] == "date"]
        parse = f", parse_dates={dates}" if dates else ""
        lines.append(f"{t} = pd.read_csv({meta[t]['filename']!r}{parse})")
        lines.append(f"{t}.columns = {t}.columns.str.strip().str.lower().str.replace(r'[^0-9a-z]+', '_', regex=True).str.strip('_')")
    lines.append("")
    lines.append(f"df = {fact}")
    cur = {fact}
    for target in sorted(needed - {fact}):
        for r in join_path(rels, fact, target) or []:
            nxt = r["parent_table"] if r["child_table"] in cur else r["child_table"]
            if nxt in cur:
                continue
            ck = r["child_column"] if r["child_table"] in cur else r["parent_column"]
            nk = r["parent_column"] if r["child_table"] in cur else r["child_column"]
            lines.append(f"df = df.merge({nxt}, left_on={ck!r}, right_on={nk!r}, how='left', suffixes=('', '_{nxt}'))")
            cur.add(nxt)
    for f in plan.get("filters", []):
        if f.get("values"):
            lines.append(f"df = df[df[{f['column']!r}].isin({f['values']!r})]")
        else:
            lines.append(f"df = df[df[{f['column']!r}] == {f['value']!r}]")
    yc = plan.get("year_col") or tm
    if plan.get("year") and yc:
        lines.append(f"df = df[df[{yc['column']!r}].dt.year == {int(plan['year'])}]")
    keys = []
    if tm:
        lines.append(f"df['period'] = df[{tm['column']!r}].dt.to_period({GRAIN[tm['grain']]!r}).astype(str)")
        keys.append("'period'")
    if dim:
        keys.append(repr(dim["column"]))
    alias, how = metric_alias(metric), PD_AGG[metric["agg"]]
    col = metric["column"] or fact
    if keys:
        lines.append(f"df = df.dropna(subset=[{', '.join(keys)}])")
        agg_part = f"{col!r}" if metric["column"] else "df.columns[0]"
        lines.append(f"result = (df.groupby([{', '.join(keys)}])[{agg_part}].{how}().round(2).rename({alias!r}).reset_index())")
        if tm:
            lines.append("result = result.sort_values('period')")
        else:
            asc = plan.get("order") == "asc"
            lines.append(f"result = result.sort_values({alias!r}, ascending={asc})" + (f".head({plan['limit']})" if plan.get("limit") else ""))
    else:
        lines.append(f"result = round(df[{col!r}].{how}(), 2)" if metric["column"] else "result = len(df)")
    lines.append("print(result)")
    return "\n".join(lines)
