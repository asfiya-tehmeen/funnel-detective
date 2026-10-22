"""Approved, parameterized SQL templates.

Every analytical number in the product comes from one of these templates. Identifiers
(metric expressions, dimension names) are only ever taken from whitelists defined in this
module; user-supplied *values* (dates, segment values) are always bound as parameters.
The exact SQL text and parameters that were executed are recorded in a QueryTrace and
returned to the UI.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import duckdb
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import DATA_END, DATA_START, DEFAULT_BASELINE, DEFAULT_CURRENT
from app.data.schema import DIMENSIONS

Dimension = Literal["market", "device", "channel", "property_type", "price_band", "intent", "visitor_type"]
MetricKey = Literal[
    "search_to_listing", "listing_to_contact", "listing_to_contact_start", "contact_start_to_submit",
    "contact_to_viewing", "save_rate", "qualified_contact_rate", "listing_to_contact_raw",
]


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    numerator: str          # SQL boolean expression over the sessions table
    denominator: str        # SQL boolean expression over the sessions table
    numerator_label: str
    denominator_label: str
    description: str


METRICS: dict[str, MetricDef] = {
    m.key: m
    for m in [
        MetricDef("search_to_listing", "Search → listing", "has_search AND has_listing", "has_search",
                  "Search sessions that viewed ≥1 listing", "Sessions with ≥1 search_submitted",
                  "Share of search sessions that go on to open a listing-detail page."),
        MetricDef("listing_to_contact", "Listing → contact", "has_listing AND has_contact", "has_listing",
                  "Listing sessions with ≥1 validated contact", "Sessions with ≥1 listing_viewed",
                  "Share of sessions that viewed a listing and submitted a validated agent contact "
                  "(contact_submitted, status ok, with a lead_id)."),
        MetricDef("listing_to_contact_start", "Listing → contact start", "has_listing AND has_contact_start",
                  "has_listing", "Listing sessions that opened the contact form", "Sessions with ≥1 listing_viewed",
                  "Share of listing sessions that start the contact flow (intent to contact)."),
        MetricDef("contact_start_to_submit", "Contact start → submit", "has_contact_start AND has_contact",
                  "has_contact_start", "Contact-start sessions with a validated contact",
                  "Sessions with ≥1 contact_started",
                  "Completion rate of the contact form (where technical regressions show up)."),
        MetricDef("contact_to_viewing", "Contact → viewing", "has_contact AND has_viewing", "has_contact",
                  "Contact sessions that requested a viewing", "Sessions with a validated contact",
                  "Share of contacting sessions that also book a viewing."),
        MetricDef("save_rate", "Listing → save", "has_listing AND has_save", "has_listing",
                  "Listing sessions that saved a property", "Sessions with ≥1 listing_viewed",
                  "Share of listing sessions that save a property (a softer intent signal)."),
        MetricDef("qualified_contact_rate", "Listing → qualified contact", "has_listing AND has_qualified",
                  "has_listing", "Listing sessions with a qualified lead", "Sessions with ≥1 listing_viewed",
                  "Share of listing sessions that produce a lead scored as qualified."),
        MetricDef("listing_to_contact_raw", "Listing → contact (raw events)", "has_listing AND has_contact_raw",
                  "has_listing", "Listing sessions with ANY contact_submitted event",
                  "Sessions with ≥1 listing_viewed",
                  "Naive definition counting every contact_submitted event, including failed submits and "
                  "events without a lead_id. Used only to expose tracking problems."),
    ]
}


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date

    @model_validator(mode="after")
    def _check(self) -> "Window":
        if self.start > self.end:
            raise ValueError("window start must be on or before end")
        if self.start < DATA_START or self.end > DATA_END:
            raise ValueError(f"window must be within {DATA_START} .. {DATA_END}")
        return self


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: Window = Field(default_factory=lambda: Window(start=DEFAULT_BASELINE[0], end=DEFAULT_BASELINE[1]))
    current: Window = Field(default_factory=lambda: Window(start=DEFAULT_CURRENT[0], end=DEFAULT_CURRENT[1]))

    @model_validator(mode="after")
    def _no_overlap(self) -> "Comparison":
        if self.baseline.end >= self.current.start:
            raise ValueError("baseline window must end before the current window starts")
        return self


class Filters(BaseModel):
    """Segment filters. Empty list = all values. Values are validated against the schema."""
    model_config = ConfigDict(extra="forbid")
    market: list[str] = []
    device: list[str] = []
    channel: list[str] = []
    property_type: list[str] = []
    price_band: list[str] = []
    intent: list[str] = []
    visitor_type: list[str] = []

    @field_validator("*")
    @classmethod
    def _allowed(cls, v: list[str], info) -> list[str]:
        allowed = DIMENSIONS[info.field_name]
        bad = [x for x in v if x not in allowed]
        if bad:
            raise ValueError(f"invalid {info.field_name} value(s) {bad}; allowed: {allowed}")
        return sorted(set(v), key=allowed.index)

    def active(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.model_dump().items() if v}

    def merged(self, **extra: list[str]) -> "Filters":
        data = self.model_dump()
        for k, v in extra.items():
            data[k] = v
        return Filters(**data)

    def describe(self) -> str:
        act = self.active()
        return ", ".join(f"{k}={'/'.join(v)}" for k, v in act.items()) if act else "all traffic"


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------
@dataclass
class QueryRecord:
    id: str
    name: str
    sql: str
    params: list[Any]
    row_count: int
    duration_ms: float

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "sql": self.sql.strip(),
                "params": [str(p) for p in self.params], "row_count": self.row_count,
                "duration_ms": round(self.duration_ms, 2)}


@dataclass
class QueryTrace:
    records: list[QueryRecord] = field(default_factory=list)

    def run(self, cur: duckdb.DuckDBPyConnection, name: str, sql: str, params: list[Any]) -> tuple[pd.DataFrame, str]:
        t0 = time.perf_counter()
        df = cur.execute(sql, params).df()
        rec = QueryRecord(id="q_" + uuid.uuid4().hex[:8], name=name, sql=sql, params=list(params),
                          row_count=len(df), duration_ms=(time.perf_counter() - t0) * 1000)
        self.records.append(rec)
        return df, rec.id

    def to_list(self) -> list[dict]:
        return [r.to_dict() for r in self.records]


# ---------------------------------------------------------------------------
# Template builders
# ---------------------------------------------------------------------------
def _dims(group_by: list[str]) -> list[str]:
    for d in group_by:
        if d not in DIMENSIONS:
            raise ValueError(f"unknown dimension {d!r}")
    return list(group_by)


def filter_sql(filters: Filters) -> tuple[str, list[Any]]:
    clauses, params = [], []
    for dim, values in filters.active().items():
        clauses.append(f"{dim} IN ({', '.join('?' for _ in values)})")
        params.extend(values)
    return ("".join(f"\n  AND {c}" for c in clauses), params)


def metric_comparison_sql(metric: str, filters: Filters, comparison: Comparison,
                          group_by: list[str] | None = None) -> tuple[str, list[Any]]:
    m = METRICS[metric]
    dims = _dims(group_by or [])
    fsql, fparams = filter_sql(filters)
    dim_cols = "".join(f"{d}, " for d in dims)
    sql = f"""
