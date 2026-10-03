"""Analysis orchestrator: plan -> tool calls -> result validation -> narration -> structured response.

`run()` is a generator that yields real progress events as each step executes (used for SSE streaming), and finally
yields {"type": "result", ...}. Numbers always originate from SQL/pandas/scikit-learn tools.
"""
from __future__ import annotations

import re
import time
from typing import Any, Iterator

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..config import Config
from ..core.cache import TTLCache
from ..core.errors import AppError, UnsupportedQuestion
from ..core.logging_setup import log_event
from ..services import catalog, narrator_utils
from ..services.codegen import pandas_snippet
from ..services.formatting import money, num
from ..services.query_builder import build_sql
from ..services.sql_engine import split_statements
from ..services.registry import Workspace
from ..tools.base import ToolContext, ToolResult, ToolRouter
from . import narrator
from .llm import LLMProvider, numbers_supported
from .memory import ConversationMemory
from .planner import INTENT, RulePlanner, validate_llm_plan

MAX_TABLE_ROWS = 200
SQL_WORDS = r"\b(generate|write|show|give|create|provide|see|copy|display|sql|queries|query|for|this|that|analysis|me|please|the|above|previous|same)\b"


def status(msg: str) -> dict[str, Any]:
    return {"type": "status", "message": msg}


class Run:
    """Per-question execution context: tool calls with bookkeeping."""

    def __init__(self, router: ToolRouter, ctx: ToolContext):
        self.router, self.ctx = router, ctx
        self.tools_used: list[str] = []
        self.validation: list[str] = []
        self.facts: dict[str, Any] = {}

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        res = self.router.call(name, args, self.ctx)
        if name not in self.tools_used:
            self.tools_used.append(name)
        return res

    def sql(self, sql: str) -> pd.DataFrame:
        return self.call("run_sql", {"sql": sql}).data["frame"]


