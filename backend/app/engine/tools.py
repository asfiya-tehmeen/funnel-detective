"""Approved analytical tools.

These are the ONLY operations the investigation planner (deterministic or LLM) may request.
Each tool:
  * validates its arguments with a strict Pydantic model (unknown fields rejected),
  * runs approved SQL templates through a QueryTrace,
  * returns Evidence whose statement text is generated from computed numbers.
"""
from __future__ import annotations

import time
import uuid
from datetime import timedelta
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.analytics import metrics as M
from app.analytics import queries as Q
from app.analytics.queries import Comparison, Dimension, Filters, MetricKey, QueryTrace, Window
from app.analytics.stats import compare_proportions, mde_for_sample_size, sample_size_two_proportions
from app.config import DATA_END, DATA_START
from app.data.schema import DIMENSIONS
from app.engine.schemas import Evidence, ToolCall

STEP_METRICS = ["listing_to_contact_start", "contact_start_to_submit", "contact_to_viewing", "save_rate"]


# ---------------------------------------------------------------------------
# formatting helpers (deterministic text)
# ---------------------------------------------------------------------------
def pct(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def pp(x: float | None, d: int = 1) -> str:
    return "n/a" if x is None else f"{x:+.{d}f}pp"


def pval(p: float | None) -> str:
    if p is None:
        return "n/a"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def seg_label(seg: dict[str, str]) -> str:
    return " / ".join(str(v) for v in seg.values()) if seg else "all traffic"


def full_window() -> Window:
    return Window(start=DATA_START, end=DATA_END)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------
@dataclass
class ToolContext:
    cur: duckdb.DuckDBPyConnection
    trace: QueryTrace = field(default_factory=QueryTrace)
    evidence: list[Evidence] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    cache: dict[str, Any] = field(default_factory=dict)

    def add(self, tool: str, title: str, statement: str, level: str, data: dict[str, Any],
            query_ids: list[str]) -> Evidence:
        ev = Evidence(id=f"ev_{len(self.evidence) + 1}", tool=tool, title=title, statement=statement,
                      evidence_level=level, data=_jsonable(data), query_ids=query_ids)
        self.evidence.append(ev)
        return ev

    def get(self, evidence_id: str) -> Evidence | None:
        return next((e for e in self.evidence if e.id == evidence_id), None)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "item"):
        return obj.item()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


@dataclass
class ToolResult:
    evidence: list[Evidence]
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# argument models
# ---------------------------------------------------------------------------
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompareMetricArgs(_Args):
    metric: MetricKey
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class StepBreakdownArgs(_Args):
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class SegmentBreakdownArgs(_Args):
    metric: MetricKey
    dimension: Dimension
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)
    adjust_for: Optional[Dimension] = "channel"


class MixDecompositionArgs(_Args):
    metric: MetricKey
    dimension: Dimension
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class ChangePointArgs(_Args):
    metric: MetricKey
    filters: Filters = Field(default_factory=Filters)
    window: Window = Field(default_factory=full_window)


class QualityCheckArgs(_Args):
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class VersionBreakdownArgs(_Args):
    metric: MetricKey
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class SegmentScanArgs(_Args):
    metric: MetricKey
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class StandardizedComparisonArgs(_Args):
    metric: MetricKey
    dimension: Dimension
    group_a: str
    group_b: Optional[str] = None
    confounders: list[Dimension] = Field(min_length=1, max_length=2)
    filters: Filters = Field(default_factory=Filters)
    window: Window = Field(default_factory=full_window)


class PowerCheckArgs(_Args):
    metric: MetricKey
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class BaselineStabilityArgs(_Args):
    metric: MetricKey
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class OpportunitySizingArgs(_Args):
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class MarketPriorityArgs(_Args):
    metric: MetricKey = "listing_to_contact"
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)


