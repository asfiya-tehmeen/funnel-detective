# Statistical methodology

All computations are in `backend/app/analytics/stats.py` and `metrics.py`, and are covered by
`tests/test_stats.py` and `tests/test_decomposition.py`.

## Proportions and intervals

- **Rates** are session-based proportions with explicit numerators and denominators (see the data dictionary).
- **Intervals for a rate:** Wilson score, 95%. It behaves well at small n and near 0 or 1.
- **Interval for a difference:** Newcombe hybrid score interval (method 10), built from the two Wilson
  intervals.
- **Changes** are reported as absolute percentage points (pp) and relative %.

## Two-proportion tests

- **Pooled two-proportion z-test** when every expected cell count is ≥ 10.
- **Fisher's exact test** otherwise. The test used and the reason are shown with each result.
- A p-value is the probability of seeing a difference at least this large *if there were no true difference
  and the assumptions hold*. It is **not** the probability that a hypothesis is true, and the UI and API say
  so wherever a p-value appears.

## Composition: mix vs rate

**Kitagawa decomposition.** For segments i with shares w and rates r in the baseline (0) and current (1)
windows:

```
total = Σ w1·r1 − Σ w0·r0
mix   = Σ (w1 − w0) · (r0 + r1)/2       change from shares shifting
rate  = Σ (r1 − r0) · (w0 + w1)/2       change within segments
mix + rate = total (exactly)
```

**Stratified (standardised) change.** This removes composition shifts in one dimension, by default channel:

```
adj  = Σ w̄_s (r1_s − r0_s),   w̄_s = average share of stratum s across the windows
test: z = adj / SE₀,  SE₀² = Σ w̄_s² p_s(1−p_s)(1/n0_s + 1/n1_s),  p_s = pooled rate in stratum s
CI:   adj ± 1.96·SE₁,  SE₁² = Σ w̄_s² (r0(1−r0)/n0 + r1(1−r1)/n1)
```

The null variance uses each stratum's pooled rate, as a Cochran–Mantel–Haenszel-style test does. A first
version floored per-stratum variances instead, which was anti-conservative in small cells; see the README.

**Simpson's paradox.** The Diagnose and Challenge views show the aggregate change next to each channel's
change and share. In the synthetic data the aggregate falls about 1.7pp while the channel-standardised change
is about +0.1pp: almost the whole decline is paid-social growth.

**Direct standardisation for selection.** To compare group A with group B (for example, app vs everyone else),
both are re-weighted to the pooled confounder mix (visitor type × channel). The share of the raw gap that
disappears is attributable to who each group serves. The remaining within-strata gap is tested with the
stratified test above.

## Many segments: multiple comparisons

- The Detect scan tests every single-dimension segment and nine deliberate dimension pairs (171 tests per
  metric at full scope). Segments with fewer than 30 denominator sessions in either window are not tested.
- It controls the false discovery rate with Benjamini–Hochberg q-values. The UI states how many false
  positives would be expected by chance (m × α).
- A **watchlist** (p < 0.01 but q ≥ 0.05) is shown separately and labelled as not significant.
- Inside an investigation, the random-variation hypothesis applies a Bonferroni check across the sibling
  segments the analyst could have looked at, which guards against "I found Riyadh mobile web by looking".

## Change points

There is a single change point in the daily binomial series: the split that maximises the log-likelihood
ratio, with at least 5 days on each side. The maximum over splits inflates significance, so the LLR is
reported as a **strength indicator** (sharp ≥ 10, moderate ≥ 4), not as a p-value.

## Sample size and MDE

Two-sided two-proportion sample size per arm:

```
n = (z₁₋α/₂·√(2p̄q̄) + z₁₋β·√(p₀q₀ + p₁q₁))² / (p₁ − p₀)²
```

The MDE for a given n is solved numerically from the same formula. The experiment planner works as follows:

- The baseline is the current-window rate in the target segment.
- **Auto MDE** takes the smallest relative lift in {5, 10, 15, 20, 30, 50}% that fits in six weeks of that
  segment's traffic. For a regression it instead uses half of the observed drop ("detect at least partial
  recovery").
- Duration is rounded up to whole weeks, with a 14-day minimum.

## Experiment readout and decision rule

- **SRM:** χ² test of the arm sizes against the planned split. p < 0.001 → *investigate*, and the result is
  not read.
