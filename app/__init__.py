"""AI Data Analyst – Flask application factory."""
from __future__ import annotations

import time
import uuid
from datetime import timedelta
from types import SimpleNamespace

from flask import Flask, g, jsonify, request, session

from .agents.llm import build_provider
from .agents.orchestrator import Orchestrator
from .config import Config
from .core.cache import TTLCache
from .core.db import AppDB
from .core.errors import AppError
from .core.logging_setup import log_event, setup_logging
from .core.security import LoginThrottle, csrf_ok
from .services.registry import DatasetRegistry
from .tools.router_factory import build_router


def create_app(config: Config | None = None) -> Flask:
    cfg = config or Config.from_env()
    setup_logging(cfg.log_level, cfg.log_dir, testing=cfg.testing)
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(SECRET_KEY=cfg.secret_key, MAX_CONTENT_LENGTH=cfg.max_upload_mb * 1024 * 1024 * 5,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=cfg.app_env == "production", PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    cache = TTLCache(cfg.cache_enabled, cfg.cache_ttl, cfg.cache_max_items)
    db = AppDB(cfg.db_path)
    router = build_router()
    provider = build_provider(cfg)
    registry = DatasetRegistry(cfg, db, cache)
    app.extensions["svc"] = SimpleNamespace(cfg=cfg, cache=cache, db=db, router=router, provider=provider, registry=registry,
                                            orchestrator=Orchestrator(cfg, cache, router, provider), throttle=LoginThrottle())

    @app.before_request
    def _begin():
        g.request_id, g.t0 = uuid.uuid4().hex[:10], time.perf_counter()
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "DELETE", "PATCH") and not csrf_ok():
            return jsonify({"error": {"code": "csrf", "message": "Missing X-Requested-With header."}}), 403

    @app.after_request
    def _end(resp):
        resp.headers["X-Request-ID"] = getattr(g, "request_id", "")
        resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                             "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"})
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
            log_event("http_request", request_id=getattr(g, "request_id", ""), method=request.method, path=request.path,
                      status=resp.status_code, duration_ms=round((time.perf_counter() - g.t0) * 1000, 1), user_id=session.get("uid"))
        return resp

    @app.errorhandler(AppError)
    def _app_error(exc: AppError):
        if exc.detail:
            log_event("app_error", request_id=g.get("request_id"), code=exc.code, detail=exc.detail[:300])
        return jsonify({"error": {"code": exc.code, "message": exc.message}}), exc.status

    @app.errorhandler(404)
    def _nf(_):
        return jsonify({"error": {"code": "not_found", "message": "Not found."}}), 404

    @app.errorhandler(405)
    def _mna(_):
        return jsonify({"error": {"code": "method_not_allowed", "message": "Method not allowed."}}), 405

    @app.errorhandler(413)
    def _big(_):
        return jsonify({"error": {"code": "too_large", "message": f"Upload exceeds the {cfg.max_upload_mb} MB per-file limit."}}), 413

    @app.errorhandler(Exception)
    def _crash(exc: Exception):
        log_event("unhandled_exception", 40, request_id=g.get("request_id"), error=repr(exc))
        return jsonify({"error": {"code": "server_error", "message": "Something went wrong. Please try again."}}), 500

    from .api.routes import bp
    app.register_blueprint(bp)
    return app
