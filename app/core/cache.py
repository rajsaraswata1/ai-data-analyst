"""Thread-safe TTL + LRU cache for deterministic, expensive computations."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable


def make_key(*parts: Any) -> str:
    """Stable key from arbitrary JSON-able parts (dataset fingerprint, query, params...)."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class TTLCache:
    def __init__(self, enabled: bool = True, ttl: int = 900, max_items: int = 512):
        self.enabled, self.ttl, self.max_items = enabled, ttl, max_items
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get_or_compute(self, key: str, fn: Callable[[], Any]) -> tuple[Any, bool]:
        """Return (value, was_cached)."""
        if not self.enabled:
            return fn(), False
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item and now - item[0] < self.ttl:
                self._data.move_to_end(key)
                self.hits += 1
                return item[1], True
            self.misses += 1
        value = fn()
        with self._lock:
            self._data[key] = (time.time(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)
        return value, False

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"enabled": self.enabled, "items": len(self._data), "hits": self.hits,
                "misses": self.misses, "hit_rate": round(self.hits / total, 3) if total else 0.0}
