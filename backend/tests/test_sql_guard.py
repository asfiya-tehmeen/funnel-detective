import pytest

from app.analytics import queries as Q
from app.analytics.queries import Comparison, Filters, Window
from app.sqlguard import SQLRejected, check_sql, run_user_sql


@pytest.mark.parametrize("sql", [
    "DROP TABLE sessions",
    "DELETE FROM sessions",
    "UPDATE sessions SET market = 'x'",
    "SELECT 1; SELECT 2",
    "SELECT * FROM read_csv('C:/Windows/win.ini')",
    "SELECT * FROM read_parquet('data/events.parquet')",
    "SELECT * FROM 'C:/secrets.csv'",
    "COPY sessions TO 'out.csv'",
    "ATTACH 'x.db' AS x",
    "PRAGMA database_list",
    "SET enable_external_access = true",
    "INSTALL httpfs",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM raw_events",
    "SELECT * FROM information_schema.tables",
    "SELECT getenv('ANTHROPIC_API_KEY')",
    "SELECT * FROM users",
    "",
    "   ",
    "SELECT " + "1," * 3000 + "1",
])
def test_rejected(cur, sql):
    with pytest.raises(SQLRejected):
        run_user_sql(cur, sql)


def test_allowed_select_and_cte(cur):
    r = run_user_sql(cur, "WITH x AS (SELECT market FROM sessions) SELECT market, COUNT(*) AS n FROM x GROUP BY 1")
    assert r["row_count"] == 5
    assert {c["name"] for c in r["columns"]} == {"market", "n"}


def test_comments_and_trailing_semicolon_ok(cur):
    r = run_user_sql(cur, "-- count sessions\nSELECT COUNT(*) AS n FROM sessions; ")
    assert r["rows"][0]["n"] > 0


def test_row_limit_enforced(cur):
    r = run_user_sql(cur, "SELECT session_id FROM sessions", row_limit=10)
    assert r["row_count"] == 10 and r["truncated"]


def test_timeout_cancels_long_query(cur):
    with pytest.raises(SQLRejected, match="time limit"):
        run_user_sql(cur, "SELECT COUNT(*) FROM events a, events b, events c WHERE a.event_id < b.event_id",
                     timeout_s=0.5)


def test_check_sql_requires_select(cur):
    with pytest.raises(SQLRejected, match="SELECT"):
        check_sql(cur, "EXPLAIN SELECT 1 FROM sessions")


def test_approved_templates_bind_values_as_parameters():
    sql, params = Q.metric_comparison_sql("listing_to_contact", Filters(market=["Riyadh"]), Comparison(), ["device"])
    assert "Riyadh" not in sql and "Riyadh" in params
    assert sql.count("?") == len(params)


def test_filters_reject_unknown_values_and_dimensions():
    with pytest.raises(ValueError):
        Filters(market=["Riyadh'; DROP TABLE sessions; --"])
    with pytest.raises(ValueError):
        Filters(city=["Dubai"])
    with pytest.raises(ValueError):
        Q.metric_comparison_sql("listing_to_contact", Filters(), Comparison(), ["market; DROP"])


def test_window_validation():
    with pytest.raises(ValueError):
        Window(start="2026-08-01", end="2026-07-01")
    with pytest.raises(ValueError):
        Window(start="2025-01-01", end="2025-02-01")
    with pytest.raises(ValueError):
        Comparison(baseline=Window(start="2026-07-01", end="2026-07-30"),
                   current=Window(start="2026-07-15", end="2026-08-01"))