- **Guardrails:** the pessimistic CI bound must not degrade by more than the margin (default 1pp).
- **Decision:**
  - *ship*: CI excludes 0 (positive) and every guardrail passes;
  - *abandon*: significantly negative, or the CI's upper bound is below the MDE;
  - *iterate*: anything else.
- Results are labelled **observed** (user-supplied or logged) or **simulated**. Plans are labelled
  **forecast**.

## Hypothesis status, explained share and confidence

- Each hypothesis is evaluated by explicit checks that support, contradict, or are neutral.
- **Status rule:**
  - *supported*: ≥ 2 supporting checks and none contradicting;
  - *partially supported*: ≥ 1 supporting and supports ≥ contradicts;
  - *not supported*: contradicting checks outnumber supporting ones;
  - *inconclusive*: no decisive checks.
- **Explained share**, where it is measurable:
  - regression: contacts lost at form completion;
  - behaviour: contacts lost through fewer starts;
  - composition: the Kitagawa mix effect;
  - noise: all of a change that isn't distinguishable from zero.

  Explanations with support are ordered by this share, because several can be true at once.
- **Finding confidence:**
  - *high*: change p < 0.01 and the lead explanation is the only supported one;
  - *medium*: change p < 0.05 and the lead is at least partially supported;
  - *low*: otherwise.

  These are rule-based ratings, **not probabilities**.

## Evidence levels

- **Descriptive:** what the data shows.
- **Associational:** co-occurrence with a segment, time or release.
- **Causal:** only from randomised experiment readouts (EXP-014 and the Measure stage).

The narrative validator rejects causal language unless causal evidence is present.

## Multiplicity and the credibility check

An explanation presupposes a real change. Two things can make a "change" not real: it is just noise, or it was
selected out of many comparable segments. `run_battery` therefore corrects the target change's p-value before any
explanation can be *supported*:

```
m      = max(sibling segments implied by the filters, n_comparisons)
p_corr = min(1, p · m)                      # Bonferroni
```

* **Sibling segments:** a market × device filter implies 5 × 3 = 15 comparable segments; a market × price band
  filter implies 20.
* **`n_comparisons`:** how many segments or steps the *workflow* examined to arrive at this one: the total size of a
  scan (several hundred tests), 15 market × device cells per step for opportunity sizing, 5 for market priority.
* If `p_corr ≥ 0.05`, a **contradicting check** ("Is the change itself credible after correcting for the number of
  comparisons made?") is added to H1–H3. By the status rule a contradicting check blocks *supported*. H4 (tracking) is
  exempt, because a tracking fault is an anomaly in the data itself, not in the target metric.
* The random-variation hypothesis (H5) is credited with the *whole* change (explained share 1.0) only when
  `p_corr ≥ 0.05`.

This trades sensitivity for specificity. A real but weak effect can sit at the boundary and be reported as ambiguous
rather than explained (one such case appears in the evaluation). The rule is a significance test, so its false-alarm
rate is bounded but not zero.

## Evaluating false alarms, not just recall

`eval/evaluate.py` grades two things, because recall alone rewards an engine that says "supported" about everything:

1. **Recall:** the planted scenarios A–E are found and explained correctly.
2. **False alarms:**
   * *Planted-world controls:* eight segments with no planted technical, tracking or behavioural effect (Cairo and
     Abu Dhabi, by market and by market × device). A "supported" regression, instrumentation or behavioural claim is a
     false alarm. Composition is allowed there, because scenario B's channel shift is real everywhere.
   * *Null world:* a dataset generated with every planted effect off (`generate(scenarios=False)`). Release labels still
     change on schedule, so a new client version appears with no real effect. Any non-noise explanation reported as
     *supported* anywhere in 14 investigations (plus their related findings) is counted.

**Protocol.** Seeds 1–8 were used while developing. Seeds 101–110 started as a held-out set but were partly inspected
while diagnosing a bug, so they are no longer pristine. Seeds 201–210 were run once, after all code changes, and are the
cleanest estimate. All of these come from one generator family, so more seeds measure sampling variability, not robustness
to differently structured data.

## Known statistical limitations

- Sessions are treated as independent; repeat visitors induce mild clustering.
- Windows are fixed, and there is no seasonality adjustment.
- Kitagawa and stratification are run one dimension at a time (or two jointly for selection). Higher-order
  interactions are not modelled.
- Quality-indicator tests reuse the session-level z/Fisher test on derived counts.
