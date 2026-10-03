"""HTTP API (Flask blueprint). Thin: validation + delegation to services/orchestrator."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request, send_from_directory, session, stream_with_context

from ..agents.memory import ConversationMemory
from ..config import BASE_DIR
from ..core.errors import AppError, NotFoundError, ValidationError
from ..core.security import hash_password, login_required, validate_credentials, verify_password
from ..services import catalog
from ..tools.base import ToolContext

bp = Blueprint("api", __name__)
MAX_QUESTION_LEN = 1000
MAX_FILES = 10
SAMPLES_DIR = Path(os.environ.get("SAMPLES_DIR", BASE_DIR / "data" / "samples"))   # outside the data volume in Docker
SAMPLE_NAMES = ("sales.csv", "customers.csv", "products.csv")


def svc():
    return current_app.extensions["svc"]


def uid() -> str:
    return session["uid"]


def _ctx() -> ToolContext:
    s = svc()
    return ToolContext(s.registry.workspace(uid()), s.cache, s.cfg, uid(), g.request_id)


# ------------------------------------------------------------------ pages
@bp.get("/")
def index():
    return send_from_directory(current_app.static_folder, "index.html")


@bp.get("/api/health")
def health():
    s = svc()
    return jsonify({"status": "ok", "llm_provider": s.provider.name if s.provider.available else "none (local deterministic engine)",
                    "cache": s.cache.stats(), "env": s.cfg.app_env})


# ------------------------------------------------------------------ auth
@bp.post("/api/auth/register")
def register():
    s = svc()
    if not s.cfg.registration_open:
        raise AppError("Registration is closed.", 403, "registration_closed")
    body = request.get_json(silent=True) or {}
    username, password = str(body.get("username", "")).strip(), str(body.get("password", ""))
    err = validate_credentials(username, password)
    if err:
        raise ValidationError(err)
    if s.db.get_user_by_name(username):
        raise AppError("That username is already taken.", 409, "username_taken")
    user_id = s.db.create_user(username, hash_password(password))
    session.clear(), session.update(uid=user_id, username=username), setattr(session, "permanent", True)
    return jsonify({"user": {"id": user_id, "username": username}}), 201


@bp.post("/api/auth/login")
def login():
    s = svc()
    body = request.get_json(silent=True) or {}
    username, password = str(body.get("username", "")).strip(), str(body.get("password", ""))
    key = f"{username.lower()}|{request.remote_addr}"
    if s.throttle.blocked(key):
        raise AppError("Too many failed attempts. Try again in a few minutes.", 429, "throttled")
    row = s.db.get_user_by_name(username)
    if not row or not verify_password(row["password_hash"], password):
        s.throttle.fail(key)
        raise AppError("Invalid username or password.", 401, "invalid_credentials")
    s.throttle.reset(key)
    session.clear(), session.update(uid=row["id"], username=row["username"]), setattr(session, "permanent", True)
    return jsonify({"user": {"id": row["id"], "username": row["username"]}})


@bp.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/api/auth/me")
def me():
    if not session.get("uid"):
        return jsonify({"user": None})
    return jsonify({"user": {"id": session["uid"], "username": session.get("username")}})


# ------------------------------------------------------------------ datasets
def _dataset_view(ws, table: str, full: bool = False) -> dict[str, Any]:
    m = ws.meta[table]
    out = {k: m[k] for k in ("dataset_id", "name", "filename", "rows", "columns", "quality_score", "ingest_warnings", "renamed_columns", "size_bytes")}
    out["schema"] = ws.schemas[table] if full else [{k: c[k] for k in ("name", "kind", "role", "missing_pct", "unique")} for c in ws.schemas[table]]
    if full:
        out["quality"] = ws.quality[table]
    return out


@bp.get("/api/datasets")
@login_required
def list_datasets():
    ws = svc().registry.workspace(uid())
    return jsonify({"datasets": [_dataset_view(ws, t) for t in ws.tables], "relationships": ws.relationships,
                    "fact_table": catalog.fact_table(ws)})


@bp.post("/api/datasets")
@login_required
def upload():
    files = request.files.getlist("files")
    if not files:
        raise ValidationError("No files were uploaded. Choose one or more .csv files.")
    if len(files) > MAX_FILES:
        raise ValidationError(f"Upload at most {MAX_FILES} files at a time.")
    ok, failed = [], []
    for f in files:
        name = Path(f.filename or "").name
        try:
            meta = svc().registry.add(uid(), name, f.read())
            ok.append({k: meta[k] for k in ("dataset_id", "name", "filename", "rows", "columns", "quality_score", "ingest_warnings")})
        except AppError as exc:
            failed.append({"filename": name, "error": exc.message})
    svc().cache.clear() if ok else None
    return jsonify({"uploaded": ok, "failed": failed}), (200 if ok else 400)


@bp.post("/api/datasets/samples")
@login_required
def load_samples():
    ws = svc().registry.workspace(uid())
    existing = {m["filename"] for m in ws.meta.values()}
    ok = []
    for name in SAMPLE_NAMES:                       # allowlisted names only – no user-controlled paths
        if name in existing or not (SAMPLES_DIR / name).exists():
            continue
        meta = svc().registry.add(uid(), name, (SAMPLES_DIR / name).read_bytes())
        ok.append(meta["filename"])
    return jsonify({"loaded": ok})


@bp.get("/api/datasets/<ds_id>")
@login_required
def dataset_detail(ds_id: str):
    ws = svc().registry.workspace(uid())
    table = ws.table_for_dataset(ds_id)
    if not table:
        raise NotFoundError("Dataset not found.")
    view = _dataset_view(ws, table, full=True)
    view["preview"] = json.loads(ws.tables[table].head(10).to_json(orient="split", date_format="iso"))
    return jsonify(view)


@bp.delete("/api/datasets/<ds_id>")
@login_required
def delete_dataset(ds_id: str):
    svc().registry.remove(uid(), ds_id)
    svc().cache.clear()
    return jsonify({"ok": True})


# ------------------------------------------------------------------ dashboard & quick analyses
@bp.get("/api/dashboard")
@login_required
def dashboard():
    ctx = _ctx()
    if not ctx.ws.tables:
        return jsonify({"empty": True})
    data = svc().router.call("generate_insights", {}, ctx).data
    from ..services.narrator_utils import jsonable
    return jsonify(jsonable({k: v for k, v in data.items() if k not in ("monthly", "cached")}))


@bp.get("/api/quick/<kind>")
@login_required
def quick(kind: str):
    questions = {"anomalies": "Detect anomalies in the dataset.", "quality": "Check data quality.",
                 "summary": "Give me an executive summary.", "schema": "What columns are in the data?"}
    if kind == "forecast":
        h = max(1, min(24, request.args.get("horizon", 6, type=int)))
        question = f"Forecast the next {h} months."
    elif kind in questions:
        question = questions[kind]
    else:
        raise NotFoundError()
    return jsonify(_run_blocking(question, None))


def _run_blocking(question: str, state: dict | None) -> dict[str, Any]:
    result = None
    for ev in svc().orchestrator.run(svc().registry.workspace(uid()), question, state, uid(), g.request_id):
        if ev["type"] == "result":
            result = ev
    return result["result"]


# ------------------------------------------------------------------ conversations
@bp.post("/api/conversations")
@login_required
def new_conversation():
    return jsonify({"id": svc().db.create_conversation(uid())}), 201


@bp.get("/api/conversations")
@login_required
def list_conversations():
    return jsonify({"conversations": [dict(r) for r in svc().db.list_conversations(uid())]})


@bp.get("/api/conversations/<cid>/messages")
@login_required
def messages(cid: str):
    if not svc().db.get_conversation(uid(), cid):
        raise NotFoundError("Conversation not found.")
    return jsonify({"messages": svc().db.list_messages(cid)})


def _prepare_ask(cid: str) -> tuple[str, dict]:
    conv = svc().db.get_conversation(uid(), cid)
    if not conv:
        raise NotFoundError("Conversation not found.")
    question = str((request.get_json(silent=True) or {}).get("question", "")).strip()
    if not question:
        raise ValidationError("Please type a question.")
    if len(question) > MAX_QUESTION_LEN:
        raise ValidationError(f"Questions are limited to {MAX_QUESTION_LEN} characters.")
    return question, json.loads(conv["state_json"] or "{}")


def _persist(cid: str, question: str, event: dict[str, Any]) -> str:
    db = svc().db
    db.add_message(cid, "user", question)
    mid = db.add_message(cid, "assistant", event["result"].get("answer", ""), event["result"])
    db.set_state(cid, event["state"])
    if len(db.list_messages(cid)) <= 2:
        db.set_title(cid, question)
    return mid


@bp.post("/api/conversations/<cid>/ask")
@login_required
def ask(cid: str):
    question, state = _prepare_ask(cid)
    final = None
    for ev in svc().orchestrator.run(svc().registry.workspace(uid()), question, state, uid(), g.request_id):
        if ev["type"] == "result":
            final = ev
    mid = _persist(cid, question, final)
    return jsonify({"message_id": mid, "result": final["result"]})


@bp.post("/api/conversations/<cid>/ask/stream")
@login_required
def ask_stream(cid: str):
    """Server-Sent Events: one `status` event per real pipeline step, then a final `result` event."""
    question, state = _prepare_ask(cid)
    ws, user, rid = svc().registry.workspace(uid()), uid(), g.request_id
    db = svc().db

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

    def gen():
        try:
            for ev in svc().orchestrator.run(ws, question, state, user, rid):
                if ev["type"] == "status":
                    yield sse("status", {"message": ev["message"]})
                else:
                    db.add_message(cid, "user", question)
                    mid = db.add_message(cid, "assistant", ev["result"].get("answer", ""), ev["result"])
                    db.set_state(cid, ev["state"])
                    if len(db.list_messages(cid)) <= 2:
                        db.set_title(cid, question)
                    yield sse("result", {"message_id": mid, "result": ev["result"]})
        except Exception:
            yield sse("error", {"message": "Something went wrong while analysing that."})

    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ reports
def _export(results: list[dict], fmt: str, title: str, basename: str) -> Response:
    fmt_map = {"md": "markdown", "markdown": "markdown", "html": "html", "csv": "csv"}
    if fmt not in fmt_map:
        raise NotFoundError("Unsupported report format. Use md, html or csv.")
    out = svc().router.call("generate_report", {"results": results, "format": fmt_map[fmt], "title": title}, _ctx()).data
    safe = "".join(ch for ch in basename if ch.isalnum() or ch in "-_")[:40] or "report"
    return Response(out["content"], mimetype=out["mimetype"] + "; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.{out["ext"]}"'})


@bp.get("/api/reports/message/<mid>.<fmt>")
@login_required
def report_message(mid: str, fmt: str):
    msg = svc().db.get_message(uid(), mid)
    if not msg or not msg["result"]:
        raise NotFoundError("Result not found.")
    return _export([msg["result"]], fmt, "AI Data Analyst Report", f"analysis-{mid}")


@bp.get("/api/reports/conversation/<cid>.<fmt>")
@login_required
def report_conversation(cid: str, fmt: str):
    if not svc().db.get_conversation(uid(), cid):
        raise NotFoundError("Conversation not found.")
    results = [m["result"] for m in svc().db.list_messages(cid) if m["role"] == "assistant" and m["result"] and m["result"].get("ok")]
    return _export(results, fmt, "AI Data Analyst – Session Report", f"session-{cid}")


@bp.get("/api/metrics")
@login_required
def metrics():
    s = svc()
    return jsonify({"cache": s.cache.stats(), "tools": {k: {**v, "avg_ms": round(v["total_ms"] / v["calls"], 2) if v["calls"] else 0}
                                                         for k, v in s.router.metrics.items()}})
