# Architecture

```
 Browser ──► Next.js (React, Tailwind, Recharts) ──/api/* rewrite──► FastAPI
                                                                      │
        ┌─────────────────────────────────────────────────────────────┼──────────────────────────┐
        │                                                             ▼                          │
        │  question ──► Planner ───────────────┐            Approved tools (typed, validated)    │
        │               • LLM (structured JSON)│            compare_metric · step_breakdown       │
        │               • keyword router       │──steps──►  segment_breakdown · mix_decomposition │
        │                (fallback / default)  │            change_point · quality_check · ...    │
        │                                      │                     │                           │
        │                    mandatory ────────┴──► Hypothesis battery (5 competing explanations) │
        │                                                     │  rule-based checks + status       │
        │                                                     ▼                                   │
        │                Finding (rule-based confidence) ─► Experiment planner ─► Impact model     │
        │                                                     │                                   │
        │          Narrator: LLM draft ──► validator ──► shown │ else deterministic template       │
        │                                                     ▼                                   │
        │                              Audit log (SQLite): question, plan, tool calls, evidence    │
        └──────────────────────────────────────┬───────────────────────────────────────────────────┘
                                               │ parameterised SQL (recorded in a QueryTrace)
                                               ▼
                     DuckDB (in-memory, external access disabled + config locked)
        raw_events ─► quarantine ─► events_all (dup flag) ─► events ─► sessions (funnel flags)
                         ▲
              events.parquet  ◄── generator (seeded)  ──► eval/ground_truth.json (never read by the app)
```

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Data | `app/data/generator.py` | Seeded simulation of ~53k sessions / ~185k events, five planted scenarios |
| | `app/data/store.py` | Typed load → quarantine → de-duplication → session table; then locks DuckDB |
| Analytics | `app/analytics/stats.py` | Pure statistical functions (no I/O) |
| | `app/analytics/queries.py` | Whitelisted metric/dimension definitions and SQL templates; filter/window validation |
| | `app/analytics/metrics.py` | Comparisons, segment tables, stratified tests, Kitagawa, standardisation, scans, quality |
| Engine | `app/engine/tools.py` | The approved tool registry: strict Pydantic args, evidence generation, error capture |
| | `app/engine/hypotheses.py` | Competing-explanation battery and the selection-effect battery |
| | `app/engine/router.py` | Deterministic question → intent/metric/segment mapping |
| | `app/engine/orchestrator.py` | Plan → workflow → battery → finding → experiment → narrative |
| AI | `app/llm/base.py`, `anthropic_provider.py` | Replaceable provider interface; Anthropic SDK adapter with JSON-schema output |
| | `app/llm/validation.py` | Schema, grounding, number, causal-language and contradiction checks |
| Decide/Measure | `app/experiments/*`, `app/impact.py` | Plan sizing; results analysis (SRM, guardrails, decision); impact scenarios |
| Data access | `app/sqlguard.py` | Restricted user SQL |
| Audit | `app/audit.py` | Append-only record of investigations and SQL runs |
| API | `app/main.py` | Thin FastAPI routes; Pydantic request models; 400/422 on invalid input |

## Investigation lifecycle

1. **Plan.** If an LLM is configured, it receives the question, the allowed intents, metric definitions,
   dimension values and the tool catalogue, and must return JSON matching a schema: intent, metric, filters,
   and up to three extra tool calls. Invalid output (schema, unknown values, bad JSON arguments, provider
   error, refusal) falls back to the keyword router, and the reason is recorded in `planner_notes`.
2. **Workflow.** The intent selects a deterministic workflow:
   - **mix vs rate:** decompositions, a scan for residual change, and the Simpson table;
   - **opportunity:** recoverable sessions per step, then the top cell plus the runner-up cells;
   - **prioritise markets:** within-channel loss and quality flags;
   - **abandon vs tracking:** investigates the segments with quality warnings;
   - **scan:** FDR-flagged segments, then watchlist items, each localised to its concentrated sub-segment;
   - **segment advantage:** standardisation over confounders.
3. **Battery.** The focus segment and metric always go through all five explanations. Each check records the
   evidence IDs it used. The workflow passes `n_comparisons` (how many segments it examined to pick this one), and the
   change's p-value is corrected for it before any explanation can be *supported* (see the methodology doc).
4. **Finding.** Leading hypothesis by status and share of the change explained; rule-based confidence;
   limitations; next analyses.
5. **Experiment.** A template chosen by the leading category, sized from the segment's current rate and
   traffic.
6. **Narrative.** The LLM draft is validated, or the deterministic template is used. Both are labelled.
7. **Audit.** The full payload (plan, calls, evidence, queries, narrative) is stored and can be re-opened.

## Security boundaries

- The AI never receives SQL, file paths or the database. It can only name tools, whose arguments are
  validated against whitelists.
- User SQL is checked twice: a single `SELECT` according to DuckDB's own parser, then denylisted keywords
  and functions and allowed relations only. It is then wrapped with a row limit and a timeout.
- After loading, DuckDB runs with `enable_external_access = false` and `lock_configuration = true`, so even
  a bypass of the checks cannot read files or re-enable access. There is a test for this.
- The ground-truth file lives outside `backend/app` and no route reads it. There is a test for this too.