SELECT
  CASE WHEN session_date BETWEEN ? AND ? THEN 'baseline' ELSE 'current' END AS period,
  {dim_cols}COUNT(*) FILTER (WHERE {m.denominator}) AS denominator,
  COUNT(*) FILTER (WHERE {m.numerator}) AS numerator
FROM sessions
WHERE (session_date BETWEEN ? AND ? OR session_date BETWEEN ? AND ?){fsql}
GROUP BY ALL
ORDER BY ALL"""
    b, c = comparison.baseline, comparison.current
    return sql, [b.start, b.end, b.start, b.end, c.start, c.end, *fparams]


def metric_trend_sql(metric: str, filters: Filters, window: Window, grain: Literal["day", "week"] = "day",
                     split_by: str | None = None) -> tuple[str, list[Any]]:
    m = METRICS[metric]
    if grain not in ("day", "week"):
        raise ValueError("grain must be 'day' or 'week'")
    split = _dims([split_by])[0] if split_by else None
    fsql, fparams = filter_sql(filters)
    bucket = "session_date" if grain == "day" else "date_trunc('week', session_date)"
    split_col = f"{split}, " if split else ""
    sql = f"""
SELECT
  CAST({bucket} AS DATE) AS bucket,
  {split_col}COUNT(*) FILTER (WHERE {m.denominator}) AS denominator,
  COUNT(*) FILTER (WHERE {m.numerator}) AS numerator
