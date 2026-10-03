"""Per-user workspace: loads datasets from disk, profiles them, detects relationships, feeds the SQL engine."""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Config
from ..core.cache import TTLCache, make_key
from ..core.db import AppDB, new_id
from ..core.errors import NotFoundError, ValidationError
from ..core.logging_setup import log_event
from .ingestion import IngestReport, parse_csv, table_name_from_filename, validate_upload
from .profiling import profile_dataframe
from .quality import assess_quality
from .relationships import detect_relationships
from .semantic import SemanticIndex
from .sql_engine import AnalyticsEngine, SQLiteEngine


@dataclass
class Workspace:
    user_id: str
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    schemas: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    quality: dict[str, dict[str, Any]] = field(default_factory=dict)
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)      # table name -> metadata
    relationships: list[dict[str, Any]] = field(default_factory=list)
    engine: AnalyticsEngine | None = None
    semantic: SemanticIndex | None = None
    fingerprint: str = ""

    def table_for_dataset(self, dataset_id: str) -> str | None:
        return next((t for t, m in self.meta.items() if m["dataset_id"] == dataset_id), None)

    def rebuild(self, engine_factory) -> None:
        self.relationships = detect_relationships(self.tables, self.schemas) if len(self.tables) > 1 else []
        self.engine = engine_factory()
        if self.tables:
            self.engine.register(self.tables)
        self.semantic = SemanticIndex(self.schemas) if self.tables else None
        parts = sorted((t, m["sha256"]) for t, m in self.meta.items())
        self.fingerprint = hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


class DatasetRegistry:
    def __init__(self, cfg: Config, db: AppDB, cache: TTLCache):
        self.cfg, self.db, self.cache = cfg, db, cache
        self._ws: dict[str, Workspace] = {}
        self._lock = threading.RLock()

    def _engine(self) -> AnalyticsEngine:
        return SQLiteEngine(timeout_s=self.cfg.query_timeout_s)

    def workspace(self, user_id: str) -> Workspace:
        with self._lock:
            ws = self._ws.get(user_id)
            if ws is None:
                ws = Workspace(user_id)
                for row in self.db.list_datasets(user_id):
                    path = Path(row["file_path"])
                    if not path.exists():
                        continue
                    try:
                        self._load_into(ws, row["id"], row["name"], row["filename"], path.read_bytes())
                    except Exception as exc:  # corrupted file on disk must not break the whole workspace
                        log_event("dataset_reload_failed", dataset_id=row["id"], error=repr(exc))
                ws.rebuild(self._engine)
                self._ws[user_id] = ws
            return ws

    def _load_into(self, ws: Workspace, ds_id: str, name: str, filename: str, raw: bytes) -> dict[str, Any]:
        df, rep = parse_csv(raw, filename, self.cfg.max_rows)
        schema = profile_dataframe(df, rep.kinds, rep.invalid_counts)
        sha = hashlib.sha256(raw).hexdigest()
        quality, _ = self.cache.get_or_compute(make_key("quality", sha), lambda: assess_quality(df, schema, rep.invalid_counts))
        ws.tables[name], ws.schemas[name], ws.quality[name] = df, schema, quality
        meta = {"dataset_id": ds_id, "name": name, "filename": filename, "rows": int(len(df)), "columns": int(df.shape[1]),
                "schema": schema, "quality_score": quality["score"], "sha256": sha, "encoding": rep.encoding,
                "delimiter": rep.delimiter, "ingest_warnings": rep.warnings, "renamed_columns": rep.renamed_columns,
                "size_bytes": len(raw)}
        ws.meta[name] = meta
        return meta

    def add(self, user_id: str, filename: str, raw: bytes) -> dict[str, Any]:
        validate_upload(filename, raw, self.cfg.max_upload_mb)
        with self._lock:
            ws = self.workspace(user_id)
            ds_id = new_id()
            name = table_name_from_filename(filename, set(ws.tables))
            meta = self._load_into(ws, ds_id, name, filename, raw)  # raises ValidationError before anything is persisted
            path = self.cfg.upload_dir / user_id / f"{ds_id}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.db.save_dataset(ds_id, user_id, name, filename, str(path), {k: meta[k] for k in ("rows", "columns", "quality_score")})
            ws.rebuild(self._engine)
        log_event("dataset_added", dataset_id=ds_id, table=name, rows=meta["rows"], columns=meta["columns"], user_id=user_id)
        return meta

    def remove(self, user_id: str, ds_id: str) -> None:
        with self._lock:
            ws = self.workspace(user_id)
            table = ws.table_for_dataset(ds_id)
            if table is None:
                raise NotFoundError("Dataset not found.")
            path = self.db.delete_dataset(user_id, ds_id)
            if path:
                Path(path).unlink(missing_ok=True)
            for d in (ws.tables, ws.schemas, ws.quality, ws.meta):
                d.pop(table, None)
            ws.rebuild(self._engine)
        log_event("dataset_removed", dataset_id=ds_id, user_id=user_id)
