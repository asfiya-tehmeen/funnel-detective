"""AI orchestration: tool validation, schema validation, unsupported-claim and contradiction checks, fallbacks."""
import json

import pytest

from app.analytics.queries import Comparison, Filters
from app.engine.orchestrator import deterministic_plan, investigate
from app.engine.schemas import Check, Evidence, Hypothesis, NarrativeDraft
from app.engine.tools import TOOLS, ToolContext, run_tool
from app.llm.base import LLMError
from app.llm.validation import validate_narrative


# --------------------------------------------------------------------------- tool validation
def test_unknown_tool_is_recorded_not_raised(cur):
    ctx = ToolContext(cur=cur)
    assert run_tool(ctx, "drop_tables", {}) is None
    assert ctx.calls[-1].status == "error" and "unknown tool" in ctx.calls[-1].error


@pytest.mark.parametrize("args", [
    {"metric": "revenue"},                                         # not an approved metric
    {"metric": "listing_to_contact", "filters": {"market": ["Narnia"]}},
    {"metric": "listing_to_contact", "sql": "DROP TABLE sessions"},  # extra field
    {"metric": "listing_to_contact", "comparison": {"baseline": {"start": "2026-08-01", "end": "2026-08-10"},
                                                    "current": {"start": "2026-06-01", "end": "2026-06-10"}}},
])
def test_invalid_tool_args_rejected(cur, args):
    ctx = ToolContext(cur=cur)
    assert run_tool(ctx, "compare_metric", args) is None
    assert ctx.calls[-1].status == "error"
    assert ctx.evidence == []


def test_tool_produces_evidence_with_sql(cur):
    ctx = ToolContext(cur=cur)
    res = run_tool(ctx, "compare_metric", {"metric": "listing_to_contact", "filters": {"market": ["Riyadh"]}})
    assert res is not None
    ev = res.evidence[0]
    assert ev.query_ids and all(any(q.id == qid for q in ctx.trace.records) for qid in ev.query_ids)
    assert "%" in ev.statement and "p=" in ev.statement


def test_every_tool_has_a_strict_schema():
    for spec in TOOLS.values():
        schema = spec.args_model.model_json_schema()
        assert schema.get("additionalProperties") is False, spec.name


# --------------------------------------------------------------------------- narrative validation
def _fixture():
    ev = [Evidence(id="ev_1", tool="compare_metric", title="Listing → contact", evidence_level="descriptive",
                   statement="Listing → contact moved from 8.50% to 3.40%, -5.10pp (p<0.001).")]
    hyps = [
        Hypothesis(id="H1", category="technical_regression", title="Regression", predicted_pattern="x",
                   checks=[Check(description="d", result="supports", detail="Form completion -22.4pp.", evidence_ids=["ev_1"])],
                   discriminating_data=["logs"], evidence_level="associational", status="supported", status_rule="r"),
        Hypothesis(id="H2", category="random_variation", title="Noise", predicted_pattern="x",
                   checks=[Check(description="d", result="contradicts", detail="p<0.001", evidence_ids=["ev_1"])],
                   discriminating_data=["more data"], evidence_level="descriptive", status="not_supported", status_rule="r"),
    ]
    return ev, hyps


def _draft(summary, kps=None, caveats=None):
    return {"summary": summary, "key_points": kps or [{"text": "Rate fell 5.1pp.", "evidence_ids": ["ev_1"],
                                                      "hypothesis_id": "H1", "stance": "supports"}],
            "caveats": caveats or []}


def test_valid_narrative_passes():
    ev, hyps = _fixture()
    d, issues = validate_narrative(_draft("Conversion fell from 8.5% to 3.4%; form completion dropped 22.4pp."), ev, hyps)
    assert issues == [] and isinstance(d, NarrativeDraft)


def test_fabricated_number_rejected():
    ev, hyps = _fixture()
    d, issues = validate_narrative(_draft("Conversion fell 17.3pp, costing 4,200 leads."), ev, hyps)
    assert d is None and any("numbers not found" in i for i in issues)


def test_causal_language_rejected_without_experiment():
    ev, hyps = _fixture()
    d, issues = validate_narrative(_draft("The release caused the drop."), ev, hyps)
    assert d is None and any("causal language" in i for i in issues)


