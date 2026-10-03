"""Dataset profiling: per-column schema, statistics and semantic roles. All numbers computed with pandas/NumPy."""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

STRONG_ID_RE = re.compile(r"(^|_)(id|key|uuid|sku)$")      # always a key, regardless of uniqueness (foreign keys)
WEAK_ID_RE = re.compile(r"(^|_)(code|number|no)$")          # key only when mostly unique
MONEY_RE = re.compile(r"revenue|sales|amount|price|cost|profit|total|income|spend|value|fee|salary")


def is_id_name(name: str) -> bool:
    return bool(STRONG_ID_RE.search(name)) or name == "id"


def _num(v: Any) -> Any:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


def infer_role(name: str, kind: str, nunique: int, non_null: int, n_rows: int) -> str:
    if kind == "datetime":
        return "date"
    if kind == "empty":
        return "empty"
    unique_ratio = nunique / non_null if non_null else 0
    weak_id = bool(WEAK_ID_RE.search(name)) and unique_ratio > 0.5
    if kind in ("integer", "float"):
        return "id" if is_id_name(name) or weak_id else "measure"
    if kind == "boolean":
        return "dimension"
    if is_id_name(name) or weak_id:
        return "id"
    if nunique <= max(50, int(0.05 * n_rows)) and unique_ratio < 0.9:
        return "dimension"
    return "text"


def profile_column(s: pd.Series, name: str, kind: str, n_rows: int, invalid: int) -> dict[str, Any]:
    non_null = int(s.notna().sum())
    nunique = int(s.nunique(dropna=True))
    missing = n_rows - non_null
    role = infer_role(name, kind, nunique, non_null, n_rows)
    col: dict[str, Any] = {
        "name": name, "kind": kind, "role": role, "non_null": non_null, "missing": missing,
        "missing_pct": round(100 * missing / n_rows, 2) if n_rows else 0.0,
        "unique": nunique, "unique_ratio": round(nunique / non_null, 4) if non_null else 0.0,
        "invalid_values": invalid, "is_money": bool(MONEY_RE.search(name)) and kind in ("integer", "float"),
    }
    if kind in ("integer", "float") and non_null:
        d = s.dropna().astype(float)
        q1, q3 = d.quantile(0.25), d.quantile(0.75)
        col["stats"] = {k: _num(v) for k, v in {
            "min": d.min(), "max": d.max(), "mean": d.mean(), "median": d.median(), "std": d.std(ddof=1) if len(d) > 1 else 0.0,
            "q1": q1, "q3": q3, "sum": d.sum(), "skew": d.skew() if len(d) > 2 else 0.0}.items()}
    elif kind == "datetime" and non_null:
        d = s.dropna()
        col["stats"] = {"min": str(d.min().date()), "max": str(d.max().date()), "span_days": int((d.max() - d.min()).days)}
    elif role in ("dimension", "text", "id") and non_null:
        vc = s.dropna().astype(str).value_counts().head(5)
        col["top_values"] = [{"value": k, "count": int(v)} for k, v in vc.items()]
    col["sample"] = [str(v) for v in s.dropna().head(3).tolist()]
    return col


def label_column(profile: dict[str, Any]) -> str | None:
    """Best human-readable label column for an entity table (e.g. customer_name)."""
    cols = profile["schema"]
    for c in cols:
        if c["role"] in ("dimension", "text", "id") and c["name"].endswith("name"):
            return c["name"]
    return None


def profile_dataframe(df: pd.DataFrame, kinds: dict[str, str], invalid: dict[str, int]) -> list[dict[str, Any]]:
    return [profile_column(df[c], c, kinds.get(c, "text"), len(df), invalid.get(c, 0)) for c in df.columns]
