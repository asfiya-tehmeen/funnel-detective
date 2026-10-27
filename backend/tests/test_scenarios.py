"""The engine must recover the planted scenarios from generic questions (no scenario-specific code paths).

This is the pytest version of eval/evaluate.py; the evaluation script reports in more detail.
"""
from app.analytics.queries import Filters
from app.engine.orchestrator import investigate


def lead(inv):
    return inv.hypotheses[0]


def test_A_regression(cur):
    inv = investigate("Why did mobile contact conversion decline in Riyadh?", cur)
    assert lead(inv).category == "technical_regression" and lead(inv).status == "supported"


def test_B_mix(cur):
    inv = investigate("Is the overall conversion decline real, or is the traffic mix changing?", cur)
    assert lead(inv).category == "traffic_composition" and lead(inv).status == "supported"


def test_C_selection(cur):
    inv = investigate("Why does the app convert better than the website?", cur)
    assert lead(inv).category == "selection_effect" and lead(inv).status == "supported"


def test_D_tracking(cur):
    inv = investigate("Are users abandoning the contact flow, or is tracking broken?", cur)
    scopes = [(inv.finding.filters, lead(inv).category)] + [(r["filters"], r["leading_category"])
                                                             for r in inv.extras["related_findings"]]
    assert ({"market": ["Jeddah"], "device": ["app"]}, "instrumentation_issue") in scopes


def test_E_behaviour(cur):
    inv = investigate("Which segments show a genuine change in behaviour?", cur)
    assert inv.finding.filters == {"market": ["Dubai"], "price_band": ["luxury"]}
    assert lead(inv).category == "behavioral_change"


def test_control_segment_not_called_a_regression(cur):
    inv = investigate("Why did contact conversion change in Abu Dhabi on desktop?", cur)
    regression = next(h for h in inv.hypotheses if h.category == "technical_regression")
    assert regression.status == "not_supported"
    _ = Filters


def test_strong_change_is_never_led_by_random_variation(cur):
    """Regression test: a variable-shadowing bug once let `random_variation` take explained_share=1.0 on a
    change with p<0.001, outranking a supported technical regression."""
    inv = investigate("Why did mobile contact conversion decline in Riyadh?", cur)
    assert inv.finding.observed_change.p_value < 0.001
    noise = next(h for h in inv.hypotheses if h.category == "random_variation")
    assert noise.explained_share is None            # only credited with the change when it is not credible
    assert inv.hypotheses[0].category == "technical_regression"


def test_uncorrected_significant_change_among_many_comparisons_is_not_explained(cur):
    """A p-value that does not survive correction for the segments examined must block 'supported'
    explanations (it contradicts H1-H3) and let noise explain the change."""
    from app.analytics.queries import Comparison, Filters
    from app.engine.hypotheses import run_battery
    from app.engine.tools import ToolContext
    f = Filters(market=["Riyadh"], device=["mobile_web"])
    hyps, _ = run_battery(ToolContext(cur=cur), "listing_to_contact", f, Comparison(), n_comparisons=10 ** 6)
    by = {h.category: h for h in hyps}
    assert all(by[c].status != "supported" for c in ("technical_regression", "traffic_composition", "behavioral_change"))
    assert any("credible after correcting" in c.description for c in by["technical_regression"].checks)
    assert by["random_variation"].explained_share == 1.0


def test_behavioural_change_needs_its_decisive_check_to_be_supported():
    """The non-specific checks ('no technical signal', 'broad-based') hold for almost any decline, so two of them
    must not be enough for 'supported'. The decisive check must support it too."""
    from app.engine.hypotheses import _hyp
    from app.engine.schemas import Check
    easy = [Check(description="broad-based", result="supports", detail="", evidence_ids=[]),
            Check(description="no tech signal", result="supports", detail="", evidence_ids=[])]
    capped = _hyp("H3", "behavioral_change", "t", "p", easy, ["x"], "associational", decisive_ok=False,
                  decisive_desc="The contact-start change")
    assert capped.status == "partially_supported"
    assert any(c.description == "Decisive check" and "capped" in c.detail for c in capped.checks)
    assert _hyp("H3", "behavioral_change", "t", "p", easy, ["x"], "associational", decisive_ok=True).status == "supported"
    assert _hyp("H3", "behavioral_change", "t", "p", easy, ["x"], "associational").status == "supported"  # rule not applied
    # Never lowers a status that was already below 'supported'.
    one = [Check(description="a", result="supports", detail="", evidence_ids=[])]
    assert _hyp("H3", "behavioral_change", "t", "p", one, ["x"], "associational", decisive_ok=False).status == "partially_supported"
