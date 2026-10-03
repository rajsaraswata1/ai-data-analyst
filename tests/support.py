"""Shared test helpers: temp-dir apps, sample data, and an orchestrator driver."""
from __future__ import annotations

import io
import pathlib
import tempfile

import pandas as pd

from app import create_app
from app.agents.llm import NullProvider
from app.agents.orchestrator import Orchestrator
from app.config import Config
from app.core.cache import TTLCache
from app.core.db import AppDB
from app.services.registry import DatasetRegistry
from app.tools.router_factory import build_router

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "data" / "samples"
H = {"X-Requested-With": "XMLHttpRequest"}


def sample_bytes(name: str) -> bytes:
    return (SAMPLES / f"{name}.csv").read_bytes()


def sample_df(name: str) -> pd.DataFrame:
    return pd.read_csv(SAMPLES / f"{name}.csv")


def make_cfg(**kw) -> Config:
    return Config.from_env(data_dir=tempfile.mkdtemp(), log_dir=tempfile.mkdtemp(), testing=True, **kw)


class Engine:
    """Registry + orchestrator with the three sample files loaded for a single user."""

    def __init__(self, files=("sales", "customers", "products"), provider=None, **cfg_kw):
        self.cfg = make_cfg(**cfg_kw)
        self.cache = TTLCache(self.cfg.cache_enabled, self.cfg.cache_ttl)
        self.db = AppDB(self.cfg.db_path)
        self.reg = DatasetRegistry(self.cfg, self.db, self.cache)
        for f in files:
            self.reg.add("u1", f + ".csv", sample_bytes(f))
        self.ws = self.reg.workspace("u1")
        self.router = build_router()
        self.orch = Orchestrator(self.cfg, self.cache, self.router, provider or NullProvider())
        self.state = None
        self.statuses: list[str] = []

    def ask(self, q: str) -> dict:
        out = None
        for ev in self.orch.run(self.ws, q, self.state, "u1", "t"):
            if ev["type"] == "status":
                self.statuses.append(ev["message"])
            else:
                out, self.state = ev["result"], ev["state"]
        return out


def new_client(**cfg_kw):
    app = create_app(make_cfg(**cfg_kw))
    return app, app.test_client()


def register(client, name="alice", pw="password123"):
    return client.post("/api/auth/register", json={"username": name, "password": pw}, headers=H)


def upload(client, names=("sales", "customers", "products")):
    files = [(io.BytesIO(sample_bytes(n)), n + ".csv") for n in names]
    return client.post("/api/datasets", data={"files": files}, headers=H, content_type="multipart/form-data")
