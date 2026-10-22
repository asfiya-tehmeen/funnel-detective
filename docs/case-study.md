# Case study: "Views are up, contacts are down"

*All figures are from the synthetic dataset. This is a portfolio exercise, not a real Property Finder
analysis.*

## Problem

Listing-detail sessions rose by 39% between the four weeks before and after 20 Jul (10,991 → 15,242),
while listing → contact conversion fell from 10.4% to 8.7% (−1.72pp, p < 0.001). The obvious reading is "the
contact experience got worse; fix it". Acting on that would point engineering at the whole funnel.

## Evidence

1. **Most of the decline is composition, not behaviour.**
   - Paid social went from 14% to 36% of listing sessions, and converts at about 3%.
   - The Kitagawa mix effect is −1.78pp of the −1.72pp total.
   - Holding channel mix fixed, conversion moved +0.07pp (p = 0.86).
   - This is Simpson's paradox in practice: every channel is roughly flat, yet the aggregate falls.
2. **Some real problems are hidden underneath.** A channel-standardised, FDR-controlled scan flags two
   segments:
   - **Riyadh × mobile web:** form completion 58.9% → 35.9%. There is a sharp break on 21 Jul, aligned with
     web release `web-2026.07.2`. Failed submits rose from 0.6% to 27.3%, and other Riyadh devices are flat.
     This is consistent with a technical regression. It is associational evidence, so the release is a
     suspect, not a proven cause.
   - **Dubai × luxury:** contact starts 13.4% → 5.9%, across every device and most channels. Saves are up
     and there is no error signal. This is consistent with a genuine behaviour change, such as hesitation at
     the top of the market.
3. **One apparent bright spot is a tracking artefact.** Raw contact events in Jeddah's app jumped after app
   5.12.0, but 21% of those sessions carry `contact_submitted` without a `lead_id`, and validated contacts
   didn't move.
4. **One apparent best practice is mostly selection.** The app converts 7.7pp better than the web, but 70% of
   that gap disappears once visitor type and channel are matched.

## Decision

| Finding | Action | Why not something else |
|---|---|---|
| Mix shift (paid social) | Report a mix-adjusted KPI. Test an exploration-first paid-social landing page (2,432 per arm, 4 weeks) with qualified-lead rate as a guardrail | Fixing the contact form for everyone would not move a composition effect |
| Riyadh mobile-web regression | Engineering triage on `web-2026.07.2` form validation. Ship the fix behind a 10% holdback to verify recovery | A 50/50 test would take about 12 weeks at this traffic (≈7 contact starts/day), and the cost of waiting is real |
| Dubai luxury hesitation | Test a low-commitment "ask about this property" CTA (1,222 per arm, 8 weeks), with guardrails on qualified-lead and viewing rates | No technical cause was found, so a fix is the wrong frame |
| Jeddah app tracking | Fix the event, add a contract test that `contact_submitted` carries a `lead_id`, and reconcile with lead-service counts | No product experiment until the data is trustworthy |
| App "advantage" | Don't fund an app-install push on the raw gap. Run a randomised adoption prompt first | Observational gap ≠ causal effect |

## Measurement

- **Plans are forecasts.** Each states its MDE, sample size, duration and decision rule:
  - ship only if the CI excludes 0, guardrails hold and there is no SRM;
  - abandon if the upper CI bound is below the MDE.
- **The logged EXP-014 shows why sizing matters.** Its +1.66pp lift (CI −0.41 to +3.73) is not significant
  because the test could only detect about 3pp. The right call is "iterate", not "it doesn't work".
- **The impact model stays an estimate.** For the paid-social test, the base scenario is the MDE translated
  to listing → contact. Its output is extra contacts → qualified contacts → viewings per month, and money
  only appears if finance supplies a value per qualified contact.

## What a reviewer should take away

The product distinguishes **what changed** (descriptive), **where** (segment and step), and **which
explanation the evidence supports** (rule-based, with competing hypotheses and their contradicting checks).
It recommends experiments sized to the traffic that actually exists, and is explicit about what it can't
know.
