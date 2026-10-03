"""Structured JSON logging with secret redaction. Never logs raw dataset contents."""
from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path

_SENSITIVE = ("key", "password", "token", "secret", "authorization", "cookie")
_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
                   "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for k, v in record.__dict__.items():
            if k in _STANDARD or k.startswith("_"):
                continue
            payload[k] = "***" if any(s in k.lower() for s in _SENSITIVE) else v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str, log_dir: Path, testing: bool = False) -> None:
    root = logging.getLogger("analyst")
    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False
    if testing:
        root.addHandler(logging.NullHandler())
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=5_000_000, backupCount=3)
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(JsonFormatter())
        root.addHandler(h)


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    """Emit one structured event, e.g. log_event('tool_call', tool='run_sql', duration_ms=12)."""
    logging.getLogger("analyst").log(level, event, extra=fields)
