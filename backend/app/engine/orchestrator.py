"""Investigation orchestrator.

question ─► plan (LLM or deterministic router, validated) ─► workflow tools ─► mandatory hypothesis
battery ─► finding (rule-based confidence) ─► experiment plan ─► narrative (LLM, validated; else template)

The LLM never computes numbers. It may (a) choose the intent/metric/segment and request extra approved
tools, and (b) write an interpretation that must pass `validate_narrative`. Any failure falls back to the
deterministic path, and the fallback reason is recorded on the investigation.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.analytics import queries as Q
from app.analytics.queries import Comparison, Filters, Window
from app.data.schema import DIMENSIONS
from app.engine import router
from app.engine.hypotheses import rank, run_battery, run_selection_battery
from app.engine.schemas import (
    Finding, Hypothesis, Investigation, InvestigationPlan, KeyPoint, Narrative, ObservedChange, PlanStep,
)
from app.engine.tools import TOOLS, ToolContext, pp, pval, run_tool, seg_label
from app.experiments.planner import PlanRequest, build_plan
from app.llm.base import LLMError, LLMProvider
from app.llm.validation import validate_narrative

INTENT_HELP = {
    "explain_change": "Explain why a metric changed for a segment.",
    "mix_vs_rate": "Is an aggregate change real, or a by-product of a change in traffic mix (Simpson's paradox)?",
    "opportunity": "Which funnel step has the largest recoverable opportunity?",
    "prioritize_markets": "Which market should be investigated first?",
    "abandon_vs_tracking": "Are users abandoning a flow, or is tracking broken?",
    "segment_advantage": "Why does one segment (e.g. app) convert better — genuinely or by selection?",
    "scan_changes": "Which segments show a statistically credible change?",
}


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def _plan_schema() -> dict[str, Any]:
    dims = {d: {"type": "array", "items": {"type": "string", "enum": v}} for d, v in DIMENSIONS.items()}
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": list(INTENT_HELP)},
            "metric": {"type": "string", "enum": list(Q.METRICS)},
            "filters": {"type": "object", "properties": dims, "required": list(DIMENSIONS),
                        "additionalProperties": False},
            "rationale": {"type": "string"},
            "extra_steps": {"type": "array", "items": {
                "type": "object",
                "properties": {"tool": {"type": "string", "enum": list(TOOLS)},
                               "args_json": {"type": "string"}, "rationale": {"type": "string"}},
                "required": ["tool", "args_json", "rationale"], "additionalProperties": False}},
        },
        "required": ["intent", "metric", "filters", "rationale", "extra_steps"],
        "additionalProperties": False,
    }


PLANNER_SYSTEM = """You plan product-analytics investigations for a real-estate marketplace funnel.
You do NOT calculate anything. You choose an investigation intent, a metric and segment filters, and you may
request up to 3 extra approved tools. Only use segment values from the provided lists. If the question names
no segment, leave filters empty. 'mobile' without qualification means mobile_web. Keep the rationale to one
or two sentences. Tool args must be a JSON object string matching that tool's argument schema; omit
'comparison' and 'window' to use the defaults."""


def llm_plan(provider: LLMProvider, question: str) -> tuple[InvestigationPlan, list[str]]:
    catalog = [{"name": t.name, "description": t.description,
                "args": list(t.args_model.model_json_schema().get("properties", {}))} for t in TOOLS.values()]
    user = json.dumps({
        "question": question,
        "intents": INTENT_HELP,
        "metrics": {k: m.description for k, m in Q.METRICS.items()},
        "dimensions": DIMENSIONS,
        "tools": catalog,
    })
    raw = provider.complete_json(PLANNER_SYSTEM, user, _plan_schema(), max_tokens=2000)
    notes = []
    steps = []
    for s in raw.get("extra_steps", [])[:3]:
        try:
            args = json.loads(s.get("args_json") or "{}")
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
            steps.append(PlanStep(tool=s["tool"], args=args, rationale=s.get("rationale", "")))
        except (ValueError, KeyError, ValidationError) as exc:
            notes.append(f"Dropped an LLM-requested step with invalid arguments ({exc}).")
    filters = Filters(**{k: v for k, v in (raw.get("filters") or {}).items() if v})
    plan = InvestigationPlan(intent=raw["intent"], metric=raw["metric"], filters=filters.active(),
                             rationale=raw.get("rationale", "")[:500], steps=steps, planner="llm")
    return plan, notes


def deterministic_plan(question: str) -> tuple[InvestigationPlan, list[str]]:
    r = router.route(question)
    notes = []
    if "mobile" in question.lower() and r["filters"].get("device") == ["mobile_web"] and "mobile web" not in question.lower():
        notes.append("Interpreted 'mobile' as mobile_web; the app is compared as a sibling segment.")
    filters = Filters(**r["filters"])
    return InvestigationPlan(intent=r["intent"], metric=r["metric"], filters=filters.active(),
                             rationale=f"Keyword router: intent '{r['intent']}' ({INTENT_HELP[r['intent']]}), metric "
                                       f"'{r['metric']}', segments {filters.describe()}.",
                             planner="deterministic"), notes


# ---------------------------------------------------------------------------
# findings & narrative
# ---------------------------------------------------------------------------
def _observed(target_data: dict[str, Any], metric: str) -> ObservedChange:
    t = target_data["test"]
    m = Q.METRICS[metric]
    adj = target_data.get("channel_adjusted") or {}
    return ObservedChange(metric=metric, metric_label=m.label, numerator_label=m.numerator_label,
                          denominator_label=m.denominator_label, baseline_rate=t["baseline_rate"],
                          current_rate=t["current_rate"], baseline_n=t["baseline_n"], current_n=t["current_n"],
                          abs_change_pp=t["abs_change_pp"], rel_change_pct=t["rel_change_pct"],
                          diff_ci_pp=tuple(t["diff_ci_pp"]), p_value=t["p_value"], test=t["test"],
                          mde_pp_at_this_n=target_data.get("mde_pp_at_this_n"),
                          channel_adjusted_change_pp=adj.get("adj_change_pp"),
                          channel_adjusted_p_value=adj.get("adj_p_value"))


CONFIDENCE_RULE = ("high: the change is significant (p<0.01), the leading explanation is 'supported' and no competing "
                   "explanation is also 'supported' · medium: the change is significant (p<0.05) and the leading "
                   "explanation is at least partially supported · low: otherwise. This is a rule-based rating of the "
                   "evidence pattern, not a probability.")


def build_finding(question: str, metric: str, filters: Filters, comparison: Comparison, observed: ObservedChange,
                  hyps: list[Hypothesis], scope_from_scan: bool = False) -> Finding:
    ranked = rank(hyps)
    lead = ranked[0] if ranked else None
    supported = [h for h in ranked if h.status == "supported"]
    p = observed.p_value
    if lead and p < 0.01 and lead.status == "supported" and len(supported) == 1:
        conf = "high"
    elif lead and p < 0.05 and lead.status in ("supported", "partially_supported"):
        conf = "medium"
    else:
        conf = "low"
    basis = [f"Change p={pval(p)}", f"leading explanation '{lead.category}' is {lead.status.replace('_', ' ')}"
             if lead else "no leading explanation"]
    if len(supported) > 1:
        basis.append(f"{len(supported)} explanations are supported at once ("
                     + ", ".join(h.category for h in supported) + "), so they may each explain part of the change")
    alts = [f"{h.title} — {h.status.replace('_', ' ')} ({h.n_support} supporting / {h.n_contra} contradicting checks)"
            for h in ranked[1:] if h.status != "not_supported"]
    rejected = [h for h in ranked[1:] if h.status == "not_supported"]
    if rejected:
        alts.append("Not supported by the checks: " + ", ".join(h.category.replace("_", " ") for h in rejected))
    limitations = ["Observational data: explanations are tested against patterns in the data, not randomised "
                   "comparisons, so they show association and do not establish cause.",
                   "Sessions are treated as independent; repeat visitors make intervals slightly narrower than they "
                   "should be.",
                   "Synthetic dataset generated for demonstration; not real Property Finder data."]
    if observed.mde_pp_at_this_n and abs(observed.abs_change_pp) < observed.mde_pp_at_this_n * 1.5:
        limitations.append(f"Small sample: the minimum detectable change at this volume is "
                           f"{observed.mde_pp_at_this_n:.2f}pp, close to the observed change.")
    if scope_from_scan:
        limitations.append("This segment was selected by scanning many segments; rely on FDR-adjusted q-values, "
                           "not raw p-values.")
    nxt = []
    if lead:
        nxt += lead.discriminating_data[:2]
    for h in ranked[1:]:
        if h.status in ("supported", "partially_supported"):
            nxt.append(f"To separate it from '{h.category.replace('_', ' ')}': {h.discriminating_data[0]}")
            break
    support_ids = sorted({eid for c in (lead.checks if lead else []) if c.result == "supports" for eid in c.evidence_ids},
                         key=lambda x: int(x.split("_")[1]))
    window = {"baseline": f"{comparison.baseline.start} to {comparison.baseline.end}",
              "current": f"{comparison.current.start} to {comparison.current.end}"}
    return Finding(business_question=question, metric=metric, scope=filters.describe(), filters=filters.active(),
                   comparison_window=window,
                   observed_change=observed, leading_hypothesis_id=lead.id if lead else None,
                   supporting_evidence=support_ids, alternative_explanations=alts, confidence=conf,
                   confidence_basis="; ".join(basis) + ". Rule — " + CONFIDENCE_RULE,
                   limitations=limitations, next_analysis=nxt[:4])


def deterministic_narrative(finding: Finding | None, hyps: list[Hypothesis], related: list[dict[str, Any]],
                            target_ev_id: str | None) -> Narrative:
    if finding is None:
        return Narrative(source="deterministic", summary="No finding could be produced.", key_points=[],
                         caveats=[], validated=True)
    o = finding.observed_change
    ranked = rank(hyps)
    lead = ranked[0]
    if o.comparison_kind == "group":
        head = (f"{o.metric_label}: {o.current_label} {o.current_rate:.1%} vs {o.baseline_label} {o.baseline_rate:.1%} "
                f"({pp(o.abs_change_pp)}).")
    else:
        head = (f"{o.metric_label} for {finding.scope} moved from {o.baseline_rate:.1%} to {o.current_rate:.1%} "
                f"({pp(o.abs_change_pp)}, p={pval(o.p_value)}).")
    summary = (f"{head} The best-supported explanation is: {lead.title.lower()} — "
               f"{lead.status.replace('_', ' ')}, with {lead.n_support} supporting and {lead.n_contra} contradicting "
               f"checks." + (f" It accounts for about {lead.explained_share:.0%} of the change."
                            if lead.explained_share is not None else "")
               + "".join(f" {h.title} is also {h.status.replace('_', ' ')}"
                         + (f" (about {h.explained_share:.0%} of the change)." if h.explained_share is not None else ".")
                         for h in ranked[1:3] if h.status in ("supported", "partially_supported"))
               + f" Confidence is {finding.confidence} under the rule shown.")
    if related:
        summary += " Related: " + "; ".join(f"{r['scope']} looks like {r['leading_category'].replace('_', ' ')}"
                                            for r in related[:2]) + "."
    kps: list[KeyPoint] = []
    if target_ev_id:
        kps.append(KeyPoint(text=head, evidence_ids=[target_ev_id], stance="context"))
    for c in lead.checks:
        if c.result == "supports" and c.evidence_ids and len(kps) < 5:
            kps.append(KeyPoint(text=f"{c.description} {c.detail}", evidence_ids=c.evidence_ids[:3],
                                hypothesis_id=lead.id, stance="supports"))
    for h in ranked[1:]:
        if len(kps) >= 7:
            break
        c = next((c for c in h.checks if c.result == "contradicts" and c.evidence_ids), None)
        if c and h.status == "not_supported":
            kps.append(KeyPoint(text=f"Against '{h.category.replace('_', ' ')}': {c.detail}",
                                evidence_ids=c.evidence_ids[:3], hypothesis_id=h.id, stance="contradicts"))
    if not kps:
        kps.append(KeyPoint(text=head, evidence_ids=finding.supporting_evidence[:1] or ["ev_1"], stance="context"))
    return Narrative(source="deterministic", summary=summary, key_points=kps, caveats=finding.limitations[:3],
                     validated=True)


NARRATOR_SYSTEM = """You explain verified product-analytics results to a product manager.
Rules (violations cause your answer to be discarded):
- Use ONLY numbers that appear in the provided evidence statements or check details. Do not compute new numbers.
- Every key point must cite the evidence ids it relies on.
- Do not use causal language (caused, drove, due to, because of, proves, led to, resulted in) — the evidence is
  observational unless an item is marked 'causal'.
