"""JSON-safety helper: converts numpy/pandas objects and drops non-serialisable frames."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items() if not isinstance(v, pd.DataFrame)}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not math.isfinite(f) else f
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return obj.strftime("%Y-%m-%d")
    if obj is pd.NaT or (not isinstance(obj, (str, bytes, list, dict)) and pd.isna(obj) is True):
        return None
    return obj
