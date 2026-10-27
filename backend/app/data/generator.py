"""Reproducible synthetic journey-event generator.

Run:  python -m scripts.generate_data   (from backend/)

The generator simulates sessions day by day. Each session draws its attributes
(market, channel, device, visitor type, intent, property type, price band),
then walks the funnel with probabilities built from explicit multipliers
(see BASE_* and *_MULT below). Five scenarios are injected on top of that
baseline behaviour; their parameters are written to eval/ground_truth.json,
which the API never reads.

Everything is driven by a single numpy Generator seeded with SEED, so the same
code + seed produces byte-identical data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import DATA_END, DATA_START
from app.data.schema import CHANNELS, DEVICES, MARKETS, PRICE_BANDS, PROPERTY_TYPES

SEED = 20260720

CHANGE_DATE = date(2026, 7, 20)          # web release + paid-social campaign start
APP_512_DATE = date(2026, 7, 27)         # staged app 5.12.0 rollout (Jeddah only)
EXP_START, EXP_END = date(2026, 5, 25), date(2026, 6, 21)

BASE_DAILY_SESSIONS = 520
MARKET_WEIGHTS = {"Dubai": 0.36, "Riyadh": 0.22, "Cairo": 0.17, "Abu Dhabi": 0.15, "Jeddah": 0.10}
MARKET_CODE = {"Dubai": "DXB", "Riyadh": "RUH", "Cairo": "CAI", "Abu Dhabi": "AUH", "Jeddah": "JED"}

CHANNEL_WEIGHTS = {
    "organic_search": 0.34, "paid_search": 0.20, "paid_social": 0.12,
    "direct": 0.18, "referral": 0.06, "crm_email": 0.10,
}
PAID_SOCIAL_SURGE = 3.4  # multiplier on paid_social volume after CHANGE_DATE (scenario B)

DEVICE_GIVEN_CHANNEL = {
    "organic_search": [0.40, 0.50, 0.10],
    "paid_search": [0.45, 0.50, 0.05],
    "paid_social": [0.15, 0.80, 0.05],
    "direct": [0.30, 0.25, 0.45],
    "referral": [0.50, 0.45, 0.05],
    "crm_email": [0.25, 0.35, 0.40],
}
RETURNING_BY_CHANNEL = {
    "organic_search": 0.30, "paid_search": 0.25, "paid_social": 0.10,
    "direct": 0.72, "referral": 0.30, "crm_email": 0.85,
}
APP_RETURNING_BOOST = 0.28

PTYPE_WEIGHTS = [0.62, 0.18, 0.12, 0.08]
BAND_WEIGHTS = {"default": [0.30, 0.38, 0.22, 0.10], "Dubai": [0.20, 0.32, 0.24, 0.24]}

# ---- Funnel probabilities -------------------------------------------------
P_SEARCH_ENTRY = {"default": 0.88, "paid_social": 0.45, "referral": 0.55}  # else lands on a listing
P_RESULTS = 0.94
P_LISTING_FROM_RESULTS = 0.64
MEAN_EXTRA_LISTINGS = 1.3
P_SAVE = 0.13
BASE_CONTACT_START = 0.135
P_SUBMIT_GIVEN_START = {"desktop": 0.72, "mobile_web": 0.63, "app": 0.75}
P_QUALIFIED = 0.72
P_VIEWING_GIVEN_CONTACT = 0.31
P_CONTACT_FAIL_BASE = 0.015

CHANNEL_MULT = {"organic_search": 1.0, "paid_search": 1.08, "paid_social": 0.42,
                "direct": 1.18, "referral": 0.9, "crm_email": 1.12}
VISITOR_MULT = {"new": 1.0, "returning": 1.75}
DEVICE_MULT = {"desktop": 1.0, "mobile_web": 0.92, "app": 1.04}
INTENT_MULT = {"rent": 1.1, "buy": 0.86}
BAND_MULT = {"budget": 1.08, "mid": 1.0, "premium": 0.92, "luxury": 0.8}
MARKET_MULT = {"Dubai": 1.0, "Riyadh": 0.96, "Abu Dhabi": 1.04, "Cairo": 0.86, "Jeddah": 0.92}
QUALIFIED_CHANNEL = {"paid_social": 0.55}

# ---- Scenario parameters --------------------------------------------------
A_SUBMIT_MULT = 0.55       # Riyadh mobile_web contact form regression
A_FAILED_SHARE = 0.45      # share of regressed submits that emit a failed event
E_START_MULT = 0.55        # Dubai luxury: fewer contact starts (genuine behaviour)
E_SAVE_MULT = 1.4          # ...but more saves
D_GHOST_ON_LISTING = 0.06  # Jeddah app 5.12.0 fires contact_submitted on listing render
EXP_LIFT = 1.07            # EXP-014 true relative lift on contact starts

DUPLICATE_RATE = 0.015
MALFORMED_RATE = 0.002
MISSING_SEARCH_RATE = 0.02


# When False, none of the planted scenarios A-E is applied (a "null world" used to measure the
# engine's false-alarm rate). Release/version labels still change on schedule, so a new client
# version appears without any real effect. EXP-014 (a historical randomised test) is unaffected.
_SCENARIOS = True


@dataclass
class _Ctx:
    rng: np.random.Generator
    rows: list
    user_pool: dict
    counters: dict


def _choice(rng: np.random.Generator, items, p):
    return items[int(rng.choice(len(items), p=p))]


def _web_version(d: date) -> str:
    return "web-2026.07.2" if d >= CHANGE_DATE else "web-2026.06.4"


def _app_version(market: str, d: date) -> str:
    if market == "Jeddah" and d >= APP_512_DATE:
        return "app-5.12.0"
    return "app-5.11.3"


def _in_experiment(market: str, d: date) -> bool:
    return market in ("Dubai", "Abu Dhabi") and EXP_START <= d <= EXP_END


def _user_variant(user_id: str) -> str:
    # Deterministic 50/50 split on a hash of the user id (stable across sessions).
    return "treatment" if int(hashlib.md5(user_id.encode()).hexdigest()[:8], 16) % 2 else "control"


def _emit(ctx: _Ctx, base: dict, ts: datetime, event_type: str, **extra) -> None:
    ctx.counters["event"] += 1
    row = dict(base)
    row.update(
        event_id=f"e{ctx.counters['event']:08d}",
        event_ts=ts,
        event_type=event_type,
        event_status="ok",
        listing_id=None,
        lead_id=None,
        is_qualified=None,
        error_code=None,
    )
    row.update(extra)
    ctx.rows.append(row)


def _simulate_session(ctx: _Ctx, d: date) -> None:
    rng = ctx.rng
    market = _choice(rng, MARKETS, [MARKET_WEIGHTS[m] for m in MARKETS])

    weights = dict(CHANNEL_WEIGHTS)
    if d >= CHANGE_DATE and _SCENARIOS:
        ramp = min(1.0, (d - CHANGE_DATE).days / 5 + 0.2)
        weights["paid_social"] *= 1 + (PAID_SOCIAL_SURGE - 1) * ramp
    total = sum(weights.values())
    channel = _choice(rng, CHANNELS, [weights[c] / total for c in CHANNELS])
    device = _choice(rng, DEVICES, DEVICE_GIVEN_CHANNEL[channel])

    p_ret = RETURNING_BY_CHANNEL[channel] + (APP_RETURNING_BOOST if device == "app" else 0.0)
    pool = ctx.user_pool[market]
    if pool and rng.random() < min(p_ret, 0.93):
        user_id = pool[int(rng.integers(len(pool)))]
        visitor_type = "returning"
    else:
        ctx.counters["user"] += 1
        user_id = f"u{MARKET_CODE[market]}{ctx.counters['user']:06d}"
        pool.append(user_id)
        visitor_type = "new"

    intent = "buy" if rng.random() < (0.55 if market == "Cairo" else 0.45) else "rent"
    ptype = _choice(rng, PROPERTY_TYPES, PTYPE_WEIGHTS)
    band = _choice(rng, PRICE_BANDS, BAND_WEIGHTS.get(market, BAND_WEIGHTS["default"]))
    version = _app_version(market, d) if device == "app" else _web_version(d)

    exp_id = variant = None
    if _in_experiment(market, d):
        exp_id, variant = "EXP-014", _user_variant(user_id)

    ctx.counters["session"] += 1
    session_id = f"s{ctx.counters['session']:07d}"
    hour = int(rng.choice(24, p=_HOUR_P))
    ts = datetime(d.year, d.month, d.day, hour, int(rng.integers(60)), int(rng.integers(60)))

    base = dict(
        session_id=session_id, user_id=user_id, market=market, device=device, channel=channel,
        visitor_type=visitor_type, intent=intent, property_type=ptype, price_band=band,
        client_version=version, experiment_id=exp_id, variant=variant,
    )

    def step(seconds_mean: float) -> None:
        nonlocal ts
        ts = ts + timedelta(seconds=float(1 + rng.exponential(seconds_mean)))

    def listing() -> str:
        return f"L{MARKET_CODE[market]}-{ptype[:3]}-{band[:3]}-{int(rng.integers(160)):03d}"

    # --- entry ---
    p_search = P_SEARCH_ENTRY.get(channel, P_SEARCH_ENTRY["default"])
    if rng.random() < p_search:
        if rng.random() >= MISSING_SEARCH_RATE:  # tracking loss: search event missing
            _emit(ctx, base, ts, "search_submitted")
        step(4)
        if rng.random() >= P_RESULTS:
            if rng.random() < 0.4:
                _emit(ctx, base, ts, "results_viewed", event_status="failed", error_code="RESULTS_TIMEOUT")
            return
        _emit(ctx, base, ts, "results_viewed")
        if rng.random() >= P_LISTING_FROM_RESULTS:
            return
    step(20)
    n_listings = 1 + int(rng.poisson(MEAN_EXTRA_LISTINGS))
    contact_mult = (
        CHANNEL_MULT[channel] * VISITOR_MULT[visitor_type] * DEVICE_MULT[device]
        * INTENT_MULT[intent] * BAND_MULT[band] * MARKET_MULT[market]
    )
    save_mult = 1.5 if visitor_type == "returning" else 1.0

    # Scenario E: Dubai luxury buyers start fewer contacts but save more (all devices/channels).
    if market == "Dubai" and band == "luxury" and d >= CHANGE_DATE and _SCENARIOS:
        contact_mult *= E_START_MULT
        save_mult *= E_SAVE_MULT
    if variant == "treatment":
        contact_mult *= EXP_LIFT

    # Session-level contact-start probability, spread across the listings viewed.
    p_start_session = min(0.9, BASE_CONTACT_START * contact_mult)
    p_start_each = 1 - (1 - p_start_session) ** (1 / n_listings)

    p_save_each = 1 - (1 - min(0.9, P_SAVE * save_mult)) ** (1 / n_listings)

    ghost_bug = version == "app-5.12.0" and _SCENARIOS
    contacted = False
    for _ in range(n_listings):
        current_listing = listing()
        _emit(ctx, base, ts, "listing_viewed", listing_id=current_listing)
        # Scenario D: app 5.12.0 fires a contact_submitted (without lead_id) on listing render.
        if ghost_bug and rng.random() < D_GHOST_ON_LISTING:
            _emit(ctx, base, ts + timedelta(milliseconds=400), "contact_submitted", listing_id=current_listing)
        step(45)
        if rng.random() < p_save_each:
            _emit(ctx, base, ts, "property_saved", listing_id=current_listing)
            step(10)
        if not contacted and rng.random() < p_start_each:
            contacted = True
            _emit(ctx, base, ts, "contact_started", listing_id=current_listing)
            if ghost_bug:
                _emit(ctx, base, ts + timedelta(milliseconds=300), "contact_submitted", listing_id=current_listing)
            step(35)
            p_submit = P_SUBMIT_GIVEN_START[device]
            regressed = market == "Riyadh" and device == "mobile_web" and d >= CHANGE_DATE and _SCENARIOS
            if regressed:
                p_submit *= A_SUBMIT_MULT
            if rng.random() < p_submit:
                if rng.random() < P_CONTACT_FAIL_BASE:
                    _emit(ctx, base, ts, "contact_submitted", listing_id=current_listing,
                          event_status="failed", error_code="LEAD_SERVICE_5XX")
                    continue
                ctx.counters["lead"] += 1
                qualified = bool(rng.random() < QUALIFIED_CHANNEL.get(channel, P_QUALIFIED))
                _emit(ctx, base, ts, "contact_submitted", listing_id=current_listing,
                      lead_id=f"lead{ctx.counters['lead']:07d}", is_qualified=qualified)
                step(60)
                p_view = P_VIEWING_GIVEN_CONTACT * (1.2 if visitor_type == "returning" else 1.0)
                if rng.random() < p_view:
                    _emit(ctx, base, ts, "viewing_requested", listing_id=current_listing)
            elif regressed and rng.random() < A_FAILED_SHARE:
                _emit(ctx, base, ts, "contact_submitted", listing_id=current_listing,
                      event_status="failed", error_code="FORM_VALIDATION_ERROR")


_HOURS = np.array([0.6, 0.4, 0.3, 0.2, 0.2, 0.3, 0.6, 1.0, 1.4, 1.6, 1.7, 1.7,
                   1.6, 1.5, 1.5, 1.6, 1.7, 1.9, 2.1, 2.3, 2.4, 2.1, 1.6, 1.0])
_HOUR_P = _HOURS / _HOURS.sum()


def _daily_volume(rng: np.random.Generator, d: date) -> int:
    trend = 1 + 0.002 * (d - DATA_START).days
    weekday = {4: 1.12, 5: 1.08}.get(d.weekday(), 1.0)  # Fri/Sat a little busier
    paid_social_extra = 1.0
    if d >= CHANGE_DATE and _SCENARIOS:
        ramp = min(1.0, (d - CHANGE_DATE).days / 5 + 0.2)
        paid_social_extra = 1 + CHANNEL_WEIGHTS["paid_social"] * (PAID_SOCIAL_SURGE - 1) * ramp
    return int(rng.poisson(BASE_DAILY_SESSIONS * trend * weekday * paid_social_extra))


def _inject_duplicates_and_malformed(rng: np.random.Generator, df: pd.DataFrame) -> pd.DataFrame:
    n = len(df)
    dup_idx = rng.choice(n, size=int(n * DUPLICATE_RATE), replace=False)
    dups = df.iloc[dup_idx].copy()
    dups["event_ts"] = dups["event_ts"] + pd.to_timedelta(rng.uniform(0.05, 2.0, len(dups)), unit="s")
    dups["event_id"] = [f"d{i:08d}" for i in range(len(dups))]

    bad_idx = rng.choice(n, size=int(n * MALFORMED_RATE), replace=False)
    bad = df.iloc[bad_idx].copy()
    bad["event_id"] = [f"m{i:08d}" for i in range(len(bad))]
    kinds = rng.integers(0, 4, len(bad))
    bad.loc[kinds == 0, "session_id"] = None
    bad.loc[kinds == 1, "event_type"] = "listing_view"  # typo'd event name from an old client
    bad.loc[kinds == 2, "market"] = None
    bad.loc[kinds == 3, "event_ts"] = pd.Timestamp("2031-01-01 00:00:00")
    out = pd.concat([df, dups, bad], ignore_index=True)
    return out.sort_values(["event_ts", "event_id"], kind="mergesort").reset_index(drop=True)


def generate(seed: int = SEED, end: date = DATA_END, scenarios: bool = True) -> pd.DataFrame:
    """Simulate the dataset. `scenarios=False` builds a null world with no planted effects (A-E off)."""
    global _SCENARIOS
    previous, _SCENARIOS = _SCENARIOS, scenarios
    try:
        return _generate(seed, end)
    finally:
        _SCENARIOS = previous


def _generate(seed: int, end: date) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ctx = _Ctx(rng=rng, rows=[], user_pool={m: [] for m in MARKETS},
               counters={"event": 0, "user": 0, "session": 0, "lead": 0})
    d = DATA_START
    while d <= end:
        for _ in range(_daily_volume(rng, d)):
            _simulate_session(ctx, d)
        d += timedelta(days=1)
    df = pd.DataFrame(ctx.rows)
    df = _inject_duplicates_and_malformed(rng, df)
    df["is_qualified"] = df["is_qualified"].astype("boolean")
    return df


def ground_truth() -> dict:
    """Scenario definitions + detection criteria used only by eval/evaluate.py."""
    return {
        "seed": SEED,
        "data_window": [DATA_START.isoformat(), DATA_END.isoformat()],
        "change_date": CHANGE_DATE.isoformat(),
        "scenarios": {
            "A_mobile_contact_regression": {
                "description": "Contact-form submission regression on mobile web in Riyadh from the web-2026.07.2 release.",
                "segment": {"market": "Riyadh", "device": "mobile_web"},
                "start_date": CHANGE_DATE.isoformat(),
                "mechanism": "contact_started -> contact_submitted probability multiplied by "
                             f"{A_SUBMIT_MULT}; {A_FAILED_SHARE:.0%} of lost submits emit a failed event.",
                "expected": {"hypothesis": "technical_regression", "step": "contact_start_to_submit"},
            },
            "B_channel_mix_shift": {
                "description": "Paid-social volume surges after the change date. Paid social converts poorly, so "
                               "aggregate listing-to-contact falls although within-channel rates are stable.",
                "start_date": CHANGE_DATE.isoformat(),
                "mechanism": f"paid_social traffic weight multiplied by {PAID_SOCIAL_SURGE} (5-day ramp).",
                "expected": {"hypothesis": "traffic_composition", "dimension": "channel"},
            },
            "C_app_selection_effect": {
                "description": "App sessions look far better at listing-to-contact, mostly because app users are "
                               "overwhelmingly returning visitors (who convert ~1.75x better).",
                "segment": {"device": "app"},
                "mechanism": f"device multiplier for app is only {DEVICE_MULT['app']} vs desktop 1.0; "
                             f"returning multiplier {VISITOR_MULT['returning']}.",
                "expected": {"hypothesis": "selection_effect", "confounder": "visitor_type"},
            },
            "D_jeddah_app_tracking": {
                "description": "Staged app-5.12.0 rollout in Jeddah double-fires contact_submitted (without a lead_id) "
                               "on contact start and on some listing renders. Raw contact counts jump; real leads do not.",
                "segment": {"market": "Jeddah", "device": "app"},
                "start_date": APP_512_DATE.isoformat(),
                "mechanism": "ghost contact_submitted events with null lead_id; some without a preceding contact_started.",
                "expected": {"hypothesis": "instrumentation_issue"},
            },
            "E_dubai_luxury_behaviour": {
                "description": "Dubai luxury-band users start ~38% fewer contacts while saving more, across all devices "
                               "and channels — a genuine behavioural change, not a regression.",
                "segment": {"market": "Dubai", "price_band": "luxury"},
                "start_date": CHANGE_DATE.isoformat(),
                "mechanism": f"contact-start multiplier {E_START_MULT}, save multiplier {E_SAVE_MULT}.",
                "expected": {"hypothesis": "behavioral_change", "step": "listing_to_contact_start"},
            },
            "EXP_014": {
                "description": "Historical randomized experiment: sticky contact CTA in Dubai and Abu Dhabi.",
                "window": [EXP_START.isoformat(), EXP_END.isoformat()],
                "true_relative_lift_contact_start": round(EXP_LIFT - 1, 4),
            },
        },
    }


def write_dataset(events_path: Path, truth_path: Path, seed: int = SEED) -> pd.DataFrame:
    df = generate(seed)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(events_path, index=False)
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(json.dumps(ground_truth(), indent=2), encoding="utf-8")
    return df
