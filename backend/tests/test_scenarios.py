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
