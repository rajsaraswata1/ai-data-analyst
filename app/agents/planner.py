"""Intent classification + plan construction. Rule-based by default; an LLM may refine the plan, but every field it
returns is validated against the real schema/values before use (invalid fields are dropped)."""
from __future__ import annotations

import re
from typing import Any

from ..core.errors import UnsupportedQuestion
from ..services import catalog
from ..services.relationships import join_path
from ..services.semantic import expand, stem, tokens
from ..services.registry import Workspace
from ..tools.viz_tool import CHART_TYPES

MODES = ["rank", "aggregate", "compare", "trend", "drivers", "anomaly", "forecast", "quality", "insights",
         "distribution", "scatter", "schema", "sql_recall"]
INTENT = {"rank": "RANKING", "aggregate": "AGGREGATION", "compare": "COMPARISON", "trend": "TREND_ANALYSIS",
          "drivers": "TREND_ANALYSIS", "anomaly": "ANOMALY_DETECTION", "forecast": "FORECAST", "quality": "DATA_QUALITY",
          "insights": "INSIGHT_GENERATION", "distribution": "VISUALIZATION", "scatter": "VISUALIZATION",
          "schema": "DATA_QUERY", "sql_recall": "SQL_GENERATION"}
NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "twelve": 12, "twenty": 20}
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())} | {"sept": 9})
AGGS = {"sum", "avg", "min", "max", "count", "count_distinct"}
GRAINS = {"day", "week", "month", "quarter", "year"}
_N = r"(\d+|" + "|".join(NUMWORDS) + r")"
ASC_RE = re.compile(r"\b(bottom|worst|lowest|smallest|least|under-?perform\w*|poor\w*|weak\w*|laggard\w*|low[- ]perform\w*|fewest)\b")


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else NUMWORDS[tok]


def classify(ql: str) -> str | None:
    s = lambda p: re.search(p, ql)
    if s(r"\b(sql|queries|query)\b") and s(r"\b(generate|write|show|give|create|provide|see|copy|display)\b"):
        return "sql_recall"
    if s(r"\bwhy\b|\breasons?\b|\bcaused?\b|\bdrivers?\b") and s(r"drop|fall|fell|declin|decreas|dip|down|increas|rise|rose|grew|growth|spike|jump|surge|change"):
        return "drivers"
    if s(r"anomal|outlier|unusual|suspicious|abnormal|irregular"):
        return "anomaly"
    if s(r"forecast|predict|projection|\bproject(ed|ions?)?\b|next \w+ (month|quarter|year|week)s?|\bfuture\b"):
        return "forecast"
    if s(r"data quality|quality|missing|\bnulls?\b|duplicate|dirty|completeness|data issues"):
        return "quality"
    if s(r"executive summary|\bsummary\b|summari[sz]e|overview|insights?\b|key findings|tell me about|business health|\breport\b"):
        return "insights"
    if s(r"distribution|histogram|spread of"):
        return "distribution"
    if s(r"scatter|correlat|relationship between"):
        return "scatter"
    if s(r"trend|over time|monthly|weekly|daily|quarterly|yearly|annual|by month|per month|growth|seasonal|month over month"):
        return "trend"
    if s(r"compar|versus|\bvs\.?\b|difference between|\bagainst\b"):
        return "compare"
    if s(r"\b(top|bottom|best|worst|highest|lowest|most|least|rank\w*|largest|smallest|biggest|leading|laggard\w*|under-?perform\w*|poor\w*|weak\w*)\b"):
        return "rank"
    if s(r"\b(columns?|schema|fields?|what data|which datasets?|tables?|describe|structure|what (do|can) (i|you))\b"):
        return "schema"
    if s(r"\b(total|sum|average|avg|mean|count|how many|number of|by|per|each|breakdown|split)\b"):
        return "aggregate"
    return None


