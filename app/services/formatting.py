"""Number formatting shared by narration, dashboard and reports."""
from __future__ import annotations

import math
from typing import Any


def num(v: Any, decimals: int = 2) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "n/a"
    if float(v).is_integer() and abs(v) < 1e15:
        return f"{int(v):,}"
    return f"{v:,.{decimals}f}"


def compact(v: float) -> str:
    a = abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:,.2f}{suf}"
    return num(v)


def money(v: Any, cur: str = "", compact_ok: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{cur}{compact(v) if compact_ok and abs(v) >= 1e6 else num(v)}"


def pct(v: Any, signed: bool = False) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "n/a"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def fmt_value(v: Any, is_money: bool, cur: str) -> str:
    return money(v, cur) if is_money else num(v)