- Never present a hypothesis with status not_supported as an explanation.
- Be concise: a 2-4 sentence summary, at most 6 key points, plain business language."""


def llm_narrative(provider: LLMProvider, finding: Finding, hyps: list[Hypothesis], evidence: list,
                  related: list[dict[str, Any]]) -> tuple[Narrative | None, list[str]]:
    payload = {
        "finding": finding.model_dump(mode="json", exclude={"confidence_basis"}),
        "hypotheses": [{"id": h.id, "category": h.category, "title": h.title, "status": h.status,
                        "checks": [{"description": c.description, "result": c.result, "detail": c.detail,
                                    "evidence_ids": c.evidence_ids} for c in h.checks]} for h in rank(hyps)],
        "evidence": [{"id": e.id, "title": e.title, "statement": e.statement, "level": e.evidence_level}
                     for e in evidence],
        "related_findings": related,
    }
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "key_points": {"type": "array", "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}},
                               "hypothesis_id": {"type": ["string", "null"]},
                               "stance": {"type": "string", "enum": ["supports", "contradicts", "context"]}},
                "required": ["text", "evidence_ids", "hypothesis_id", "stance"], "additionalProperties": False}},
            "caveats": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "key_points", "caveats"], "additionalProperties": False,
    }
    raw = provider.complete_json(NARRATOR_SYSTEM, json.dumps(payload, default=str), schema, max_tokens=3000)
    draft, issues = validate_narrative(raw, evidence, hyps,
                                       extra_texts=[json.dumps(finding.observed_change.model_dump(), default=str)])
    if draft is None:
        return None, issues
    return Narrative(source="llm", summary=draft.summary, key_points=draft.key_points, caveats=draft.caveats,
                     validated=True, model=provider.model), []


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------
def _related_summary(scope: Filters, metric: str, hyps: list[Hypothesis], target) -> dict[str, Any]:
    lead = rank(hyps)[0]
    t = target.data["test"] if target else {}
    return {"scope": scope.describe(), "filters": scope.active(), "metric": metric,
            "metric_label": Q.METRICS[metric].label, "abs_change_pp": t.get("abs_change_pp"),
            "p_value": t.get("p_value"), "leading_category": lead.category, "leading_title": lead.title,
            "leading_status": lead.status,
            "hypotheses": [{"category": h.category, "status": h.status} for h in rank(hyps)]}


LOCALIZE_SHARE = 0.6


def _localize(ctx: ToolContext, metric: str, f: Filters, comparison: Comparison,
              notes: list[str] | None = None) -> Filters:
    """Drill into a one-dimension segment: if a single sub-segment holds >= 60% of its within-channel
    loss (and is itself significant at p < 0.05), investigate that sub-segment instead."""
    fixed = {d: v[0] for d, v in f.active().items() if len(v) == 1}
    if len(fixed) != 1:
        return f
    parent = run_tool(ctx, "compare_metric", {"metric": metric, "filters": f, "comparison": comparison})
    if not parent or not parent.data.get("channel_adjusted"):
        return f
    p_adj = parent.data["channel_adjusted"]["adj_change_pp"]
    parent_loss = p_adj / 100 * parent.data["test"]["current_n"]
    if parent_loss == 0:
        return f
    best = None
    for d in ("market", "device", "price_band", "property_type", "intent", "visitor_type"):
        if d in fixed:
            continue
        res = run_tool(ctx, "segment_breakdown", {"metric": metric, "dimension": d, "filters": f,
                                                   "comparison": comparison, "adjust_for": "channel"})
        for r in (res.data["rows"] if res else []):
            imp, p = r.get("adj_impact_sessions"), r.get("adj_p_value")
            if imp is None or p is None or p >= 0.05 or (imp < 0) != (parent_loss < 0):
                continue
            share = imp / parent_loss
            if share >= LOCALIZE_SHARE and (best is None or share > best[0]):
                best = (share, d, r["segment"][d])
    if best is None:
        return f
    share, d, v = best
    child = f.merged(**{d: [v]})
    if notes is not None:
        notes.append(f"Localised {f.describe()} to {child.describe()}: it holds {min(share, 1):.0%} of the parent "
                     "segment's within-channel change.")
    return child


def _scan_focus(ctx: ToolContext, metrics: list[str], filters: Filters, comparison: Comparison,
                limit: int = 3, watchlist: int = 0, notes: list[str] | None = None) -> list[tuple[str, Filters, float]]:
    """Segments to investigate: FDR-flagged first (by impact), then up to `watchlist` watchlist items (by p).

    Watchlist items are not significant after correction; they are investigated because the hypothesis
    battery (including the random-variation and multiplicity checks) is what decides, and they are
    labelled as such in the notes.
    """
    cands: list[tuple[str, Filters, float]] = []
    watch: list[tuple[str, Filters, float, float]] = []
    for m in metrics:
        res = run_tool(ctx, "segment_scan", {"metric": m, "filters": filters, "comparison": comparison})
        if not res:
            continue
        for r in res.data["flagged"] + res.data["positive"]:
            cands.append((m, filters.merged(**{k: [v] for k, v in r["segment"].items()}), r["scan_impact_sessions"]))
        for r in res.data["watchlist"]:
            if len(r["segment"]) == 2:  # specific enough to act on
                watch.append((m, filters.merged(**{k: [v] for k, v in r["segment"].items()}),
                              r["scan_impact_sessions"], r["scan_p_value"]))
    cands.sort(key=lambda c: -abs(c[2]))
    watch.sort(key=lambda c: c[3])
    n_flagged = len(cands)
    cands += [(m, f, imp) for m, f, imp, _ in watch]
    chosen: list[set[tuple[str, str]]] = []
    out = []
    n_watch = 0
    for i, (m, f, imp) in enumerate(cands):
        from_watchlist = i >= n_flagged
        if from_watchlist and n_watch >= watchlist:
            break
        f = _localize(ctx, m, f, comparison, notes)
        items = {(d, v[0]) for d, v in f.active().items() if len(v) == 1}
        # Skip a segment if it, or a broader/narrower version of it, was already chosen.
        if any(items <= c or c <= items for c in chosen):
            continue
        chosen.append(items)
        out.append((m, f, imp))
        if from_watchlist:
            n_watch += 1
            if notes is not None:
                notes.append(f"{f.describe()} ({Q.METRICS[m].label}) came from the scan's watchlist — not significant "
                             "after FDR correction — and was checked with the full hypothesis battery.")
        if len(out) >= limit:
            break
    return out


def investigate(question: str, cur, comparison: Comparison | None = None, ui_filters: Filters | None = None,
                provider: LLMProvider | None = None, focus: dict[str, Any] | None = None) -> Investigation:
    """Run one investigation. `focus` ({metric, filters}) pins the scope, e.g. when opened from a Detect row."""
    t0 = time.perf_counter()
    comparison = comparison or Comparison()
    ctx = ToolContext(cur=cur)
    notes: list[str] = []
    mode = "deterministic"

    plan = None
    if focus is not None:
        ff = Filters(**(focus.get("filters") or {}))
        metric_key = focus.get("metric") or "listing_to_contact"
        if metric_key not in Q.METRICS:
            raise ValueError(f"unknown metric {metric_key!r}")
        plan = InvestigationPlan(intent="explain_change", metric=metric_key, filters=ff.active(),
                                 rationale=f"Opened from the workspace with a fixed focus: {Q.METRICS[metric_key].label} "
                                           f"for {ff.describe()}.", planner="deterministic")
        provider_for_plan = None
    else:
        provider_for_plan = provider
    if provider_for_plan is not None:
        try:
            plan, pnotes = llm_plan(provider_for_plan, question)
            notes += pnotes
            mode = "llm"
        except (LLMError, ValidationError, KeyError, ValueError) as exc:
            notes.append(f"LLM planner unavailable or returned an invalid plan ({type(exc).__name__}: "
                         f"{str(exc)[:160]}); used the deterministic router.")
    if plan is None:
        plan, pnotes = deterministic_plan(question)
        notes += pnotes
    filters = Filters(**plan.filters)
    if not filters.active() and ui_filters is not None and ui_filters.active():
        filters = ui_filters
        plan.filters = filters.active()
        notes.append(f"The question names no segment, so the workspace filters were applied ({filters.describe()}).")

    for step in plan.steps:
        run_tool(ctx, step.tool, {**step.args}, requested_by="planner")

    metric = plan.metric
    hyps: list[Hypothesis] = []
    related: list[dict[str, Any]] = []
    target = None
    observed: ObservedChange | None = None
    extras: dict[str, Any] = {}
    scope_from_scan = False
    experiment_req: PlanRequest | None = None

    if plan.intent == "segment_advantage":
        group_dim, group_val = next(((d, v[0]) for d, v in filters.active().items() if len(v) == 1), (None, None))
        if group_dim is None:
            group_dim, group_val = "device", "app"
            notes.append("No segment named; compared app against all other devices.")
        base_filters = filters.merged(**{group_dim: []})
        window = Window(start=comparison.baseline.start, end=comparison.current.end)
        hyps, sel = run_selection_battery(ctx, metric, base_filters, window, group_dim, group_val, None)
        J = sel["joint"].data
        ga, gb = J["groups"][J["group_a"]], J["groups"][J["group_b"]]
        from app.analytics.stats import compare_proportions
        gt = compare_proportions(gb["x"], gb["n"], ga["x"], ga["n"])
        m = Q.METRICS[metric]
        observed = ObservedChange(metric=metric, metric_label=m.label, numerator_label=m.numerator_label,
                                  denominator_label=m.denominator_label, baseline_rate=gt.baseline_rate,
                                  current_rate=gt.current_rate, baseline_n=gt.baseline_n, current_n=gt.current_n,
                                  abs_change_pp=gt.abs_change_pp, rel_change_pct=gt.rel_change_pct,
                                  diff_ci_pp=gt.diff_ci_pp, p_value=gt.p_value, test=gt.test, mde_pp_at_this_n=None,
                                  comparison_kind="group", baseline_label=J["group_b"], current_label=J["group_a"])
        filters = base_filters.merged(**{group_dim: [group_val]})
        extras["selection"] = {"group_dimension": group_dim, "group": group_val,
                               "standardizations": {k: {"confounder": v.data["confounder"],
                                                        "raw_gap_pp": v.data["raw_gap_pp"],
                                                        "standardized_gap_pp": v.data["standardized_gap_pp"],
                                                        "share_explained": v.data["share_of_gap_explained_by_mix"],
                                                        "evidence_id": v.evidence[0].id}
                                                    for k, v in sel["results"].items()}}
        experiment_req = PlanRequest(category="selection_effect", filters=base_filters, comparison=comparison,
                                     group=group_val)
    else:
        focus_metric, focus_filters = metric, filters
        if plan.intent == "mix_vs_rate":
            for d in ("channel", "device", "visitor_type", "market", "price_band"):
                if d not in filters.active():
                    run_tool(ctx, "mix_decomposition", {"metric": metric, "dimension": d, "filters": filters,
                                                        "comparison": comparison})
            scan = run_tool(ctx, "segment_scan", {"metric": metric, "filters": filters, "comparison": comparison})
            extras["simpson"] = simpson_table(ctx, metric, filters, comparison)
            if scan:
                extras["residual_scan"] = [{"segment": r["segment"], "change_pp": r["scan_change_pp"],
                                            "q_value": r["scan_q_value"]} for r in scan.data["flagged"][:5]]
        elif plan.intent == "opportunity":
            opp = run_tool(ctx, "opportunity_sizing", {"filters": filters, "comparison": comparison})
            if opp and opp.data["steps"]:
                top = opp.data["steps"][0]
                focus_metric = top["metric"]
                cell = top["largest_decline_cell"]
                focus_filters = filters.merged(market=[cell["market"]], device=[cell["device"]])
                scope_from_scan = True
                notes.append(f"Largest recoverable step: {top['label']}; focusing on its largest-decline cell "
                             f"({cell['market']} / {cell['device']}).")
                # The runner-up steps are investigated too: rankings between close steps are not robust.
                for other in opp.data["steps"][1:3]:
                    oc = other["largest_decline_cell"]
                    if other["sessions_lost_vs_baseline"] <= 0 or (oc.get("adj_p_value") or 1) >= 0.05:
                        continue
                    of = filters.merged(market=[oc["market"]], device=[oc["device"]])
                    rh, rc = run_battery(ctx, other["metric"], of, comparison)
                    related.append(_related_summary(of, other["metric"], rh, rc["target"]))
        elif plan.intent == "prioritize_markets":
            mp = run_tool(ctx, "market_priority", {"metric": metric, "filters": filters, "comparison": comparison})
            if mp and mp.data["rows"]:
                top = mp.data["rows"][0]
                focus_filters = filters.merged(market=[top["market"]])
                notes.append(f"Highest-priority market by within-channel loss: {top['market']}.")
                for r in mp.data["rows"][1:3]:
                    rf = filters.merged(market=[r["market"]])
                    rh, rc = run_battery(ctx, metric, rf, comparison)
                    related.append(_related_summary(rf, metric, rh, rc["target"]))
        elif plan.intent in ("abandon_vs_tracking", "scan_changes"):
            if plan.intent == "abandon_vs_tracking" and not filters.active():
                from app.analytics import metrics as M
                warns = M.quality_warnings(ctx.cur, ctx.trace, filters, comparison)
                segs = [w for w in warns if w.get("segment") and w["severity"] == "high"]
                extras["quality_warnings"] = warns
                if segs:
                    kind_metric = {"tracking": "listing_to_contact_raw", "failed_submit": "contact_start_to_submit"}
                    cands = [(kind_metric.get(w.get("kind"), metric),
                              filters.merged(**{k: [v] for k, v in w["segment"].items()}), 0.0) for w in segs]
                    notes.append("Segments with tracking-quality warnings were investigated first: "
                                 + "; ".join(f"{seg_label(w['segment'])} ({w['title']})" for w in segs[:3]) + ".")
                else:
                    cands = _scan_focus(ctx, ["contact_start_to_submit", "listing_to_contact_raw"], filters, comparison)
            elif plan.intent == "scan_changes":
                cands = _scan_focus(ctx, ["listing_to_contact_start", "contact_start_to_submit", "contact_to_viewing",
                                          "listing_to_contact"], filters, comparison, limit=4, watchlist=2, notes=notes)
                scope_from_scan = True
            else:
                cands = [(metric, filters, 0.0)]
            if cands:
                focus_metric, focus_filters, _ = cands[0]
                for m2, f2, _ in cands[1:4]:
                    rh, rc = run_battery(ctx, m2, f2, comparison)
                    related.append(_related_summary(f2, m2, rh, rc["target"]))
            else:
                notes.append("No segment passed the scan's FDR threshold; investigating the requested scope instead.")
        hyps, bctx = run_battery(ctx, focus_metric, focus_filters, comparison)
        target = bctx["target"]
        observed = _observed(target.data, focus_metric)
        metric, filters = focus_metric, focus_filters
        lead = rank(hyps)[0]
        version = None
        for call in ctx.calls:
            if call.tool == "version_breakdown" and call.status == "ok":
                ev = ctx.get(call.evidence_ids[0])
                nv = (ev.data.get("new_versions") if ev else None) or []
                version = ", ".join(nv) or None
        exp_filters = filters
        if lead.category == "technical_regression" and "device" not in bctx["fixed"]:
            worst = min((c for c in lead.segment_comparisons if c.get("q_value") is not None and c["q_value"] < 0.05
                         and (c.get("change_pp") or 0) < 0), key=lambda c: c["change_pp"], default=None)
            if worst and "device" in worst["segment"]:
                exp_filters = filters.merged(device=[worst["segment"]["device"]])
                notes.append(f"Experiment targeted at {worst['segment']['device']}, where the regression concentrates.")
        experiment_req = PlanRequest(category=lead.category, filters=exp_filters, comparison=comparison,
                                     version=version)

    finding = build_finding(question, metric, filters, comparison, observed, hyps, scope_from_scan) if observed else None

    experiment = None
    if experiment_req is not None:
        try:
            experiment = build_plan(ctx.cur, ctx.trace, experiment_req)
        except ValueError as exc:
            notes.append(f"Could not size an experiment: {exc}")

    target_ev = target.evidence[0].id if target else (finding.supporting_evidence[0] if finding and finding.supporting_evidence else None)
    narrative = None
    if provider is not None and finding is not None:
        try:
            narrative, issues = llm_narrative(provider, finding, hyps, ctx.evidence, related)
            if narrative is None:
                fb = deterministic_narrative(finding, hyps, related, target_ev)
                fb.validation_issues = issues
                fb.fallback_reason = "LLM narrative failed validation; showing the deterministic summary instead."
                narrative = fb
        except LLMError as exc:
            narrative = deterministic_narrative(finding, hyps, related, target_ev)
            narrative.fallback_reason = f"LLM unavailable ({exc}); showing the deterministic summary."
    if narrative is None:
        narrative = deterministic_narrative(finding, hyps, related, target_ev)

    extras["related_findings"] = related
    extras["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return Investigation(
        id="inv_" + uuid.uuid4().hex[:10], created_at=datetime.now(timezone.utc), question=question, mode=mode,
        plan=plan, planner_notes=notes, tool_calls=ctx.calls, evidence=ctx.evidence, hypotheses=rank(hyps),
        finding=finding, recommended_experiment=experiment, narrative=narrative, queries=ctx.trace.to_list(),
        extras=extras)


def simpson_table(ctx: ToolContext, metric: str, filters: Filters, comparison: Comparison,
                  dimension: str = "channel") -> dict[str, Any]:
    """Aggregate vs per-segment changes: the Simpson's-paradox view of the data."""
    from app.analytics import metrics as M
    tbl = M.segment_table(ctx.cur, ctx.trace, metric, filters, comparison, [dimension], name="simpson_by_" + dimension)
    wide, qid = M.metric_comparison(ctx.cur, ctx.trace, metric, filters, comparison, name="simpson_total")
    tot = wide.iloc[0]
    rows = [{"segment": seg_label(r["segment"]), "baseline_rate": r["baseline_rate"], "current_rate": r["current_rate"],
             "baseline_share": r["baseline_n"] / max(1, tot.baseline_n), "current_share": r["current_n"] / max(1, tot.current_n),
             "abs_change_pp": r["abs_change_pp"], "q_value": r["q_value"]} for r in tbl["rows"]]
    total = {"baseline_rate": tot.baseline_x / tot.baseline_n, "current_rate": tot.current_x / tot.current_n,
             "abs_change_pp": (tot.current_x / tot.current_n - tot.baseline_x / tot.baseline_n) * 100}
    n_down = sum(1 for r in rows if (r["abs_change_pp"] or 0) < 0)
    return {"dimension": dimension, "rows": rows, "total": total, "query_ids": [tbl["query_id"], qid],
            "explanation": (f"The aggregate moved {total['abs_change_pp']:+.2f}pp while {n_down} of {len(rows)} "
                            f"{dimension} segments declined. When segment shares shift, the aggregate can move even if "
                            "each segment is flat — or move opposite to every segment (Simpson's paradox).")}
