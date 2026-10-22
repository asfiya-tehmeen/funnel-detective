"""Funnel definitions, denominators, duplicate and malformed event handling."""
import pandas as pd

from app.analytics import metrics as M
from app.analytics.queries import Comparison, Filters, QueryTrace, Window
from app.data.generator import generate


def _sessions(store):
    return store.cursor().execute("SELECT * FROM sessions ORDER BY session_id").df().set_index("session_id")


def test_funnel_flags_and_validated_contacts(tiny_store):
    store = tiny_store([
        # s1: full journey with a validated, qualified lead and a viewing
        dict(session_id="s1", event_type="search_submitted"),
        dict(session_id="s1", event_type="results_viewed"),
        dict(session_id="s1", event_type="listing_viewed", listing_id="L1"),
        dict(session_id="s1", event_type="contact_started", listing_id="L1"),
        dict(session_id="s1", event_type="contact_submitted", listing_id="L1", lead_id="lead1", is_qualified=True),
        dict(session_id="s1", event_type="viewing_requested", listing_id="L1"),
        # s2: failed submit -> not a contact
        dict(session_id="s2", event_type="listing_viewed", listing_id="L2"),
        dict(session_id="s2", event_type="contact_started", listing_id="L2"),
        dict(session_id="s2", event_type="contact_submitted", listing_id="L2", event_status="failed",
             error_code="FORM_VALIDATION_ERROR"),
        # s3: ghost submit without lead_id and without contact_started -> not a contact, flagged
        dict(session_id="s3", event_type="listing_viewed", listing_id="L3"),
        dict(session_id="s3", event_type="contact_submitted", listing_id="L3"),
        # s4: search only
        dict(session_id="s4", event_type="search_submitted"),
    ])
    s = _sessions(store)
    assert s.loc["s1", ["has_search", "has_listing", "has_contact_start", "has_contact", "has_qualified",
                        "has_viewing"]].all()
    assert s.loc["s1", "n_qualified_leads"] == 1
    assert not s.loc["s2", "has_contact"] and s.loc["s2", "has_contact_raw"] and s.loc["s2", "n_failed_submits"] == 1
    assert not s.loc["s3", "has_contact"] and s.loc["s3", "has_contact_raw"]
    assert s.loc["s3", "n_submits_without_lead"] == 1 and s.loc["s3", "n_submits_before_start"] == 1
    assert s.loc["s4", "has_search"] and not s.loc["s4", "has_listing"]

    cur, tr = store.cursor(), QueryTrace()
    w = Window(start="2026-06-01", end="2026-06-01")
    f = M.funnel(cur, tr, Filters(), w)
    steps = {x["key"]: x["sessions"] for x in f["steps"]}
    assert steps == {"search_sessions": 2, "listing_sessions": 3, "contact_start_sessions": 2,
                     "contact_sessions": 1, "viewing_sessions": 1}
    assert f["qualified_contacts"] == 1
    # Denominators: listing->contact = 1/3, raw definition = 3/3, start->submit = 1/2.
    df = cur.execute("SELECT COUNT(*) FILTER (WHERE has_listing AND has_contact) * 1.0 / COUNT(*) FILTER (WHERE has_listing), "
                     "COUNT(*) FILTER (WHERE has_listing AND has_contact_raw) * 1.0 / COUNT(*) FILTER (WHERE has_listing), "
                     "COUNT(*) FILTER (WHERE has_contact_start AND has_contact) * 1.0 / COUNT(*) FILTER (WHERE has_contact_start) "
                     "FROM sessions").fetchone()
    assert df == (1 / 3, 1.0, 0.5)


def test_duplicates_removed_but_flagged(tiny_store):
    store = tiny_store([
        dict(session_id="s1", event_type="listing_viewed", listing_id="L1", t=0),
        dict(session_id="s1", event_type="listing_viewed", listing_id="L1", t=1),   # double fire within 5s
        dict(session_id="s1", event_type="listing_viewed", listing_id="L1", t=60),  # genuine re-view
        dict(session_id="s1", event_type="contact_submitted", listing_id="L1", lead_id="a", t=70),
        dict(session_id="s1", event_type="contact_submitted", listing_id="L1", lead_id="a", t=71),
    ])
    cur = store.cursor()
    assert cur.execute("SELECT COUNT(*) FROM events_all WHERE is_duplicate").fetchone()[0] == 2
    assert cur.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert cur.execute("SELECT n_listing_views FROM sessions").fetchone()[0] == 2


def test_malformed_rows_quarantined(tiny_store):
    store = tiny_store([
        dict(session_id="s1", event_type="listing_viewed", listing_id="L1"),
        dict(session_id=None, event_type="listing_viewed"),
        dict(session_id="s2", event_type="listing_view"),            # unknown event name
        dict(session_id="s3", event_type="listing_viewed", market=None),
        dict(session_id="s4", event_type="listing_viewed", t=10 ** 9),  # far future timestamp
    ])
    cur = store.cursor()
    reasons = dict(cur.execute("SELECT quarantine_reason, COUNT(*) FROM quarantine GROUP BY 1").fetchall())
    assert reasons == {"missing session_id": 1, "unknown event_type": 1, "invalid market": 1,
                       "timestamp outside data window": 1}
    assert cur.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_store_is_locked_down(tiny_store):
    store = tiny_store([dict(session_id="s1", event_type="listing_viewed", listing_id="L1")])
    cur = store.cursor()
    import duckdb
    import pytest
    with pytest.raises(duckdb.Error):
        cur.execute("SET enable_external_access = true")
    with pytest.raises(duckdb.Error):
        cur.execute("SELECT * FROM read_csv('anything.csv')")


def test_generator_is_deterministic():
    a = generate(seed=123, end=pd.Timestamp("2026-05-27").date())
    b = generate(seed=123, end=pd.Timestamp("2026-05-27").date())
    c = generate(seed=124, end=pd.Timestamp("2026-05-27").date())
    pd.testing.assert_frame_equal(a, b)
    assert not a.equals(c)
    assert set(a["event_type"].dropna()) >= {"search_submitted", "listing_viewed", "contact_started"}


def test_real_dataset_shape(store):
    s = store.summary()
    assert 100_000 <= s["raw_events"] <= 200_000
    assert s["quarantined_events"] > 0 and s["duplicate_events"] > 0
    assert s["users"] < s["sessions"]  # repeat visitors exist


def test_overall_change_uses_sessions_denominator(cur):
    res = M.overall_change(cur, QueryTrace(), "listing_to_contact", Filters(), Comparison())
    t = res["test"]
    n = cur.execute("SELECT COUNT(*) FROM sessions WHERE has_listing AND session_date BETWEEN DATE '2026-07-20' "
                    "AND DATE '2026-08-16'").fetchone()[0]
    assert t["current_n"] == n
