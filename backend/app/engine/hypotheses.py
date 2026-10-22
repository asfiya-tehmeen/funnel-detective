"""Hypothesis engine.

For any metric change in a scope, the engine ALWAYS evaluates the full set of competing
explanations (technical regression, traffic composition, behavioural change, instrumentation
issue, random variation). Each hypothesis states the pattern it predicts, then a set of checks
compares that prediction with evidence produced by approved tools. Status is assigned by an
explicit, displayed rule — it is not a probability and not a model-generated score.

The battery is mandatory: a planner (LLM or deterministic) can add tools but cannot skip it.
"""
from __future__ import annotations

from typing import Any

from app.analytics import queries as Q
from app.analytics.queries import Comparison, Filters, Window
from app.analytics.stats import compare_proportions
from app.data.schema import DIMENSIONS
from app.engine.schemas import Check, Hypothesis
from app.engine.tools import ToolContext, pct, pp, pval, run_tool, seg_label

STATUS_RULE = ("supported: ≥2 supporting checks and none contradicting · partially supported: ≥1 supporting and at "
               "least as many supporting as contradicting · not supported: more contradicting than supporting, or "
               "only contradicting · inconclusive: no decisive checks. Status summarises rule-based checks; it is "
               "not a probability. Explanations with support are ordered by the share of the change they can account "
               "for, because several can be true at once.")
STATUS_ORDER = {"supported": 0, "partially_supported": 1, "inconclusive": 2, "not_supported": 3}
CONTACT_FAMILY = {"listing_to_contact", "listing_to_contact_start", "contact_start_to_submit",
                  "qualified_contact_rate", "listing_to_contact_raw"}


def _status(checks: list[Check]) -> str:
    s = sum(c.result == "supports" for c in checks)
    c = sum(c.result == "contradicts" for c in checks)
    if s >= 2 and c == 0:
        return "supported"
    if s >= 1 and s >= c:
        return "partially_supported"
    if c > 0:
        return "not_supported"
    return "inconclusive"


def _hyp(hid: str, category: str, title: str, predicted: str, checks: list[Check], discriminating: list[str],
         level: str, comparisons: list[dict[str, Any]] | None = None, share: float | None = None,
         share_basis: str | None = None) -> Hypothesis:
    return Hypothesis(id=hid, category=category, title=title, predicted_pattern=predicted, checks=checks,
                      segment_comparisons=comparisons or [], discriminating_data=discriminating,
                      evidence_level=level, status=_status(checks), status_rule=STATUS_RULE,
                      explained_share=None if share is None else max(0.0, min(1.0, share)),
                      explained_share_basis=share_basis)


def _ids(res) -> list[str]:
    return [e.id for e in res.evidence] if res else []


def _fixed_dims(filters: Filters) -> dict[str, str]:
    return {d: v[0] for d, v in filters.active().items() if len(v) == 1}


def _sign_share(rows: list[dict[str, Any]], key: str, direction: float) -> tuple[int, int]:
    tested = [r for r in rows if r.get("tested") and r.get(key) is not None]
    same = sum((r[key] < 0) == (direction < 0) for r in tested)
    return same, len(tested)


