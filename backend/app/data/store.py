"""DuckDB-backed data store.

Load pipeline (all SQL is kept in this module and exposed via /api/sql/pipeline):

  raw_events   - parquet loaded as-is
  quarantine   - rows that fail schema checks, with a reason
  events_all   - schema-valid rows, with an is_duplicate flag
  events       - events_all without duplicates (the analytical event table)
  sessions     - one row per session with funnel-step flags (the analytical session table)

After loading, external file/network access is disabled and the configuration is
locked, so no later query (including user SQL) can read the filesystem.
"""
from __future__ import annotations

import threading
from pathlib import Path

import duckdb

from app.config import DATA_END, DATA_START, EVENTS_PATH
from app.data.schema import DEVICES, EVENT_TYPES, MARKETS

DUPLICATE_WINDOW_SECONDS = 5

RAW_SCHEMA = {
    "event_id": "VARCHAR", "session_id": "VARCHAR", "user_id": "VARCHAR", "event_ts": "TIMESTAMP",
    "event_type": "VARCHAR", "event_status": "VARCHAR", "market": "VARCHAR", "device": "VARCHAR",
    "channel": "VARCHAR", "visitor_type": "VARCHAR", "intent": "VARCHAR", "property_type": "VARCHAR",
    "price_band": "VARCHAR", "listing_id": "VARCHAR", "client_version": "VARCHAR", "lead_id": "VARCHAR",
    "is_qualified": "BOOLEAN", "error_code": "VARCHAR", "experiment_id": "VARCHAR", "variant": "VARCHAR",
}


def _in_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


PIPELINE_SQL: dict[str, str] = {
    "quarantine": f"""
CREATE OR REPLACE TABLE quarantine AS
SELECT *,
  CASE
    WHEN session_id IS NULL THEN 'missing session_id'
    WHEN user_id IS NULL THEN 'missing user_id'
    WHEN event_ts IS NULL THEN 'missing event_ts'
    WHEN event_type NOT IN ({_in_list(EVENT_TYPES)}) THEN 'unknown event_type'
    WHEN market IS NULL OR market NOT IN ({_in_list(MARKETS)}) THEN 'invalid market'
    WHEN device IS NULL OR device NOT IN ({_in_list(DEVICES)}) THEN 'invalid device'
    WHEN event_ts < DATE '{DATA_START}' OR event_ts >= DATE '{DATA_END}' + INTERVAL 1 DAY THEN 'timestamp outside data window'
  END AS quarantine_reason
FROM raw_events
WHERE quarantine_reason IS NOT NULL""",
    "events_all": f"""
CREATE OR REPLACE TABLE events_all AS
WITH valid AS (
  SELECT * FROM raw_events WHERE event_id NOT IN (SELECT event_id FROM quarantine)
), ordered AS (
  SELECT *,
    LAG(event_ts) OVER (
      PARTITION BY session_id, event_type, COALESCE(listing_id, ''), COALESCE(lead_id, ''), event_status
      ORDER BY event_ts, event_id
    ) AS prev_ts
  FROM valid
)
SELECT * EXCLUDE (prev_ts),
  COALESCE(event_ts - prev_ts < INTERVAL {DUPLICATE_WINDOW_SECONDS} SECOND, FALSE) AS is_duplicate
FROM ordered""",
    "events": """
CREATE OR REPLACE VIEW events AS
SELECT * EXCLUDE (is_duplicate) FROM events_all WHERE NOT is_duplicate""",
    "sessions": """
CREATE OR REPLACE TABLE sessions AS
WITH first_contact_start AS (
  SELECT session_id, MIN(event_ts) AS ts FROM events WHERE event_type = 'contact_started' GROUP BY 1
)
SELECT
  e.session_id,
  arg_min(e.user_id, e.event_ts)        AS user_id,
  CAST(MIN(e.event_ts) AS DATE)         AS session_date,
  arg_min(e.market, e.event_ts)         AS market,
  arg_min(e.device, e.event_ts)         AS device,
  arg_min(e.channel, e.event_ts)        AS channel,
  arg_min(e.visitor_type, e.event_ts)   AS visitor_type,
  arg_min(e.intent, e.event_ts)         AS intent,
  arg_min(e.property_type, e.event_ts)  AS property_type,
  arg_min(e.price_band, e.event_ts)     AS price_band,
  arg_min(e.client_version, e.event_ts) AS client_version,
  arg_min(e.experiment_id, e.event_ts)  AS experiment_id,
  arg_min(e.variant, e.event_ts)        AS variant,
  COUNT(*)                                                         AS n_events,
  bool_or(e.event_type = 'search_submitted')                       AS has_search,
  bool_or(e.event_type = 'results_viewed' AND e.event_status = 'ok') AS has_results,
  bool_or(e.event_type = 'listing_viewed')                         AS has_listing,
  COUNT(*) FILTER (WHERE e.event_type = 'listing_viewed')          AS n_listing_views,
  bool_or(e.event_type = 'property_saved')                         AS has_save,
  bool_or(e.event_type = 'contact_started')                        AS has_contact_start,
  -- Validated contact: successful submit that carries a lead_id from the lead service.
  bool_or(e.event_type = 'contact_submitted' AND e.event_status = 'ok' AND e.lead_id IS NOT NULL) AS has_contact,
  -- Naive contact: any contact_submitted event (what a dashboard counting raw events would show).
  bool_or(e.event_type = 'contact_submitted')                      AS has_contact_raw,
  bool_or(e.event_type = 'contact_submitted' AND e.event_status = 'ok' AND e.lead_id IS NOT NULL
          AND e.is_qualified)                                      AS has_qualified,
  bool_or(e.event_type = 'viewing_requested')                      AS has_viewing,
  COUNT(*) FILTER (WHERE e.event_type = 'contact_submitted' AND e.event_status = 'failed') AS n_failed_submits,
  COUNT(*) FILTER (WHERE e.event_type = 'contact_submitted' AND e.event_status = 'ok' AND e.lead_id IS NULL) AS n_submits_without_lead,
  COUNT(*) FILTER (WHERE e.event_type = 'contact_submitted'
                   AND (fcs.ts IS NULL OR e.event_ts < fcs.ts))    AS n_submits_before_start,
  COUNT(DISTINCT e.lead_id) FILTER (WHERE e.is_qualified)          AS n_qualified_leads
FROM events e
LEFT JOIN first_contact_start fcs USING (session_id)
GROUP BY e.session_id""",
}


