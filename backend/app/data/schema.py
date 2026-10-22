"""Event schema: the single source of truth for column names, allowed values and types.

Used by the generator (to emit data), the store (to validate/quarantine rows),
the data dictionary endpoint, and the SQL guard (to know which columns exist).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

MARKETS = ["Dubai", "Riyadh", "Cairo", "Abu Dhabi", "Jeddah"]
DEVICES = ["desktop", "mobile_web", "app"]
CHANNELS = ["organic_search", "paid_search", "paid_social", "direct", "referral", "crm_email"]
PROPERTY_TYPES = ["apartment", "villa", "townhouse", "commercial"]
PRICE_BANDS = ["budget", "mid", "premium", "luxury"]
INTENTS = ["rent", "buy"]
VISITOR_TYPES = ["new", "returning"]

EVENT_TYPES = [
    "search_submitted",
    "results_viewed",
    "listing_viewed",
    "property_saved",
    "contact_started",
    "contact_submitted",
    "viewing_requested",
]
EVENT_STATUSES = ["ok", "failed"]

# Dimensions a user (or the AI planner) may slice by. Anything else is rejected.
DIMENSIONS = {
    "market": MARKETS,
    "device": DEVICES,
    "channel": CHANNELS,
    "property_type": PROPERTY_TYPES,
    "price_band": PRICE_BANDS,
    "intent": INTENTS,
    "visitor_type": VISITOR_TYPES,
}

EventType = Literal[
    "search_submitted",
    "results_viewed",
    "listing_viewed",
    "property_saved",
    "contact_started",
    "contact_submitted",
    "viewing_requested",
]


class Event(BaseModel):
    """One tracked client event. Mirrors the parquet columns exactly."""

    event_id: str
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    event_ts: datetime
    event_type: EventType
    event_status: Literal["ok", "failed"]
    market: Literal["Dubai", "Riyadh", "Cairo", "Abu Dhabi", "Jeddah"]
    device: Literal["desktop", "mobile_web", "app"]
    channel: Literal["organic_search", "paid_search", "paid_social", "direct", "referral", "crm_email"]
    visitor_type: Literal["new", "returning"]
    intent: Literal["rent", "buy"]
    property_type: Literal["apartment", "villa", "townhouse", "commercial"]
    price_band: Literal["budget", "mid", "premium", "luxury"]
    listing_id: Optional[str] = None
    client_version: str
    lead_id: Optional[str] = None
    is_qualified: Optional[bool] = None
    error_code: Optional[str] = None
    experiment_id: Optional[str] = None
    variant: Optional[Literal["control", "treatment"]] = None


COLUMN_DOCS: dict[str, str] = {
    "event_id": "Unique identifier of the tracked event (string). Duplicated client firings get new event_ids.",
    "session_id": "Identifier of the browsing session. A session belongs to one user, market and device.",
    "user_id": "Pseudonymous user identifier; repeat visitors reuse it across sessions.",
    "event_ts": "Event timestamp (UTC, naive).",
    "event_type": "Journey step: " + ", ".join(EVENT_TYPES) + ".",
    "event_status": "'ok' or 'failed'. A failed contact_submitted means the form returned an error.",
    "market": "Market (city) the session searched in: " + ", ".join(MARKETS) + ".",
    "device": "Client surface: desktop, mobile_web, or app.",
    "channel": "Acquisition channel of the session: " + ", ".join(CHANNELS) + ".",
    "visitor_type": "'new' or 'returning', as reported by the client cookie/app install.",
    "intent": "Rent or buy, from the session's search filter or landing listing.",
    "property_type": "Property type searched/landed on: " + ", ".join(PROPERTY_TYPES) + ".",
    "price_band": "Market-relative price band: budget, mid, premium, luxury.",
    "listing_id": "Listing identifier for listing-level events; null for search/results events.",
    "client_version": "Web release or app version that emitted the event.",
    "lead_id": "Lead identifier returned by the lead service. A valid contact_submitted must carry one.",
    "is_qualified": "Lead-scoring outcome for contact_submitted (true = qualified lead). Null otherwise.",
    "error_code": "Error code for failed events, else null.",
    "experiment_id": "Experiment the user was assigned to, if any.",
    "variant": "'control' or 'treatment' when experiment_id is set.",
}