def run_battery(ctx: ToolContext, metric: str, filters: Filters, comparison: Comparison) -> tuple[list[Hypothesis], dict[str, Any]]:
    by = "hypothesis_battery"
    base = {"filters": filters, "comparison": comparison}
    target = run_tool(ctx, "compare_metric", {"metric": metric, **base}, by)
    if target is None:
        raise ValueError(ctx.calls[-1].error or "could not compute the target metric")
    T = target.data["test"]
    direction = T["abs_change_pp"]
    contact_family = metric in CONTACT_FAMILY

    steps = run_tool(ctx, "step_breakdown", base, by)
    S = steps.data["steps"] if steps else {}
    quality = run_tool(ctx, "quality_check", base, by)
    QD = quality.data if quality else {"baseline": {}, "current": {}, "duplicate_rate": {}}
    power = run_tool(ctx, "power_check", {"metric": metric, **base}, by)
    stability = run_tool(ctx, "baseline_stability", {"metric": metric, **base}, by)

    # Which step moved most (relative, within-channel)? Use it for the change-point and siblings.
    def rel(m: str) -> float:
        r = S.get(m)
        if not r or not r["channel_adjusted"] or not r["raw"]["baseline_rate"]:
            return 0.0
        return r["channel_adjusted"]["adj_change_pp"] / 100 / r["raw"]["baseline_rate"]
    focus_step = metric
    if contact_family and S:
        focus_step = min(("listing_to_contact_start", "contact_start_to_submit"), key=lambda m: rel(m) * (1 if direction < 0 else -1))
    full = Window(start=comparison.baseline.start, end=comparison.current.end)
    cp = run_tool(ctx, "change_point", {"metric": focus_step, "filters": filters, "window": full}, by)

    fixed = _fixed_dims(filters)
    free_dims = [d for d in ("channel", "device", "visitor_type", "market", "price_band") if d not in fixed]
    mixes = {d: run_tool(ctx, "mix_decomposition", {"metric": metric, "dimension": d, **base}, by) for d in free_dims}
    siblings = {}
    for d, v in fixed.items():
        siblings[d] = run_tool(ctx, "segment_breakdown", {"metric": focus_step, "dimension": d,
                                                          "filters": filters.merged(**{d: []}),
                                                          "comparison": comparison}, by)
    within = {}
    for d in ("device", "channel"):
        if d not in fixed:
            within[d] = run_tool(ctx, "segment_breakdown", {"metric": focus_step, "dimension": d, **base,
                                                            "adjust_for": None if d == "channel" else "channel"}, by)
    versions = run_tool(ctx, "version_breakdown", {"metric": focus_step, **base}, by)
    raw_cmp = None
    if metric in ("listing_to_contact", "contact_start_to_submit", "qualified_contact_rate"):
        raw_cmp = run_tool(ctx, "compare_metric", {"metric": "listing_to_contact_raw", **base}, by)

    hyps: list[Hypothesis] = []
    start, submit = S.get("listing_to_contact_start"), S.get("contact_start_to_submit")
    qb, qc = QD.get("baseline") or {}, QD.get("current") or {}
    fb, fc = qb.get("failed_submit_rate") or 0, qc.get("failed_submit_rate") or 0
    lb, lc = qb.get("submit_without_lead_rate") or 0, qc.get("submit_without_lead_rate") or 0
    ob, oc = qb.get("submit_before_start_rate") or 0, qc.get("submit_before_start_rate") or 0
    steps_ids, q_ids, cp_ids = _ids(steps), _ids(quality), _ids(cp)
    scope = filters.describe()

    def increase(rate_key: str, n_key: str, min_abs: float, min_ratio: float) -> tuple[bool, bool, float | None]:
        """(meaningful significant increase, significant increase at all, p) for a quality rate."""
        nb_, nc_ = qb.get(n_key) or 0, qc.get(n_key) or 0
        rb_, rc_ = qb.get(rate_key) or 0, qc.get(rate_key) or 0
        if nb_ < 1 or nc_ < 1:
            return False, False, None
        t = compare_proportions(round(rb_ * nb_), nb_, round(rc_ * nc_), nc_)
        sig_up = t.p_value < 0.01 and rc_ > rb_
        return sig_up and rc_ - rb_ >= min_abs and rc_ >= min_ratio * max(rb_, 0.002), sig_up, t.p_value

    failed_up, failed_sig, failed_p = increase("failed_submit_rate", "contact_start_sessions", 0.03, 2)
    lead_up, lead_sig, lead_p = increase("submit_without_lead_rate", "listing_sessions", 0.02, 3)
    order_up, _, order_p = increase("submit_before_start_rate", "listing_sessions", 0.02, 3)

    # ---------------- H1 technical regression ----------------
    checks: list[Check] = []
    form_share = None
    if contact_family and start and submit and submit["channel_adjusted"] and start["channel_adjusted"]:
        sa, st = submit["channel_adjusted"], start["channel_adjusted"]
        r_sub, r_st = rel("contact_start_to_submit"), rel("listing_to_contact_start")
        if sa["adj_p_value"] < 0.01 and sa["adj_change_pp"] < 0 and abs(r_sub) >= 1.5 * abs(r_st):
            res = "supports"
        elif sa["adj_p_value"] > 0.1 or sa["adj_change_pp"] >= 0:
            res = "contradicts"
        else:
            res = "neutral"
        checks.append(Check(description="Is the decline concentrated in form completion (contact start → submit)?",
                            result=res, evidence_ids=steps_ids,
                            detail=f"Form completion {pp(sa['adj_change_pp'])} within-channel ({r_sub:+.0%} rel, "
                                   f"p={pval(sa['adj_p_value'])}) vs contact starts {pp(st['adj_change_pp'])} "
                                   f"({r_st:+.0%} rel)."))
        lost_total = -direction / 100 * T["current_n"]
        if direction < 0 and lost_total > 0 and metric != "contact_start_to_submit":
            lost_form = -sa["adj_change_pp"] / 100 * submit["raw"]["current_n"]
            share = lost_form / lost_total
            form_share = share
            res = "supports" if share >= 0.5 else ("contradicts" if share < 0.25 else "neutral")
            checks.append(Check(description="Is a form problem big enough to explain the size of the decline?",
                                result=res, evidence_ids=steps_ids + _ids(target),
                                detail=f"≈{max(lost_form, 0):.0f} contacts lost at form completion (within-channel) vs "
                                       f"≈{lost_total:.0f} lost overall ({max(share, 0):.0%})."))
        elif metric == "contact_start_to_submit" and direction < 0:
            form_share = sa["adj_change_pp"] / direction
    if failed_up:
        res = "supports"
    elif not failed_sig:
        res = "contradicts"
    else:
        res = "neutral"
    checks.append(Check(description="Did failed contact submissions increase?", result=res, evidence_ids=q_ids,
                        detail=f"Failed submits per contact-start session {pct(fb)} → {pct(fc)} "
                               f"(test of increase p={pval(failed_p)})."))
    tech_signal = failed_up or (bool(checks) and checks[0].result == "supports" and contact_family)
    if cp:
        strength = cp.data.get("strength")
        res = "supports" if strength == "sharp" else ("neutral" if strength == "moderate" else "contradicts")
        checks.append(Check(description=f"Is there an abrupt break in {Q.METRICS[focus_step].label}?", result=res,
                            evidence_ids=cp_ids,
                            detail=f"Best single break on {cp.data.get('date')} ({strength}, LLR "
                                   f"{cp.data.get('llr', 0):.1f}): {pct(cp.data.get('rate_before'))} → "
                                   f"{pct(cp.data.get('rate_after'))}."))
    comps: list[dict[str, Any]] = []
    if "device" in fixed and siblings.get("device"):
        rows = siblings["device"].data["rows"]
        mine = next((r for r in rows if r["segment"]["device"] == fixed["device"]), None)
        others = [r for r in rows if r["segment"]["device"] != fixed["device"] and r["tested"]]
        comps += [{"segment": r["segment"], "baseline_rate": r["baseline_rate"], "current_rate": r["current_rate"],
                   "change_pp": r.get("adj_change_pp", r["abs_change_pp"]), "q_value": r.get("adj_q_value")}
                  for r in rows]
        if mine and mine["tested"]:
            m_chg = mine.get("adj_change_pp", mine["abs_change_pp"])
            o_chg = [o.get("adj_change_pp", o["abs_change_pp"]) for o in others]
            sig_mine = (mine.get("adj_p_value") or mine["p_value"]) < 0.01
            similar = [c for c in o_chg if (c < 0) == (m_chg < 0) and abs(c) >= abs(m_chg) / 3]
            if sig_mine and not similar:
                res = "supports"
            elif len(similar) == len(o_chg) and o_chg:
                res = "contradicts"
            else:
                res = "neutral"
            checks.append(Check(description=f"Is the change specific to {fixed['device']} (vs other devices, "
                                            f"{filters.merged(device=[]).describe()})?", result=res,
                                evidence_ids=_ids(siblings["device"]),
                                detail=f"{fixed['device']} {pp(m_chg)}; other devices: "
                                       + ", ".join(f"{o['segment']['device']} {pp(o.get('adj_change_pp', o['abs_change_pp']))}"
                                                   for o in others) + "."))
    elif within.get("device"):
        rows = [r for r in within["device"].data["rows"] if r["tested"]]
        losses = {r["segment"]["device"]: -(r.get("adj_impact_sessions") or r["impact_sessions"] or 0) for r in rows}
        total = sum(v for v in losses.values() if v > 0)
        top = max(losses, key=losses.get) if losses else None
        share = (losses[top] / total) if top and total > 0 else 0
        comps += [{"segment": r["segment"], "baseline_rate": r["baseline_rate"], "current_rate": r["current_rate"],
                   "change_pp": r.get("adj_change_pp", r["abs_change_pp"]), "q_value": r.get("adj_q_value")}
                  for r in rows]
        res = "supports" if share >= 0.6 and len(rows) > 1 else "contradicts"
        checks.append(Check(description="Is the decline concentrated on one device/platform?", result=res,
                            evidence_ids=_ids(within["device"]),
                            detail=f"{top} accounts for {share:.0%} of the sessions lost across devices." if top
                            else "No device-level data."))
    if versions:
        nv = versions.data.get("new_versions", [])
        if nv and tech_signal:
            res, det = "supports", f"Technical signals coincide with new client version(s) {', '.join(nv)}."
        elif nv:
            res, det = "neutral", (f"New client version(s) {', '.join(nv)} appeared, but without a technical "
                                   "signal a release is only a coincident event.")
        else:
            res, det = "neutral", "No new client version appeared in the current window."
        checks.append(Check(description="Does the timing coincide with a release?", result=res,
                            evidence_ids=_ids(versions), detail=det))
    hyps.append(_hyp("H1", "technical_regression", f"UI or technical regression affecting {scope}",
                     "An abrupt drop starting on a specific date, concentrated in one step (usually form completion) "
                     "and one platform, with more failed submissions and alignment with a release.",
                     checks,
                     ["Client-side error logs and lead-service error rates by release",
                      "Form-step funnel (field-level abandonment) for the affected platform",
                      "A holdback/rollback of the suspect release on a share of traffic"],
                     "associational", comps, share=form_share,
                     share_basis="Within-channel contacts lost at form completion as a share of all contacts lost."))

    # ---------------- H2 traffic composition ----------------
    checks = []
    best_dim, best_share, best = None, None, None
    for d, r in mixes.items():
        if r and r.data.get("mix_share_of_change") is not None:
            if best_share is None or r.data["mix_share_of_change"] > best_share:
                best_dim, best_share, best = d, r.data["mix_share_of_change"], r
    if best is not None:
        res = "supports" if best_share >= 0.5 else ("contradicts" if best_share <= 0.2 else "neutral")
        checks.append(Check(description="Does a shift in segment mix account for most of the change?", result=res,
                            evidence_ids=_ids(best),
                            detail=f"Largest mix effect is by {best_dim}: {pp(best.data['mix_effect_pp'], 2)} of "
                                   f"{pp(best.data['total_change_pp'], 2)} ({best_share:.0%})."))
        grew = max(best.data["segments"], key=lambda s: s["current_share"] - s["baseline_share"])
        overall_rate = T["baseline_rate"]
        if grew["current_share"] - grew["baseline_share"] > 0.03:
            res = "supports" if (grew["baseline_rate"] < overall_rate) == (direction < 0) else "contradicts"
            checks.append(Check(description="Did the fastest-growing segment convert differently from average?",
                                result=res, evidence_ids=_ids(best),
                                detail=f"{seg_label(grew['segment'])} grew from {pct(grew['baseline_share'], 0)} to "
                                       f"{pct(grew['current_share'], 0)} of the denominator and converts at "
                                       f"{pct(grew['baseline_rate'])} vs {pct(overall_rate)} overall."))
    adj_target = target.data.get("channel_adjusted")
    if adj_target:
        p_adj = adj_target["adj_p_value"]
        res = "supports" if p_adj >= 0.05 else ("contradicts" if p_adj < 0.01 else "neutral")
        checks.append(Check(description="Is the within-channel change small once channel mix is held fixed?",
                            result=res, evidence_ids=_ids(target),
                            detail=f"Channel-standardised change {pp(adj_target['adj_change_pp'], 2)} "
                                   f"(p={pval(p_adj)}) vs raw {pp(direction, 2)}."))
    hyps.append(_hyp("H2", "traffic_composition", f"Traffic-mix shift (not a behaviour change) in {scope}",
                     "Segment shares change (e.g. more low-intent paid-social traffic) while rates within each segment "
                     "stay flat; a mix decomposition attributes most of the change to the mix effect.",
                     checks,
                     ["Campaign calendar and spend by channel", "Mix-adjusted KPI reported alongside the raw KPI",
                      "Within-channel trend for the most-grown channel"],
                     "associational",
                     [{"segment": s["segment"], "baseline_share": s["baseline_share"], "current_share": s["current_share"],
                       "baseline_rate": s["baseline_rate"], "current_rate": s["current_rate"]}
                      for s in (best.data["segments"][:6] if best else [])],
                     share=best_share if (best_share is not None and direction != 0) else None,
                     share_basis=f"Kitagawa mix effect by {best_dim} as a share of the total change." if best_dim else None))

    # ---------------- H3 behavioural change ----------------
    checks = []
    start_share = None
    if contact_family and start and submit and start["channel_adjusted"] and submit["channel_adjusted"]:
        st, sa = start["channel_adjusted"], submit["channel_adjusted"]
        if direction != 0 and (st["adj_change_pp"] < 0) == (direction < 0):
            if metric == "listing_to_contact_start":
                start_share = st["adj_change_pp"] / direction
            elif metric != "contact_start_to_submit":
                start_share = st["adj_change_pp"] * submit["raw"]["current_rate"] / direction
        if st["adj_p_value"] < 0.01 and (st["adj_change_pp"] < 0) == (direction < 0) and sa["adj_p_value"] > 0.05:
            res = "supports"
        elif st["adj_p_value"] > 0.1:
            res = "contradicts"
        else:
            res = "neutral"
        checks.append(Check(description="Did intent to contact (listing → contact start) change while form "
                                        "completion held steady?", result=res, evidence_ids=steps_ids,
                            detail=f"Contact starts {pp(st['adj_change_pp'])} within-channel (p={pval(st['adj_p_value'])}); "
                                   f"form completion {pp(sa['adj_change_pp'])} (p={pval(sa['adj_p_value'])})."))
    for d, r in within.items():
        if not r:
            continue
        key = "adj_change_pp" if any(x.get("adj_change_pp") is not None for x in r.data["rows"]) else "abs_change_pp"
        same, n = _sign_share(r.data["rows"], key, direction)
        if n >= 2:
            res = "supports" if same / n >= 0.75 else ("contradicts" if same / n <= 0.5 else "neutral")
            checks.append(Check(description=f"Is the change broad-based across {d}s?", result=res,
                                evidence_ids=_ids(r),
                                detail=f"{same} of {n} tested {d} segments move in the same direction."))
    no_tech = not failed_sig and not lead_sig
    checks.append(Check(description="Are technical and tracking signals absent?",
                        result="supports" if no_tech else "contradicts", evidence_ids=q_ids,
                        detail=f"Failed submits {pct(fb)} → {pct(fc)} (p={pval(failed_p)}); submits without lead_id "
                               f"{pct(lb)} → {pct(lc)} (p={pval(lead_p)})."))
    sv = S.get("save_rate")
    if sv and sv["channel_adjusted"]:
        res = "supports" if sv["channel_adjusted"]["adj_p_value"] < 0.05 else "neutral"
        checks.append(Check(description="Did adjacent behaviour (saving properties) shift too?", result=res,
                            evidence_ids=steps_ids,
                            detail=f"Save rate {pp(sv['channel_adjusted']['adj_change_pp'])} within-channel "
                                   f"(p={pval(sv['channel_adjusted']['adj_p_value'])})."))
    hyps.append(_hyp("H3", "behavioral_change", f"Genuine change in user behaviour in {scope}",
                     "Users in the segment change what they do (e.g. fewer contact starts, more saves) across devices "
                     "and channels, with no rise in errors or tracking anomalies.",
                     checks,
                     ["Supply and pricing changes for the segment (listing inventory, price movements)",
                      "User research or survey on why users save but do not contact",
                      "Competitor or seasonality data for the market"],
                     "associational", share=start_share,
                     share_basis="Within-channel contacts lost through fewer contact starts as a share of all contacts lost."))

    # ---------------- H4 instrumentation ----------------
    checks = []
    res = "supports" if lead_up else "contradicts"
    checks.append(Check(description="Are contact events arriving without a lead_id?", result=res, evidence_ids=q_ids,
                        detail=f"contact_submitted without lead_id: {pct(lb)} → {pct(lc)} of listing sessions "
                               f"(p={pval(lead_p)})."))
    res = "supports" if order_up else "neutral"
    checks.append(Check(description="Are submits firing before contact_started (impossible order)?", result=res,
                        evidence_ids=q_ids, detail=f"Out-of-order submits: {pct(ob)} → {pct(oc)} of listing sessions "
                                                   f"(p={pval(order_p)})."))
    if raw_cmp:
        rt = raw_cmp.data["test"]
        gap = rt["abs_change_pp"] - direction
        if abs(gap) >= 2 and (lead_sig or order_up):
            res, why = "supports", "events without a lead_id or out of order account for the gap"
        elif abs(gap) >= 2 and failed_sig:
            res, why = "neutral", ("the gap comes from failed submissions, which are real user-facing errors "
                                   "rather than a tracking fault")
        elif abs(gap) >= 2:
            res, why = "neutral", "the source of the gap is unclear"
        else:
            res, why = "contradicts", "the two definitions agree"
        checks.append(Check(description="Do raw-event and validated metrics disagree in a way that points to tracking?",
                            result=res, evidence_ids=_ids(raw_cmp) + _ids(target),
                            detail=f"Raw-event contact rate {pp(rt['abs_change_pp'])} vs validated {pp(direction)} "
                                   f"(difference {gap:+.1f}pp); {why}."))
    dup = QD.get("duplicate_rate") or {}
    if dup:
        res = "supports" if dup.get("current", 0) - dup.get("baseline", 0) > 0.01 else "neutral"
        checks.append(Check(description="Did duplicate firing increase?", result=res, evidence_ids=q_ids,
                            detail=f"Duplicate events {pct(dup.get('baseline'))} → {pct(dup.get('current'))}."))
    hyps.append(_hyp("H4", "instrumentation_issue", f"Tracking/instrumentation problem in {scope}",
                     "Event counts change without a matching change in real leads: events missing lead_id, firing out "
                     "of order or duplicated, and raw vs validated metrics diverging, often after a client release.",
                     checks,
                     ["Lead-service (CRM) lead counts for the same segment and window",
                      "Client release notes / tracking-plan diff for the new version",
                      "Event payload validation logs"],
                     "descriptive"))

    # ---------------- H5 random variation ----------------
    checks = []
    p = T["p_value"]
    res = "supports" if p >= 0.05 else ("contradicts" if p < 0.001 else "neutral")
    checks.append(Check(description="Is the change distinguishable from noise?", result=res, evidence_ids=_ids(target),
                        detail=f"{T['test']}, p={pval(p)}; 95% CI {T['diff_ci_pp'][0]:+.2f} to {T['diff_ci_pp'][1]:+.2f}pp."))
    if power:
        mde, obs = power.data.get("mde_pp"), power.data.get("observed_abs_pp")
        if mde is not None:
            checks.append(Check(description="Is the observed change larger than the minimum detectable effect?",
                                result="contradicts" if obs >= mde else "supports", evidence_ids=_ids(power),
                                detail=f"Observed {obs:.2f}pp vs MDE {mde:.2f}pp at {power.data['n_per_window']:,} "
                                       "sessions per window."))
    k = 1
    for d in fixed:
        k *= len(DIMENSIONS[d])
    if k > 1:
        res = "contradicts" if p * k < 0.05 else "supports"
        checks.append(Check(description=f"Does it survive correction for the {k} sibling segments one could have looked at?",
                            result=res, evidence_ids=_ids(target),
                            detail=f"Bonferroni-adjusted p = {pval(min(1.0, p * k))} (p × {k})."))
    if stability and stability.data:
        unusual = stability.data["unusual_baseline"]
        checks.append(Check(description="Was the baseline window itself unusual (regression to the mean)?",
                            result="supports" if unusual else "contradicts", evidence_ids=_ids(stability),
                            detail=f"Baseline vs prior period {pp(stability.data['test']['abs_change_pp'])}, "
                                   f"p={pval(stability.data['test']['p_value'])}."))
    hyps.append(_hyp("H5", "random_variation", f"Random variation / small sample in {scope}",
                     "The change is within what sampling noise produces: wide intervals, p-values that do not survive "
                     "multiple-comparison correction, and an effect smaller than the detectable minimum.",
                     checks,
                     ["More weeks of data for the same segment", "A pre-registered comparison window",
                      "Pooling with similar segments to increase sample size"],
                     "descriptive",
                     # A change indistinguishable from zero is, most parsimoniously, entirely noise.
                     share=1.0 if p >= 0.05 else None,
                     share_basis="The change is not distinguishable from zero (p ≥ 0.05), so noise can account for all of it."
                     if p >= 0.05 else None))

    context = {"target": target, "steps": steps, "quality": quality, "focus_step": focus_step,
               "change_point": cp, "mixes": mixes, "power": power, "fixed": fixed}
    return hyps, context


