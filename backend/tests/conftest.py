from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

# Keep tests deterministic and offline: never call a real LLM, write the audit DB to a temp dir.
os.environ["FD_LLM_PROVIDER"] = "none"


@pytest.fixture(scope="session", autouse=True)
def _isolated_var_dir(tmp_path_factory):
    os.environ["FD_VAR_DIR"] = str(tmp_path_factory.mktemp("var"))
    import app.audit as audit
    import app.config as config
    config.VAR_DIR = Path(os.environ["FD_VAR_DIR"])
    config.AUDIT_DB_PATH = config.VAR_DIR / "audit.sqlite3"
    audit.AUDIT_DB_PATH = config.AUDIT_DB_PATH
    audit._audit = audit.AuditLog(config.AUDIT_DB_PATH)
    yield


@pytest.fixture(scope="session")
def store():
    """The real generated dataset (generated on the fly if missing)."""
    from app.config import EVENTS_PATH
    from app.data.store import DataStore
    if not EVENTS_PATH.exists():
        from app.data.generator import write_dataset
        from scripts.generate_data import TRUTH_PATH
        write_dataset(EVENTS_PATH, TRUTH_PATH)
    return DataStore()


@pytest.fixture
def cur(store):
    return store.cursor()


BASE = dict(user_id="u1", market="Dubai", device="desktop", channel="organic_search", visitor_type="new",
            intent="rent", property_type="apartment", price_band="mid", client_version="web-1",
            experiment_id=None, variant=None, event_status="ok", listing_id=None, lead_id=None,
            is_qualified=None, error_code=None)


def make_events(rows: list[dict]) -> pd.DataFrame:
    out = []
    t0 = datetime(2026, 6, 1, 10, 0, 0)
    for i, r in enumerate(rows):
        e = dict(BASE)
        e.update(event_id=f"t{i:05d}", event_ts=t0 + timedelta(seconds=r.pop("t", i * 10)))
        e.update(r)
        out.append(e)
    df = pd.DataFrame(out)
    df["is_qualified"] = df["is_qualified"].astype("boolean")
    return df


@pytest.fixture
def tiny_store(tmp_path):
    """Build a DataStore from a hand-written event list."""
    from app.data.store import DataStore

    def build(rows: list[dict]):
        df = make_events(rows)
        p = tmp_path / "events.parquet"
        df.to_parquet(p, index=False)
        return DataStore(p, lock_down=True)

    return build