FROM sessions
WHERE session_date BETWEEN ? AND ?{fsql}
GROUP BY ALL
ORDER BY ALL"""
    return sql, [window.start, window.end, *fparams]


def funnel_steps_sql(filters: Filters, window: Window) -> tuple[str, list[Any]]:
    fsql, fparams = filter_sql(filters)
    sql = f"""
SELECT
  COUNT(*)                                        AS sessions,
  COUNT(*) FILTER (WHERE has_search)              AS search_sessions,
  COUNT(*) FILTER (WHERE has_results)             AS results_sessions,
  COUNT(*) FILTER (WHERE has_listing)             AS listing_sessions,
  COUNT(*) FILTER (WHERE has_listing AND has_save) AS save_sessions,
  COUNT(*) FILTER (WHERE has_listing AND has_contact_start) AS contact_start_sessions,
  COUNT(*) FILTER (WHERE has_listing AND has_contact) AS contact_sessions,
  COUNT(*) FILTER (WHERE has_contact AND has_viewing) AS viewing_sessions,
  COALESCE(SUM(n_qualified_leads), 0)             AS qualified_contacts
FROM sessions
WHERE session_date BETWEEN ? AND ?{fsql}"""
    return sql, [window.start, window.end, *fparams]


def quality_sql(filters: Filters, comparison: Comparison, group_by: list[str] | None = None) -> tuple[str, list[Any]]:
    """Session-level tracking-quality indicators per period (and optional segments)."""
    dims = _dims(group_by or [])
    fsql, fparams = filter_sql(filters)
    dim_cols = "".join(f"{d}, " for d in dims)
    sql = f"""
SELECT
  CASE WHEN session_date BETWEEN ? AND ? THEN 'baseline' ELSE 'current' END AS period,
  {dim_cols}COUNT(*) AS sessions,
  COUNT(*) FILTER (WHERE has_listing) AS listing_sessions,
  COUNT(*) FILTER (WHERE has_listing AND has_contact) AS validated_contact_sessions,
  COUNT(*) FILTER (WHERE has_listing AND has_contact_raw) AS raw_contact_sessions,
  COUNT(*) FILTER (WHERE has_contact_start) AS contact_start_sessions,
  COUNT(*) FILTER (WHERE n_failed_submits > 0) AS sessions_with_failed_submit,
  COUNT(*) FILTER (WHERE n_submits_without_lead > 0) AS sessions_with_submit_without_lead,
  COUNT(*) FILTER (WHERE n_submits_before_start > 0) AS sessions_with_submit_before_start,
  COUNT(*) FILTER (WHERE has_listing AND NOT has_search) AS listing_sessions_without_search
FROM sessions
WHERE (session_date BETWEEN ? AND ? OR session_date BETWEEN ? AND ?){fsql}
GROUP BY ALL
ORDER BY ALL"""
    b, c = comparison.baseline, comparison.current
    return sql, [b.start, b.end, b.start, b.end, c.start, c.end, *fparams]


def duplicate_events_sql(filters: Filters, window: Window) -> tuple[str, list[Any]]:
    fsql, fparams = filter_sql(filters)
    sql = f"""
