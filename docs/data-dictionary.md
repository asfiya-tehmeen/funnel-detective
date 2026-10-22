# Data dictionary

All data is synthetic. The same definitions are served live at `GET /api/dictionary` and in the
**Data & SQL** tab.

## Tables

| Table | Grain | Description |
|---|---|---|
| `raw_events` | event | Parquet as generated, cast to the declared schema. Not queryable from the console. |
| `quarantine` | event | Rows failing schema checks, with `quarantine_reason`. |
| `events_all` | event | Schema-valid events, with `is_duplicate`. |
| `events` | event | `events_all` without duplicates: the analytical event table. |
| `sessions` | session | One row per session with funnel flags: the analytical table behind every metric. |

## Event columns

| Column | Type | Description |
|---|---|---|
| `event_id` | varchar | Unique event id. Duplicated client firings get new ids. |
| `session_id` | varchar | Browsing session. One user, market and device per session. |
| `user_id` | varchar | Pseudonymous user; repeat visitors reuse it. |
| `event_ts` | timestamp | Event time (UTC, naive). |
| `event_type` | varchar | `search_submitted`, `results_viewed`, `listing_viewed`, `property_saved`, `contact_started`, `contact_submitted`, `viewing_requested` |
| `event_status` | varchar | `ok` / `failed` (for example, a contact form error) |
| `market` | varchar | Dubai, Riyadh, Cairo, Abu Dhabi, Jeddah |
| `device` | varchar | desktop, mobile_web, app |
| `channel` | varchar | organic_search, paid_search, paid_social, direct, referral, crm_email |
| `visitor_type` | varchar | new / returning (client cookie or app install) |
| `intent` | varchar | rent / buy |
| `property_type` | varchar | apartment, villa, townhouse, commercial |
| `price_band` | varchar | budget, mid, premium, luxury (market-relative) |
| `listing_id` | varchar | Listing for listing-level events |
| `client_version` | varchar | Web release (`web-2026.06.4`, `web-2026.07.2`) or app version (`app-5.11.3`, `app-5.12.0`) |
| `lead_id` | varchar | Lead-service id. A valid `contact_submitted` must carry one. |
| `is_qualified` | boolean | Lead-scoring outcome for `contact_submitted` |
| `error_code` | varchar | e.g. `FORM_VALIDATION_ERROR`, `LEAD_SERVICE_5XX`, `RESULTS_TIMEOUT` |
| `experiment_id`, `variant` | varchar | `EXP-014` with control/treatment (Dubai and Abu Dhabi, 25 May–21 Jun) |

## Session columns

| Column | Definition |
|---|---|
| `session_date` | Date of the session's first event |
| dimension columns | Taken from the session's first event |
| `has_search`, `has_listing`, `has_save`, `has_contact_start`, `has_viewing` | Session contains that event type |
| `has_results` | Session has a `results_viewed` with status ok |
| `has_contact` | **Validated contact**: a `contact_submitted` with status ok **and** a `lead_id` |
| `has_contact_raw` | Any `contact_submitted` event (naive definition; includes failures and lead-less events) |
| `has_qualified` | Validated contact with `is_qualified = true` |
| `n_failed_submits` | Count of failed `contact_submitted` |
| `n_submits_without_lead` | Count of ok `contact_submitted` without a `lead_id` |
| `n_submits_before_start` | Count of `contact_submitted` with no earlier `contact_started` in the session |
| `n_qualified_leads` | Distinct qualified lead ids |

## Metrics (session-based)

| Key | Label | Numerator | Denominator |
|---|---|---|---|
| `search_to_listing` | Search → listing | search sessions that viewed ≥1 listing | sessions with ≥1 search |
| `listing_to_contact` | Listing → contact | listing sessions with a validated contact | sessions with ≥1 listing view |
| `listing_to_contact_start` | Listing → contact start | listing sessions that opened the contact form | listing sessions |
| `contact_start_to_submit` | Contact start → submit | contact-start sessions with a validated contact | sessions with ≥1 contact start |
| `contact_to_viewing` | Contact → viewing | contact sessions that requested a viewing | sessions with a validated contact |
| `save_rate` | Listing → save | listing sessions that saved | listing sessions |
| `qualified_contact_rate` | Listing → qualified contact | listing sessions with a qualified lead | listing sessions |
| `listing_to_contact_raw` | Listing → contact (raw events) | listing sessions with any `contact_submitted` | listing sessions |

**Qualified contacts** (a count): distinct qualified `lead_id`s in sessions within the window.

Listing sessions include sessions that land directly on a listing (paid social, referral) without searching,
so "Viewed a listing" can exceed "Searched".

## Default windows

The data covers 25 May – 16 Aug 2026. The default comparison is **baseline 22 Jun – 19 Jul** vs
**current 20 Jul – 16 Aug** (4 weeks vs 4 weeks), and both windows can be edited in the workspace.
