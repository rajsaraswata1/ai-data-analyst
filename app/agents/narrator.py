"""Deterministic narration: turns computed facts into the answer + insight bullets. Every number comes from facts."""
from __future__ import annotations

from typing import Any

from ..services.formatting import num, pct


def noun(col: str) -> str:
    return col[:-5] if col.endswith("_name") else col.replace("_", " ")


def plural(word: str) -> str:
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word if word.endswith("s") else word + "s"


def metric_label(metric: dict[str, Any]) -> str:
    col = (metric.get("column") or metric["table"]).replace("_", " ")
    return {"sum": f"total {col}", "avg": f"average {col}", "min": f"minimum {col}", "max": f"maximum {col}",
            "count": f"number of {col} records", "count_distinct": f"number of unique {col.replace(' id', '')}s"}[metric["agg"]]


def filter_text(plan: dict[str, Any]) -> str:
    parts = [f"{f['column'].replace('_', ' ')} = {', '.join(f['values']) if f.get('values') else f['value']}" for f in plan.get("filters", [])]
    if plan.get("year"):
        parts.append(f"year {plan['year']}")
    return f" (filtered: {'; '.join(parts)})" if parts else ""


def grouped(plan: dict[str, Any], f: dict[str, Any], fv) -> tuple[str, list[str]]:
    dim_noun = noun(plan["dimension"]["column"])
    ml = metric_label(plan["metric"])
    top, tv, n = f["top_label"], f["top_value"], f["n_groups"]
    share = f.get("top_share_pct")
    sh = f", {share:.1f}% of the overall {ml}" if share is not None else ""
    ins: list[str] = []
    if f.get("underperform"):
        u = f["under"]
        ans = (f"{u['n_flagged']} of {u['n_total']} {plural(dim_noun)} are underperforming: their {ml} is at or below the "
               f"25th percentile ({fv(u['q1'])}). The weakest is {u['worst_label']} with {fv(u['worst_value'])}.")
        ins += [f"Together the underperformers account for only {pct(u['flagged_share_pct'])} of {ml}.",
                f"The median {dim_noun} achieves {fv(u['median'])}; {u['worst_label']} is {abs(u['worst_vs_median_pct']):.0f}% below that."]
        if u["n_flagged"] > 1:
            ins.append("Weakest: " + ", ".join(f"{r[0]} ({fv(r[1])})" for r in u["names"][:5]) + ".")
        return ans, ins
    if plan["mode"] == "rank" and plan.get("limit") and plan["limit"] > 1:
        rows = f["rows"][: plan["limit"]]
        word = "bottom" if plan["order"] == "asc" else "top"
        ans = f"The {word} {len(rows)} {plural(dim_noun)} by {ml}: " + "; ".join(f"{i}. {r[0]} ({fv(r[1])})" for i, r in enumerate(rows, 1)) + "."
        if f.get("topn_share_pct") is not None:
            ins.append(f"These {len(rows)} account for {pct(f['topn_share_pct'])} of the overall {ml}.")
    elif plan["mode"] == "rank":
        word = "lowest" if plan["order"] == "asc" else "highest"
        ans = f"{top} has the {word} {ml}: {fv(tv)}{sh}."
        ins.append(f"Compared across {n} {plural(dim_noun)}.")
    elif plan["mode"] == "compare":
        ans = (f"{top} leads on {ml} with {fv(tv)}{sh}; {f['bottom_label']} is lowest with {fv(f['bottom_value'])}"
               + (f" (a gap of {fv(tv - f['bottom_value'])})." if n > 1 else "."))
    else:
        ans = f"{ml.capitalize()} by {dim_noun}: {top} is highest at {fv(tv)}{sh}; {f['bottom_label']} is lowest at {fv(f['bottom_value'])}."
    if f.get("top_vs_second_pct") is not None and n > 1:
        ins.append(f"{top} is {pct(f['top_vs_second_pct'])} ahead of {f['second_label']} ({fv(f['second_value'])}).")
    if f.get("top_vs_bottom_ratio") and n > 2:
        ins.append(f"The best {dim_noun} is {f['top_vs_bottom_ratio']:.1f}x the weakest ({f['bottom_label']}).")
    if f.get("top3_share_pct") is not None and n > 4:
        ins.append(f"The top 3 {plural(dim_noun)} contribute {pct(f['top3_share_pct'])} of the overall {ml}.")
    return ans, ins