class ExperimentReadoutArgs(_Args):
    experiment_id: Literal["EXP-014"] = "EXP-014"
    metric: MetricKey = "listing_to_contact_start"


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------
def _window_text(c: Comparison) -> str:
    return f"{c.baseline.start}–{c.baseline.end} vs {c.current.start}–{c.current.end}"


def t_compare_metric(ctx: ToolContext, a: CompareMetricArgs) -> ToolResult:
    res = M.overall_change(ctx.cur, ctx.trace, a.metric, a.filters, a.comparison)
    t = res["test"]
    m = Q.METRICS[a.metric]
    qids = [res["query_id"]]
    adj = None
    if len(a.filters.channel) != 1:
        wide, qid = M.metric_comparison(ctx.cur, ctx.trace, a.metric, a.filters, a.comparison, ["channel"],
                                        name="compare_metric_by_channel")
        adj = M.stratified_change(wide)
        qids.append(qid)
    res["channel_adjusted"] = adj
    sig = "statistically distinguishable from no change" if t["significant"] else "not statistically distinguishable from no change"
    stmt = (f"{m.label} for {a.filters.describe()} moved from {pct(t['baseline_rate'], 2)} "
            f"({t['baseline_x']:,}/{t['baseline_n']:,}) to {pct(t['current_rate'], 2)} "
            f"({t['current_x']:,}/{t['current_n']:,}), {pp(t['abs_change_pp'], 2)} "
            f"(95% CI {t['diff_ci_pp'][0]:+.2f} to {t['diff_ci_pp'][1]:+.2f}pp; {t['test']}, p={pval(t['p_value'])}); "
            f"{sig} at α=0.05."
            + (f" Holding channel mix fixed, the change is {pp(adj['adj_change_pp'], 2)} (p={pval(adj['adj_p_value'])})."
               if adj else ""))
    ev = ctx.add("compare_metric", f"{m.label}: {_window_text(a.comparison)}", stmt, "descriptive",
                 {**res, "metric_label": m.label}, qids)
    return ToolResult([ev], res)


def t_step_breakdown(ctx: ToolContext, a: StepBreakdownArgs) -> ToolResult:
    out, evs = {}, []
    for metric in STEP_METRICS:
        wide, qid = M.metric_comparison(ctx.cur, ctx.trace, metric, a.filters, a.comparison, ["channel"],
                                        name=f"step_{metric}_by_channel")
        tot = wide[["baseline_n", "baseline_x", "current_n", "current_x"]].sum()
        if tot.baseline_n < 1 or tot.current_n < 1:
            continue
        raw = compare_proportions(int(tot.baseline_x), int(tot.baseline_n), int(tot.current_x), int(tot.current_n))
        adj = M.stratified_change(wide)
        out[metric] = {"raw": raw.to_dict(), "channel_adjusted": adj, "query_id": qid}
    lines = []
    for metric, r in out.items():
        adj = r["channel_adjusted"]
        rel = r["raw"]["rel_change_pct"]
        lines.append(f"{Q.METRICS[metric].label}: {pct(r['raw']['baseline_rate'])} → {pct(r['raw']['current_rate'])} "
                     f"({pp(r['raw']['abs_change_pp'])}{'' if rel is None else f', {rel:+.0f}% rel'}; "
                     f"within-channel {pp(adj['adj_change_pp']) if adj else 'n/a'}, p={pval(adj['adj_p_value'] if adj else None)})")
    ev = ctx.add("step_breakdown", f"Journey-step changes for {a.filters.describe()}",
                 "; ".join(lines) + ".", "descriptive", {"steps": out}, [r["query_id"] for r in out.values()])
    evs.append(ev)
    return ToolResult(evs, {"steps": out})


