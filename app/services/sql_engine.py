"""Read-only analytical SQL engine behind an `AnalyticsEngine` interface (SQLite today, DuckDB later).

Safety layers: (1) static validation (single SELECT/WITH statement, keyword blocklist, comment stripping),
(2) SQLite *authorizer* that denies everything except SELECT/READ on registered tables (enforced by the engine),
(3) query timeout via progress handler, (4) row cap.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from ..core.errors import AppError, UnsafeSQLError

BLOCKED = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "ATTACH", "DETACH", "COPY", "INSTALL", "LOAD",
           "PRAGMA", "VACUUM", "TRUNCATE", "REINDEX", "ANALYZE", "EXEC", "EXECUTE", "GRANT", "REVOKE", "MERGE"]
_BLOCK_RE = re.compile(r"\b(" + "|".join(BLOCKED) + r")\b", re.I)


def _strip_literals_and_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return re.sub(r'"(?:[^"]|"")*"', '""', sql)


def validate_sql(sql: str) -> str:
    """Return the normalised SQL (no trailing ';') or raise UnsafeSQLError."""
    if not sql or not sql.strip():
        raise UnsafeSQLError("Empty SQL statement.")
    body = _strip_literals_and_comments(sql).strip().rstrip(";").strip()
    if ";" in body:
        raise UnsafeSQLError("Only a single SQL statement is allowed.")
    if not re.match(r"^(select|with)\b", body, re.I):
        raise UnsafeSQLError("Only read-only SELECT queries are allowed.")
    hit = _BLOCK_RE.search(body)
    if hit:
        raise UnsafeSQLError(f"The keyword {hit.group(1).upper()} is not allowed in analytical queries.")
    return sql.strip().rstrip(";").strip()


def split_statements(text: str) -> list[str]:
    """Split a multi-statement SQL block (statements separated by ';' + blank line, '--' comments allowed) into statements."""
    out = []
    for chunk in re.split(r";\s*\n\s*\n", text.strip()):
        code = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip().rstrip(";").strip()
        if code:
            out.append(chunk.strip().rstrip(";").strip())
    return out


class AnalyticsEngine(ABC):
    @abstractmethod
    def register(self, tables: dict[str, pd.DataFrame]) -> None: ...
    @abstractmethod
    def execute(self, sql: str, max_rows: int) -> pd.DataFrame: ...


class SQLiteEngine(AnalyticsEngine):
    """In-memory SQLite populated from DataFrames; dates stored as ISO text so strftime() works."""

    def __init__(self, timeout_s: float = 15.0):
        self.timeout_s = timeout_s
        self._conn: sqlite3.Connection | None = None
        self._tables: set[str] = set()
        self._lock = threading.Lock()

    def register(self, tables: dict[str, pd.DataFrame]) -> None:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        for name, df in tables.items():
            out = df.copy()
            for c in out.columns:
                if pd.api.types.is_datetime64_any_dtype(out[c]):
                    out[c] = out[c].dt.strftime("%Y-%m-%d").where(out[c].notna(), None)
                elif out[c].dtype == "object" or str(out[c].dtype).startswith(("str", "bool")):
                    out[c] = out[c].astype(object).where(out[c].notna(), None)
            out.to_sql(name, conn, index=False)
        with self._lock:
            if self._conn:
                self._conn.close()
            self._conn, self._tables = conn, set(tables)

    def _authorizer(self, action: int, arg1: Any, arg2: Any, db: Any, source: Any) -> int:
        ok = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        if action in ok:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and (arg1 in self._tables or arg1 is None):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def execute(self, sql: str, max_rows: int = 5000) -> pd.DataFrame:
        clean = validate_sql(sql)
        with self._lock:
            if self._conn is None:
                raise AppError("No datasets are loaded.", 400, "no_data")
            conn = self._conn
            deadline = time.monotonic() + self.timeout_s
            conn.set_authorizer(self._authorizer)
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
            try:
                cur = conn.execute(clean)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchmany(max_rows + 1)
            except sqlite3.OperationalError as exc:
                msg = str(exc)
                if "interrupted" in msg:
                    raise AppError("The query took too long and was stopped.", 408, "timeout", msg)
                if "not authorized" in msg:
                    raise UnsafeSQLError("That query is not permitted.", msg)
                raise AppError("The generated SQL could not be executed.", 400, "sql_error", msg)
            except sqlite3.DatabaseError as exc:
                raise AppError("The generated SQL could not be executed.", 400, "sql_error", str(exc))
            finally:
                conn.set_progress_handler(None, 0)
                conn.set_authorizer(None)
        df = pd.DataFrame(rows[:max_rows], columns=cols)
        df.attrs["truncated"] = len(rows) > max_rows
        return df
