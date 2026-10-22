# Five-minute demo script

**Setup:** backend and frontend running, the browser on the **Detect** tab with default windows and no
filters. Say up front: *"Everything here is synthetic data I generated; this is an independent project, not a
Property Finder product."*

### 0:00 – 0:40 · The problem (Detect)
- Point at the KPI tiles: listing → contact **8.7%, down 1.7pp**, while qualified contacts are **up**.
  *"Each tile states its numerator, denominator and window. Hover the 'i'."*
- Point at the second line: **"Holding channel mix fixed: +0.1pp."** *"That one line already says this
  might not be what it looks like."*
- Funnel: listing sessions rose sharply. Data-quality warnings: two high-severity flags, raised before any
  conclusion is drawn.

### 0:40 – 1:30 · Is the decline real? (Challenge)
- Click the example question *"Is the overall conversion decline real, or is the traffic mix changing?"*
- Show the Finding: −1.72pp observed, channel-standardised +0.07pp.
- Show the lead explanation: **traffic composition, supported**, mix ≈ 100% of the change.
- Scroll to the **Simpson's paradox view**: every channel is flat, paid social goes from 14% to 36% of
  traffic, and the aggregate falls.
- *"A dashboard would have sent engineering after the contact form."*

### 1:30 – 2:40 · Where is the real problem? (Detect scan → Challenge)
- Back to Detect, **Run segment scan**. *"About 600 segment tests, standardised over channel, FDR-controlled.
  It also says how many false positives chance alone would produce."*
- Click **Investigate** on *Riyadh · Mobile web* (contact start → submit).
- Walk the five competing explanations. Regression is **supported**, with six checks; open it:
  - form completion −22pp while starts are flat;
  - failed submits rose from 0.6% to 27%;
  - there's a sharp break on 21 Jul;
  - the app and desktop are flat;
  - the timing aligns with `web-2026.07.2`.
- Then open **Random variation: not supported** to show the contradicting checks.
- *"It says associational, not causal: the release is a suspect, not proven."*
- Click an evidence chip such as `ev_3` to jump to the evidence ledger, then open **SQL** to show the
  parameterised query.

### 2:40 – 3:20 · Tracking vs behaviour (Challenge)
- Ask *"Are users abandoning the contact flow, or is tracking broken?"*
- It finds both: Riyadh is real abandonment; Jeddah app is **instrumentation**. Ghost `contact_submitted`
  events without a `lead_id` mean raw contacts are up while real leads aren't.

### 3:20 – 4:20 · Decide and measure
- **Decide:** the plan is built from the lead explanation. Point at the MDE, sample size, and **84 days**,
  plus the note suggesting a holdback. *"It tells you when an A/B test is the wrong tool."*
- Impact calculator: low/base/high scenarios. No money figure appears until a value and its source are
  entered.
- **Measure → EXP-014:** +1.7pp with a CI crossing zero gives **Iterate**. *"Underpowered: it could only
  detect 3pp."*
- Switch to **Simulate**, run it, and show the "simulated outcome" label.

### 4:20 – 5:00 · Trust and architecture
- **Data & SQL → SQL console:** run an example, then type `DROP TABLE sessions` to show the rejection.
- Audit trail: every investigation stored with its plan, tool calls and queries.
- Close: *"The LLM is optional. It can choose approved tools and phrase findings, but it never computes a
  number, and its text is rejected if it cites a number not in the evidence, uses causal language without an
  experiment, or contradicts the checks. I evaluate the engine against planted scenarios on held-out seeds.
  It finds them all when asked; unprompted, the subtle luxury-segment change is found in 15 of 18 seeds
  (9 of 10 held-out)."*
