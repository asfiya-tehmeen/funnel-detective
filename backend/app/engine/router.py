"""Deterministic question → plan translation (used without an LLM, and as the LLM fallback).

It recognises segment mentions (markets, devices, channels, ...), the metric, and the question
type, and maps them onto one of the constrained investigation intents.
"""
from __future__ import annotations

import re

from app.engine.schemas import Intent

SEGMENT_SYNONYMS: dict[str, dict[str, list[str]]] = {
    "market": {"Dubai": ["dubai", "dxb"], "Riyadh": ["riyadh", "ruh"], "Cairo": ["cairo"],
               "Abu Dhabi": ["abu dhabi", "abudhabi", "auh"], "Jeddah": ["jeddah", "jedda", "jed"]},
    "device": {"mobile_web": ["mobile web", "mobile-web", "mweb", "mobile site", "mobile"],
               "app": ["app", "apps", "native app", "ios", "android"], "desktop": ["desktop", "web desktop"]},
    "channel": {"paid_social": ["paid social", "social", "facebook", "instagram", "tiktok", "meta ads"],
                "paid_search": ["paid search", "sem", "google ads", "ppc"],
                "organic_search": ["organic", "seo", "organic search"], "direct": ["direct"],
                "crm_email": ["email", "crm", "newsletter"], "referral": ["referral", "referrals"]},
    "price_band": {"luxury": ["luxury", "high-end", "high end"], "premium": ["premium"],
                   "budget": ["budget", "cheap", "affordable"], "mid": ["mid-range", "mid range", "mid-market"]},
    "property_type": {"villa": ["villa", "villas"], "apartment": ["apartment", "apartments", "flat", "flats"],
                      "townhouse": ["townhouse", "townhouses"], "commercial": ["commercial", "office", "offices"]},
    "intent": {"rent": ["rent", "rental", "renting", "renters", "tenants"],
               "buy": ["buy", "buying", "buyers", "purchase", "sale"]},
    "visitor_type": {"new": ["new users", "new visitors", "first-time"], "returning": ["returning", "repeat"]},
}

METRIC_PATTERNS: list[tuple[str, list[str]]] = [
    ("contact_to_viewing", ["viewing", "viewings", "book a viewing"]),
    ("search_to_listing", ["search to listing", "search-to-listing", "click-through", "results to listing"]),
    ("save_rate", ["save rate", "saves", "saving", "saved"]),
    ("qualified_contact_rate", ["qualified"]),
    ("contact_start_to_submit", ["form completion", "contact form", "submit rate", "abandon", "start to submit",
                                 "start → submit"]),
    ("listing_to_contact_start", ["contact start", "contact-start", "start contact", "starting contact"]),
    ("listing_to_contact", ["contact", "lead", "conversion", "convert"]),
]

INTENT_PATTERNS: list[tuple[Intent, list[str]]] = [
    ("abandon_vs_tracking", ["tracking", "broken", "instrumentation", "data quality", "abandoning", "abandon",
                             "is the data", "bug in the data", "spike", "duplicate"]),
    ("mix_vs_rate", ["traffic mix", "mix", "composition", "simpson", "is the overall", "real, or", "real or",
                     "traffic is changing", "channel mix"]),
    ("opportunity", ["recoverable", "opportunity", "largest drop", "biggest drop", "which funnel step",
                     "which step"]),
    ("prioritize_markets", ["which market", "investigate first", "prioriti", "where should we", "which city"]),
    ("segment_advantage", ["better than", "converts better", "convert better", "outperform", "higher conversion",
                           "advantage", "best performing", "best-performing", "why does the app", "push users"]),
    ("scan_changes", ["which segments", "what changed", "genuine change", "anything unusual", "anomal",
                      "real change in behavio", "behaviour change", "behavior change"]),
]


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None


def extract_segments(question: str) -> dict[str, list[str]]:
    text = question.lower()
    found: dict[str, list[str]] = {}
    for dim, values in SEGMENT_SYNONYMS.items():
        for value, phrases in values.items():
            if any(_contains(text, p) for p in phrases):
                found.setdefault(dim, []).append(value)
    # "mobile" alone should not also match when "mobile web" is present — handled by synonyms order;
    # "app" vs "mobile app": treat "mobile app" as app only.
    if _contains(text, "mobile app"):
        found["device"] = [v for v in found.get("device", []) if v != "mobile_web"] or ["app"]
    return found


def extract_metric(question: str) -> str:
    text = question.lower()
    for metric, phrases in METRIC_PATTERNS:
        if any(p in text for p in phrases):
            return metric
    return "listing_to_contact"


def classify_intent(question: str) -> Intent:
    text = question.lower()
    for intent, phrases in INTENT_PATTERNS:
        if any(p in text for p in phrases):
            return intent
    return "explain_change"


def route(question: str) -> dict:
    intent = classify_intent(question)
    segments = extract_segments(question)
    metric = extract_metric(question)
    if intent == "abandon_vs_tracking" and metric == "listing_to_contact":
        metric = "contact_start_to_submit" if "abandon" in question.lower() else metric
    return {"intent": intent, "metric": metric, "filters": segments}