def t_segment_breakdown(ctx: ToolContext, a: SegmentBreakdownArgs) -> ToolResult:
    tbl = M.segment_table(ctx.cur, ctx.trace, a.metric, a.filters, a.comparison, [a.dimension],
                          name=f"breakdown_{a.metric}_by_{a.dimension}", adjust_for=a.adjust_for)
    rows = sorted(tbl["rows"], key=lambda r: r["impact_sessions"] if r["impact_sessions"] is not None else 0)
    parts = []
    for r in rows:
        if not r["tested"]:
            parts.append(f"{seg_label(r['segment'])}: too few sessions to test (n={r['baseline_n']}/{r['current_n']})")
            continue
        adj = f", within-{a.adjust_for} {pp(r.get('adj_change_pp'))}" if r.get("adj_change_pp") is not None else ""
        parts.append(f"{seg_label(r['segment'])}: {pct(r['baseline_rate'])} → {pct(r['current_rate'])} "
                     f"({pp(r['abs_change_pp'])}{adj}, q={pval(r['q_value'])})")
    stmt = f"{Q.METRICS[a.metric].label} by {a.dimension} for {a.filters.describe()}: " + "; ".join(parts) + "."
    ev = ctx.add("segment_breakdown", f"{Q.METRICS[a.metric].label} by {a.dimension}", stmt, "associational",
                 tbl, [tbl["query_id"]])
    return ToolResult([ev], tbl)


def t_mix_decomposition(ctx: ToolContext, a: MixDecompositionArgs) -> ToolResult:
    res = M.decompose(ctx.cur, ctx.trace, a.metric, a.filters, a.comparison, a.dimension)
    share = res["mix_share_of_change"]
    top = res["segments"][:3]
    tops = "; ".join(f"{seg_label(s['segment'])} share {pct(s['baseline_share'], 0)} → {pct(s['current_share'], 0)} "
                     f"at {pct(s['baseline_rate'])}/{pct(s['current_rate'])}" for s in top)
    stmt = (f"Kitagawa decomposition by {a.dimension}: total change {pp(res['total_change_pp'], 2)} = mix effect "
            f"{pp(res['mix_effect_pp'], 2)} + within-segment rate effect {pp(res['rate_effect_pp'], 2)}"
            + (f" (mix accounts for {share:.0%} of the change)" if share is not None else "")
            + f". Largest contributors: {tops}.")
    ev = ctx.add("mix_decomposition", f"Mix vs rate by {a.dimension}", stmt, "associational", res, [res["query_id"]])
    return ToolResult([ev], res)


def t_change_point(ctx: ToolContext, a: ChangePointArgs) -> ToolResult:
    cp = M.change_point(ctx.cur, ctx.trace, a.metric, a.filters, a.window)
    if cp is None:
        ev = ctx.add("change_point", "Change point", "Not enough daily data to estimate a change point.",
                     "descriptive", {}, [])
        return ToolResult([ev], {})
    strength = "sharp" if cp["llr"] >= 10 else ("moderate" if cp["llr"] >= 4 else "weak/no clear")
    stmt = (f"Daily {Q.METRICS[a.metric].label} for {a.filters.describe()} shows a {strength} break on {cp['date']}: "
            f"{pct(cp['rate_before'])} before vs {pct(cp['rate_after'])} after (log-likelihood ratio {cp['llr']:.1f}; "
            "the split is chosen to maximise the LLR, so treat it as a strength indicator, not a p-value).")
    ev = ctx.add("change_point", f"Change point in {Q.METRICS[a.metric].label}", stmt, "associational",
                 {**cp, "strength": strength}, [cp["query_id"]])
    return ToolResult([ev], {**cp, "strength": strength})


