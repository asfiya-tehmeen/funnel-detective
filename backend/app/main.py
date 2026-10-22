"""FastAPI application: every route is a thin layer over deterministic analytics."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.analytics import metrics as M
from app.analytics import queries as Q
from app.analytics.queries import Comparison, Dimension, Filters, MetricKey, QueryTrace, Window
from app.analytics.stats import P_VALUE_NOTE, mde_for_sample_size, sample_size_two_proportions
from app.audit import get_audit
from app.config import DATA_END, DATA_START, DISCLAIMER, EVENTS_PATH, get_settings
from app.data.schema import COLUMN_DOCS, DIMENSIONS, EVENT_TYPES
from app.data.store import PIPELINE_SQL, get_store
from app.engine.orchestrator import investigate, simpson_table
from app.engine.schemas import EVIDENCE_LEVEL_HELP
from app.engine.tools import TOOLS, ToolContext, run_tool, tool_catalog
from app.experiments.analysis import ResultsRequest, SimulationRequest, analyse, simulate
from app.experiments.planner import PlanRequest, build_plan
from app.impact import ImpactRequest, estimate
from app.llm.base import get_provider
from app.sqlguard import SQLRejected, run_user_sql

log = logging.getLogger("funnel_detective")

EXAMPLE_QUESTIONS = [
    "Why did mobile contact conversion decline in Riyadh?",
    "Is the overall conversion decline real, or is the traffic mix changing?",
    "Which funnel step has the largest recoverable opportunity?",
    "Which market should we investigate first?",
    "Are users abandoning the contact flow, or is tracking broken?",
    "Why does the app convert better than the website?",
    "Which segments show a genuine change in behaviour?",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not EVENTS_PATH.exists() and os.getenv("FD_AUTOGENERATE", "1") == "1":
        from scripts.generate_data import TRUTH_PATH
        from app.data.generator import write_dataset
        log.warning("Dataset missing; generating it now (deterministic seed).")
        write_dataset(EVENTS_PATH, TRUTH_PATH)
    get_store()
    get_audit()
    yield


app = FastAPI(title="Funnel Detective API", version=__version__, lifespan=lifespan,
              description="Synthetic-data portfolio project. " + DISCLAIMER)
app.add_middleware(CORSMiddleware, allow_origins=list(get_settings().cors_origins), allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type"])


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(SQLRejected)
async def sql_rejected_handler(_: Request, exc: SQLRejected):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def cursor():
    return get_store().cursor()


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_settings()
    return {"status": "ok", "version": __version__, "llm_mode": "llm" if s.llm_enabled else "deterministic",
            "llm_model": s.llm_model if s.llm_enabled else None, "data": get_store().summary()}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    s = get_settings()
    c = Comparison()
    return {
        "disclaimer": DISCLAIMER,
        "dimensions": DIMENSIONS,
        "metrics": {k: {"label": m.label, "numerator": m.numerator_label, "denominator": m.denominator_label,
                        "description": m.description, "numerator_sql": m.numerator, "denominator_sql": m.denominator}
                    for k, m in Q.METRICS.items()},
        "date_range": {"start": str(DATA_START), "end": str(DATA_END)},
        "default_comparison": c.model_dump(mode="json"),
        "llm": {"enabled": s.llm_enabled, "provider": "anthropic" if s.llm_enabled else None,
                "model": s.llm_model if s.llm_enabled else None},
        "example_questions": EXAMPLE_QUESTIONS,
        "evidence_levels": EVIDENCE_LEVEL_HELP,
        "p_value_note": P_VALUE_NOTE,
    }


# ---------------------------------------------------------------------------
# overview / detect / diagnose
# ---------------------------------------------------------------------------
KPI_METRICS = ["search_to_listing", "listing_to_contact", "contact_to_viewing"]


@app.post("/api/overview")
def overview(scope: Scope) -> dict[str, Any]:
    cur, trace = cursor(), QueryTrace()
    f, c = scope.filters, scope.comparison
    kpis = []
    for metric in KPI_METRICS:
        m = Q.METRICS[metric]
        try:
            res = M.overall_change(cur, trace, metric, f, c)
            wide, qid = M.metric_comparison(cur, trace, metric, f, c, ["channel"], name=f"kpi_{metric}_by_channel")
            adj = M.stratified_change(wide) if len(f.channel) != 1 else None
        except ValueError as exc:
            kpis.append({"metric": metric, "label": m.label, "error": str(exc)})
            continue
        kpis.append({"metric": metric, "label": m.label, "description": m.description,
                     "numerator": m.numerator_label, "denominator": m.denominator_label,
                     "test": res["test"], "channel_adjusted": adj, "query_id": res["query_id"]})
    fb, fcur = M.funnel(cur, trace, f, c.baseline), M.funnel(cur, trace, f, c.current)
    full = Window(start=DATA_START, end=DATA_END)
    trends = {metric: M.trend(cur, trace, metric, f, full, "week") for metric in KPI_METRICS}
    markets = M.segment_table(cur, trace, "listing_to_contact", f.merged(market=[]), c, ["market"],
                              name="market_comparison", adjust_for="channel")
    warnings = M.quality_warnings(cur, trace, f, c)
    return {"scope": f.describe(), "comparison": c.model_dump(mode="json"), "kpis": kpis,
            "qualified_contacts": {"baseline": fb["qualified_contacts"], "current": fcur["qualified_contacts"],
                                   "definition": "Distinct lead_ids on validated contact_submitted events scored as "
                                                 "qualified, in sessions within the window."},
            "funnel": {"baseline": fb, "current": fcur}, "trends": trends, "markets": markets,
            "warnings": warnings, "queries": trace.to_list()}


class DetectRequest(Scope):
    metrics: list[MetricKey] = Field(default_factory=lambda: ["listing_to_contact_start", "contact_start_to_submit",
                                                               "contact_to_viewing", "listing_to_contact"],
                                     max_length=6)


@app.post("/api/detect")
def detect(req: DetectRequest) -> dict[str, Any]:
    cur, trace = cursor(), QueryTrace()
    scans = {m: M.segment_scan(cur, trace, m, req.filters, req.comparison) for m in req.metrics}
    return {"scans": scans, "queries": trace.to_list()}


class BreakdownRequest(Scope):
    metric: MetricKey = "listing_to_contact"
    dimension: Dimension = "channel"
    adjust_for: Optional[Dimension] = "channel"


@app.post("/api/breakdown")
def breakdown(req: BreakdownRequest) -> dict[str, Any]:
    cur, trace = cursor(), QueryTrace()
    adj = req.adjust_for if req.adjust_for != req.dimension else None
    tbl = M.segment_table(cur, trace, req.metric, req.filters, req.comparison, [req.dimension], adjust_for=adj)
    dec = M.decompose(cur, trace, req.metric, req.filters, req.comparison, req.dimension)
    steps = {m: M.overall_change(cur, trace, m, req.filters, req.comparison)["test"]
             for m in ["search_to_listing", "listing_to_contact_start", "contact_start_to_submit",
                       "contact_to_viewing", "save_rate"]}
    return {"table": tbl, "decomposition": dec, "steps": steps, "queries": trace.to_list()}


class TrendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: MetricKey = "listing_to_contact"
    filters: Filters = Field(default_factory=Filters)
    window: Window = Field(default_factory=lambda: Window(start=DATA_START, end=DATA_END))
    grain: Literal["day", "week"] = "week"
    split_by: Optional[Dimension] = None


@app.post("/api/trend")
def trend(req: TrendRequest) -> dict[str, Any]:
    cur, trace = cursor(), QueryTrace()
    out = M.trend(cur, trace, req.metric, req.filters, req.window, req.grain, req.split_by)
    out["queries"] = trace.to_list()
    return out


class SimpsonRequest(Scope):
    metric: MetricKey = "listing_to_contact"
    dimension: Dimension = "channel"


@app.post("/api/simpson")
def simpson(req: SimpsonRequest) -> dict[str, Any]:
    ctx = ToolContext(cur=cursor())
    out = simpson_table(ctx, req.metric, req.filters, req.comparison, req.dimension)
    out["decomposition"] = M.decompose(ctx.cur, ctx.trace, req.metric, req.filters, req.comparison, req.dimension)
    out["queries"] = ctx.trace.to_list()
    return out


# ---------------------------------------------------------------------------
# investigations
# ---------------------------------------------------------------------------
class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=500)
    filters: Optional[Filters] = None
    comparison: Comparison = Field(default_factory=Comparison)
    use_llm: bool = True
    focus: Optional["Focus"] = None


class Focus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: MetricKey
    filters: Filters = Field(default_factory=Filters)


InvestigationRequest.model_rebuild()


@app.post("/api/investigations")
def create_investigation(req: InvestigationRequest) -> dict[str, Any]:
    provider = get_provider() if req.use_llm else None
    focus = {"metric": req.focus.metric, "filters": req.focus.filters.active()} if req.focus else None
    inv = investigate(req.question.strip(), cursor(), req.comparison, req.filters, provider, focus)
    payload = inv.model_dump(mode="json")
    get_audit().record_investigation(payload)
    return payload


@app.get("/api/investigations")
def list_investigations(limit: int = 50) -> list[dict[str, Any]]:
    return get_audit().list_investigations(max(1, min(limit, 200)))


@app.get("/api/investigations/{inv_id}")
def get_investigation(inv_id: str) -> dict[str, Any]:
    inv = get_audit().get_investigation(inv_id)
    if inv is None:
        raise HTTPException(404, "Investigation not found")
    return inv


@app.get("/api/tools")
def tools() -> list[dict[str, Any]]:
    return tool_catalog()


# ---------------------------------------------------------------------------
# experiments, impact, stats
# ---------------------------------------------------------------------------
@app.post("/api/experiments/plan")
def experiment_plan(req: PlanRequest) -> dict[str, Any]:
    trace = QueryTrace()
    plan = build_plan(cursor(), trace, req)
    plan["queries"] = trace.to_list()
    return plan


@app.post("/api/experiments/analyze")
def experiment_analyze(req: ResultsRequest) -> dict[str, Any]:
    return analyse(req)


@app.post("/api/experiments/simulate")
def experiment_simulate(req: SimulationRequest) -> dict[str, Any]:
    return simulate(req)


@app.get("/api/experiments/historical")
def experiment_historical() -> dict[str, Any]:
    ctx = ToolContext(cur=cursor())
    out = {}
    for metric in ("listing_to_contact_start", "listing_to_contact", "qualified_contact_rate"):
        res = run_tool(ctx, "experiment_readout", {"experiment_id": "EXP-014", "metric": metric})
        if res:
            out[metric] = {**res.data, "statement": res.evidence[0].statement}
    p = out["listing_to_contact_start"]
    t = p["test"]
    from app.experiments.analysis import Arm, Guardrail
    g = out["qualified_contact_rate"]["test"]
    decision = analyse(ResultsRequest(
        metric_label="Listing → contact start", source="logged_experiment",
        control=Arm(n=t["baseline_n"], conversions=t["baseline_x"]),
        treatment=Arm(n=t["current_n"], conversions=t["current_x"]),
        guardrails=[Guardrail(name="Listing → qualified contact", control=Arm(n=g["baseline_n"], conversions=g["baseline_x"]),
                              treatment=Arm(n=g["current_n"], conversions=g["current_x"]), max_degradation_pp=1.0)],
        mde_pp=None))
    return {"experiment_id": "EXP-014", "name": "Sticky contact CTA on listing pages",
            "population": "Dubai and Abu Dhabi, all devices; randomised 50/50 by user id",
            "window": p["window"], "readouts": out, "analysis": decision, "queries": ctx.trace.to_list()}


class ImpactDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: MetricKey = "listing_to_contact"
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


@app.post("/api/impact/defaults")
def impact_defaults(req: ImpactDefaultsRequest) -> dict[str, Any]:
    cur, trace = cursor(), QueryTrace()
    res = M.overall_change(cur, trace, req.metric, req.filters, req.comparison)
    t = res["test"]
    days = (req.comparison.current.end - req.comparison.current.start).days + 1
    q = M.overall_change(cur, trace, "qualified_contact_rate", req.filters, req.comparison)["test"]
    v = M.overall_change(cur, trace, "contact_to_viewing", req.filters, req.comparison)["test"]
    qual_rate = (q["current_x"] / t["current_x"]) if t["current_x"] else 0.7
    return {"eligible_sessions_per_month": round(t["current_n"] / days * 30),
            "baseline_rate": t["current_rate"], "qualified_rate": min(1.0, qual_rate),
            "viewing_rate": v["current_rate"], "metric": req.metric, "scope": req.filters.describe(),
            "source": f"Current window {req.comparison.current.start}–{req.comparison.current.end} of the synthetic "
                      "dataset (sessions/day × 30).", "queries": trace.to_list()}


@app.post("/api/impact")
def impact(req: ImpactRequest) -> dict[str, Any]:
    return estimate(req)


class SampleSizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_rate: float = Field(gt=0, lt=1)
    mde_pp: float = Field(gt=0, lt=100)
    alpha: float = Field(0.05, gt=0, lt=0.5)
    power: float = Field(0.8, gt=0.5, lt=0.999)
    daily_traffic: Optional[float] = Field(None, gt=0)


@app.post("/api/stats/sample-size")
def sample_size(req: SampleSizeRequest) -> dict[str, Any]:
    n = sample_size_two_proportions(req.baseline_rate, req.mde_pp / 100, req.alpha, req.power)
    out = {"n_per_arm": n, "total": 2 * n, "formula": "n = (z₁₋α/₂·√(2p̄q̄) + z₁₋β·√(p₀q₀+p₁q₁))² / (p₁−p₀)²",
           "inputs": req.model_dump()}
    if req.daily_traffic:
        out["days"] = (2 * n) / req.daily_traffic
    out["mde_curve"] = [{"n_per_arm": k, "mde_pp": mde_for_sample_size(req.baseline_rate, k, req.alpha, req.power) * 100}
                        for k in (500, 1000, 2000, 5000, 10000, 20000, 50000)]
    return out


# ---------------------------------------------------------------------------
# data access
# ---------------------------------------------------------------------------
TABLE_DOCS = {
    "sessions": "One row per session with funnel-step flags. The analytical table behind every metric.",
    "events": "Schema-valid, de-duplicated events.",
    "events_all": "Schema-valid events including duplicates, with an is_duplicate flag.",
    "quarantine": "Rows rejected by schema validation, with quarantine_reason.",
}
SESSION_COLUMNS = {
    "session_date": "Date of the session's first event.",
    "has_search / has_results / has_listing / has_save / has_contact_start / has_viewing":
        "Session reached that step (results only counts status ok).",
    "has_contact": "Validated contact: contact_submitted with status ok and a lead_id.",
    "has_contact_raw": "Any contact_submitted event (naive definition, includes failures and ghosts).",
    "has_qualified": "Validated contact scored as qualified.",
    "n_failed_submits / n_submits_without_lead / n_submits_before_start": "Tracking-quality counters.",
    "n_qualified_leads": "Distinct qualified lead_ids in the session.",
}


@app.get("/api/dictionary")
def dictionary() -> dict[str, Any]:
    return {"event_columns": COLUMN_DOCS, "session_columns": SESSION_COLUMNS, "tables": TABLE_DOCS,
            "event_types": EVENT_TYPES, "dimensions": DIMENSIONS,
            "metrics": {k: {"label": m.label, "numerator": m.numerator_label, "denominator": m.denominator_label,
                            "numerator_sql": m.numerator, "denominator_sql": m.denominator, "description": m.description}
                        for k, m in Q.METRICS.items()},
            "pipeline_sql": PIPELINE_SQL, "disclaimer": DISCLAIMER}


@app.get("/api/sql/catalog")
def sql_catalog() -> dict[str, Any]:
    c, f = Comparison(), Filters(market=["Riyadh"])
    examples = {
        "metric_comparison": Q.metric_comparison_sql("listing_to_contact", f, c, ["device"]),
        "metric_trend": Q.metric_trend_sql("listing_to_contact", f, Window(start=DATA_START, end=DATA_END), "week"),
        "funnel_steps": Q.funnel_steps_sql(f, c.current),
        "quality_by_segment": Q.quality_sql(f, c, ["device"]),
        "duplicate_events": Q.duplicate_events_sql(f, c.current),
        "quarantine_summary": Q.quarantine_sql(),
        "version_breakdown": Q.version_breakdown_sql("contact_start_to_submit", f, c),
        "experiment_counts": Q.experiment_counts_sql("EXP-014", "listing_to_contact_start"),
    }
    return {"queries": {k: {**v, "example_sql": examples[k][0].strip(),
                            "example_params": [str(p) for p in examples[k][1]]}
                        for k, v in Q.APPROVED_QUERIES.items()},
            "tools": [{"name": t.name, "description": t.description} for t in TOOLS.values()]}


class ApprovedQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["metric_comparison", "metric_trend", "funnel_steps", "quality_by_segment", "duplicate_events",
                  "quarantine_summary", "version_breakdown", "experiment_counts"]
    metric: MetricKey = "listing_to_contact"
    group_by: list[Dimension] = Field(default_factory=list, max_length=2)
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)
    grain: Literal["day", "week"] = "week"


@app.post("/api/sql/approved")
def run_approved(req: ApprovedQueryRequest) -> dict[str, Any]:
    window = Window(start=req.comparison.baseline.start, end=req.comparison.current.end)
    builders = {
        "metric_comparison": lambda: Q.metric_comparison_sql(req.metric, req.filters, req.comparison, req.group_by),
        "metric_trend": lambda: Q.metric_trend_sql(req.metric, req.filters, window, req.grain,
                                                   req.group_by[0] if req.group_by else None),
        "funnel_steps": lambda: Q.funnel_steps_sql(req.filters, req.comparison.current),
        "quality_by_segment": lambda: Q.quality_sql(req.filters, req.comparison, req.group_by),
        "duplicate_events": lambda: Q.duplicate_events_sql(req.filters, window),
        "quarantine_summary": Q.quarantine_sql,
        "version_breakdown": lambda: Q.version_breakdown_sql(req.metric, req.filters, req.comparison),
        "experiment_counts": lambda: Q.experiment_counts_sql("EXP-014", req.metric),
    }
    sql, params = builders[req.name]()
    trace = QueryTrace()
    df, _ = trace.run(cursor(), req.name, sql, params)
    from app.sqlguard import _records
    get_audit().record_sql("approved:" + req.name, sql, params, "ok", len(df), None)
    return {"columns": [{"name": c, "type": str(t)} for c, t in zip(df.columns, df.dtypes)],
            "rows": _records(df.head(500)), "row_count": len(df), "truncated": len(df) > 500,
            "query": trace.to_list()[0]}


class UserSQLRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=5000)
    limit: int = Field(200, ge=1, le=5000)


@app.post("/api/sql/run")
def run_sql(req: UserSQLRequest) -> dict[str, Any]:
    s = get_settings()
    try:
        out = run_user_sql(cursor(), req.sql, min(req.limit, s.sql_row_limit), s.sql_timeout_s)
    except SQLRejected as exc:
        get_audit().record_sql("user", req.sql, None, "rejected", None, str(exc))
        raise
    get_audit().record_sql("user", req.sql, None, "ok", out["row_count"], None)
    return out


@app.get("/api/audit")
def audit(limit: int = 50) -> dict[str, Any]:
    a = get_audit()
    limit = max(1, min(limit, 200))
    return {"investigations": a.list_investigations(limit), "sql_runs": a.list_sql_runs(limit)}


