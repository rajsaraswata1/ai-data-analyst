"""Data-quality engine. Issues are computed deterministically; the LLM never calculates them.

Score (0-100) = 40% completeness + 20% uniqueness + 25% validity + 15% structure, each a documented ratio.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

HIGH_CARDINALITY_RATIO = 0.5
OUTLIER_IQR_K = 3.0
WEIGHTS = {"completeness": 0.40, "uniqueness": 0.20, "validity": 0.25, "structure": 0.15}


def _sev(pct: float, high: float, med: float) -> str:
    return "high" if pct >= high else "medium" if pct >= med else "low"


def assess_quality(df: pd.DataFrame, schema: list[dict[str, Any]], invalid: dict[str, int]) -> dict[str, Any]:
    n, m = len(df), df.shape[1]
    issues: list[dict[str, Any]] = []
    cells = max(n * m, 1)

    # --- missing values
    missing_cells = 0
    for c in schema:
        if c["missing"] and c["kind"] != "empty":
            missing_cells += c["missing"]
            issues.append({"type": "missing_values", "severity": _sev(c["missing_pct"], 20, 5), "columns": [c["name"]],
                           "affected_rows": c["missing"],
                           "message": f"{c['name']} contains {c['missing_pct']}% missing values ({c['missing']:,} rows).",
                           "recommendation": "Impute (median/mode), drop rows, or confirm that blanks are expected."})
    # --- empty / constant columns
    structure_bad = 0
    for c in schema:
        if c["kind"] == "empty":
            structure_bad += 1
            issues.append({"type": "empty_column", "severity": "high", "columns": [c["name"]], "affected_rows": n,
                           "message": f"{c['name']} is completely empty.", "recommendation": "Remove the column."})
            missing_cells += n
        elif c["unique"] == 1 and n > 1:
            structure_bad += 1
            issues.append({"type": "constant_column", "severity": "low", "columns": [c["name"]], "affected_rows": n,
                           "message": f"{c['name']} has a single constant value.", "recommendation": "It carries no information; consider dropping it."})
    # --- duplicates
    dup = int(df.duplicated().sum())
    if dup:
        pct = 100 * dup / n
        issues.append({"type": "duplicate_rows", "severity": _sev(pct, 5, 1), "columns": [], "affected_rows": dup,
                       "message": f"{dup:,} fully duplicated rows ({pct:.2f}%).",
                       "recommendation": "Deduplicate unless repeated records are legitimate."})
    # --- invalid typed values
    invalid_cells = 0
    for col, cnt in invalid.items():
        invalid_cells += cnt
        kind = next((s["kind"] for s in schema if s["name"] == col), "numeric")
        pct = 100 * cnt / n
        issues.append({"type": "type_inconsistency", "severity": _sev(pct, 5, 1), "columns": [col], "affected_rows": cnt,
                       "message": f"{col} is inferred as {kind} but {cnt:,} value(s) ({pct:.2f}%) could not be parsed as such.",
                       "recommendation": "Fix or remove the malformed values at the source."})
    # --- high cardinality & identifier candidates
    for c in schema:
        if c["role"] == "id":
            issues.append({"type": "identifier", "severity": "info", "columns": [c["name"]], "affected_rows": 0,
                           "message": f"{c['name']} looks like an identifier ({c['unique']:,} unique values).",
                           "recommendation": "Exclude identifiers from statistical analysis; use them as join keys."})
        elif (c["role"] == "text" and c["unique_ratio"] > HIGH_CARDINALITY_RATIO and c["kind"] == "text"
              and not c["name"].endswith("name") and n >= 100):
            issues.append({"type": "high_cardinality", "severity": "low", "columns": [c["name"]], "affected_rows": 0,
                           "message": f"{c['name']} has very high cardinality ({c['unique']:,} distinct values).",
                           "recommendation": "Avoid using it as a grouping dimension."})
    # --- numeric outliers (extreme only: IQR k=3)
    for c in schema:
        if c["role"] == "measure" and "stats" in c:
            st = c["stats"]
            iqr = (st["q3"] or 0) - (st["q1"] or 0)
            if iqr > 0:
                lo, hi = st["q1"] - OUTLIER_IQR_K * iqr, st["q3"] + OUTLIER_IQR_K * iqr
                col = df[c["name"]].dropna()
                if st["min"] is not None and st["min"] >= 0:
                    lo = max(lo, 0.0)
                out = int(((col < lo) | (col > hi)).sum())
                if out:
                    pct = 100 * out / len(col)
                    issues.append({"type": "numeric_outliers", "severity": "medium" if pct >= 1 else "low", "columns": [c["name"]],
                                   "affected_rows": out,
                                   "message": f"{c['name']} has {out:,} extreme outliers ({pct:.2f}%) outside [{lo:,.2f}, {hi:,.2f}].",
                                   "recommendation": "Verify these values; see the Anomalies view for details."})
    # --- score
    completeness = 1 - missing_cells / cells
    uniqueness = 1 - dup / n if n else 1
    validity = 1 - invalid_cells / cells
    structure = 1 - structure_bad / max(m, 1)
    comps = {"completeness": completeness, "uniqueness": uniqueness, "validity": validity, "structure": structure}
    score = 100 * sum(WEIGHTS[k] * max(0.0, min(1.0, v)) for k, v in comps.items())
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    issues.sort(key=lambda i: (order[i["severity"]], -i["affected_rows"]))
    return {"score": round(score, 1), "components": {k: round(100 * v, 1) for k, v in comps.items()},
            "weights": WEIGHTS, "issues": issues,
            "summary": {"rows": n, "columns": m, "duplicate_rows": dup, "missing_cells": int(missing_cells),
                        "issue_count": sum(1 for i in issues if i["severity"] != "info")}}
