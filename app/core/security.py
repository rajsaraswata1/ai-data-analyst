"""Authentication helpers: scrypt password hashing, login guard, CSRF header check, login throttling."""
from __future__ import annotations

import re
import threading
import time
from functools import wraps

from flask import jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LEN = 8
MAX_ATTEMPTS, LOCK_SECONDS = 5, 300


def hash_password(password: str) -> str:
    return generate_password_hash(password)  # scrypt + per-password salt, never plaintext


def verify_password(stored_hash: str, password: str) -> bool:
    return check_password_hash(stored_hash, password)


def validate_credentials(username: str, password: str) -> str | None:
    if not USERNAME_RE.match(username or ""):
        return "Username must be 3-32 characters (letters, digits, _ . -)."
    if len(password or "") < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    return None


class LoginThrottle:
    """In-memory brute-force protection keyed by username+ip."""

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            recent = [t for t in self._fails.get(key, []) if now - t < LOCK_SECONDS]
            self._fails[key] = recent
            return len(recent) >= MAX_ATTEMPTS

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return jsonify({"error": {"code": "unauthorized", "message": "Please sign in."}}), 401
        return fn(*args, **kwargs)
    return wrapper


def csrf_ok() -> bool:
    """State-changing requests must carry a custom header (cannot be set cross-site without CORS preflight)."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"