class Orchestrator:
    def __init__(self, cfg: Config, cache: TTLCache, router: ToolRouter, provider: LLMProvider):
        self.cfg, self.cache, self.router, self.provider = cfg, cache, router, provider
        self.planner = RulePlanner()

    # ------------------------------------------------------------------ entry point
    def run(self, ws: Workspace, question: str, state: dict[str, Any] | None, user_id: str, request_id: str) -> Iterator[dict[str, Any]]:
        t0 = time.perf_counter()
        mem = ConversationMemory(state)
        ctx = ToolContext(ws, self.cache, self.cfg, user_id, request_id)
        run = Run(self.router, ctx)
        plan: dict[str, Any] = {"question": question, "mode": None, "intent": None}
        llm_info = {"provider": self.provider.name, "used_for_planning": False, "used_for_narration": False}
        yield status("Understanding your question…")
        try:
            plan = self.planner.plan(question, ws, mem.last)
            if plan["mode"] == "sql_recall" and plan.get("residue"):
                plan = self.planner.plan(re.sub(SQL_WORDS, " ", question, flags=re.I), ws, mem.last)
                plan["question"] = question
            if self.provider.available and plan["mode"] != "sql_recall":
                yield status("Asking the LLM to refine the analysis plan…")
                js = self.provider.plan(question, self._catalog_for_llm(ws), mem.for_llm())
                plan, rejected = validate_llm_plan(js, ws, plan)
                llm_info["used_for_planning"] = plan["planner"] == "llm+rules"
                if rejected:
                    log_event("llm_plan_fields_rejected", request_id=request_id, rejected=rejected)
            yield status(f"Intent: {plan['intent'].replace('_', ' ').title()}")
            handler = getattr(self, f"_h_{plan['mode']}")
            result = yield from handler(plan, run, mem)
        except UnsupportedQuestion as exc:
            result = self._help(question, ws, exc.message)
        except AppError as exc:
            log_event("analysis_error", request_id=request_id, code=exc.code, detail=exc.detail[:300])
            result = self._base(plan, run, ws)
            result.update(ok=False, error=exc.message, answer=exc.message, follow_ups=self._suggest(ws))
        except Exception as exc:  # never leak internals
            log_event("analysis_crash", 40, request_id=request_id, error=repr(exc))
            result = self._base(plan, run, ws)
            result.update(ok=False, error="Something went wrong while analysing that. Please rephrase or try another question.",
                          answer="Something went wrong while analysing that. Please rephrase or try another question.")
        facts = run.facts
        if result.get("ok") and self.provider.available and facts:
            yield status("Drafting the explanation…")
            self._llm_narrate(question, result, facts, llm_info)
        result["question"] = question
        result["llm"] = llm_info
        result["tools_used"] = list(dict.fromkeys(ctx.calls))
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        if result.get("ok"):
            mem.update(plan, result.get("sql"), result.pop("_top_entity", None), result.pop("_entities", []), result.get("answer", ""))
            if mem.last is not None:
                mem.last["pandas_code"] = result.get("pandas_code")
        result.pop("_top_entity", None), result.pop("_entities", None)
        yield {"type": "result", "result": narrator_utils.jsonable(result), "state": mem.state}

    # ------------------------------------------------------------------ helpers
    def _base(self, plan: dict[str, Any], run: Run, ws: Workspace) -> dict[str, Any]:
        tables = {plan.get("table")} if plan.get("table") else set()
        for k in ("metric", "dimension", "time"):
            if plan.get(k):
                tables.add(plan[k]["table"])
        tables |= {f["table"] for f in plan.get("filters", [])}
        files = [ws.meta[t]["filename"] for t in sorted(t for t in tables if t in ws.meta)]
        return {"ok": True, "intent": plan.get("intent"), "mode": plan.get("mode"), "answer": "", "insights": [], "charts": [],
                "table": None, "sql": None, "pandas_code": None, "method": [], "sources": files,
                "assumptions": list(plan.get("assumptions", [])) + [f"Used {x} from the previous question." for x in plan.get("inherited", [])],
                "warnings": [], "validation": run.validation, "anomalies": None, "forecast": None, "follow_ups": [],
                "planner": plan.get("planner", "rules")}

    def _catalog_for_llm(self, ws: Workspace) -> dict[str, Any]:
        out = {}
        for t, cols in ws.schemas.items():
            out[t] = [{"name": c["name"], "role": c["role"], "kind": c["kind"],
                       **({"values": [v["value"] for v in c.get("top_values", [])][:12]} if c["role"] == "dimension" and c["unique"] <= 12 else {})}
                      for c in cols]
        return {"tables": out, "relationships": [r["description"] for r in ws.relationships]}

    def _llm_narrate(self, question: str, result: dict[str, Any], facts: dict[str, Any], info: dict[str, Any]) -> None:
        js = self.provider.narrate(question, facts)
        ok = isinstance(js, dict) and isinstance(js.get("answer"), str) and isinstance(js.get("insights", []), list)
        if ok:
            text = js["answer"] + " " + " ".join(str(i) for i in js.get("insights", []))
            if not numbers_supported(text, facts):
                ok = False
                info["rejected"] = "LLM text contained numbers not present in computed facts; deterministic text used instead."
                log_event("llm_narration_rejected", reason="unsupported_numbers")
        if ok:
            result["answer"], result["insights"] = js["answer"].strip(), [str(i) for i in js.get("insights", [])][:5]
            info["used_for_narration"] = True

    def _help(self, question: str, ws: Workspace, message: str) -> dict[str, Any]:
        hits = [h for h in (ws.semantic.search(question, 4) if ws.semantic else []) if h["score"] >= 0.6]
        run = Run(self.router, ToolContext(ws, self.cache, self.cfg))
        res = self._base({"intent": None}, run, ws)
        extra = ""
        if hits:
            extra = " Related columns I found: " + ", ".join(f"{h['table']}.{h['column']}" for h in hits) + "."
        res.update(ok=True, mode="help", answer=message + extra, follow_ups=self._suggest(ws), unsupported=True)
        return res

    def _suggest(self, ws: Workspace) -> list[str]:
        if not ws.tables:
            return []
        fact = catalog.fact_table(ws)
        dims = catalog.dimension_options(ws, fact) if fact else []
        out = ["Give me an executive summary", "Detect anomalies in the dataset", "Show monthly trends", "Forecast for the next 6 months"]
        if dims:
            out.insert(0, f"Which {dims[0]['column'].replace('_', ' ')} has the highest {catalog.primary_metric(ws, fact) or 'value'}?")
        return out[:5]

    @staticmethod
    def _table(frame: pd.DataFrame) -> dict[str, Any]:
        f = frame.head(MAX_TABLE_ROWS)
        return {"columns": [str(c) for c in f.columns], "rows": narrator_utils.jsonable(f.values.tolist()),
                "total_rows": int(len(frame)), "truncated": bool(len(frame) > MAX_TABLE_ROWS or frame.attrs.get("truncated"))}

    def _fv(self, ws: Workspace, metric: dict[str, Any]):
        info = catalog.col_info(ws, metric["table"], metric["column"]) if metric.get("column") else None
        is_money = bool(info and info["is_money"]) and metric["agg"] in ("sum", "avg", "min", "max")
        cur = self.cfg.currency
        return (lambda v: money(v, cur)) if is_money else (lambda v: num(v))

    def _pandas(self, plan: dict[str, Any], ws: Workspace) -> str | None:
        try:
            return pandas_snippet(plan, ws.relationships, ws.meta, ws.schemas)
        except Exception:
            return None

    def _total(self, run: Run, plan: dict[str, Any]) -> float | None:
        p = {**plan, "time": plan.get("time"), "dimension": None, "limit": None}
        frame = run.sql(build_sql(p, run.ctx.ws.relationships, grouped=False))
        v = frame.iloc[0, 0] if len(frame) else None
        return None if v is None or pd.isna(v) else float(v)

    # ------------------------------------------------------------------ rank / aggregate / compare
    def _h_rank(self, plan, run, mem):
        return (yield from self._h_grouped(plan, run, mem))

    _h_aggregate = _h_compare = _h_rank

    def _h_grouped(self, plan, run: Run, mem):
        ws, metric, dim = run.ctx.ws, plan["metric"], plan.get("dimension")
        res = self._base(plan, run, ws)
        fv = self._fv(ws, metric)
        yield status("Generating SQL…")
        sql = build_sql(plan, ws.relationships)
        yield status("Running SQL…")
        frame = run.sql(sql)
        if frame.empty:
            raise AppError("No rows matched that question (check the filters or spelling of names).", 200, "empty_result")
        alias = frame.columns[-1]
        res.update(sql=sql, pandas_code=self._pandas(plan, ws))
        if dim is None:
            value = float(frame.iloc[0, 0]) if pd.notna(frame.iloc[0, 0]) else 0.0
            res["answer"], res["insights"] = narrator.single(plan, value, fv)
            res["table"] = self._table(frame)
            res["charts"] = [run.call("create_chart", {"chart_type": "kpi", "frame": frame, "y": alias,
                                                        "title": narrator.metric_label(metric).capitalize(),
                                                        "format": "money" if fv(1).startswith(self.cfg.currency) and self.cfg.currency else "number"}).data["chart"]]
            res["method"] = [f"Applied {metric['agg']} to {metric['column'] or 'rows'} in {metric['table']}{narrator.filter_text(plan)}."]
            run.facts = {"value": value, "metric": narrator.metric_label(metric)}
            res["follow_ups"] = ["Show monthly trends", "Break it down by region", "Detect anomalies in the dataset"]
            return res
        label = dim["column"]
        yield status("Validating results…")
        overall = self._total(run, plan) if metric["agg"] == "sum" else None
        if overall is not None and not plan.get("limit") and not plan.get("underperform_limited"):
            gsum = float(frame[alias].sum())
            ok = abs(gsum - overall) <= max(0.01, 1e-6 * abs(overall))
            if ok:
                run.validation.append(f"Sum of groups ({num(gsum)}) matches the overall total ({num(overall)}).")
            else:
                gap = overall - gsum
                run.validation.append(f"Groups sum to {num(gsum)}; {num(gap)} ({abs(gap) / abs(overall) * 100:.2f}% of the overall total {num(overall)}) "
                                      f"has no {narrator.noun(label)} (missing or unmatched key values) and is excluded from the groups.")
                res["warnings"].append(f"{abs(gap) / abs(overall) * 100:.2f}% of the total could not be attributed to a {narrator.noun(label)} because of missing/unmatched key values.")
        run.validation.append(f"{len(frame):,} group(s) returned; no NaN/inf values in results." if np.isfinite(frame[alias].astype(float)).all()
                              else "Result contains non-finite values.")
        yield status("Analysing results with pandas…")
        stats = run.call("run_pandas_analysis", {"operation": "share_of_total", "frame": frame, "label": label, "value": alias}).data
        ranked = frame.sort_values(alias, ascending=(plan["order"] == "asc")).reset_index(drop=True)
        facts: dict[str, Any] = {"n_groups": stats["n"], "top_label": ranked.iloc[0][label], "top_value": float(ranked.iloc[0][alias]),
                                 "bottom_label": ranked.iloc[-1][label], "bottom_value": float(ranked.iloc[-1][alias]),
                                 "second_label": stats["second_label"], "second_value": stats["second_value"],
                                 "top_vs_second_pct": stats["top_vs_second_pct"], "top_vs_bottom_ratio": stats["top_vs_bottom_ratio"],
                                 "rows": ranked[[label, alias]].values.tolist(), "underperform": False}
        if overall:
            facts["top_share_pct"] = float(ranked.iloc[0][alias] / overall * 100)
            ranked["share_pct"] = (ranked[alias] / overall * 100).round(2)
            lim = plan.get("limit")
            if lim:
                facts["topn_share_pct"] = float(ranked[alias].head(lim).sum() / overall * 100)
            if len(ranked) > 4 and plan["order"] == "desc":
                facts["top3_share_pct"] = float(ranked[alias].head(3).sum() / overall * 100)
        if plan["mode"] == "rank" and plan["order"] == "asc" and not plan.get("limit") and plan.get("underperform"):
            under = run.call("run_pandas_analysis", {"operation": "underperformers", "frame": frame, "label": label, "value": alias}).data
            uf = under["frame"]
            facts.update(underperform=True, under={"n_flagged": under["n_flagged"], "n_total": under["n_total"], "q1": under["q1"],
                                                   "median": under["median"], "flagged_share_pct": under["flagged_share_pct"],
                                                   "worst_label": uf.iloc[0][label], "worst_value": float(uf.iloc[0][alias]),
                                                   "worst_vs_median_pct": float(uf.iloc[0]["vs_median_pct"]) if under["median"] else 0.0,
                                                   "names": uf[[label, alias]].values.tolist()})
            ranked["underperforming"] = ranked[alias] <= under["q1"]
            res["method"] = [f"Joined and grouped {metric['column'] or 'rows'} by {label} and summed it per {narrator.noun(label)}.",
                             f"Computed the 25th percentile ({fv(under['q1'])}) across all {under['n_total']} {narrator.plural(narrator.noun(label))}.",
                             "Flagged every item at or below that threshold as underperforming and ranked from weakest."]
            res["assumptions"].append("“Underperforming” is defined as total at or below the 25th percentile of all groups.")
            chart_frame = ranked.head(10)
        else:
            chart_frame = ranked.head(plan["limit"] or 12)
            res["method"] = [f"Grouped {metric['table']}{'' if dim['table'] == metric['table'] else ' joined with ' + dim['table']} by {label}.",
                             f"Calculated the {metric['agg'].replace('_', ' ')} of {metric['column'] or 'rows'} per group.",
                             f"Sorted {'ascending' if plan['order'] == 'asc' else 'descending'}" + (f" and kept the first {plan['limit']}." if plan.get("limit") else "."),
                             "Reconciled the grouped totals against an independent overall total query." if overall else "Validated that results are finite."]
        shown = ranked.head(plan["limit"]) if plan.get("limit") and not facts["underperform"] else ranked
        ctype_mode = "rank" if plan["mode"] == "rank" else "aggregate"
        ctype = self._chart_type(ctype_mode, len(chart_frame), plan)
        res["charts"] = [run.call("create_chart", {
            "chart_type": ctype, "frame": chart_frame, "x": label, "y": alias,
            "title": f"{narrator.metric_label(metric).capitalize()} by {narrator.noun(label)}" + (" (bottom 10)" if facts["underperform"] else ""),
            "x_label": narrator.noun(label).capitalize(), "y_label": narrator.metric_label(metric),
            "format": "money" if fv(1).startswith(self.cfg.currency) and self.cfg.currency else "number"}).data["chart"]]
        res["table"] = self._table(shown)
        res["answer"], res["insights"] = narrator.grouped(plan, facts, fv)
        if narrator.filter_text(plan):
            res["answer"] += narrator.filter_text(plan)
        run.facts = {k: v for k, v in facts.items() if k != "rows"} | {"top_rows": facts["rows"][:10], "metric": narrator.metric_label(metric), "total": overall}
        res["_top_entity"] = {"table": dim["table"], "column": label, "value": str(ranked.iloc[0][label])}
        res["_entities"] = [str(v) for v in ranked[label].head(10)]
        res["follow_ups"] = ["Show its monthly trend", "Generate SQL for this analysis", "Detect anomalies in the dataset"]
        return res

    def _chart_type(self, mode: str, n: int, plan: dict[str, Any]) -> str:
        from ..tools.viz_tool import select_chart_type
        return select_chart_type(mode, n, plan.get("chart"), plan.get("share", False))

    # ------------------------------------------------------------------ trend
    def _h_trend(self, plan, run: Run, mem):
        ws, metric, tm = run.ctx.ws, plan["metric"], plan["time"]
        res = self._base(plan, run, ws)
        fv = self._fv(ws, metric)
        yield status("Generating SQL…")
        sql = build_sql(plan, ws.relationships)
        yield status("Running SQL…")
        frame = run.sql(sql)
        if frame.empty:
            raise AppError("No data matched that trend request.", 200, "empty_result")
        alias = frame.columns[-1]
        dim = plan.get("dimension")
        overall_plan = {**plan, "dimension": None}
        overall = run.sql(build_sql(overall_plan, ws.relationships)) if dim else frame
        yield status("Validating results…")
        if metric["agg"] == "sum":
            total = self._total(run, plan)
            ssum = float(overall[alias].sum())
            ok = total is not None and abs(ssum - total) <= max(0.01, 1e-6 * abs(total))
            run.validation.append(f"Sum over {len(overall)} {tm['grain']}s ({num(ssum)}) {'matches' if ok else 'DOES NOT match'} the overall total ({num(total)}).")
        yield status("Computing trend statistics…")
        s = run.call("run_pandas_analysis", {"operation": "trend_stats", "frame": overall, "period": "period", "value": alias}).data
        mode_chart = plan.get("chart") or "line"
        chart_args = {"chart_type": mode_chart if mode_chart in ("line", "bar") else "line", "frame": frame, "x": "period", "y": alias,
                      "title": f"{narrator.metric_label(metric).capitalize()} by {tm['grain']}" + (f" and {narrator.noun(dim['column'])}" if dim else "") + narrator.filter_text(plan),
                      "x_label": tm["grain"].capitalize(), "y_label": narrator.metric_label(metric),
                      "format": "money" if fv(1).startswith(self.cfg.currency) and self.cfg.currency else "number"}
        if dim:
            chart_args["series_col"] = dim["column"]
        res["charts"] = [run.call("create_chart", chart_args).data["chart"]]
        res["answer"], res["insights"] = narrator.trend(plan, s, fv)
        res.update(sql=sql, pandas_code=self._pandas(plan, ws), table=self._table(frame))
        res["method"] = [f"Bucketed {tm['table']}.{tm['column']} by {tm['grain']}.",
                         f"Aggregated {metric['column'] or 'rows'} ({metric['agg']}) per {tm['grain']}{narrator.filter_text(plan)}.",
                         "Fitted a linear regression (SciPy) to measure trend direction and significance.",
                         "Computed period-over-period changes, peak and trough."]
        run.facts = {k: v for k, v in s.items() if k != "frame"} | {"metric": narrator.metric_label(metric)}
        te = None
        res["_top_entity"] = te
        res["follow_ups"] = ["Forecast for the next 6 months", "Detect anomalies in the dataset", "Generate SQL for this analysis"]
        return res

    # ------------------------------------------------------------------ drivers (why did X change)
    def _h_drivers(self, plan, run: Run, mem):
        ws, metric, tm, dr = run.ctx.ws, plan["metric"], plan["time"], plan["drivers"]
        res = self._base(plan, run, ws)
        fv = self._fv(ws, metric)
        fact = metric["table"]
        yield status("Comparing the two periods…")
        base = {**plan, "dimension": None, "periods": [dr["prev"], dr["cur"]], "order": None, "limit": None}
        sql_total = build_sql(base, ws.relationships)
        tot = run.sql(sql_total)
        alias = tot.columns[-1]
        by_p = dict(zip(tot["period"], tot[alias].astype(float)))
        if dr["cur"] not in by_p or dr["prev"] not in by_p:
            raise AppError(f"I need data for both {dr['prev']} and {dr['cur']} to compare them.", 200, "insufficient_data")
        prev_t, cur_t = by_p[dr["prev"]], by_p[dr["cur"]]
        delta, pct_ = cur_t - prev_t, ((cur_t / prev_t - 1) * 100 if prev_t else float("nan"))
        yield status("Finding contributing segments…")
        filtered_cols = {f["column"] for f in plan.get("filters", [])}
        dims = [d for d in catalog.dimension_options(ws, fact) if d["column"] not in filtered_cols][:4]
        for t in ws.tables:
            lab = catalog.label_for_table(ws, t)
            if lab and t != fact and lab not in filtered_cols and any(r for r in ws.relationships if t in (r["child_table"], r["parent_table"])):
                dims.append({"table": t, "column": lab})
        breakdowns, sqls, main_frame = [], [sql_total], None
        for d in dims:
            p = {**base, "dimension": {"table": d["table"], "column": d["column"]}}
            sql_d = build_sql(p, ws.relationships)
            fr = run.sql(sql_d)
            if fr.empty:
                continue
            cmp_ = run.call("run_pandas_analysis", {"operation": "compare_periods", "frame": fr, "dim": d["column"], "period": "period",
                                                    "value": fr.columns[-1], "prev": dr["prev"], "cur": dr["cur"]}).data
            f = cmp_["frame"]
            row = f.iloc[0] if delta < 0 else f.iloc[-1]
            breakdowns.append({"dimension": narrator.noun(d["column"]), "column": d["column"], "frame": f, "sql": sql_d,
                               "main": {"label": str(row[d["column"]]), "delta": float(row["delta"]), "pct_change": None if pd.isna(row["pct_change"]) else float(row["pct_change"]),
                                        "share_of_change_pct": float(row["share_of_change_pct"]) if pd.notna(row["share_of_change_pct"]) else 0.0}})
        if not breakdowns:
            raise AppError("I couldn't break the change down by any category in this data.", 200, "no_dimensions")
        breakdowns.sort(key=lambda b: -abs(b["main"]["share_of_change_pct"]))
        best = breakdowns[0]
        top_breakdowns = [b for b in breakdowns if abs(b["main"]["share_of_change_pct"]) > 0][:3]
        direction_word = re.search(r"\b(fall|fell|drop|declin\w*|decreas\w*|dip)\b", plan["question"].lower())
        note = ""
        if direction_word and delta > 0:
            note = f"Note: {narrator.metric_label(metric)} actually increased in {dr['cur']} rather than falling."
        d_facts = {"prev": dr["prev"], "cur": dr["cur"], "prev_total": prev_t, "cur_total": cur_t, "delta": delta, "pct": pct_,
                   "breakdowns": top_breakdowns, "direction_note": note}
        res["answer"], res["insights"] = narrator.drivers(plan, d_facts, fv)
        f = best["frame"]
        f2 = f.rename(columns={dr["prev"]: f"{dr['prev']}", dr["cur"]: f"{dr['cur']}"})
        res["charts"] = [run.call("create_chart", {"chart_type": "bar", "frame": f2.head(15), "x": best["column"], "y": "delta",
                                                    "title": f"Change in {narrator.metric_label(metric)} by {best['dimension']} ({dr['prev']} → {dr['cur']})",
                                                    "x_label": best["dimension"].capitalize(), "y_label": "Change",
                                                    "format": "money" if fv(1).startswith(self.cfg.currency) and self.cfg.currency else "number"}).data["chart"]]
        res["table"] = self._table(f2.round(2))
        res.update(sql=f"-- Totals for the two periods\n{sql_total};\n\n-- Breakdown by {best['dimension']}\n{best['sql']}")
        res["method"] = [f"Computed {narrator.metric_label(metric)} for {dr['prev']} and {dr['cur']} with SQL.",
                         "Broke the change down by each available category (and by customer/product where joined) with pandas pivots.",
                         "Ranked categories by their share of the net change to find the largest contributors.",
                         "Reported the dimension where one segment explains the largest share of the change."]
        res["validation"] = run.validation + [f"Segment deltas for '{best['dimension']}' explain {num(float(f['delta'].sum()))} of the {num(delta)} overall change"
                                                 + (" (the rest comes from rows without a matching " + best['dimension'] + ")." if abs(float(f['delta'].sum()) - delta) > 0.01 else ".")]
        res["assumptions"].append("Month-over-month comparison; contributions measure where the change occurred, not external causes.")
        run.facts = {"prev": dr["prev"], "cur": dr["cur"], "prev_total": prev_t, "cur_total": cur_t, "delta": delta, "pct": pct_,
                     "main_contributors": [{"dimension": b["dimension"], **b["main"]} for b in top_breakdowns]}
        res["follow_ups"] = [f"Show monthly trend", "Detect anomalies in the dataset"]
        return res

    # ------------------------------------------------------------------ anomalies
    def _h_anomaly(self, plan, run: Run, mem):
        ws = run.ctx.ws
        res = self._base(plan, run, ws)
        table = plan.get("table")
        metric = (plan.get("metric") or {}).get("column")
        yield status("Scanning for anomalies (IQR/z-score + Isolation Forest)…")
        r = run.call("detect_anomalies", {"table": table, **({"metric": metric} if metric else {})}).data
        res["sources"] = [ws.meta[r["table"]]["filename"]]
        fv = (lambda v: money(v, self.cfg.currency)) if (catalog.col_info(ws, r["table"], r["metric"]) or {}).get("is_money") else (lambda v: num(v))
        res["answer"], res["insights"] = narrator.anomalies(plan, r, fv)
        res["anomalies"] = r["anomalies"]
        if r["anomalies"]:
            fr = pd.DataFrame([{"id": a["id"] or f"row {a['row']}", "actual": a["actual"]} for a in r["anomalies"][:15] if a["actual"] is not None])
            if not fr.empty:
                res["charts"] = [run.call("create_chart", {"chart_type": "hbar", "frame": fr, "x": "id", "y": "actual",
                                                            "title": f"Top anomalies in {r['metric']}", "x_label": "Record", "y_label": r["metric"],
                                                            "reference": r["expected_range"][1], "reference_label": "Upper expected bound",
                                                            "format": "money" if fv(1).startswith(self.cfg.currency) and self.cfg.currency else "number"}).data["chart"]]
            res["table"] = self._table(pd.DataFrame([{k: a[k] for k in ("row", "id", "date", "actual", "expected_low", "expected_high", "score", "detected_by", "reason")} for a in r["anomalies"]]))
        m = r["metric"]
        res["pandas_code"] = ("q1, q3 = df[%r].quantile([0.25, 0.75])\niqr = q3 - q1\nmask = (df[%r] < q1 - 3*iqr) | (df[%r] > q3 + 3*iqr)\nanomalies = df[mask]" % (m, m, m)
                              if r["method"] == "iqr" else
                              "z = (df[%r] - df[%r].mean()) / df[%r].std()\nanomalies = df[z.abs() > 3]" % (m, m, m))
        res["method"] = [f"Selected the metric '{m}' in {r['table']} ({r['rows_scanned']:,} rows).",
                         f"Chose the {r['method'].upper()} rule automatically: {r['rule']}.",
                         "Ran a multivariate Isolation Forest (scikit-learn) to rank rows by how isolated they are." if r["isolation_forest_used"]
                         else "Dataset too small/narrow for Isolation Forest; univariate rule only.",
                         "For each flagged row, recorded the actual value, expected range, score and a statistics-based reason."]
        res["validation"] = run.validation + [f"Expected range [{num(r['expected_range'][0])}, {num(r['expected_range'][1])}] computed from the data's own quartiles/mean."]
        res["assumptions"].append("Flagged values are statistical outliers, not necessarily errors; verify them against the source.")
        run.facts = {"total": r["total"], "metric": m, "rows_scanned": r["rows_scanned"], "method": r["method"], "rule": r["rule"],
                     "top": [{k: a[k] for k in ("id", "actual", "expected_low", "expected_high", "score")} for a in r["anomalies"][:5]]}
        res["follow_ups"] = ["Give me an executive summary", "Show monthly trends", "Check data quality"]
        yield status("Preparing explanation…")
        return res

    # ------------------------------------------------------------------ forecast
    def _h_forecast(self, plan, run: Run, mem):
        ws, metric = run.ctx.ws, plan["metric"]
        res = self._base(plan, run, ws)
        fv = self._fv(ws, metric)
        yield status("Aggregating the monthly history with SQL…")
        yield status("Fitting and backtesting forecast models…")
        r = run.call("forecast_metric", {"plan": {k: plan[k] for k in ("metric", "time", "filters", "year") if k in plan}, "horizon": plan["horizon"]}).data
        h, f = r["history"], r["forecast"]
        labels = h["periods"] + f["periods"]
        n = len(h["periods"])
        none_h, none_f = [None] * n, [None] * len(f["periods"])
        chart = {"type": "line", "title": f"{narrator.metric_label(metric).capitalize()}: history and {r['horizon']}-month forecast (estimate)",
                 "x_label": "Month", "y_label": narrator.metric_label(metric), "format": "money" if fv(1).startswith(self.cfg.currency) and self.cfg.currency else "number",
                 "currency": self.cfg.currency if fv(1).startswith(self.cfg.currency) and self.cfg.currency else "", "labels": labels,
                 "series": [{"name": "Actual", "values": h["values"] + none_f},
                            {"name": "Forecast (estimate)", "values": none_h[:-1] + [h["values"][-1]] + f["point"], "dashed": True}],
                 "band": {"lower": none_h[:-1] + [h["values"][-1]] + f["lower"], "upper": none_h[:-1] + [h["values"][-1]] + f["upper"]}}
        res["charts"] = [chart]
        res["answer"], res["insights"] = narrator.forecast(plan, r, fv)
        res["forecast"] = {k: r[k] for k in ("model", "backtest", "backtest_months", "horizon", "history", "forecast", "interval", "notes", "disclaimer")}
        res["table"] = self._table(pd.DataFrame({"period": f["periods"], "forecast": f["point"], "lower_95_approx": f["lower"], "upper_95_approx": f["upper"]}))
        res["sql"] = r["sql"]
        res["method"] = ["Aggregated the metric by month with SQL.",
                         "Fitted candidate models: moving average, linear trend, Holt exponential smoothing" + (", seasonal linear trend." if len(h["periods"]) >= 24 else "."),
                         f"Backtested each model on the last {r['backtest_months']} months and selected the lowest MAE: {r['model'].replace('_', ' ')}.",
                         "Projected forward and built approximate 95% intervals from backtest error (widening with horizon)."]
        res["validation"] = run.validation + [f"Backtest {r['model']}: MAE {num(r['backtest'][r['model']]['mae'])}, MAPE {r['backtest'][r['model']]['mape_pct']:.1f}%."]
        res["assumptions"] += ["Forecasts assume past patterns continue; they are estimates, not guarantees."] + r["notes"]
        run.facts = {"model": r["model"], "horizon": r["horizon"], "forecast_total": sum(f["point"]), "range_low": sum(f["lower"]), "range_high": sum(f["upper"]),
                     "first_period": f["periods"][0], "first_point": f["point"][0], "backtest": r["backtest"][r["model"]]}
        res["follow_ups"] = ["Show monthly trends", "Detect anomalies in the dataset"]
        return res

    # ------------------------------------------------------------------ data quality
    def _h_quality(self, plan, run: Run, mem):
        ws = run.ctx.ws
        res = self._base(plan, run, ws)
        yield status("Reviewing data-quality reports…")
        reports = run.call("get_data_quality", {}).data["reports"]
        rows, scores = [], []
        for t, q in reports.items():
            scores.append({"table": t, "score": q["score"]})
            for i in q["issues"]:
                if i["severity"] != "info":
                    rows.append({"table": t, "severity": i["severity"], "type": i["type"], "columns": ", ".join(i["columns"]),
                                 "affected_rows": i["affected_rows"], "issue": i["message"], "recommendation": i["recommendation"]})
        sc = pd.DataFrame(scores)
        res["sources"] = [ws.meta[t]["filename"] for t in reports]
        worst = sc.sort_values("score").iloc[0]
        res["answer"] = (f"Data quality scores: " + ", ".join(f"{r.table} {r.score:.1f}/100" for r in sc.itertuples()) +
                         f". {len(rows)} issue(s) were found; lowest score is {worst['table']} ({worst['score']:.1f}).")
        res["insights"] = [r["issue"] for r in rows[:6]] or ["No data-quality issues were detected."]
        res["charts"] = [run.call("create_chart", {"chart_type": "bar", "frame": sc, "x": "table", "y": "score", "title": "Data quality score by dataset",
                                                    "x_label": "Dataset", "y_label": "Score (0-100)"}).data["chart"]]
        res["table"] = self._table(pd.DataFrame(rows)) if rows else None
        res["method"] = ["Profiled each file at upload (types, missing values, duplicates, outliers).",
                         "Score = 40% completeness + 20% uniqueness + 25% validity + 15% structure (see Data Quality panel).",
                         "Every issue lists affected rows and a recommendation; nothing is computed by the LLM."]
        run.facts = {"scores": scores, "issues": [r["issue"] for r in rows[:10]]}
        res["follow_ups"] = ["Detect anomalies in the dataset", "Give me an executive summary"]
        return res

    # ------------------------------------------------------------------ executive summary
    def _h_insights(self, plan, run: Run, mem):
        ws = run.ctx.ws
        res = self._base(plan, run, ws)
        yield status("Computing headline KPIs…")
        o = run.call("generate_insights", {}).data
        if o.get("empty") or not o.get("kpis"):
            raise AppError(o.get("note", "No numeric metrics were found to summarise."), 200, "no_metric")
        yield status("Preparing insights…")
        res["sources"] = [ws.meta[o["fact_table"]]["filename"]]
        k = {x["label"]: x for x in o["kpis"]}
        res["answer"] = "Executive summary — " + "; ".join(f"{x['label']}: {x['display']}" for x in o["kpis"][:5]) + "."
        res["insights"] = o["insights"]
        res["charts"] = o["charts"]
        res["table"] = {"columns": ["KPI", "Value"], "rows": [[x["label"], x["display"]] for x in o["kpis"]], "total_rows": len(o["kpis"]), "truncated": False}
        res["method"] = [f"Identified '{o['fact_table']}' as the main fact table and '{o['metric']}' as the headline metric.",
                         "Computed KPIs, monthly trend and category breakdowns with SQL (joining related files automatically).",
                         "Ran anomaly detection and read the data-quality score."]
        res["assumptions"].append(f"'{o['metric']}' was treated as the headline business metric.")
        run.facts = {"kpis": [{x["label"]: x["value"]} for x in o["kpis"]], "insights": o["insights"]}
        res["follow_ups"] = ["Detect anomalies in the dataset", "Forecast for the next 6 months", "Which products are underperforming?"]
        return res

    # ------------------------------------------------------------------ schema
    def _h_schema(self, plan, run: Run, mem):
        ws = run.ctx.ws
        res = self._base(plan, run, ws)
        yield status("Reading dataset schema…")
        s = run.call("get_dataset_schema", {}).data
        rows = [[t, c["name"], c["kind"], c["role"], c["unique"], f"{c['missing_pct']}%"] for t, d in s["tables"].items() for c in d["columns"]]
        res["sources"] = [ws.meta[t]["filename"] for t in s["tables"]]
        res["answer"] = f"{len(s['tables'])} dataset(s) loaded: " + ", ".join(f"{t} ({d['rows']:,} rows, {len(d['columns'])} columns)" for t, d in s["tables"].items()) + "."
        res["insights"] = [r["description"] for r in s["relationships"]] or ["No relationships between files were detected."]
        res["table"] = {"columns": ["table", "column", "type", "role", "unique", "missing"], "rows": rows, "total_rows": len(rows), "truncated": False}
        res["method"] = ["Read the profiled schema captured at upload.", "Relationships come from matching key columns with value-containment checks."]
        res["follow_ups"] = self._suggest(ws)
        return res

    # ------------------------------------------------------------------ distribution / scatter
    def _h_distribution(self, plan, run: Run, mem):
        ws, sc = run.ctx.ws, plan["scatter"]
        res = self._base(plan, run, ws)
        yield status("Computing the distribution…")
        t, col = sc["table"], sc["x"]
        d = run.call("run_pandas_analysis", {"operation": "describe_column", "table": t, "column": col}).data
        frame = pd.DataFrame({col: ws.tables[t][col].dropna().astype(float)})
        res["charts"] = [run.call("create_chart", {"chart_type": "histogram", "frame": frame, "x": col, "title": f"Distribution of {col}", "x_label": col}).data["chart"]]
        skew = float(sps.skew(frame[col]))
        res["answer"] = f"{col} has mean {num(d['mean'])}, median {num(d['median'])}, min {num(d['min'])} and max {num(d['max'])} across {d['count']:,} values."
        res["insights"] = [f"Skewness is {skew:.2f}: the distribution is {'right-skewed (a few very large values)' if skew > 1 else 'left-skewed' if skew < -1 else 'roughly symmetric'}.",
                           f"The middle 50% of values lie between {num(d['q1'])} and {num(d['q3'])}."]
        res["table"] = {"columns": ["statistic", "value"], "rows": [[k, round(v, 4)] for k, v in d.items()], "total_rows": len(d), "truncated": False}
        res["pandas_code"] = f"df[{col!r}].describe()\ndf[{col!r}].plot.hist(bins=30)"
        res["sources"] = [ws.meta[t]["filename"]]
        res["method"] = [f"Took all non-missing values of {col}.", "Binned them into a histogram with NumPy.", "Computed summary statistics and skewness."]
        run.facts = {**d, "skew": skew}
        res["follow_ups"] = ["Detect anomalies in the dataset", "Show monthly trends"]
        return res

    def _h_scatter(self, plan, run: Run, mem):
        ws, sc = run.ctx.ws, plan["scatter"]
        res = self._base(plan, run, ws)
        yield status("Computing the relationship…")
        t, x, y = sc["table"], sc["x"], sc["y"]
        df = ws.tables[t][[x, y]].dropna().astype(float)
        r_p, p_p = sps.pearsonr(df[x], df[y])
        r_s, _ = sps.spearmanr(df[x], df[y])
        sample = df.sample(min(len(df), 2000), random_state=7)
        res["charts"] = [run.call("create_chart", {"chart_type": "scatter", "frame": sample, "x": x, "y": y, "title": f"{y} vs {x}", "x_label": x, "y_label": y}).data["chart"]]
        strength = "strong" if abs(r_p) >= 0.7 else "moderate" if abs(r_p) >= 0.4 else "weak"
        res["answer"] = f"{x} and {y} show a {strength} {'positive' if r_p > 0 else 'negative'} linear relationship (Pearson r = {r_p:.2f}, Spearman ρ = {r_s:.2f}, n = {len(df):,})."
        res["insights"] = [f"p-value for the Pearson correlation: {p_p:.3g}.", "Correlation does not imply causation."]
        if len(df) > 2000:
            res["warnings"].append("The scatter plot shows a random sample of 2,000 points; statistics use all rows.")
        res["sources"] = [ws.meta[t]["filename"]]
        res["pandas_code"] = f"df[[{x!r}, {y!r}]].dropna().corr()\ndf.plot.scatter(x={x!r}, y={y!r})"
        res["method"] = [f"Selected {x} and {y} from {t}.", "Removed rows with missing values.", "Computed Pearson and Spearman correlations (SciPy)."]
        run.facts = {"pearson_r": r_p, "spearman": r_s, "n": len(df), "p_value": p_p}
        res["follow_ups"] = ["Detect anomalies in the dataset"]
        return res

    # ------------------------------------------------------------------ "generate SQL for this analysis"
    def _h_sql_recall(self, plan, run: Run, mem: ConversationMemory):
        ws = run.ctx.ws
        res = self._base(plan, run, ws)
        last = mem.last
        if not last or not last.get("sql"):
            res.update(answer="There is no earlier analysis with SQL in this conversation yet. Ask a question first (for example “Which region generated the highest revenue?”) and then ask me to generate its SQL.",
                       follow_ups=self._suggest(ws))
            return res
        yield status("Validating the SQL (read-only check)…")
        statements = split_statements(last["sql"])
        frames = [run.sql(s) for s in statements]          # every statement is validated + executed read-only
        frame = frames[-1]
        res["sql"], res["pandas_code"] = last["sql"], last.get("pandas_code")
        res["answer"] = f"Here is the SQL behind the previous analysis (“{last.get('question')}”). It is read-only and was just re-validated and executed ({len(statements)} statement(s)); the last returned {len(frame):,} row(s)."
        res["insights"] = ["Generated from the same plan the analysis used — column and join choices are explained in the method below.",
                           "Only SELECT queries are ever executed; DDL/DML keywords are blocked."]
        res["table"] = self._table(frame)
        res["method"] = ["Retrieved the SQL from the conversation context.", "Validated it (single SELECT, no blocked keywords).",
                         "Executed it through the sandboxed SQL tool (SQLite authorizer allows reads on uploaded tables only)."]
        res["sources"] = list({*(ws.meta[t]["filename"] for t in ws.meta if f'"{t}"' in last["sql"])})
        run.facts = {}
        res["follow_ups"] = ["Show its monthly trend", "Detect anomalies in the dataset"]
        return res