def t_quality_check(ctx: ToolContext, a: QualityCheckArgs) -> ToolResult:
    q = M.quality(ctx.cur, ctx.trace, a.filters, a.comparison)
    seg = q["segments"][0] if q["segments"] else {"baseline": None, "current": None}
    b, c = seg["baseline"] or {}, seg["current"] or {}
    dups = {}
    qids = [q["query_id"]]
    for period, w in (("baseline", a.comparison.baseline), ("current", a.comparison.current)):
        sql, params = Q.duplicate_events_sql(a.filters, w)
        df, qid = ctx.trace.run(ctx.cur, f"duplicate_events_{period}", sql, params)
        qids.append(qid)
        dups[period] = float(df.duplicate_events.sum() / df.events.sum()) if not df.empty and df.events.sum() else 0.0
    data = {"baseline": b, "current": c, "duplicate_rate": dups}
    stmt = (f"Tracking quality for {a.filters.describe()} (baseline → current): failed submits per contact start "
            f"{pct(b.get('failed_submit_rate'))} → {pct(c.get('failed_submit_rate'))}; contact_submitted without lead_id "
            f"{pct(b.get('submit_without_lead_rate'))} → {pct(c.get('submit_without_lead_rate'))} of listing sessions; "
            f"submits before contact_started {pct(b.get('submit_before_start_rate'))} → {pct(c.get('submit_before_start_rate'))}; "
            f"raw-minus-validated contact rate {b.get('raw_vs_validated_gap', 0) or 0:.1f}pp → "
            f"{c.get('raw_vs_validated_gap', 0) or 0:.1f}pp; duplicate events {pct(dups['baseline'])} → {pct(dups['current'])}.")
    ev = ctx.add("quality_check", f"Tracking quality: {a.filters.describe()}", stmt, "descriptive", data, qids)
    return ToolResult([ev], data)


def t_version_breakdown(ctx: ToolContext, a: VersionBreakdownArgs) -> ToolResult:
    sql, params = Q.version_breakdown_sql(a.metric, a.filters, a.comparison)
    df, qid = ctx.trace.run(ctx.cur, "version_breakdown", sql, params)
    rows = []
    for r in df.itertuples():
        rows.append({"period": r.period, "client_version": r.client_version, "denominator": int(r.denominator),
                     "numerator": int(r.numerator), "rate": (r.numerator / r.denominator) if r.denominator else None,
                     "sessions_with_failed_submit": int(r.sessions_with_failed_submit)})
    base_versions = {r["client_version"] for r in rows if r["period"] == "baseline" and r["denominator"] > 0}
    new_versions = sorted({r["client_version"] for r in rows if r["period"] == "current" and r["denominator"] > 0}
                          - base_versions)
    parts = [f"{r['client_version']} ({r['period']}): {pct(r['rate'])} of {r['denominator']:,}" for r in rows
             if r["denominator"] >= 30]
    stmt = (f"{Q.METRICS[a.metric].label} by client version for {a.filters.describe()}: " + "; ".join(parts) + ". "
            + (f"Versions first seen in the current window: {', '.join(new_versions)}." if new_versions
               else "No new client versions appeared in the current window."))
    data = {"rows": rows, "new_versions": new_versions}
    ev = ctx.add("version_breakdown", "Metric by client version", stmt, "associational", data, [qid])
    return ToolResult([ev], data)


def t_segment_scan(ctx: ToolContext, a: SegmentScanArgs) -> ToolResult:
    res = M.segment_scan(ctx.cur, ctx.trace, a.metric, a.filters, a.comparison)
    flagged = res["flagged"][:5]
    if flagged:
        tops = "; ".join(f"{seg_label(r['segment'])} {pp(r['scan_change_pp'])} (q={pval(r['scan_q_value'])})"
                         for r in flagged)
        stmt = f"Of {res['n_tests']} segment tests on {Q.METRICS[a.metric].label}, {res['n_flagged']} pass FDR<5%: {tops}."
    else:
        stmt = (f"Of {res['n_tests']} segment tests on {Q.METRICS[a.metric].label}, none shows a within-channel "
                "change that survives FDR<5%.")
    if res["watchlist"]:
        stmt += " Watchlist (p<0.01, not FDR-significant): " + "; ".join(
            f"{seg_label(r['segment'])} {pp(r['scan_change_pp'])}" for r in res["watchlist"][:3]) + "."
    qids = sorted({r["query_id"] for r in res["flagged"] + res["watchlist"]})
    ev = ctx.add("segment_scan", f"Segment scan: {Q.METRICS[a.metric].label}", stmt, "associational", res, qids)
    return ToolResult([ev], res)


