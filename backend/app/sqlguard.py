"""Restricted SQL interface for user-authored queries.

Defence in depth:
  1. DuckDB external access is disabled and the configuration locked at load time (no files, no network,
     no extensions, no re-enabling) — see data/store.py.
  2. Exactly one statement, and it must parse as a SELECT (DuckDB's own parser decides, not a regex).
  3. Keyword/function denylist as a second line (PRAGMA, ATTACH, COPY, read_*, ...).
  4. Only whitelisted relations may be referenced.
  5. The query is wrapped with a hard row limit and run with a timeout (interrupt).
This interface is for humans in the Data tab. The AI layer never receives it — the AI can only call
approved, parameterised tools.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

import duckdb

ALLOWED_RELATIONS = {"sessions", "events", "events_all", "quarantine"}
DENY_PATTERNS = [
    r"\battach\b", r"\bdetach\b", r"\bcopy\b", r"\bexport\b", r"\bimport\b", r"\binstall\b", r"\bload\b",
    r"\bpragma\b", r"\bset\b", r"\breset\b", r"\bcall\b", r"\bcreate\b", r"\binsert\b", r"\bupdate\b",
    r"\bdelete\b", r"\bdrop\b", r"\balter\b", r"\btruncate\b", r"\bvacuum\b", r"\bcheckpoint\b",
    r"\bread_\w+\s*\(", r"\bglob\s*\(", r"\bparquet_\w+\s*\(", r"\bsniff_csv\b", r"\bgetenv\s*\(",
    r"\bduckdb_\w+\s*\(", r"\bpg_\w+", r"\binformation_schema\b", r"\bsqlite_\w+", r"'[a-z]+://",
    r"\bcurrent_setting\s*\(", r"\braw_events\b",
]
MAX_SQL_LENGTH = 5000


class SQLRejected(ValueError):
    pass


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)


def check_sql(con: duckdb.DuckDBPyConnection, sql: str) -> str:
    """Return the normalised SELECT text, or raise SQLRejected with a user-facing reason."""
    if not sql or not sql.strip():
        raise SQLRejected("Query is empty.")
    if len(sql) > MAX_SQL_LENGTH:
        raise SQLRejected(f"Query is longer than {MAX_SQL_LENGTH} characters.")
    clean = _strip_comments(sql).strip().rstrip(";").strip()
    if ";" in clean:
        raise SQLRejected("Only a single statement is allowed.")
    lowered = clean.lower()
    for pat in DENY_PATTERNS:
        m = re.search(pat, lowered)
        if m:
            word = m.group().strip(" (")
            raise SQLRejected(f"Query uses a disallowed keyword or function: '{word}'.")
    if re.search(r"\b(?:from|join)\s*'", lowered):
        raise SQLRejected("File paths and string table references are not allowed.")
    try:
        stmts = con.extract_statements(clean)
    except duckdb.Error as exc:
        raise SQLRejected(f"SQL could not be parsed: {str(exc).splitlines()[0]}") from exc
    if len(stmts) != 1:
        raise SQLRejected("Only a single statement is allowed.")
    if stmts[0].type != duckdb.StatementType.SELECT:
        raise SQLRejected(f"Only SELECT queries are allowed (got {stmts[0].type.name}).")
    # Every identifier following FROM/JOIN must be an allowed relation or a CTE defined in the query.
    ctes = set(re.findall(r"(?:with|,)\s*([a-z_][a-z0-9_]*)\s+as\s*\(", lowered))
    for rel in re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_.\"]*)", lowered):
        name = rel.strip('"').split(".")[-1]
        if name not in ALLOWED_RELATIONS and name not in ctes:
            raise SQLRejected(f"Relation '{name}' is not available. Allowed: {', '.join(sorted(ALLOWED_RELATIONS))}.")
    return clean


def run_user_sql(con: duckdb.DuckDBPyConnection, sql: str, row_limit: int = 500, timeout_s: float = 5.0) -> dict[str, Any]:
    clean = check_sql(con, sql)
    row_limit = max(1, min(int(row_limit), 5000))
    wrapped = f"SELECT * FROM ({clean}) AS user_query LIMIT {row_limit + 1}"
    timer = threading.Timer(timeout_s, con.interrupt)
    t0 = time.perf_counter()
    timer.start()
    try:
        df = con.execute(wrapped).df()
    except duckdb.InterruptException as exc:
        raise SQLRejected(f"Query exceeded the {timeout_s:.0f}s time limit and was cancelled.") from exc
    except duckdb.Error as exc:
        raise SQLRejected(f"Query failed: {str(exc).splitlines()[0][:300]}") from exc
    finally:
        timer.cancel()
    truncated = len(df) > row_limit
    df = df.head(row_limit)
    return {
        "columns": [{"name": c, "type": str(t)} for c, t in zip(df.columns, df.dtypes)],
        "rows": _records(df),
        "row_count": len(df),
        "truncated": truncated,
        "row_limit": row_limit,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        "executed_sql": wrapped,
    }


def _records(df) -> list[dict[str, Any]]:
    out = []
    for rec in df.astype(object).where(df.notna(), None).to_dict("records"):
        out.append({k: (v.isoformat() if hasattr(v, "isoformat") else (v.item() if hasattr(v, "item") else v))
                    for k, v in rec.items()})
    return out
