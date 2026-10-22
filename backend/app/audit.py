"""Append-only audit trail of investigations and SQL runs (SQLite, stdlib only)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import AUDIT_DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  question TEXT NOT NULL,
  mode TEXT NOT NULL,
  intent TEXT,
  metric TEXT,
  filters TEXT,
  n_tool_calls INTEGER,
  n_tool_errors INTEGER,
  n_queries INTEGER,
  leading_hypothesis TEXT,
  narrative_source TEXT,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sql_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  sql_text TEXT NOT NULL,
  params TEXT,
  status TEXT NOT NULL,
  row_count INTEGER,
  error TEXT
);
"""


class AuditLog:
    def __init__(self, path: Path = AUDIT_DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def record_investigation(self, inv: dict[str, Any]) -> None:
        calls = inv.get("tool_calls", [])
        leading = next((h for h in inv.get("hypotheses", [])), None)
        row = (inv["id"], str(inv["created_at"]), inv["question"], inv["mode"], inv["plan"]["intent"],
               inv["plan"]["metric"], json.dumps(inv["plan"]["filters"]), len(calls),
               sum(c["status"] == "error" for c in calls), len(inv.get("queries", [])),
               f"{leading['id']} {leading['category']} ({leading['status']})" if leading else None,
               inv["narrative"]["source"], json.dumps(inv, default=str))
        with _lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO investigations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)

    def record_sql(self, kind: str, sql: str, params: list[Any] | None, status: str, row_count: int | None,
                   error: str | None) -> None:
        with _lock, self._conn() as c:
            c.execute("INSERT INTO sql_runs (created_at, kind, sql_text, params, status, row_count, error) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (datetime.now(timezone.utc).isoformat(), kind, sql[:20000], json.dumps(params or [], default=str),
                       status, row_count, error))

    def list_investigations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT id, created_at, question, mode, intent, metric, filters, n_tool_calls, "
                             "n_tool_errors, n_queries, leading_hypothesis, narrative_source FROM investigations "
                             "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "filters": json.loads(r["filters"] or "{}")} for r in rows]

    def get_investigation(self, inv_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT payload FROM investigations WHERE id = ?", (inv_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_sql_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT * FROM sql_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


_audit: AuditLog | None = None


def get_audit() -> AuditLog:
    global _audit
    with _lock:
        if _audit is None:
            _audit = AuditLog()
        return _audit
