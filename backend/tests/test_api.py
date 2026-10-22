import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(store):
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health_and_meta(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["llm_mode"] == "deterministic"
    m = client.get("/api/meta").json()
    assert "synthetic" in m["disclaimer"].lower() and "not an official Property Finder product" in m["disclaimer"]
    assert set(m["dimensions"]) >= {"market", "device", "channel"}


def test_overview_has_definitions_and_sql(client):
    r = client.post("/api/overview", json={"filters": {"market": ["Riyadh"]}})
    assert r.status_code == 200
    d = r.json()
    k = d["kpis"][0]
    assert k["numerator"] and k["denominator"] and k["test"]["current_n"] > 0
    assert d["queries"] and all("?" in q["sql"] or q["params"] == [] for q in d["queries"])
    assert d["funnel"]["current"]["steps"][0]["sessions"] > 0


@pytest.mark.parametrize("body", [
    {"filters": {"market": ["Narnia"]}},
    {"filters": {"city": ["Dubai"]}},
    {"comparison": {"baseline": {"start": "2026-08-01", "end": "2026-08-10"}, "current": {"start": "2026-07-01", "end": "2026-07-10"}}},
    {"comparison": {"baseline": {"start": "2020-01-01", "end": "2020-02-01"}, "current": {"start": "2026-07-01", "end": "2026-07-10"}}},
])
def test_overview_invalid_inputs(client, body):
    assert client.post("/api/overview", json=body).status_code == 422


def test_investigation_end_to_end_and_audit(client):
    r = client.post("/api/investigations", json={"question": "Why did mobile contact conversion decline in Riyadh?"})
    assert r.status_code == 200
    inv = r.json()
    assert inv["finding"]["observed_change"]["abs_change_pp"] < 0
    assert inv["hypotheses"][0]["category"] == "technical_regression"
    assert inv["recommended_experiment"]["sample_size_per_arm"] > 0
    assert inv["queries"] and inv["tool_calls"] and inv["evidence"]
    listed = client.get("/api/investigations").json()
    assert any(i["id"] == inv["id"] for i in listed)
    stored = client.get(f"/api/investigations/{inv['id']}").json()
    assert stored["question"] == inv["question"]
    assert client.get("/api/investigations/inv_missing").status_code == 404


def test_investigation_with_focus(client):
    r = client.post("/api/investigations", json={"question": "Jeddah app", "focus": {
        "metric": "listing_to_contact_raw", "filters": {"market": ["Jeddah"], "device": ["app"]}}})
    assert r.status_code == 200
    assert r.json()["hypotheses"][0]["category"] == "instrumentation_issue"


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "x" * 600}, {"question": "ok?", "use_llm": "maybe"},
                                  {"question": "why", "focus": {"metric": "revenue"}}])
def test_investigation_invalid(client, body):
    assert client.post("/api/investigations", json=body).status_code == 422


def test_sql_endpoints(client):
    ok = client.post("/api/sql/run", json={"sql": "SELECT COUNT(*) AS n FROM sessions"})
    assert ok.status_code == 200 and ok.json()["rows"][0]["n"] > 0
    bad = client.post("/api/sql/run", json={"sql": "DROP TABLE sessions"})
    assert bad.status_code == 400 and "disallowed" in bad.json()["detail"]
    appr = client.post("/api/sql/approved", json={"name": "metric_comparison", "group_by": ["market"]})
    assert appr.status_code == 200 and appr.json()["row_count"] == 10
    assert client.post("/api/sql/approved", json={"name": "arbitrary"}).status_code == 422
    audit = client.get("/api/audit").json()
    assert any(s["status"] == "rejected" for s in audit["sql_runs"])


def test_breakdown_simpson_trend_detect(client):
    assert client.post("/api/breakdown", json={"dimension": "device"}).status_code == 200
    s = client.post("/api/simpson", json={}).json()
    assert s["decomposition"]["mix_share_of_change"] > 0.5
    t = client.post("/api/trend", json={"grain": "day"}).json()
    assert len(t["points"]) == 84
    d = client.post("/api/detect", json={"metrics": ["contact_start_to_submit"]}).json()
    flagged = d["scans"]["contact_start_to_submit"]["flagged"]
    assert any(r["segment"] == {"market": "Riyadh", "device": "mobile_web"} for r in flagged)


def test_experiment_and_impact_endpoints(client):
    p = client.post("/api/experiments/plan", json={"category": "technical_regression",
                                                   "filters": {"market": ["Riyadh"], "device": ["mobile_web"]}})
    assert p.status_code == 200 and p.json()["status"] == "forecast"
    a = client.post("/api/experiments/analyze", json={"control": {"n": 1000, "conversions": 100},
                                                       "treatment": {"n": 1000, "conversions": 140}})
    assert a.status_code == 200 and a.json()["outcome_type"] == "observed outcome"
    assert client.post("/api/experiments/analyze", json={"control": {"n": 10, "conversions": 20},
                                                          "treatment": {"n": 10, "conversions": 1}}).status_code == 422
    h = client.get("/api/experiments/historical").json()
    assert h["readouts"]["listing_to_contact_start"]["test"]["current_n"] > 0
    d = client.post("/api/impact/defaults", json={}).json()
    assert d["eligible_sessions_per_month"] > 0
    i = client.post("/api/impact", json={"eligible_sessions_per_month": 1000, "baseline_rate": 0.1, "uplift_pp_low": 0.1,
                                         "uplift_pp_base": 0.2, "uplift_pp_high": 0.3, "qualified_rate": 0.7,
                                         "viewing_rate": 0.3})
    assert i.status_code == 200
    ss = client.post("/api/stats/sample-size", json={"baseline_rate": 0.1, "mde_pp": 2}).json()
    assert 3800 <= ss["n_per_arm"] <= 3900
    assert client.post("/api/stats/sample-size", json={"baseline_rate": 1.2, "mde_pp": 2}).status_code == 422


def test_ground_truth_never_exposed(client):
    for path in ["/api/meta", "/api/dictionary", "/api/sql/catalog", "/api/tools"]:
        body = client.get(path).text
        assert "ground_truth" not in body and "A_mobile_contact_regression" not in body
