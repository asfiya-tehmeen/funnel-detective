"""Simpson's paradox, traffic-mix decomposition and standardisation."""
import pandas as pd
import pytest

from app.analytics import metrics as M
from app.analytics.queries import Comparison, Filters, QueryTrace, Window


def _wide(rows):
    return pd.DataFrame(rows, columns=["channel", "baseline_n", "baseline_x", "current_n", "current_x"])


def test_kitagawa_sums_exactly():
    w = _wide([("a", 1000, 100, 500, 55), ("b", 1000, 30, 3000, 90)])
    k = M.kitagawa(w, ["channel"])
    assert k["mix_effect_pp"] + k["rate_effect_pp"] == pytest.approx(k["total_change_pp"])


def test_simpsons_paradox_every_segment_up_aggregate_down():
    # Both segments improve, but traffic shifts to the low-converting segment.
    w = _wide([("high_intent", 1000, 200, 200, 42), ("low_intent", 200, 4, 1000, 25)])
    k = M.kitagawa(w, ["channel"])
    assert k["total_change_pp"] < 0              # aggregate falls
    assert k["rate_effect_pp"] > 0               # within-segment improves
    assert k["mix_effect_pp"] < k["total_change_pp"]  # mix more than explains the fall
    adj = M.stratified_change(w)
    assert adj["adj_change_pp"] > 0


def test_pure_mix_shift_has_no_rate_effect():
    w = _wide([("a", 1000, 100, 400, 40), ("b", 1000, 20, 1600, 32)])
    k = M.kitagawa(w, ["channel"])
    assert k["rate_effect_pp"] == pytest.approx(0, abs=1e-9)
    assert k["mix_share_of_change"] == pytest.approx(1.0)
    adj = M.stratified_change(w)
    assert adj["adj_change_pp"] == pytest.approx(0, abs=1e-9)
    assert adj["adj_p_value"] == pytest.approx(1.0)


def test_traffic_mix_scenario_in_data(cur):
    """Scenario B: channel mix explains most of the aggregate decline; within-channel change ~0."""
    tr = QueryTrace()
    d = M.decompose(cur, tr, "listing_to_contact", Filters(), Comparison(), "channel")
    assert d["total_change_pp"] < -1
    assert d["mix_share_of_change"] > 0.6
    paid = next(s for s in d["segments"] if s["segment"]["channel"] == "paid_social")
    assert paid["current_share"] > 2 * paid["baseline_share"]
    wide, _ = M.metric_comparison(cur, tr, "listing_to_contact", Filters(), Comparison(), ["channel"])
    assert M.stratified_change(wide)["adj_p_value"] > 0.05


def test_selection_effect_scenario_in_data(cur):
    """Scenario C: app vs desktop gap shrinks substantially once visitor type and channel are held fixed."""
    g = M.standardized_gap(cur, QueryTrace(), "listing_to_contact", Filters(), Window(start="2026-05-25", end="2026-07-19"),
                           "device", "app", "desktop", ["visitor_type", "channel"])
    assert g["raw_gap_pp"] > 3
    assert g["share_of_gap_explained_by_mix"] > 0.5
    app_ret = g["groups"]["app"]["strata"]
    assert sum(v["share"] for k, v in app_ret.items() if k.startswith("returning")) > 0.7


def test_standardization_rejects_same_dimension(cur):
    with pytest.raises(ValueError):
        M.standardized_gap(cur, QueryTrace(), "listing_to_contact", Filters(), Window(start="2026-05-25", end="2026-07-19"),
                           "device", "app", None, ["device"])


def test_segment_scan_controls_false_discoveries(cur):
    res = M.segment_scan(cur, QueryTrace(), "listing_to_contact", Filters(), Comparison())
    assert res["n_tests"] > 100
    # Composition-driven declines must not flood the scan once channel is held fixed.
    assert res["n_flagged"] <= 5
    assert "Benjamini-Hochberg" in res["method"]
