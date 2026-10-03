"""Anomaly detection: robust univariate rules (IQR / z-score, auto-selected from skewness) plus a multivariate
Isolation Forest. Every flag carries the actual value, expected range, score and a statistics-based reason."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest

IQR_K_SKEWED = 3.0
Z_THRESHOLD = 3.0
SKEW_LIMIT = 1.0
IF_MIN_ROWS = 200
IF_CONTAMINATION = 0.005
MAX_RETURNED = 25


def _fmt(v: float) -> str:
    return f"{v:,.2f}" if abs(v) < 1e15 else f"{v:.3e}"


def detect_anomalies(df: pd.DataFrame, metric: str, measures: list[str], id_col: str | None = None,
                     date_col: str | None = None) -> dict[str, Any]:
    col = df[metric].dropna().astype(float)
    if len(col) < 10:
        return {"method": None, "anomalies": [], "total": 0, "note": "Too few values for anomaly detection."}
    # skewness on winsorised data (1st-99th pct) so the outliers we are hunting don't distort method selection
    wins = col.clip(col.quantile(0.01), col.quantile(0.99))
    skew = float(stats.skew(wins)) if wins.std() > 0 else 0.0
    q1, q3 = float(col.quantile(0.25)), float(col.quantile(0.75))
    iqr = q3 - q1
    if iqr > 0 and (abs(skew) > SKEW_LIMIT or len(col) < 30):
        method, lo, hi = "iqr", q1 - IQR_K_SKEWED * iqr, q3 + IQR_K_SKEWED * iqr
        why_method = f"|skew|={abs(skew):.2f} > {SKEW_LIMIT}" if abs(skew) > SKEW_LIMIT else "small sample"
        rule = f"IQR rule (k={IQR_K_SKEWED}; Q1={_fmt(q1)}, Q3={_fmt(q3)}) chosen because {why_method}"
    else:
        mu, sd = float(col.mean()), float(col.std())
        method, lo, hi = "zscore", mu - Z_THRESHOLD * sd, mu + Z_THRESHOLD * sd
        rule = f"z-score rule (|z|>{Z_THRESHOLD}; mean={_fmt(mu)}, sd={_fmt(sd)}) chosen because the distribution is near-symmetric (skew={skew:.2f})"

    # multivariate Isolation Forest (scores used to rank + catch unusual combinations)
    feats = [m for m in measures if df[m].notna().sum() > 0]
    if_scores = pd.Series(np.nan, index=df.index)
    if_flag = pd.Series(False, index=df.index)
    if len(df) >= IF_MIN_ROWS and len(feats) >= 2:
        X = df[feats].astype(float)
        X = X.fillna(X.median())
        model = IsolationForest(n_estimators=200, contamination=IF_CONTAMINATION, random_state=42).fit(X)
        if_scores = pd.Series(-model.score_samples(X), index=df.index)   # higher = more anomalous
        if_flag = pd.Series(model.predict(X) == -1, index=df.index)

    if float(col.min()) >= 0:
        lo = max(lo, 0.0)  # a never-negative metric cannot have a negative lower bound
    stat_flag = (df[metric] < lo) | (df[metric] > hi)
    med = {m: float(df[m].median()) for m in feats}
    mad = {m: float(stats.median_abs_deviation(df[m].dropna(), scale="normal")) or 1.0 for m in feats}
    out = []
    for idx in df.index[stat_flag.fillna(False) | if_flag]:
        v = float(df.at[idx, metric]) if pd.notna(df.at[idx, metric]) else None
        by_stat = bool(stat_flag.get(idx, False))
        if by_stat and v is not None:
            direction = "high" if v > hi else "low"
            bound = hi if v > hi else lo
            ratio = v / bound if bound else float("nan")
            reason = (f"{metric} of {_fmt(v)} is unusually {direction}: outside the expected range "
                      f"[{_fmt(lo)}, {_fmt(hi)}] ({rule}).")
            if direction == "high" and np.isfinite(ratio):
                reason += f" It is {ratio:.1f}x the upper bound and {v / col.median():.1f}x the median ({_fmt(float(col.median()))})."
            score = float(if_scores.get(idx)) if pd.notna(if_scores.get(idx)) else abs(v - med.get(metric, v)) / (mad.get(metric, 1.0))
            kind = "statistical" + ("+isolation_forest" if if_flag.get(idx) else "")
        else:  # multivariate-only
            devs = {m: abs(float(df.at[idx, m]) - med[m]) / mad[m] for m in feats if pd.notna(df.at[idx, m])}
            top = max(devs, key=devs.get)
            reason = (f"Isolation Forest flagged an unusual combination of {', '.join(feats)}; most atypical field: "
                      f"{top}={_fmt(float(df.at[idx, top]))} (median {_fmt(med[top])}, robust z={devs[top]:.1f}).")
            score, kind = float(if_scores[idx]), "isolation_forest"
        rec = {"row": int(idx), "id": str(df.at[idx, id_col]) if id_col and pd.notna(df.at[idx, id_col]) else None,
               "date": str(df.at[idx, date_col].date()) if date_col and pd.notna(df.at[idx, date_col]) else None,
               "metric": metric, "actual": v, "expected_low": round(lo, 2), "expected_high": round(hi, 2),
               "score": round(score, 4), "detected_by": kind, "reason": reason}
        out.append(rec)
    out.sort(key=lambda r: -r["score"])
    return {"method": method, "rule": rule, "metric": metric, "total": len(out), "rows_scanned": int(len(df)),
            "anomalies": out[:MAX_RETURNED], "expected_range": [round(lo, 2), round(hi, 2)],
            "isolation_forest_used": bool(len(df) >= IF_MIN_ROWS and len(feats) >= 2), "features": feats,
            "skew": round(skew, 3)}
