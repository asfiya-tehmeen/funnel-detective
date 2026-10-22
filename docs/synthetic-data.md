# Synthetic data generation guide

```bash
cd backend
python -m scripts.generate_data              # default seed 20260720
python -m scripts.generate_data --seed 7     # any other seed
```

The script writes `backend/data/events.parquet` and `eval/ground_truth.json`. With the same code and seed,
the output is identical: a test asserts this, and the Linux Docker build produced the same 184,993 events as
the Windows development machine.

## How a session is simulated

For each day from 25 May to 16 Aug 2026:

1. **Volume.** Poisson around 520 sessions/day, with slight growth, busier Fridays and Saturdays, and extra
   paid-social traffic after 20 Jul.
2. **Attributes.**
   - Market: Dubai 36%, Riyadh 22%, Cairo 17%, Abu Dhabi 15%, Jeddah 10%.
   - Channel: drawn from a mix in which paid social's weight is multiplied by 3.4 after 20 Jul, with a
     five-day ramp.
   - Device: conditional on channel (paid social is 80% mobile web; direct and CRM traffic is mostly app).
   - Visitor type: returning visitors reuse an existing `user_id` from that market's pool. The returning
     probability depends on channel, plus 0.28 for app.
   - Intent, property type and price band: drawn per session (Dubai has a larger luxury share).
3. **Funnel walk.**
   - Entry is a search (88%; 45% for paid social, 55% for referral) or a landing listing.
   - Results load with 94% probability; 64% of searches go on to open listings; extra listings follow a
     Poisson(1.3) count.
   - The contact-start probability is the base 13.5% × channel × visitor × device × intent × price band ×
     market multipliers, spread across the listings viewed.
   - Form completion depends on device (72% desktop, 63% mobile web, 75% app).
   - 72% of leads are qualified (55% for paid social); viewing follows 31% of contacts.
4. **Noise and data issues.**
   - 1.5% of events are duplicated within 2 seconds.
   - 0.2% are malformed (null `session_id`, an old event name, null market, a far-future timestamp).
   - 2% of searches have no `search_submitted` event.
   - Results load fails occasionally, and the lead service returns 5xx for 1.5% of submits.

All parameters are constants at the top of `app/data/generator.py`.

## Planted scenarios (ground truth: `eval/ground_truth.json`)

| | Scenario | Mechanism | What a correct investigation should say |
|---|---|---|---|
| A | Mobile contact-flow regression | Riyadh × mobile web from 20 Jul (release `web-2026.07.2`): form completion × 0.55; 45% of lost submits log `FORM_VALIDATION_ERROR` | Technical regression, concentrated in form completion on one platform, sharp break, failed submits up |
| B | Channel-mix shift | Paid-social volume × 3.4 after 20 Jul; within-channel rates unchanged | The aggregate decline is mostly composition; within-channel change ≈ 0 |
| C | Selection effect | App device multiplier is only 1.04, but app users are mostly returning (×1.75) | App's raw advantage largely disappears once visitor type × channel are held fixed |
| D | Tracking issue + small sample | Jeddah app 5.12.0 (staged rollout from 27 Jul) fires `contact_submitted` without `lead_id` on contact start and some listing renders | Instrumentation: raw contacts jump, validated contacts don't; out-of-order events; small n |
| E | Genuine behaviour change | Dubai × luxury from 20 Jul, all devices and channels: contact starts × 0.55, saves × 1.4 | Behavioural change: intent drops while form completion is stable; broad-based; no technical signal |
| — | EXP-014 | Sticky contact CTA, Dubai + Abu Dhabi, 25 May–21 Jun, 50/50 by user hash, true +7% relative on contact starts | Underpowered: positive point estimate, CI includes 0 |

## Assumptions

- Rates are stylised. They aim at plausible orders of magnitude (≈10% listing→contact, ≈65% form completion)
  and are not calibrated to any real marketplace.
- Session attributes are fixed for the session. Cross-device journeys are modelled only by returning users
  switching device between sessions.
- Visitor type is client-reported (cookie or install), so it is not derived from the dataset window, which
  avoids left-censoring.
- One planned design change, documented: Dubai's luxury share was raised from 16% to 24% after a multi-seed
  evaluation showed scenario E sat below the detection threshold (z ≈ 3.9) at four-week windows. See
  "What Broke and What I Learned" in the README.