def run_selection_battery(ctx: ToolContext, metric: str, filters: Filters, window: Window, dimension: str,
                          group_a: str, group_b: str | None) -> tuple[list[Hypothesis], dict[str, Any]]:
    """Competing explanations for 'segment A converts better than B'."""
    by = "hypothesis_battery"
    results = {}
    for confs in (["visitor_type"], ["channel"], ["intent"], ["visitor_type", "channel"]):
        if dimension in confs:
            continue
        results["+".join(confs)] = run_tool(ctx, "standardized_comparison", {
            "metric": metric, "dimension": dimension, "group_a": group_a, "group_b": group_b,
            "confounders": confs, "filters": filters, "window": window}, by)
    valid = {k: v for k, v in results.items() if v}
    if not valid:
        raise ValueError(ctx.calls[-1].error if ctx.calls else "standardisation failed")
    joint = valid.get("visitor_type+channel") or next(iter(valid.values()))
    best_single = max((v for k, v in valid.items() if "+" not in k),
                      key=lambda v: v.data.get("share_of_gap_explained_by_mix") or -9, default=joint)
    J = joint.data
    share = J.get("share_of_gap_explained_by_mix")
    w = J.get("within_strata_test") or {}
    raw_gap = J["raw_gap_pp"]
    label_b = J["group_b"]

    checks = []
    if share is not None:
        res = "supports" if share >= 0.5 else ("contradicts" if share <= 0.2 else "neutral")
        checks.append(Check(description="Does the gap shrink once audiences are made comparable?", result=res,
                            evidence_ids=_ids(joint),
                            detail=f"Raw gap {pp(raw_gap)} → {pp(J['standardized_gap_pp'])} after standardising by "
                                   f"{J['confounder']} ({share:.0%} explained by mix)."))
    BS = best_single.data
    res = "supports" if BS["max_share_difference"] >= 0.2 else ("contradicts" if BS["max_share_difference"] < 0.05 else "neutral")
    ga, gb = BS["groups"][BS["group_a"]], BS["groups"][BS["group_b"]]
    biggest = max(ga["strata"], key=lambda s: abs(ga["strata"][s]["share"] - gb["strata"][s]["share"]))
    checks.append(Check(description=f"Do {group_a} and {label_b} serve different audiences?", result=res,
                        evidence_ids=_ids(best_single),
                        detail=f"{BS['confounder']}={biggest}: {pct(ga['strata'][biggest]['share'], 0)} of {group_a} vs "
                               f"{pct(gb['strata'][biggest]['share'], 0)} of {label_b}."))
    if w:
        res = "supports" if w["adj_p_value"] >= 0.05 else "neutral"
        checks.append(Check(description="Does any gap remain within comparable audiences?", result=res,
                            evidence_ids=_ids(joint),
                            detail=f"Within-strata gap {pp(w['adj_change_pp'])} (p={pval(w['adj_p_value'])})"
                                   + ("; selection explains part, not all, of the gap." if w["adj_p_value"] < 0.05 else ".")))
    strata_rows = [{"stratum": s, f"{group_a}_share": ga["strata"][s]["share"], f"{label_b}_share": gb["strata"][s]["share"],
                    f"{group_a}_rate": ga["strata"][s]["rate"], f"{label_b}_rate": gb["strata"][s]["rate"]}
                   for s in ga["strata"]]
    hyps = [_hyp("H1", "selection_effect", f"{group_a}'s advantage is mostly about who uses it (selection)",
                 f"{group_a} attracts a different audience (e.g. returning, high-intent users); once audiences are "
                 "matched the gap shrinks substantially.", checks,
                 [f"A randomised encouragement experiment (e.g. prompt a random half of users to {group_a})",
                  "User-level longitudinal analysis before/after adoption", "Intent signals prior to the session"],
                 "associational", strata_rows)]
    checks = []
    if w:
        res = "supports" if (w["adj_p_value"] < 0.05 and w["adj_change_pp"] > 0) else "contradicts"
        checks.append(Check(description=f"Is {group_a} better within comparable audiences?", result=res,
                            evidence_ids=_ids(joint),
                            detail=f"Within-strata gap {pp(w['adj_change_pp'])}, p={pval(w['adj_p_value'])}."))
    if share is not None:
        checks.append(Check(description="Does most of the raw gap survive standardisation?",
                            result="supports" if share < 0.5 else "contradicts", evidence_ids=_ids(joint),
                            detail=f"{1 - share:.0%} of the raw gap remains after standardisation."))
    hyps.append(_hyp("H2", "behavioral_change", f"Genuine {group_a} advantage (the surface itself converts better)",
                     f"{group_a} converts better even among comparable users, so the gap survives standardisation.",
                     checks, ["Randomised experiment on the surface", "Feature-level comparison of contact flows"],
                     "associational"))
    checks = []
    a_ci, b_ci = ga["raw_ci"], gb["raw_ci"]
    overlap = a_ci and b_ci and (a_ci[0] <= b_ci[1] and b_ci[0] <= a_ci[1])
    checks.append(Check(description="Do the raw rates' confidence intervals overlap?",
                        result="supports" if overlap else "contradicts", evidence_ids=_ids(joint),
                        detail=f"{group_a} {pct(ga['raw_rate'])} [{pct(a_ci[0])}, {pct(a_ci[1])}] vs {label_b} "
                               f"{pct(gb['raw_rate'])} [{pct(b_ci[0])}, {pct(b_ci[1])}]."))
    hyps.append(_hyp("H3", "random_variation", "The gap is sampling noise",
                     "Intervals overlap and the gap is not distinguishable from zero.", checks,
                     ["More data"], "descriptive"))
    return hyps, {"joint": joint, "results": valid, "raw_gap_pp": raw_gap}


def rank(hyps: list[Hypothesis]) -> list[Hypothesis]:
    """Order explanations for display and for choosing the leading one.

    1. Explanations with at least partial support come before inconclusive and unsupported ones.
    2. Among those, the one that accounts for more of the change comes first (explained_share).
       A supported explanation that has no share measure (instrumentation, noise, selection) is treated
       as accounting for all of it; a partially supported one for none — so it can never outrank a
       supported explanation on magnitude alone.
    3. Then by status, then by net supporting checks.
    """
    def key(h: Hypothesis):
        bucket = 0 if h.status in ("supported", "partially_supported") else (1 if h.status == "inconclusive" else 2)
        share = h.explained_share if h.explained_share is not None else (1.0 if h.status == "supported" else 0.0)
        return (bucket, -round(share, 2), STATUS_ORDER[h.status], -(h.n_support - h.n_contra))
    return sorted(hyps, key=key)
