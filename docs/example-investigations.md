# Example investigations

These are real outputs of the deterministic engine on the default dataset (seed 20260720), with default
windows (baseline 22 Jun–19 Jul vs current 20 Jul–16 Aug). Each runs in about 0.1–3 s. All data is synthetic.

Reproduce any of them in the app's question box, or with
`POST /api/investigations {"question": "..."}`.

---

### 1. "Why did mobile contact conversion decline in Riyadh?"

- **Plan:** explain change · listing → contact · Riyadh × mobile web. The router notes that it interpreted
  "mobile" as mobile web and compares the app as a sibling. 14 tool calls, 22 SQL queries.
- **Observed:** 8.13% → 3.75%, **−4.37pp** (95% CI −6.21 to −2.67, p < 0.001). Holding channel mix fixed:
  −2.88pp.

| Explanation | Status | Share of change |
|---|---|---|
| Technical regression | **Supported** (6 ✓ / 0 ✕) | ~52% |
| Traffic composition | Partially supported | ~34% |
| Behavioural change | Not supported | |
| Instrumentation | Not supported | |
| Random variation | Not supported | |

Key checks for the lead explanation:
- Form completion fell −21.9pp within-channel (−37% relative) while contact starts moved −0.5pp.
- Failed submits rose from 0.6% to 27.3% of contact starts.
- There is a sharp break on 21 Jul (LLR 10.9).
- Mobile web fell −21.9pp; app +6.7pp; desktop +0.8pp.
- The timing coincides with release `web-2026.07.2`.

**Confidence:** high. **Experiment:** "Contact-form fix validation"
- MDE 11.5pp, 286 sessions per arm, **84 days**. Riyadh mobile web has about 7 contact starts a day, so the
  plan flags that this is longer than eight weeks and suggests a 10% holdback.
- The recommended action is to fix or roll back, then verify.

### 2. "Is the overall conversion decline real, or is the traffic mix changing?"

- **Observed:** listing → contact 10.42% → 8.70%, **−1.72pp** (p < 0.001). **Channel-standardised: +0.07pp
  (p = 0.86).**
- **Lead: traffic composition, supported.**
  - The Kitagawa mix effect by channel is −1.78pp of −1.72pp.
  - Paid social grew from 14% to 36% of listing sessions and converts at 3.1% against 10.4% overall.
- The regression explanation is "not supported" at this scope, accounting for about 17%.
- The Simpson table shows every channel roughly flat while the aggregate falls.
- **Experiment:** paid-social landing experience, 2,432 per arm, 28 days. The plan also recommends reporting
  a mix-adjusted KPI.

### 3. "Which funnel step has the largest recoverable opportunity?"

- Opportunity sizing counts only within-channel declines with p < 0.05. It ranks **contact start → submit**
  first and localises it to **Riyadh × mobile web**: 58.9% → 35.9%, −23.1pp.
- **Lead: technical regression, supported** (~95% of that step's change).
- Runner-up steps are investigated too, so a close ranking doesn't hide a second problem.

### 4. "Which market should we investigate first?"

- Riyadh ranks first by within-channel loss × volume: listing → contact −3.09pp, of which −1.14pp is
  within-channel.
- At market level, **composition (~63%) and regression (~32%) are both supported**, and the finding says so.
  The experiment is targeted where the regression concentrates.
- Dubai and Jeddah were also examined; at market level both look like traffic composition.

### 5. "Are users abandoning the contact flow, or is tracking broken?"

The workflow starts from the data-quality warnings and finds **both patterns, in different places**:
- **Riyadh × mobile web: real abandonment.** It's a technical regression (supported): failed submits up,
  form completion −23.1pp.
- **Jeddah × app: a tracking problem.** Instrumentation is supported:
  - 21% of listing sessions have `contact_submitted` events without a `lead_id` (baseline 0%);
  - submits fire before `contact_started`;
  - raw contacts jump while validated contacts don't.

### 6. "Why does the app convert better than the website?"

- **Raw:** app 15.9% vs everything else 8.25%, a gap of **+7.7pp**.
- **Standardised by visitor type × channel:** the gap is +2.3pp, so **70% of it is explained by audience mix.**
  44% of app sessions come from direct traffic against 10% for the rest, and app users are mostly returning.
- **Lead: selection effect, supported.** A within-strata gap of +2.6pp remains (p < 0.001), so "genuine app
  advantage" is partially supported, explaining part but not all of the gap.
- **Experiment:** a randomised app-adoption prompt (an encouragement design), so the causal effect of the
  app can be measured rather than assumed.

### 7. "Which segments show a genuine change in behaviour?"

- The scan spans several hundred segment tests, FDR-controlled and standardised over channel. The top
  credible change is **Dubai × luxury, listing → contact start: 13.4% → 5.9%** (−5.5pp within-channel,
  p < 0.001).
- **Lead: behavioural change, supported (~74%):**
  - broad-based across 3 of 3 devices and 5 of 6 channels;
  - no technical or tracking signal;
  - save rate up +5.7pp.
- Riyadh × mobile web is reported as a related finding (regression).
- Watchlist items that were checked are labelled as not significant after FDR correction.
- **Experiment:** a low-commitment contact option, 1,222 per arm, 56 days.

---

### Logged experiment EXP-014 (Measure stage)

- Sticky contact CTA, Dubai and Abu Dhabi, randomised by user.
- Contact start: 17.48% control vs 19.14% treatment, **+1.66pp (95% CI −0.41 to +3.73, p = 0.116)**.
- **Decision: iterate.** The test could only detect about 3.0pp at 80% power, so this null result is weak
  evidence of no effect.
- The planted true effect was +7% relative (about +1.2pp), which is below what the test could detect.