def _entities(ql: str, ws: Workspace) -> list[dict[str, Any]]:
    """Categorical values mentioned in the question (e.g. 'North', 'Electronics') -> filters."""
    found: dict[tuple[str, str], list[str]] = {}
    for value_l, owners in sorted(catalog.entity_value_index(ws).items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z0-9]){re.escape(value_l)}(?![a-z0-9])", ql):
            table, col = owners[0]
            for t, c in owners:
                df = ws.tables[t]
                orig = next((v for v in df[c].dropna().astype(str).unique() if v.lower() == value_l), None)
                if orig and (t, c) == (table, col):
                    found.setdefault((t, c), [])
                    if orig not in found[(t, c)]:
                        found[(t, c)].append(orig)
    out = []
    for (t, c), vals in found.items():
        out.append({"table": t, "column": c, "values": vals} if len(vals) > 1 else {"table": t, "column": c, "value": vals[0]})
    return out


def _resolve_metric(ql: str, qstems: set[str], ws: Workspace, fact: str, last: dict | None, followup: bool):
    assumptions: list[str] = []
    agg = "avg" if re.search(r"\b(average|avg|mean)\b", ql) else "sum"
    if re.search(r"\b(how many|number of|count( of)?|no\. of)\b", ql):
        for t in [fact] + [x for x in ws.tables if x != fact]:
            for c in ws.schemas[t]:
                base = {stem(x) for x in tokens(c["name"])} - {"id", "key", "code"}
                if c["role"] == "id" and base & qstems:
                    return {"table": t, "column": c["name"], "agg": "count_distinct"}, [], True
        return {"table": fact, "column": None, "agg": "count"}, [f"Counted rows in '{fact}'."], True
    hits = ws.semantic.search(ql, roles={"measure"}, top_k=4) if ws.semantic else []
    hits = [h for h in hits if h["score"] >= 0.8]
    if hits:
        best = max(hits, key=lambda h: (h["score"], h["table"] == fact))
        return {"table": best["table"], "column": best["column"], "agg": agg}, assumptions, True
    if followup and last and last.get("metric"):
        return {**last["metric"], "agg": last["metric"].get("agg", agg) if agg == "sum" else agg}, [], False
    col = catalog.primary_metric(ws, fact)
    if col is None:
        raise UnsupportedQuestion(f"I couldn't find a numeric column to analyze in '{fact}'.")
    assumptions.append(f"No specific metric was named, so '{col}' (the main numeric measure in '{fact}') was used.")
    return {"table": fact, "column": col, "agg": agg}, assumptions, False


def _resolve_dimension(qstems: set[str], metric_stems: set[str], ws: Workspace, fact: str, ql: str,
                       filter_cols: set[tuple[str, str]] | None = None) -> dict[str, Any] | None:
    qs = qstems - metric_stems
    cands: list[tuple[float, str, str]] = []
    for t in ws.tables:
        if t != fact and join_path(ws.relationships, fact, t) is None:
            continue
        tstem = {stem(x) for x in tokens(t)}
        tstem_hit = bool(tstem & qs) or any(expand(x) & tstem for x in qs if x in {"customer", "client", "product", "item"})
        label = catalog.label_for_table(ws, t)
        if label and t != fact and tstem_hit:
            cands.append((0.95, t, label))
        for c in ws.schemas[t]:
            if c["role"] != "dimension" or c["kind"] == "boolean" or not 1 < c["unique"] <= 200:
                continue
            ctoks = {stem(x) for x in tokens(c["name"])}
            score = 0.0
            if ctoks & qs:
                score = 1.0 if ctoks <= qs else 0.9
            elif any(expand(x) & ctoks for x in qs):
                score = 0.8
            if score and tstem_hit:
                score += 0.1
            if score:
                cands.append((score, t, c["name"]))
    if not any(c[0] >= 0.9 for c in cands):
        # no descriptive column: fall back to grouping by an identifier ("products" -> product_id)
        for t in ws.tables:
            if t != fact and join_path(ws.relationships, fact, t) is None:
                continue
            for c in ws.schemas[t]:
                base = {stem(x) for x in tokens(c["name"])} - {"id", "key", "code", "sku"}
                if c["role"] == "id" and base and base & qs and 1 < c["unique"] <= 100000:
                    cands.append((0.85, t, c["name"]))
    if not cands:
        return None
    # "top 3 products in the North region": 'region' only qualifies the filter, so don't group by it if anything else matches
    others = [x for x in cands if (x[1], x[2]) not in (filter_cols or set())]
    cands = others or cands
    score, t, c = max(cands, key=lambda x: (x[0], x[1] == fact))
    return {"table": t, "column": c, "score": score}