def single(plan: dict[str, Any], value: float, fv) -> tuple[str, list[str]]:
    return f"The {metric_label(plan['metric'])} is {fv(value)}{filter_text(plan)}.", []


def trend(plan: dict[str, Any], s: dict[str, Any], fv) -> tuple[str, list[str]]:
    ml = metric_label(plan["metric"])
    grain = plan["time"]["grain"]
    ans = (f"{ml.capitalize()} over {s['n']} {grain}s ({s['first_period']} to {s['last_period']}): total {fv(s['total'])}, "
           f"peaking in {s['peak_period']} at {fv(s['peak_value'])}.")
    ins = []
    if "direction" in s:
        ins.append(f"Linear trend: {s['direction']} (slope {fv(s['slope'])} per {grain}, R²={s['r_squared']:.2f}, p={s['p_value']:.3g}).")
    if s.get("last_change_pct") is not None:
        ins.append(f"The latest {grain} ({s['last_period']}) changed {pct(s['last_change_pct'], True)} versus the previous one.")
    if s.get("recent_vs_prior_pct") is not None:
        ins.append(f"The last 3 {grain}s averaged {pct(s['recent_vs_prior_pct'], True)} versus the 3 before.")
    ins.append(f"Lowest point: {s['trough_period']} ({fv(s['trough_value'])}).")
    return ans + filter_text(plan), ins


def drivers(plan: dict[str, Any], d: dict[str, Any], fv) -> tuple[str, list[str]]:
    ml = metric_label(plan["metric"])
    word = "fell" if d["delta"] < 0 else "rose"
    ans = (f"{ml.capitalize()} {word} {pct(abs(d['pct']))} from {d['prev']} ({fv(d['prev_total'])}) to {d['cur']} ({fv(d['cur_total'])}), "
           f"a change of {'-' if d['delta'] < 0 else '+'}{fv(abs(d['delta']))}.")
    if d.get("direction_note"):
        ans += " " + d["direction_note"]
    ins = []
    for b in d["breakdowns"]:
        top = b["main"]
        ins.append(f"By {b['dimension']}: {top['label']} contributed {'-' if top['delta'] < 0 else '+'}{fv(abs(top['delta']))}"
                   f" ({pct(top['pct_change'], True) if top['pct_change'] is not None else 'new'}; "
                   f"{pct(abs(top['share_of_change_pct']))} of the net change).")
    ins.append("This shows where the change occurred in the data; it cannot identify external causes.")
    return ans, ins


def anomalies(plan: dict[str, Any], r: dict[str, Any], fv) -> tuple[str, list[str]]:
    if not r["total"]:
        return (f"No anomalies were found in {r['metric']} ({r['rows_scanned']:,} rows scanned) using the {r['method']} rule.", [])
    top = r["anomalies"][0]
    ans = (f"{r['total']} anomalous rows were flagged in {r['metric']} out of {r['rows_scanned']:,} scanned. "
           f"The most extreme is {top.get('id') or 'row ' + str(top['row'])} with {fv(top['actual'])} "
           f"(expected {fv(top['expected_low'])} – {fv(top['expected_high'])}).")
    ins = [f"Method: {r['rule']}.", top["reason"]]
    if r["isolation_forest_used"]:
        ins.append(f"An Isolation Forest over {', '.join(r['features'])} was also used to rank rows and catch unusual combinations.")
    return ans, ins


def forecast(plan: dict[str, Any], r: dict[str, Any], fv) -> tuple[str, list[str]]:
    f = r["forecast"]
    total, lo, hi = sum(f["point"]), sum(f["lower"]), sum(f["upper"])
    bt = r["backtest"][r["model"]]
    ans = (f"Estimated {metric_label(plan['metric'])} for the next {r['horizon']} months ({f['periods'][0]} to {f['periods'][-1]}): "
           f"about {fv(total)} in total (rough 95% range {fv(lo)} to {fv(hi)}). This is a statistical estimate, not a guarantee.")
    ins = [f"Model chosen by backtest: {r['model'].replace('_', ' ')} (MAE {fv(bt['mae'])}, MAPE {bt['mape_pct']:.1f}% on the last {r['backtest_months']} months).",
           f"Next month ({f['periods'][0]}): {fv(f['point'][0])} (range {fv(f['lower'][0])} – {fv(f['upper'][0])})."] + r["notes"]
    return ans, ins
