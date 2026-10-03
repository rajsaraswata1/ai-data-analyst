"""Baseline monthly forecasting with automatic model selection by rolling backtest.

Models: moving average, linear trend, Holt's linear exponential smoothing (grid-searched), and
linear trend + monthly seasonal indices (needs >= 24 months). Prediction intervals are *approximate*
(backtest RMSE scaled by sqrt(1 + h/n)); forecasts are estimates, not guarantees.
"""
from __future__ import annotations

import calendar
from typing import Callable

import numpy as np
import pandas as pd

from ..core.errors import AppError

MIN_POINTS = 6
Z95 = 1.96


def _ma(y: np.ndarray, h: int, months=None) -> np.ndarray:
    return np.repeat(y[-3:].mean(), h)


def _linear(y: np.ndarray, h: int, months=None) -> np.ndarray:
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    return intercept + slope * np.arange(len(y), len(y) + h)


def _holt(y: np.ndarray, h: int, months=None) -> np.ndarray:
    best = (np.inf, 0.5, 0.1)
    for a in np.linspace(0.1, 0.9, 9):
        for b in np.linspace(0.05, 0.5, 6):
            level, trend, sse = y[0], y[1] - y[0], 0.0
            for t in range(1, len(y)):
                pred = level + trend
                sse += (y[t] - pred) ** 2
                new_level = a * y[t] + (1 - a) * (level + trend)
                trend = b * (new_level - level) + (1 - b) * trend
                level = new_level
            if sse < best[0]:
                best = (sse, a, b)
    _, a, b = best
    level, trend = y[0], y[1] - y[0]
    for t in range(1, len(y)):
        new_level = a * y[t] + (1 - a) * (level + trend)
        trend = b * (new_level - level) + (1 - b) * trend
        level = new_level
    return np.array([level + (i + 1) * trend for i in range(h)])


def _seasonal_additive(y: np.ndarray, h: int, months: np.ndarray) -> np.ndarray:
    """Linear trend plus month-of-year seasonal offsets (additive; suits constant-amplitude seasonality)."""
    n = len(y)
    x = np.arange(n)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (intercept + slope * x)
    idx = {m: np.mean(resid[months[:n] == m]) for m in range(1, 13) if (months[:n] == m).any()}
    center = np.mean(list(idx.values()))
    future = intercept + slope * np.arange(n, n + h)
    return np.array([future[i] + idx.get(int(months[n + i]), center) - center for i in range(h)])


def _seasonal_multiplicative(y: np.ndarray, h: int, months: np.ndarray) -> np.ndarray:
    """Linear trend scaled by month-of-year seasonal ratios (multiplicative; suits seasonality that grows with level)."""
    n = len(y)
    x = np.arange(n)
    slope, intercept = np.polyfit(x, y, 1)
    trend = intercept + slope * x
    ratio = y / np.where(trend == 0, np.nan, trend)
    idx = {m: np.nanmean(ratio[months[:n] == m]) for m in range(1, 13) if (months[:n] == m).any()}
    future = intercept + slope * np.arange(n, n + h)
    return np.array([future[i] * idx.get(int(months[n + i]), 1.0) for i in range(h)])


MODELS: dict[str, Callable] = {"moving_average": _ma, "linear_trend": _linear, "holt_linear": _holt,
                               "seasonal_additive": _seasonal_additive, "seasonal_multiplicative": _seasonal_multiplicative}


def _future_months(last: pd.Period, h: int) -> list[pd.Period]:
    return [last + i for i in range(1, h + 1)]


def forecast_monthly(series: pd.Series, horizon: int = 6, last_observed_date: pd.Timestamp | None = None) -> dict:
    """series: index = 'YYYY-MM' strings (sorted), values = metric totals."""
    s = series.copy()
    s.index = pd.PeriodIndex(s.index, freq="M")
    s = s.sort_index().asfreq("M")
    notes: list[str] = []
    if s.isna().any():
        notes.append(f"{int(s.isna().sum())} month(s) had no data and were filled by linear interpolation.")
        s = s.interpolate()
    if last_observed_date is not None:
        last_p = s.index[-1]
        if last_observed_date.day < 0.8 * calendar.monthrange(last_p.year, last_p.month)[1] and last_observed_date.to_period("M") == last_p:
            s = s.iloc[:-1]
            notes.append(f"The last month ({last_p}) looks incomplete (data ends {last_observed_date.date()}) and was excluded.")
    if len(s) < MIN_POINTS:
        raise AppError(f"Forecasting needs at least {MIN_POINTS} monthly data points; only {len(s)} available.", 400, "insufficient_data")
    horizon = max(1, min(int(horizon), 24))
    y = s.to_numpy(dtype=float)
    n = len(y)
    all_months = np.array([p.month for p in list(s.index) + _future_months(s.index[-1], max(horizon, 6))])

    candidates = [m for m in MODELS if not m.startswith("seasonal") or n >= 24]
    h_test = max(2, min(6, n // 4))
    scores: dict[str, dict] = {}
    errors: dict[str, np.ndarray] = {}
    for name in candidates:
        pred = MODELS[name](y[: n - h_test], h_test, all_months)
        err = y[n - h_test:] - pred
        errors[name] = err
        scores[name] = {"mae": float(np.mean(np.abs(err))),
                        "mape_pct": float(np.mean(np.abs(err) / np.where(y[n - h_test:] == 0, np.nan, np.abs(y[n - h_test:]))) * 100)}
    best = min(scores, key=lambda k: scores[k]["mae"])
    point = MODELS[best](y, horizon, all_months)
    rmse = float(np.sqrt(np.mean(errors[best] ** 2)))
    steps = np.arange(1, horizon + 1)
    half = Z95 * rmse * np.sqrt(1 + steps / n)
    lower, upper = np.maximum(point - half, 0) if (y >= 0).all() else point - half, point + half
    fut = _future_months(s.index[-1], horizon)
    return {"model": best, "backtest": {k: {m: round(v, 2) for m, v in d.items()} for k, d in scores.items()},
            "backtest_months": h_test, "horizon": horizon,
            "history": {"periods": [str(p) for p in s.index], "values": [round(float(v), 2) for v in y]},
            "forecast": {"periods": [str(p) for p in fut], "point": [round(float(v), 2) for v in point],
                         "lower": [round(float(v), 2) for v in lower], "upper": [round(float(v), 2) for v in upper]},
            "interval": "approximate 95%", "notes": notes,
            "disclaimer": "Forecasts are statistical estimates based on past patterns, not guarantees."}