def t_standardized_comparison(ctx: ToolContext, a: StandardizedComparisonArgs) -> ToolResult:
    allowed = DIMENSIONS[a.dimension]
    for g in (a.group_a, a.group_b):
        if g is not None and g not in allowed:
            raise ValueError(f"{g!r} is not a valid {a.dimension}; allowed: {allowed}")
    res = M.standardized_gap(ctx.cur, ctx.trace, a.metric, a.filters, a.window, a.dimension, a.group_a,
                             a.group_b, list(a.confounders))
    ga, gb = res["groups"][res["group_a"]], res["groups"][res["group_b"]]
    w = res["within_strata_test"] or {}
    share = res["share_of_gap_explained_by_mix"]
    stmt = (f"{Q.METRICS[a.metric].label}: {res['group_a']} {pct(ga['raw_rate'])} vs {res['group_b']} "
            f"{pct(gb['raw_rate'])} (raw gap {pp(res['raw_gap_pp'])}). Standardised to a common "
            f"{res['confounder']} mix the gap is {pp(res['standardized_gap_pp'])}"
            + (f", so about {share:.0%} of the raw gap is attributable to differences in {res['confounder']} mix"
               if share is not None else "")
            + f". Within-strata gap {pp(w.get('adj_change_pp'))}, p={pval(w.get('adj_p_value'))}.")
    ev = ctx.add("standardized_comparison", f"{res['group_a']} vs {res['group_b']}, standardised by {res['confounder']}",
                 stmt, "associational", res, [res["query_id"]])
    return ToolResult([ev], res)


def t_power_check(ctx: ToolContext, a: PowerCheckArgs) -> ToolResult:
    res = M.overall_change(ctx.cur, ctx.trace, a.metric, a.filters, a.comparison)
    t = res["test"]
    n = min(t["baseline_n"], t["current_n"])
    p0 = min(max(t["baseline_rate"], 0.005), 0.98)
    mde = mde_for_sample_size(p0, n) * 100 if n >= 2 else None
    observed = abs(t["abs_change_pp"])
    needed = None
    if observed > 0 and 0 < p0 + t["abs_change_pp"] / 100 < 1:
        try:
            needed = sample_size_two_proportions(p0, t["abs_change_pp"] / 100)
        except ValueError:
            needed = None
    data = {"n_per_window": n, "mde_pp": mde, "observed_abs_pp": observed, "n_needed_for_observed": needed,
            "test": t, "query_id": res["query_id"]}
    stmt = (f"With {n:,} denominator sessions per window, the minimum detectable change (80% power, α=0.05) is "
            f"about {mde:.2f}pp; the observed change is {observed:.2f}pp"
            + (f", which would need ~{needed:,} sessions per window to detect reliably" if needed else "") + ".")
    ev = ctx.add("power_check", f"Sample size / MDE: {a.filters.describe()}", stmt, "descriptive", data,
                 [res["query_id"]])
    return ToolResult([ev], data)