def test_causal_language_allowed_with_causal_evidence():
    ev, hyps = _fixture()
    ev.append(Evidence(id="ev_2", tool="experiment_readout", title="EXP", evidence_level="causal", statement="5.1pp"))
    d, issues = validate_narrative(_draft("The treatment caused a 5.1pp lift."), ev, hyps)
    assert issues == []


def test_unknown_evidence_rejected():
    ev, hyps = _fixture()
    d, issues = validate_narrative(_draft("Fell 5.1pp.", [{"text": "x", "evidence_ids": ["ev_99"], "hypothesis_id": None,
                                                            "stance": "context"}]), ev, hyps)
    assert d is None and any("unknown evidence" in i for i in issues)


def test_contradictory_claim_rejected():
    ev, hyps = _fixture()
    d, issues = validate_narrative(_draft("Fell 5.1pp.", [{"text": "It is just noise.", "evidence_ids": ["ev_1"],
                                                            "hypothesis_id": "H2", "stance": "supports"}]), ev, hyps)
    assert d is None and any("not supported" in i for i in issues)
    d, issues = validate_narrative(_draft("Fell 5.1pp.", [{"text": "Not a regression.", "evidence_ids": ["ev_1"],
                                                            "hypothesis_id": "H1", "stance": "contradicts"}]), ev, hyps)
    assert d is None and any("marks it supported" in i for i in issues)


def test_schema_violations_rejected():
    ev, hyps = _fixture()
    for bad in [{"summary": "x"}, {"summary": "x", "key_points": [], "caveats": []},
                {"summary": "x", "key_points": [{"text": "y", "evidence_ids": []}], "caveats": []},
                {**_draft("ok"), "confidence": 0.97}]:
        d, issues = validate_narrative(bad, ev, hyps)
        assert d is None and issues


# --------------------------------------------------------------------------- LLM fallbacks
class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, plan=None, narrative=None, error=False):
        self.plan, self.narrative, self.error = plan, narrative, error
        self.calls = 0

    def complete_json(self, system, user, schema, max_tokens=4000):
        self.calls += 1
        if self.error:
            raise LLMError("provider down")
        is_planner = system.startswith("You plan")
        return self.plan if is_planner and self.plan is not None else self.narrative


GOOD_PLAN = {"intent": "explain_change", "metric": "listing_to_contact",
             "filters": {"market": ["Riyadh"], "device": ["mobile_web"], "channel": [], "property_type": [],
                         "price_band": [], "intent": [], "visitor_type": []},
             "rationale": "Riyadh mobile web.", "extra_steps": [
                 {"tool": "segment_breakdown", "args_json": json.dumps({"metric": "listing_to_contact", "dimension": "channel"}),
                  "rationale": "channel view"},
                 {"tool": "run_sql", "args_json": "{\"sql\": \"DROP TABLE sessions\"}", "rationale": "evil"},
                 {"tool": "compare_metric", "args_json": "not json", "rationale": "broken"}]}


def test_provider_down_falls_back_to_deterministic(cur):
    inv = investigate("Why did mobile contact conversion decline in Riyadh?", cur, provider=FakeProvider(error=True))
    assert inv.mode == "deterministic"
    assert inv.narrative.source == "deterministic"
    assert any("deterministic router" in n for n in inv.planner_notes)
    assert inv.hypotheses[0].category == "technical_regression"


def test_llm_plan_used_and_bad_steps_contained(cur):
    hallucinating = {"summary": "Conversion collapsed by 42.7pp, caused by the release.",
                     "key_points": [{"text": "x", "evidence_ids": ["ev_1"], "hypothesis_id": None, "stance": "context"}],
                     "caveats": []}
    inv = investigate("anything", cur, provider=FakeProvider(plan=GOOD_PLAN, narrative=hallucinating))
    assert inv.mode == "llm" and inv.plan.planner == "llm"
    assert inv.finding.filters == {"market": ["Riyadh"], "device": ["mobile_web"]}
    planner_calls = [c for c in inv.tool_calls if c.requested_by == "planner"]
    assert [c.tool for c in planner_calls] == ["segment_breakdown", "run_sql"]
    assert planner_calls[0].status == "ok" and planner_calls[1].status == "error"
    assert any("invalid arguments" in n for n in inv.planner_notes)
    # Hallucinated narrative is rejected and replaced, with reasons kept for the UI.
    assert inv.narrative.source == "deterministic"
    assert inv.narrative.fallback_reason and inv.narrative.validation_issues


