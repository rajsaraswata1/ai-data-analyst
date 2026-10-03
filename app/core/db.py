"""SQLite persistence for users, dataset metadata, conversations and messages."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
  filename TEXT NOT NULL, file_path TEXT NOT NULL, meta_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
  state_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, result_json TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class AppDB:
    def __init__(self, path: Path):
        self.path = str(path)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # users
    def create_user(self, username: str, password_hash: str) -> str:
        uid = new_id()
        with self.conn() as c:
            c.execute("INSERT INTO users VALUES(?,?,?,?)", (uid, username, password_hash, time.time()))
        return uid

    def get_user_by_name(self, username: str) -> Optional[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def get_user(self, uid: str) -> Optional[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    # datasets
    def save_dataset(self, ds_id: str, user_id: str, name: str, filename: str, file_path: str, meta: dict) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO datasets VALUES(?,?,?,?,?,?,?)",
                      (ds_id, user_id, name, filename, file_path, json.dumps(meta, default=str), time.time()))

    def list_datasets(self, user_id: str) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM datasets WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()

    def delete_dataset(self, user_id: str, ds_id: str) -> Optional[str]:
        with self.conn() as c:
            row = c.execute("SELECT file_path FROM datasets WHERE id=? AND user_id=?", (ds_id, user_id)).fetchone()
            c.execute("DELETE FROM datasets WHERE id=? AND user_id=?", (ds_id, user_id))
        return row["file_path"] if row else None

    # conversations
    def create_conversation(self, user_id: str, title: str = "New analysis") -> str:
        cid = new_id()
        with self.conn() as c:
            c.execute("INSERT INTO conversations VALUES(?,?,?,?,?)", (cid, user_id, title, "{}", time.time()))
        return cid

    def get_conversation(self, user_id: str, cid: str) -> Optional[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, user_id)).fetchone()

    def list_conversations(self, user_id: str) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT id,title,created_at FROM conversations WHERE user_id=? ORDER BY created_at DESC",
                             (user_id,)).fetchall()

    def set_state(self, cid: str, state: dict) -> None:
        with self.conn() as c:
            c.execute("UPDATE conversations SET state_json=? WHERE id=?", (json.dumps(state, default=str), cid))

    def set_title(self, cid: str, title: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE conversations SET title=? WHERE id=?", (title[:80], cid))

    def add_message(self, cid: str, role: str, content: str, result: Optional[dict] = None) -> str:
        mid = new_id()
        with self.conn() as c:
            c.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)",
                      (mid, cid, role, content, json.dumps(result, default=str) if result else None, time.time()))
        return mid

    def list_messages(self, cid: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at, rowid", (cid,)).fetchall()
        return [{"id": r["id"], "role": r["role"], "content": r["content"],
                 "result": json.loads(r["result_json"]) if r["result_json"] else None} for r in rows]

    def get_message(self, user_id: str, mid: str) -> Optional[dict[str, Any]]:
        with self.conn() as c:
            r = c.execute("""SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id
                             WHERE m.id=? AND c.user_id=?""", (mid, user_id)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "conversation_id": r["conversation_id"], "role": r["role"], "content": r["content"],
                "result": json.loads(r["result_json"]) if r["result_json"] else None}