def t_baseline_stability(ctx: ToolContext, a: BaselineStabilityArgs) -> ToolResult:
    b = a.comparison.baseline
    length = (b.end - b.start).days + 1
    prior_end = b.start - timedelta(days=1)
    prior_start = max(DATA_START, prior_end - timedelta(days=length - 1))
    if prior_end < DATA_START or (prior_end - prior_start).days < 6:
        ev = ctx.add("baseline_stability", "Baseline stability", "No prior period available before the baseline.",
                     "descriptive", {}, [])
        return ToolResult([ev], {})
    comp = Comparison(baseline=Window(start=prior_start, end=prior_end), current=b)
    res = M.overall_change(ctx.cur, ctx.trace, a.metric, a.filters, comp)
    t = res["test"]
    stmt = (f"Baseline check: {Q.METRICS[a.metric].label} in the baseline window ({b.start}–{b.end}) was "
            f"{pct(t['current_rate'])} vs {pct(t['baseline_rate'])} in the prior period ({prior_start}–{prior_end}), "
            f"{pp(t['abs_change_pp'])}, p={pval(t['p_value'])}. "
            + ("The baseline itself was unusual, so part of the later change may be regression to the mean."
               if t["significant"] else "The baseline was in line with the prior period."))
    data = {"prior_window": [str(prior_start), str(prior_end)], "test": t, "unusual_baseline": t["significant"],
            "query_id": res["query_id"]}
    ev = ctx.add("baseline_stability", "Baseline stability", stmt, "descriptive", data, [res["query_id"]])
    return ToolResult([ev], data)


def t_opportunity_sizing(ctx: ToolContext, a: OpportunitySizingArgs) -> ToolResult:
    """Rank funnel steps by recoverable sessions.

    'Recoverable' = sessions lost to a credible within-channel decline: for each market × device cell, the
    channel-standardised change × current denominator, summed over cells whose decline has p < 0.05. Raw
    declines are not used because a traffic-mix shift lowers raw rates without anything being 'broken'.
    The gap to the top-quartile cell is reported as a softer, upper-bound opportunity.
    """
    out = []
    qids = []
    for metric in ["search_to_listing", "listing_to_contact_start", "contact_start_to_submit", "contact_to_viewing"]:
        tbl = M.segment_table(ctx.cur, ctx.trace, metric, a.filters, a.comparison, ["market", "device"],
                              name=f"opportunity_{metric}", adjust_for="channel")
        qids.append(tbl["query_id"])
        cells = [r for r in tbl["rows"] if r["current_n"] >= 100 and r.get("adj_change_pp") is not None]
        if not cells:
            continue
        rates = sorted(r["current_rate"] for r in cells)
        bench = rates[int(0.75 * (len(rates) - 1))]
        gap = sum(max(0.0, bench - r["current_rate"]) * r["current_n"] for r in cells)
        credible = [r for r in cells if r["adj_change_pp"] < 0 and r["adj_p_value"] < 0.05]
        lost = sum(-r["adj_impact_sessions"] for r in credible)
        worst = min(cells, key=lambda r: r["adj_impact_sessions"])
        out.append({"metric": metric, "label": Q.METRICS[metric].label, "benchmark_rate": bench,
                    "sessions_to_benchmark": gap, "sessions_lost_vs_baseline": lost,
                    "credible_decline_cells": [{**r["segment"], "adj_change_pp": r["adj_change_pp"],
                                                "adj_p_value": r["adj_p_value"]} for r in credible],
                    "current_denominator": sum(r["current_n"] for r in cells),
                    "largest_decline_cell": {**worst["segment"], "lost_sessions": -worst["adj_impact_sessions"],
                                             "adj_change_pp": worst["adj_change_pp"],
                                             "adj_p_value": worst["adj_p_value"]}})
    out.sort(key=lambda r: -r["sessions_lost_vs_baseline"])
    stmt = ("Recoverable step conversions in the current window (sessions lost to within-channel declines with "
            "p<0.05, summed over market × device cells with ≥100 sessions): " + "; ".join(
                f"{r['label']} ≈{r['sessions_lost_vs_baseline']:.0f} sessions (largest cell "
                f"{r['largest_decline_cell']['market']} / {r['largest_decline_cell']['device']}, "
                f"{pp(r['largest_decline_cell']['adj_change_pp'])}); gap to top-quartile cell "
                f"≈{r['sessions_to_benchmark']:.0f}" for r in out) +
            ". Gaps to the top-quartile cell mix real underperformance with differences in who each cell serves.")
    ev = ctx.add("opportunity_sizing", "Recoverable opportunity by funnel step", stmt, "descriptive",
                 {"steps": out}, qids)
    return ToolResult([ev], {"steps": out})