class DataStore:
    """Owns the DuckDB database. Use `cursor()` per request/thread."""

    def __init__(self, events_path: Path = EVENTS_PATH, lock_down: bool = True):
        if not events_path.exists():
            raise FileNotFoundError(
                f"Dataset not found at {events_path}. Run `python -m scripts.generate_data` from backend/."
            )
        self.events_path = events_path
        self._con = duckdb.connect(database=":memory:")
        self._lock = threading.Lock()
        path_sql = str(events_path).replace("\\", "/").replace("'", "''")
        # Cast every column to its declared type: an all-null column in parquet has no usable type,
        # and a missing column is a schema error we want to surface immediately.
        available = {r[0] for r in self._con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path_sql}')").fetchall()}
        missing = sorted(set(RAW_SCHEMA) - available)
        if missing:
            raise ValueError(f"Dataset is missing required columns: {missing}")
        cols = ", ".join(f"TRY_CAST({c} AS {t}) AS {c}" for c, t in RAW_SCHEMA.items())
        self._con.execute(f"CREATE TABLE raw_events AS SELECT {cols} FROM read_parquet('{path_sql}')")
        for name in ("quarantine", "events_all", "events", "sessions"):
            self._con.execute(PIPELINE_SQL[name])
        if lock_down:
            self._con.execute("SET enable_external_access = false")
            self._con.execute("SET lock_configuration = true")

    def cursor(self) -> duckdb.DuckDBPyConnection:
        with self._lock:
            return self._con.cursor()

    def interrupt(self) -> None:
        self._con.interrupt()

    def summary(self) -> dict:
        cur = self.cursor()
        q = lambda sql: cur.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "raw_events": q("SELECT COUNT(*) FROM raw_events"),
            "quarantined_events": q("SELECT COUNT(*) FROM quarantine"),
            "duplicate_events": q("SELECT COUNT(*) FROM events_all WHERE is_duplicate"),
            "events": q("SELECT COUNT(*) FROM events"),
            "sessions": q("SELECT COUNT(*) FROM sessions"),
            "users": q("SELECT COUNT(DISTINCT user_id) FROM sessions"),
            "date_min": str(q("SELECT MIN(session_date) FROM sessions")),
            "date_max": str(q("SELECT MAX(session_date) FROM sessions")),
        }


_store: DataStore | None = None
_store_lock = threading.Lock()


def get_store() -> DataStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = DataStore()
        return _store
