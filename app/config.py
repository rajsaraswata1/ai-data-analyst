"""Application configuration, loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no python-dotenv dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    app_env: str = "development"
    secret_key: str = ""
    max_upload_mb: int = 50
    max_rows: int = 2_000_000
    cache_enabled: bool = True
    cache_ttl: int = 900
    cache_max_items: int = 512
    log_level: str = "INFO"
    currency: str = "₹"
    registration_open: bool = True
    query_timeout_s: float = 15.0
    max_result_rows: int = 5000
    llm_provider: str = "none"
    llm_api_key: str = field(default="", repr=False)  # never printed
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_timeout: float = 30.0
    data_dir: Path = BASE_DIR / "data"
    log_dir: Path = BASE_DIR / "logs"
    testing: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        _load_dotenv(BASE_DIR / ".env")
        env = os.environ.get
        cfg = cls(
            app_env=env("APP_ENV", "development"),
            secret_key=env("SECRET_KEY", ""),
            max_upload_mb=int(env("MAX_UPLOAD_SIZE_MB", "50")),
            max_rows=int(env("MAX_ROWS", "2000000")),
            cache_enabled=_bool("CACHE_ENABLED", True),
            cache_ttl=int(env("CACHE_TTL_SECONDS", "900")),
            log_level=env("LOG_LEVEL", "INFO").upper(),
            currency=env("CURRENCY_SYMBOL", "₹"),
            registration_open=_bool("REGISTRATION_OPEN", True),
            llm_provider=env("LLM_PROVIDER", "none").lower(),
            llm_api_key=env("LLM_API_KEY", ""),
            llm_base_url=env("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_model=env("LLM_MODEL", ""),
            llm_timeout=float(env("LLM_TIMEOUT_SECONDS", "30")),
            data_dir=Path(env("DATA_DIR", str(BASE_DIR / "data"))),
            log_dir=Path(env("LOG_DIR", str(BASE_DIR / "logs"))),
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        cfg.data_dir = Path(cfg.data_dir)
        cfg.log_dir = Path(cfg.log_dir)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.upload_dir.mkdir(parents=True, exist_ok=True)
        cfg.secret_key = cfg.secret_key or cfg._persistent_dev_secret()
        return cfg

    def _persistent_dev_secret(self) -> str:
        """Production must set SECRET_KEY; in development we persist a random one to disk."""
        if self.app_env == "production" and not self.testing:
            raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")
        path = self.data_dir / ".secret_key"
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_hex(32)
        path.write_text(key)
        return key