def test_llm_invalid_plan_schema_falls_back(cur):
    inv = investigate("Is the overall conversion decline real, or is the traffic mix changing?", cur,
                      provider=FakeProvider(plan={"intent": "delete_everything"}, narrative={}))
    assert inv.plan.planner == "deterministic" and inv.plan.intent == "mix_vs_rate"


def test_valid_llm_narrative_is_used(cur):
    base = investigate("Why did mobile contact conversion decline in Riyadh?", cur)
    ev_id = base.evidence[0].id
    o = base.finding.observed_change
    good = {"summary": f"Listing to contact fell from {o.baseline_rate * 100:.1f}% to {o.current_rate * 100:.1f}%.",
            "key_points": [{"text": "Form completion is where the drop concentrates.", "evidence_ids": [ev_id],
                            "hypothesis_id": base.hypotheses[0].id, "stance": "supports"}],
            "caveats": ["Observational data."]}
    inv = investigate("Why did mobile contact conversion decline in Riyadh?", cur,
                      provider=FakeProvider(plan=GOOD_PLAN, narrative=good))
    assert inv.narrative.source == "llm" and inv.narrative.validated


def test_deterministic_router_examples():
    cases = {
        "Why did mobile contact conversion decline in Riyadh?": ("explain_change", {"market": ["Riyadh"], "device": ["mobile_web"]}),
        "Is the overall conversion decline real, or is the traffic mix changing?": ("mix_vs_rate", {}),
        "Which funnel step has the largest recoverable opportunity?": ("opportunity", {}),
        "Which market should we investigate first?": ("prioritize_markets", {}),
        "Are users abandoning the contact flow, or is tracking broken?": ("abandon_vs_tracking", {}),
        "Why does the app convert better than the website?": ("segment_advantage", {"device": ["app"]}),
        "Did viewings drop for luxury villas in Dubai?": ("explain_change", {"market": ["Dubai"], "price_band": ["luxury"],
                                                                              "property_type": ["villa"]}),
    }
    for q, (intent, filters) in cases.items():
        plan, _ = deterministic_plan(q)
        assert plan.intent == intent, q
        assert plan.filters == filters, q


def test_deterministic_narratives_pass_their_own_validator(cur):
    for q in ["Why did mobile contact conversion decline in Riyadh?",
              "Is the overall conversion decline real, or is the traffic mix changing?",
              "Why does the app convert better than the website?"]:
        inv = investigate(q, cur)
        raw = inv.narrative.model_dump(include={"summary", "key_points", "caveats"})
        d, issues = validate_narrative(raw, inv.evidence, inv.hypotheses,
                                       extra_texts=[str(inv.finding.observed_change.model_dump())])
        assert issues == [], (q, issues)


def test_battery_always_tests_competing_explanations(cur):
    inv = investigate("Why did contact conversion change in Cairo?", cur)
    cats = {h.category for h in inv.hypotheses}
    assert cats == {"technical_regression", "traffic_composition", "behavioral_change", "instrumentation_issue",
                    "random_variation"}
    for h in inv.hypotheses:
        assert h.checks and h.discriminating_data and h.predicted_pattern
        assert h.evidence_level != "causal"  # observational data never yields causal evidence


def test_confidence_is_rule_based(cur):
    inv = investigate("Why did mobile contact conversion decline in Riyadh?", cur)
    assert inv.finding.confidence in ("high", "medium", "low")
    assert "not a probability" in inv.finding.confidence_basis


def test_comparison_window_respected(cur):
    c = Comparison(baseline={"start": "2026-06-01", "end": "2026-06-28"}, current={"start": "2026-06-29", "end": "2026-07-19"})
    inv = investigate("Why did contact conversion change in Dubai?", cur, comparison=c)
    assert inv.finding.comparison_window["current"] == "2026-06-29 to 2026-07-19"
    _ = Filters
