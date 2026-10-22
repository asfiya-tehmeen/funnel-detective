import pytest
from pydantic import ValidationError

from app.analytics.queries import Filters, QueryTrace
from app.analytics.stats import sample_size_two_proportions
from app.experiments.analysis import Arm, Guardrail, ResultsRequest, SimulationRequest, analyse, simulate
from app.experiments.planner import PlanRequest, build_plan
from app.impact import ImpactRequest, estimate


def test_plan_fields_and_sample_size(cur):
    plan = build_plan(cur, QueryTrace(), PlanRequest(category="behavioral_change",
                                                     filters=Filters(market=["Dubai"], price_band=["luxury"])))
    for key in ["problem_statement", "target_segment", "hypothesis", "intervention", "control", "treatment",
                "primary_metric", "guardrail_metrics", "mechanism", "mde", "sample_size_per_arm", "duration_days",
                "implementation_cost", "risks", "decision_rule"]:
        assert plan[key], key
    assert plan["status"] == "forecast"
    n = sample_size_two_proportions(plan["baseline_rate"], plan["mde"]["absolute_pp"] / 100)
    assert plan["sample_size_per_arm"] == n
    assert plan["duration_days"] % 7 == 0 and plan["duration_days"] >= 14


def test_auto_mde_prefers_feasible_duration(cur):
    plan = build_plan(cur, QueryTrace(), PlanRequest(category="traffic_composition"))
    assert plan["duration_days"] <= 6 * 7 or "does not fit" in plan["mde"]["basis"]


def test_data_fix_plan_has_no_ab_test(cur):
    plan = build_plan(cur, QueryTrace(), PlanRequest(category="instrumentation_issue",
                                                     filters=Filters(market=["Jeddah"], device=["app"])))
    assert plan["kind"] == "data_fix" and plan["sample_size_per_arm"] is None


def test_unknown_category(cur):
    with pytest.raises(ValueError):
        build_plan(cur, QueryTrace(), PlanRequest(category="vibes"))


def _req(cx, tx, n=20000, **kw):
    return ResultsRequest(control=Arm(n=n, conversions=cx), treatment=Arm(n=n, conversions=tx), **kw)


def test_decision_ship_iterate_abandon():
    assert analyse(_req(1600, 1850))["decision"] == "ship"
    assert analyse(_req(1600, 1630))["decision"] == "iterate"
    assert analyse(_req(1800, 1500))["decision"] == "abandon"
    # CI upper bound below the MDE -> not worth shipping even optimistically.
    assert analyse(_req(1600, 1605, n=200000, mde_pp=0.5))["decision"] == "abandon"


def test_guardrail_failure_blocks_ship():
    g = Guardrail(name="qualified", control=Arm(n=20000, conversions=14000), treatment=Arm(n=20000, conversions=12500),
                  max_degradation_pp=1.0)
    r = analyse(_req(1600, 1850, guardrails=[g]))
    assert r["decision"] == "iterate" and r["guardrails"][0]["status"] == "fail"


def test_srm_detected():
    r = analyse(ResultsRequest(control=Arm(n=20000, conversions=1600), treatment=Arm(n=23000, conversions=1900)))
    assert r["srm"]["mismatch"] and r["decision"] == "investigate"


def test_results_validation():
    with pytest.raises(ValidationError):
        Arm(n=10, conversions=11)
    with pytest.raises(ValidationError):
        Arm(n=0, conversions=0)


def test_simulation_is_labelled_and_reproducible():
    a = simulate(SimulationRequest(baseline_rate=0.08, true_relative_lift=0.15, n_per_arm=30000, seed=3))
    b = simulate(SimulationRequest(baseline_rate=0.08, true_relative_lift=0.15, n_per_arm=30000, seed=3))
    assert a["test"] == b["test"]
    assert a["outcome_type"] == "simulated outcome" and "not an observed result" in a["simulation"]["note"]


def test_impact_formulas():
    r = estimate(ImpactRequest(eligible_sessions_per_month=10000, baseline_rate=0.08, uplift_pp_low=0.2,
                               uplift_pp_base=0.5, uplift_pp_high=1.0, qualified_rate=0.7, viewing_rate=0.3, months=12))
    base = r["scenarios"]["base"]
    assert base["additional_contacts_per_month"] == pytest.approx(50)
    assert base["additional_qualified_per_month"] == pytest.approx(35)
    assert base["additional_viewings_per_month"] == pytest.approx(15)
    assert base["additional_qualified_total"] == pytest.approx(420)
    assert "modelled_value_total" not in base and r["value_note"]
    assert "not realised revenue" in r["disclaimer"]


def test_impact_value_only_when_supplied():
    r = estimate(ImpactRequest(eligible_sessions_per_month=10000, baseline_rate=0.08, uplift_pp_low=0.2,
                               uplift_pp_base=0.5, uplift_pp_high=1.0, qualified_rate=0.7, viewing_rate=0.3,
                               value_per_qualified_contact=200, value_source="finance", months=1))
    assert r["scenarios"]["base"]["modelled_value_total"] == pytest.approx(35 * 200)


@pytest.mark.parametrize("bad", [
    dict(uplift_pp_low=1.0, uplift_pp_base=0.5, uplift_pp_high=2.0),   # unordered scenarios
    dict(uplift_pp_low=0.1, uplift_pp_base=0.5, uplift_pp_high=95.0),  # rate > 100%
    dict(eligible_sessions_per_month=-5),
    dict(qualified_rate=1.5),
])
def test_impact_validation(bad):
    args = dict(eligible_sessions_per_month=10000, baseline_rate=0.08, uplift_pp_low=0.2, uplift_pp_base=0.5,
                uplift_pp_high=1.0, qualified_rate=0.7, viewing_rate=0.3)
    args.update(bad)
    with pytest.raises(ValidationError):
        ImpactRequest(**args)