def t_market_priority(ctx: ToolContext, a: MarketPriorityArgs) -> ToolResult:
    tbl = M.segment_table(ctx.cur, ctx.trace, a.metric, a.filters.merged(market=[]), a.comparison, ["market"],
                          name="market_priority", adjust_for="channel")
    warn = M.quality_warnings(ctx.cur, ctx.trace, a.filters.merged(market=[]), a.comparison)
    flagged_markets = {w["segment"]["market"] for w in warn if w.get("segment")}
    rows = []
    for r in tbl["rows"]:
        mk = r["segment"]["market"]
        rows.append({"market": mk, "baseline_rate": r["baseline_rate"], "current_rate": r["current_rate"],
                     "abs_change_pp": r["abs_change_pp"], "adj_change_pp": r.get("adj_change_pp"),
                     "adj_p_value": r.get("adj_p_value"), "adj_q_value": r.get("adj_q_value"),
                     "lost_sessions_within_channel": r.get("adj_impact_sessions"),
                     "current_n": r["current_n"], "quality_flag": mk in flagged_markets})
    rows.sort(key=lambda r: (r["lost_sessions_within_channel"] or 0))
    stmt = ("Markets ranked by within-channel change in " + Q.METRICS[a.metric].label + " × current volume: " +
            "; ".join(f"{r['market']} {pp(r['adj_change_pp'])} within-channel (raw {pp(r['abs_change_pp'])}), "
                      f"≈{(r['lost_sessions_within_channel'] or 0):+.0f} sessions, q={pval(r['adj_q_value'])}"
                      + (", tracking-quality warning" if r["quality_flag"] else "") for r in rows) + ".")
    ev = ctx.add("market_priority", "Market prioritisation", stmt, "associational",
                 {"rows": rows, "warnings": warn}, [tbl["query_id"]])
    return ToolResult([ev], {"rows": rows, "warnings": warn})