def _noun(col: str) -> str:
    return col[:-5] if col.endswith("_name") else col.replace("_", " ")


class RulePlanner:
    def plan(self, question: str, ws: Workspace, last: dict | None) -> dict[str, Any]:
        ql = " ".join(question.lower().split())
        if not ws.tables:
            raise UnsupportedQuestion("Upload at least one CSV file first, then ask me about it.")
        fact = catalog.fact_table(ws)
        mode = classify(ql)
        entities = _entities(ql, ws)
        pronoun = bool(re.search(r"\b(its|it|their|them|that|those|these|same|there|this one)\b", ql))
        followup = bool(last) and (pronoun or (mode in (None, "trend", "rank", "aggregate") and len(ql.split()) <= 7))
        if mode is None and last and (entities or pronoun):
            mode = last["mode"] if last["mode"] not in ("sql_recall", "schema", "quality") else "aggregate"
        if mode is None and ws.semantic and any(h["score"] >= 0.8 for h in ws.semantic.search(ql, roles={"measure"}, top_k=2)):
            mode = "aggregate"   # e.g. "revenue in 2025"
        if mode is None:
            raise UnsupportedQuestion("I couldn't map that question to an analysis I can run.")
        qstems = {stem(t) for t in tokens(ql)}
        plan: dict[str, Any] = {"question": question, "mode": mode, "intent": INTENT[mode], "table": fact, "followup": followup,
                                "assumptions": [], "inherited": [], "filters": [], "limit": None, "order": None, "year": None,
                                "dimension": None, "time": None, "chart": None, "horizon": 6, "planner": "rules"}
        if mode in ("schema", "quality", "anomaly", "insights", "sql_recall"):
            if mode == "sql_recall":
                residue = set(tokens(ql)) - {"generate", "write", "show", "give", "create", "provide", "see", "copy", "display", "sql", "query",
                                              "queries", "for", "this", "that", "analysis", "me", "please", "it", "above", "last", "previous", "same"}
                plan["residue"] = bool(residue)
            if mode == "anomaly":
                plan["metric"] = _resolve_metric(ql, qstems, ws, fact, last, followup)[0] if ws.semantic else None
            return plan
        metric, assumptions, explicit = _resolve_metric(ql, qstems, ws, fact, last, followup)
        plan["metric"] = metric
        plan["assumptions"] += assumptions
        if not explicit and followup and last and last.get("metric"):
            plan["inherited"].append("metric")
        metric_stems = {stem(x) for x in tokens(metric["column"] or "")} | {stem(x) for x in tokens(metric["table"])} if explicit else set()
        # chart override
        for word, ch in (("pie", "pie"), ("donut", "pie"), ("scatter", "scatter"), ("histogram", "histogram"),
                         ("bar chart", "bar"), ("line chart", "line"), ("line graph", "line")):
            if word in ql:
                plan["chart"] = ch
        plan["share"] = bool(re.search(r"\b(share|proportion|percentage|breakdown|composition|contribution|split)\b", ql)) or plan["chart"] == "pie"
        # filters: explicit entities, then pronoun-resolved entity from memory
        plan["filters"] = entities
        if followup and last and not entities and pronoun and last.get("top_entity"):
            te = last["top_entity"]
            plan["filters"] = [{"table": te["table"], "column": te["column"], "value": te["value"]}]
            plan["inherited"].append(f"{te['column']}={te['value']}")
        elif followup and last and last.get("filters") and not pronoun and not explicit and not explicit_dimension_mention(qstems, ws):
            keep = [f for f in last["filters"] if f["column"] not in {e["column"] for e in entities}]
            plan["filters"] = keep + entities
        y = re.search(r"\b(?:in|for|during|of|from|year)\s+(20\d{2}|19\d{2})\b", ql)
        plan["year"] = y.group(1) if y else None
        if plan["year"]:
            plan["year_col"] = self._date_col(ws, metric, fact)
            if plan["year_col"] is None:
                plan["year"] = None
                plan["assumptions"].append("A year was mentioned but no date column exists, so the year filter was ignored.")
        # top-N and ordering
        m = re.search(rf"\b(top|bottom|best|worst|highest|lowest|largest|smallest|first)\s+{_N}\b", ql) or \
            re.search(rf"\b{_N}\s+(best|worst|top|bottom|highest|lowest|largest|smallest)\b", ql)
        if m:
            n = next(g for g in m.groups() if g and (g.isdigit() or g in NUMWORDS))
            plan["limit"] = _num(n)
        plan["order"] = "asc" if ASC_RE.search(ql) else "desc"
        plan["underperform"] = bool(re.search(r"under-?perform|poor|weak|laggard|low[- ]perform|struggl", ql))
        # grouping dimension
        cue = bool(re.search(r"\b(by|per|each|across|between|every|which|top|bottom|best|worst|highest|lowest|rank\w*|compare|versus|vs)\b", ql))
        dim = _resolve_dimension(qstems, metric_stems, ws, fact, ql, {(e['table'], e['column']) for e in entities}) if mode in ("rank", "compare", "aggregate", "drivers", "trend") else None
        if dim and (mode in ("rank", "compare") or (cue and dim["score"] >= 0.9)):
            plan["dimension"] = {"table": dim["table"], "column": dim["column"]}
        elif mode == "trend" and dim and re.search(r"\bby\b|\beach\b|\bper\b(?! month)", ql) and dim["score"] >= 0.9:
            plan["dimension"] = {"table": dim["table"], "column": dim["column"]}
        # a filter on the grouped column from a comparison ("compare North and South") defines the dimension
        if mode == "compare" and plan["dimension"] is None:
            multi = next((f for f in entities if f.get("values")), None)
            if multi:
                plan["dimension"] = {"table": multi["table"], "column": multi["column"]}
        if plan["dimension"] is None and followup and last and last.get("dimension") and mode in ("rank", "aggregate", "compare") and (pronoun or mode != "aggregate") and not explicit_dimension_mention(qstems, ws):
            plan["dimension"] = last["dimension"]
            plan["inherited"].append("dimension")
        if mode in ("rank", "compare") and plan["dimension"] is None:
            dims = catalog.dimension_options(ws, fact)
            names = ", ".join(sorted({d["column"] for d in dims})[:8]) or "none found"
            raise UnsupportedQuestion(f"I need something to group by. Available categories: {names}. Try e.g. “{metric['column'] or 'revenue'} by {dims[0]['column'] if dims else 'region'}”.")
        if plan["dimension"] and (catalog.col_info(ws, plan["dimension"]["table"], plan["dimension"]["column"]) or {}).get("role") == "id":
            plan["assumptions"].append(f"No descriptive name column was found, so results are grouped by the identifier '{plan['dimension']['column']}'. "
                                       "Upload the matching lookup file (e.g. products.csv) to see names.")
        # single-dimension entity filter that equals the grouped dimension is redundant for rank; keep for others
        if mode == "rank" and plan["dimension"] and plan["limit"] is None and not plan["underperform"]:
            card = next((c["unique"] for c in ws.schemas[plan["dimension"]["table"]] if c["name"] == plan["dimension"]["column"]), 10)
            plan["limit"] = None if card <= 15 else 10
        if mode == "rank" and plan["underperform"]:
            plan["order"], plan["limit"] = "asc", None
        # time dimension
        if mode in ("trend", "forecast", "drivers"):
            plan["time"] = self._time(ql, ws, metric, fact, plan)
        if mode == "drivers":
            self._drivers(ql, ws, plan)
        if mode == "forecast":
            plan["horizon"] = self._horizon(ql)
        if mode in ("distribution", "scatter"):
            self._measure_pair(ql, ws, plan, fact)
        return plan

    def _date_col(self, ws: Workspace, metric: dict, fact: str) -> dict[str, Any] | None:
        tables = [metric["table"]] + [t for t in ws.tables if t != metric["table"] and join_path(ws.relationships, metric["table"], t) is not None]
        for t in tables:
            dates = catalog.date_columns(ws, t)
            if dates:
                return {"table": t, "column": next((d for d in dates if "order" in d or "sale" in d or "transaction" in d), dates[0])}
        return None

    def _time(self, ql: str, ws: Workspace, metric: dict, fact: str, plan: dict) -> dict[str, Any]:
        grain = "month"
        for pat, g in ((r"\b(daily|per day|by day|each day)\b", "day"), (r"\b(weekly|per week|by week)\b", "week"),
                       (r"\b(quarterly|per quarter|by quarter)\b", "quarter"), (r"\b(yearly|annual\w*|per year|by year)\b", "year")):
            if re.search(pat, ql):
                grain = g
        tables = [metric["table"]] + [t for t in ws.tables if t != metric["table"] and join_path(ws.relationships, metric["table"], t) is not None]
        for t in tables:
            dates = catalog.date_columns(ws, t)
            if dates:
                col = next((d for d in dates if "order" in d or "sale" in d or "transaction" in d), dates[0])
                return {"table": t, "column": col, "grain": grain}
        raise UnsupportedQuestion("This analysis needs a date column, but none was found in the uploaded data.")

    def _horizon(self, ql: str) -> int:
        m = re.search(rf"(?:next|coming|following)?\s*{_N}\s*(month|quarter|year|week)s?\b", ql)
        if m:
            n, unit = _num(m.group(1)), m.group(2)
            return max(1, min(24, n * {"month": 1, "quarter": 3, "year": 12, "week": 1}[unit] if unit != "week" else max(1, n // 4)))
        m = re.search(r"next\s+(month|quarter|year)\b", ql)
        return {"month": 1, "quarter": 3, "year": 12}[m.group(1)] if m else 6

    def _drivers(self, ql: str, ws: Workspace, plan: dict) -> None:
        tm = plan["time"]
        mm = next((MONTHS[w] for w in re.findall(r"[a-z]+", ql) if w in MONTHS and (w != "may" or re.search(r"\bin may\b|may \d{4}", ql))), None)
        if mm is None:
            raise UnsupportedQuestion("Tell me which month to investigate, e.g. “Why did revenue fall in March?”.")
        series = ws.tables[tm["table"]][tm["column"]].dropna()
        want_year = int(plan["year"]) if plan.get("year") else None
        years = sorted({d.year for d in series if d.month == mm}, reverse=True)
        years = [y for y in years if (want_year is None or y == want_year)]
        if not years:
            raise UnsupportedQuestion("There is no data for that month in the uploaded datasets.")
        cur = f"{years[0]}-{mm:02d}"
        prev = f"{years[0] - 1}-12" if mm == 1 else f"{years[0]}-{mm - 1:02d}"
        plan["drivers"] = {"cur": cur, "prev": prev}
        plan["year"] = None
        plan["time"] = {**tm, "grain": "month"}
        if want_year is None and len(years) > 1:
            plan["assumptions"].append(f"No year was given, so the most recent {cur} was analysed.")

    def _measure_pair(self, ql: str, ws: Workspace, plan: dict, fact: str) -> None:
        measures = [c["name"] for c in catalog.measure_columns(ws, fact)]
        hits = [h for h in (ws.semantic.search(ql, roles={"measure"}, top_k=4) if ws.semantic else []) if h["table"] == fact and h["score"] >= 0.8]
        names = list(dict.fromkeys(h["column"] for h in hits))
        plan["scatter"] = {"table": fact}
        if plan["mode"] == "distribution":
            plan["scatter"]["x"] = names[0] if names else (plan["metric"]["column"] or measures[0])
            return
        for m in measures:
            if len(names) >= 2:
                break
            if m not in names:
                names.append(m)
        if len(names) < 2:
            raise UnsupportedQuestion("A scatter plot needs two numeric columns.")
        plan["scatter"]["x"], plan["scatter"]["y"] = names[0], names[1]
        plan["assumptions"].append(f"Plotted {names[1]} against {names[0]}.")


def explicit_dimension_mention(qstems: set[str], ws: Workspace) -> bool:
    for t in ws.tables:
        for c in ws.schemas[t]:
            if c["role"] == "dimension" and {stem(x) for x in tokens(c["name"])} & qstems:
                return True
    return False


def validate_llm_plan(js: dict[str, Any] | None, ws: Workspace, base: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merge an LLM-proposed plan into `base`, keeping only fields that validate against the real schema/values."""
    if not isinstance(js, dict):
        return base, ["no usable LLM plan"]
    plan, rejected = dict(base), []

    def col_ok(t, c, roles=None):
        info = catalog.col_info(ws, t, c) if t in ws.tables else None
        return info is not None and (roles is None or info["role"] in roles)

    if js.get("mode") in MODES and js["mode"] not in ("sql_recall",):
        plan["mode"], plan["intent"] = js["mode"], INTENT[js["mode"]]
    m = js.get("metric")
    if isinstance(m, dict) and m.get("agg") in AGGS and (col_ok(m.get("table"), m.get("column"), {"measure", "id"}) or (m.get("column") is None and m.get("table") in ws.tables)):
        plan["metric"] = {"table": m["table"], "column": m.get("column"), "agg": m["agg"]}
    elif m is not None:
        rejected.append("metric")
    d = js.get("dimension")
    if isinstance(d, dict) and col_ok(d.get("table"), d.get("column"), {"dimension", "text"}):
        plan["dimension"] = {"table": d["table"], "column": d["column"]}
    elif d is not None:
        rejected.append("dimension")
    if isinstance(js.get("filters"), list):
        good = []
        for f in js["filters"]:
            if isinstance(f, dict) and col_ok(f.get("table"), f.get("column"), {"dimension"}) and \
               str(f.get("value")) in set(ws.tables[f["table"]][f["column"]].dropna().astype(str)):
                good.append({"table": f["table"], "column": f["column"], "value": str(f["value"])})
            else:
                rejected.append("filter")
        if good:
            plan["filters"] = good
    if js.get("time_grain") in GRAINS and plan.get("time"):
        plan["time"] = {**plan["time"], "grain": js["time_grain"]}
    if js.get("order") in ("asc", "desc"):
        plan["order"] = js["order"]
    if isinstance(js.get("limit"), int) and 1 <= js["limit"] <= 1000:
        plan["limit"] = js["limit"]
    if isinstance(js.get("horizon"), int) and 1 <= js["horizon"] <= 24:
        plan["horizon"] = js["horizon"]
    if js.get("chart") in CHART_TYPES:
        plan["chart"] = js["chart"]
    if plan["mode"] in ("trend", "forecast", "drivers") and not plan.get("time") and base.get("time"):
        plan["time"] = base["time"]
    if plan["mode"] in ("rank", "compare") and not plan.get("dimension"):
        return base, rejected + ["mode requires a dimension"]
    plan["planner"] = "llm+rules"
    return plan, rejected