SELECT event_type,
  COUNT(*) AS events,
  COUNT(*) FILTER (WHERE is_duplicate) AS duplicate_events
FROM events_all
WHERE CAST(event_ts AS DATE) BETWEEN ? AND ?{fsql}
GROUP BY ALL
ORDER BY ALL"""
    return sql, [window.start, window.end, *fparams]


def quarantine_sql() -> tuple[str, list[Any]]:
    return ("""
SELECT quarantine_reason, COUNT(*) AS events
FROM quarantine
GROUP BY ALL
ORDER BY events DESC""", [])


def version_breakdown_sql(metric: str, filters: Filters, comparison: Comparison) -> tuple[str, list[Any]]:
    m = METRICS[metric]
    fsql, fparams = filter_sql(filters)
    sql = f"""
SELECT
  CASE WHEN session_date BETWEEN ? AND ? THEN 'baseline' ELSE 'current' END AS period,
  client_version,
  COUNT(*) FILTER (WHERE {m.denominator}) AS denominator,
  COUNT(*) FILTER (WHERE {m.numerator}) AS numerator,
  COUNT(*) FILTER (WHERE n_failed_submits > 0) AS sessions_with_failed_submit
FROM sessions
WHERE (session_date BETWEEN ? AND ? OR session_date BETWEEN ? AND ?){fsql}
GROUP BY ALL
ORDER BY ALL"""
    b, c = comparison.baseline, comparison.current
    return sql, [b.start, b.end, b.start, b.end, c.start, c.end, *fparams]


def experiment_counts_sql(experiment_id: str, metric: str) -> tuple[str, list[Any]]:
    m = METRICS[metric]
    sql = f"""
SELECT variant,
  COUNT(DISTINCT user_id) AS users,
  COUNT(*) FILTER (WHERE {m.denominator}) AS denominator,
  COUNT(*) FILTER (WHERE {m.numerator}) AS numerator,
  MIN(session_date) AS first_day,
  MAX(session_date) AS last_day
FROM sessions
WHERE experiment_id = ?
GROUP BY ALL
ORDER BY ALL"""
    return sql, [experiment_id]


# Catalogue shown in the Data tab: name -> (description, builder signature description)
APPROVED_QUERIES: dict[str, dict[str, Any]] = {
    "metric_comparison": {
        "description": "Numerator/denominator for one metric in the baseline and current windows, optionally "
                       "grouped by up to two dimensions.",
        "params": {"metric": list(METRICS), "group_by": list(DIMENSIONS), "filters": "segment filters",
                   "comparison": "baseline/current windows"},
    },
    "metric_trend": {
        "description": "Daily or weekly numerator/denominator for one metric, optionally split by a dimension.",
        "params": {"metric": list(METRICS), "grain": ["day", "week"], "split_by": list(DIMENSIONS),
                   "filters": "segment filters", "window": "date window"},
    },
    "funnel_steps": {
        "description": "Session counts at each funnel step and total qualified leads in a window.",
        "params": {"filters": "segment filters", "window": "date window"},
    },
    "quality_by_segment": {
        "description": "Tracking-quality indicators (failed submits, submits without lead_id, out-of-order "
                       "submits, raw vs validated contacts) per period.",
        "params": {"group_by": list(DIMENSIONS), "filters": "segment filters", "comparison": "windows"},
    },
    "duplicate_events": {
        "description": "Duplicate client firings (same session/event/listing within 5 seconds) by event type.",
        "params": {"filters": "segment filters", "window": "date window"},
    },
    "quarantine_summary": {"description": "Rows rejected by schema validation, by reason.", "params": {}},
    "version_breakdown": {
        "description": "Metric and failed-submit sessions by client version (web release / app version) per period.",
        "params": {"metric": list(METRICS), "filters": "segment filters", "comparison": "windows"},
    },
    "experiment_counts": {
        "description": "Per-variant users, denominator and numerator for a logged experiment.",
        "params": {"experiment_id": ["EXP-014"], "metric": list(METRICS)},
    },
}
