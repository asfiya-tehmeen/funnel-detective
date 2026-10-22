"""Turn a leading hypothesis into a concrete, measurable experiment plan.

Baseline rates and eligible traffic come from the data (current window of the target segment);
the intervention text comes from a small template library keyed by hypothesis category.
All sample-size numbers are computed, never written by an LLM. Everything in a plan is a
FORECAST — clearly labelled as such — until results are analysed.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.analytics import metrics as M
from app.analytics import queries as Q
from app.analytics.queries import Comparison, Filters, QueryTrace
from app.analytics.stats import sample_size_two_proportions

MDE_LADDER = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]
MAX_WEEKS = 6

TEMPLATES: dict[str, dict[str, Any]] = {
    "technical_regression": {
        "kind": "experiment",
        "name": "Contact-form fix validation",
        "hypothesis": "If the contact-form regression in {scope} is fixed, form completion will return towards its "
                      "pre-release level without lowering lead quality.",
        "intervention": "Ship the fixed contact form (or roll back the suspect release) behind a feature flag.",
        "control": "Current production contact form ({version}).",
        "treatment": "Fixed contact form (or previous release).",
        "primary_metric": "contact_start_to_submit",
        "guardrails": ["qualified_contact_rate", "listing_to_contact_start", "Failed-submit rate (must not rise)"],
        "mechanism": "Removes the error that blocks users who already decided to contact an agent.",
        "cost": "Low–medium: engineering fix plus a flag; most cost is diagnosis.",
        "risks": ["Fix introduces new validation gaps (spam leads)", "Other markets on the same release may have "
                  "a milder version of the bug", "If the issue is confirmed by error logs, holding back the fix "
                  "for an experiment has a real cost — consider a short holdback (10%) instead of 50/50"],
        "mde_mode": "recover",
    },
    "behavioral_change": {
        "kind": "experiment",
        "name": "Low-commitment contact option",
        "hypothesis": "Users in {scope} are saving but not contacting; offering a lower-commitment way to engage "
                      "(e.g. 'request price history / availability' or a callback slot) will raise contact starts.",
        "intervention": "Add a secondary 'Ask about this property' CTA with a short form and expected-response-time "
                        "message on listing pages in the segment.",
        "control": "Current listing page with the standard contact CTA.",
        "treatment": "Listing page with the additional low-commitment CTA.",
        "primary_metric": "listing_to_contact_start",
        "guardrails": ["qualified_contact_rate", "contact_to_viewing", "Agent response time"],
        "mechanism": "Lowers the perceived commitment of the first contact for hesitant high-value buyers.",
        "cost": "Medium: UI work plus agent-side handling of a new lead type.",
        "risks": ["More contacts but lower qualification", "Agents ignore the new lead type",
                  "The behaviour change may be market-wide (pricing/supply) and not addressable in the UI"],
        "mde_mode": "relative",
    },
    "traffic_composition": {
        "kind": "experiment",
        "name": "Paid-social landing experience",
        "hypothesis": "The aggregate decline in {scope} is driven by more low-intent traffic. A landing experience "
                      "built for exploratory users will raise their conversion without hurting lead quality.",
        "intervention": "For paid-social sessions, land on a curated listing set with 'save search' and soft "
                        "lead-capture instead of a single listing page.",
        "control": "Current paid-social landing page.",
        "treatment": "Exploration-first landing experience.",
        "primary_metric": "listing_to_contact",
        "guardrails": ["qualified_contact_rate", "save_rate", "Cost per qualified contact (marketing)"],
        "mechanism": "Matches the page to exploratory intent instead of expecting an immediate agent contact.",
        "cost": "Medium: landing template + campaign set-up. Also: report a mix-adjusted KPI (no engineering).",
        "risks": ["Optimising the page cannot fix a channel that is simply low-intent",
                  "Marketing may change spend mid-test, shifting the population"],
        "mde_mode": "relative",
        "segment_override": {"channel": ["paid_social"]},
    },
    "selection_effect": {
        "kind": "experiment",
        "name": "Randomised app-adoption prompt",
        "hypothesis": "Observed differences overstate the effect of {group}; a randomised encouragement design "
                      "will measure the causal effect of moving users to {group}.",
        "intervention": "Show an 'open in app / install' prompt to a random half of eligible web users.",
        "control": "No prompt.",
        "treatment": "Prompt shown.",
        "primary_metric": "listing_to_contact",
        "guardrails": ["Session bounce rate", "qualified_contact_rate"],
        "mechanism": "Random assignment breaks the link between user intent and surface choice.",
        "cost": "Low–medium: prompt UI; analysis needs intention-to-treat.",
        "risks": ["Low prompt uptake dilutes the measurable effect", "Prompt annoyance raises bounce"],
        "mde_mode": "relative",
    },
    "instrumentation_issue": {
        "kind": "data_fix",
        "name": "Tracking fix and reconciliation (no product experiment)",
        "hypothesis": "The apparent change in {scope} is a tracking artefact; once events are fixed, raw and "
                      "validated contact counts will reconcile with lead-service records.",
        "intervention": "Fix event emission in {version}; add a contract test that contact_submitted carries a lead_id.",
        "control": "n/a — not an A/B test.",
        "treatment": "n/a — data validation plan.",
        "primary_metric": "listing_to_contact_raw",
        "guardrails": ["Validated listing → contact (must be unchanged by the fix)"],
        "mechanism": "Removes phantom events; no user behaviour is expected to change.",
        "cost": "Low: client fix + tracking contract test.",
        "risks": ["Decisions taken on the inflated numbers before the fix", "Other app versions may share the bug"],
        "mde_mode": "none",
    },
    "random_variation": {
        "kind": "monitor",
        "name": "Monitor and collect more data (no experiment yet)",
        "hypothesis": "The observed movement in {scope} is within sampling noise.",
        "intervention": "No change. Keep monitoring with a pre-registered comparison window.",
        "control": "n/a", "treatment": "n/a",
        "primary_metric": "listing_to_contact",
        "guardrails": [],
        "mechanism": "n/a",
        "cost": "None.",
        "risks": ["A real but small change could be missed at this sample size"],
        "mde_mode": "relative",
    },
}


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    filters: Filters = Field(default_factory=Filters)
    comparison: Comparison = Field(default_factory=Comparison)
    alpha: float = Field(0.05, gt=0, lt=0.5)
    power: float = Field(0.8, gt=0.5, lt=0.99)
    relative_mde: float = Field(0.10, gt=0.005, lt=2.0, description="Relative MDE used when auto_mde is false")
    auto_mde: bool = Field(True, description="Pick the smallest relative MDE from a ladder that fits in MAX_WEEKS")
    traffic_allocation: float = Field(1.0, gt=0, le=1.0, description="Share of eligible traffic entering the test")
    group: Optional[str] = None
    version: Optional[str] = None


def build_plan(cur, trace: QueryTrace, req: PlanRequest) -> dict[str, Any]:
    tpl = TEMPLATES.get(req.category)
    if tpl is None:
        raise ValueError(f"unknown hypothesis category {req.category!r}")
    filters = req.filters
    if tpl.get("segment_override"):
        filters = filters.merged(**tpl["segment_override"])
    metric = tpl["primary_metric"]
    m = Q.METRICS[metric]
    res = M.overall_change(cur, trace, metric, filters, req.comparison)
    t = res["test"]
    days = (req.comparison.current.end - req.comparison.current.start).days + 1
    daily_eligible = t["current_n"] / days
    p_now, p_before = t["current_rate"], t["baseline_rate"]
    fmt = {"scope": filters.describe(), "group": req.group or "the segment", "version": req.version or "the current client"}
    plan: dict[str, Any] = {
        "kind": tpl["kind"], "status": "forecast",
        "forecast_note": "All numbers below are planning assumptions and forecasts, not observed outcomes.",
        "name": tpl["name"], "hypothesis_category": req.category,
        "problem_statement": (f"{m.label} in {filters.describe()} moved from {p_before:.2%} to {p_now:.2%} "
                              f"({t['abs_change_pp']:+.2f}pp) between the baseline and current windows."),
        "target_segment": filters.active(),
        "hypothesis": tpl["hypothesis"].format(**fmt),
        "intervention": tpl["intervention"].format(**fmt),
        "control": tpl["control"].format(**fmt), "treatment": tpl["treatment"].format(**fmt),
        "primary_metric": {"key": metric, "label": m.label, "numerator": m.numerator_label,
                           "denominator": m.denominator_label},
        "guardrail_metrics": [{"key": g, "label": Q.METRICS[g].label} if g in Q.METRICS else {"key": None, "label": g}
                              for g in tpl["guardrails"]],
        "mechanism": tpl["mechanism"], "implementation_cost": tpl["cost"], "risks": tpl["risks"],
        "baseline_rate": p_now, "pre_change_rate": p_before, "daily_eligible_sessions": daily_eligible,
        "eligible_sessions_in_current_window": t["current_n"], "query_id": res["query_id"],
        "alpha": req.alpha, "power": req.power, "traffic_allocation": req.traffic_allocation,
    }
    if tpl["mde_mode"] == "none" or tpl["kind"] != "experiment":
        plan.update(mde=None, sample_size_per_arm=None, duration_days=None,
                    decision_rule=("Not an A/B test. Done when raw and validated contact counts reconcile with "
                                   "lead-service records for 7 consecutive days." if tpl["kind"] == "data_fix" else
                                   "Re-run the comparison after more data accrues; act only if the change exceeds the "
                                   "minimum detectable effect and survives multiple-comparison correction."))
        if tpl["kind"] == "monitor" and 0 < p_now < 1:
            mde_abs = p_now * req.relative_mde
            n = sample_size_two_proportions(p_now, mde_abs, req.alpha, req.power)
            plan["monitoring_sample_needed"] = {"relative_change": req.relative_mde, "sessions_per_window": n,
                                                "days_at_current_traffic": math.ceil(n / max(daily_eligible, 1e-9))}
        return plan

    if tpl["mde_mode"] == "recover" and p_before > p_now:
        mde_abs = max((p_before - p_now) * 0.5, p_now * 0.05)
        mde_basis = ("Half of the observed drop (we want to detect at least partial recovery), "
                     f"{mde_abs * 100:.2f}pp.")
    elif req.auto_mde:
        # Smallest relative MDE from a fixed ladder that fits in MAX_WEEKS at current traffic.
        in_test = daily_eligible * req.traffic_allocation
        chosen = None
        for rel in MDE_LADDER:
            if 0 < p_now * (1 + rel) < 1:
                n_try = sample_size_two_proportions(p_now, p_now * rel, req.alpha, req.power)
                if 2 * n_try / max(in_test, 1e-9) <= MAX_WEEKS * 7:
                    chosen = rel
                    break
        rel = chosen or MDE_LADDER[-1]
        mde_abs = p_now * rel
        mde_basis = (f"Smallest relative lift from {', '.join(f'{x:.0%}' for x in MDE_LADDER)} detectable within "
                     f"{MAX_WEEKS} weeks at current traffic: {rel:.0%} ({mde_abs * 100:.2f}pp absolute)."
                     + ("" if chosen else " Even the largest step does not fit — the segment is too small for a "
                                         "clean A/B test."))
    else:
        mde_abs = p_now * req.relative_mde
        mde_basis = f"{req.relative_mde:.0%} relative lift on the current rate ({mde_abs * 100:.2f}pp absolute)."
    if not 0 < p_now + mde_abs < 1 or p_now <= 0:
        raise ValueError("baseline rate too close to 0 or 1 for a sample-size calculation")
    n = sample_size_two_proportions(p_now, mde_abs, req.alpha, req.power)
    in_test_daily = daily_eligible * req.traffic_allocation
    raw_days = (2 * n) / max(in_test_daily, 1e-9)
    duration = max(14, math.ceil(raw_days / 7) * 7)
    plan.update(
        mde={"absolute_pp": mde_abs * 100, "relative": mde_abs / p_now, "basis": mde_basis},
        sample_size_per_arm=n,
        duration_days=duration,
        duration_note=(f"{2 * n:,} eligible sessions needed at ~{in_test_daily:,.0f}/day in test "
                       f"→ {raw_days:.1f} days, rounded up to whole weeks with a 14-day minimum to cover weekly cycles."
                       + (" Longer than 8 weeks: consider a larger MDE, a broader segment or a more sensitive metric."
                          if duration > 56 else "")),
        assumptions=[f"Baseline = current-window rate {p_now:.2%} (from data)",
                     f"Two-sided α={req.alpha}, power={req.power}, 50/50 split",
                     f"Eligible traffic = {daily_eligible:,.0f} sessions/day (current-window average)",
                     "Sessions treated as independent (user-level clustering would widen intervals slightly)"],
        decision_rule=(f"Ship if the treatment–control difference in {m.label} is positive with a two-sided 95% CI "
                       "excluding 0, no guardrail degrades by more than its margin (default: lower 95% bound above "
                       "−1pp), and sample-ratio mismatch p ≥ 0.001. Iterate if the effect is positive but the CI "
                       "includes 0 or a guardrail is borderline. Abandon if the CI excludes the MDE "
                       "(upper bound below it) or the effect is significantly negative."),
    )
    return plan