def t_experiment_readout(ctx: ToolContext, a: ExperimentReadoutArgs) -> ToolResult:
    sql, params = Q.experiment_counts_sql(a.experiment_id, a.metric)
    df, qid = ctx.trace.run(ctx.cur, "experiment_counts", sql, params)
    arms = {r.variant: r for r in df.itertuples()}
    if not {"control", "treatment"} <= set(arms):
        raise ValueError(f"experiment {a.experiment_id} has no control/treatment data")
    c, t = arms["control"], arms["treatment"]
    test = compare_proportions(int(c.numerator), int(c.denominator), int(t.numerator), int(t.denominator))
    mde = mde_for_sample_size(c.numerator / c.denominator, int(min(c.denominator, t.denominator))) * 100
    data = {"experiment_id": a.experiment_id, "metric": a.metric, "test": test.to_dict(), "mde_pp": mde,
            "users": {"control": int(c.users), "treatment": int(t.users)},
            "window": [str(c.first_day), str(c.last_day)]}
    stmt = (f"{a.experiment_id} (randomised by user): {Q.METRICS[a.metric].label} control {pct(test.baseline_rate, 2)} "
            f"vs treatment {pct(test.current_rate, 2)}, {pp(test.abs_change_pp, 2)} (95% CI "
            f"{test.diff_ci_pp[0]:+.2f} to {test.diff_ci_pp[1]:+.2f}pp, p={pval(test.p_value)}). "
            f"The test could only detect ≈{mde:.2f}pp at 80% power, so a null result is weak evidence of no effect.")
    ev = ctx.add("experiment_readout", f"{a.experiment_id} readout", stmt, "causal", data, [qid])
    return ToolResult([ev], data)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[_Args]
    fn: Callable[[ToolContext, Any], ToolResult]


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("compare_metric", "Baseline vs current value of one metric for a segment, with CI and test.",
             CompareMetricArgs, t_compare_metric),
    ToolSpec("step_breakdown", "Changes in each journey step (contact start, form completion, viewing, save) "
             "for a segment, raw and within-channel.", StepBreakdownArgs, t_step_breakdown),
    ToolSpec("segment_breakdown", "Metric change by one dimension, optionally standardised over another.",
             SegmentBreakdownArgs, t_segment_breakdown),
    ToolSpec("mix_decomposition", "Kitagawa split of a change into mix (composition) and within-segment rate effects.",
             MixDecompositionArgs, t_mix_decomposition),
    ToolSpec("change_point", "Most likely single break date in the daily metric series.", ChangePointArgs,
             t_change_point),
    ToolSpec("quality_check", "Tracking-quality indicators (failed submits, missing lead_id, out-of-order events, "
             "duplicates) baseline vs current.", QualityCheckArgs, t_quality_check),
    ToolSpec("version_breakdown", "Metric by client version (web release / app version).", VersionBreakdownArgs,
             t_version_breakdown),
    ToolSpec("segment_scan", "Scan single and paired segments for FDR-significant within-channel changes.",
             SegmentScanArgs, t_segment_scan),
    ToolSpec("standardized_comparison", "Compare two groups raw and standardised over confounders "
             "(selection-effect check).", StandardizedComparisonArgs, t_standardized_comparison),
    ToolSpec("power_check", "Sample size and minimum detectable effect for the observed comparison.",
             PowerCheckArgs, t_power_check),
    ToolSpec("baseline_stability", "Whether the baseline window itself deviated from the period before it.",
             BaselineStabilityArgs, t_baseline_stability),
    ToolSpec("opportunity_sizing", "Rank funnel steps by recoverable sessions.", OpportunitySizingArgs,
             t_opportunity_sizing),
    ToolSpec("market_priority", "Rank markets by within-channel decline × volume, with quality flags.",
             MarketPriorityArgs, t_market_priority),
    ToolSpec("experiment_readout", "Read out a logged randomised experiment (causal evidence).",
             ExperimentReadoutArgs, t_experiment_readout),
]}


def tool_catalog() -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "args_schema": t.args_model.model_json_schema()}
            for t in TOOLS.values()]


class ToolError(Exception):
    pass


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any] | BaseModel,
             requested_by: str = "workflow") -> ToolResult | None:
    """Validate and execute a tool. Failures are recorded on the call log, never raised to the caller."""
    t0 = time.perf_counter()
    call_id = "tc_" + uuid.uuid4().hex[:8]
    raw_args = args.model_dump(mode="json") if isinstance(args, BaseModel) else dict(args)
    spec = TOOLS.get(name)
    n_ev, n_q = len(ctx.evidence), len(ctx.trace.records)
    try:
        if spec is None:
            raise ToolError(f"unknown tool {name!r}; allowed: {sorted(TOOLS)}")
        parsed = args if isinstance(args, spec.args_model) else spec.args_model.model_validate(raw_args)
        key = f"{name}:{parsed.model_dump_json()}"
        if key in ctx.cache:
            result = ctx.cache[key]
        else:
            result = spec.fn(ctx, parsed)
            ctx.cache[key] = result
        ctx.calls.append(ToolCall(id=call_id, tool=name, args=parsed.model_dump(mode="json"), status="ok",
                                  evidence_ids=[e.id for e in result.evidence],
                                  query_ids=[r.id for r in ctx.trace.records[n_q:]],
                                  duration_ms=(time.perf_counter() - t0) * 1000, requested_by=requested_by))
        return result
    except (ValidationError, ValueError, ToolError, duckdb.Error) as exc:
        msg = str(exc)
        if isinstance(exc, ValidationError):
            msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        del ctx.evidence[n_ev:]
        ctx.calls.append(ToolCall(id=call_id, tool=name, args=raw_args, status="error", error=msg[:500],
                                  duration_ms=(time.perf_counter() - t0) * 1000, requested_by=requested_by))
        return None
