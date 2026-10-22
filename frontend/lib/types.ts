// Types mirror the FastAPI response shapes (backend/app/engine/schemas.py and app/main.py).

export type Dimension = "market" | "device" | "channel" | "property_type" | "price_band" | "intent" | "visitor_type";
export type Filters = Partial<Record<Dimension, string[]>>;
export interface Window { start: string; end: string }
export interface Comparison { baseline: Window; current: Window }

export interface QueryRecord {
  id: string; name: string; sql: string; params: string[]; row_count: number; duration_ms: number;
}

export interface MetricDef {
  label: string; numerator: string; denominator: string; description: string;
  numerator_sql: string; denominator_sql: string;
}

export interface Meta {
  disclaimer: string;
  dimensions: Record<Dimension, string[]>;
  metrics: Record<string, MetricDef>;
  date_range: Window;
  default_comparison: Comparison;
  llm: { enabled: boolean; provider: string | null; model: string | null };
  example_questions: string[];
  evidence_levels: Record<string, string>;
  p_value_note: string;
}

export interface PropTest {
  baseline_rate: number; current_rate: number; baseline_n: number; current_n: number;
  baseline_x: number; current_x: number; abs_change_pp: number; rel_change_pct: number | null;
  diff_ci_pp: [number, number]; baseline_ci: [number, number]; current_ci: [number, number];
  test: string; p_value: number; significant: boolean; assumption_note: string; p_value_note: string;
}

export interface Stratified {
  adj_change_pp: number; adj_se_pp: number; adj_p_value: number; adj_ci_pp: [number, number];
  strata_used: number; strata_dropped: number;
}

export interface Kpi {
  metric: string; label: string; description?: string; numerator?: string; denominator?: string;
  test?: PropTest; channel_adjusted?: Stratified | null; query_id?: string; error?: string;
}

export interface FunnelStep {
  key: string; label: string; sessions: number; step_conversion: number | null; drop_off: number | null;
}
export interface Funnel { steps: FunnelStep[]; sessions: number; qualified_contacts: number; note: string; query_id: string }

export interface TrendPoint {
  bucket: string; denominator: number; numerator: number; rate: number | null;
  ci_low: number | null; ci_high: number | null; [k: string]: unknown;
}
export interface Trend { metric: string; grain: string; split_by: string | null; points: TrendPoint[]; query_id: string }

export interface SegmentRow {
  segment: Record<string, string>; baseline_n: number; baseline_x: number; current_n: number; current_x: number;
  baseline_rate: number | null; current_rate: number | null; abs_change_pp: number | null;
  rel_change_pct: number | null; p_value: number | null; q_value: number | null; tested: boolean;
  diff_ci_pp: [number, number] | null; test: string | null; impact_sessions: number | null;
  adj_change_pp?: number; adj_p_value?: number; adj_q_value?: number | null; adj_impact_sessions?: number;
  adj_ci_pp?: [number, number];
  scan_change_pp?: number; scan_q_value?: number; scan_p_value?: number; scan_adjusted?: boolean;
  scan_impact_sessions?: number; group_by?: string[]; query_id?: string;
}
export interface SegmentTable {
  metric: string; group_by: string[]; adjust_for: string | null; rows: SegmentRow[]; n_tests: number;
  multiple_comparison_note: string | null; min_segment_n: number; query_id: string;
}

export interface Warning {
  severity: "info" | "high"; title: string; detail: string; segment?: Record<string, string>; query_id?: string;
  kind?: string;
}

export interface Overview {
  scope: string; comparison: Comparison; kpis: Kpi[];
  qualified_contacts: { baseline: number; current: number; definition: string };
  funnel: { baseline: Funnel; current: Funnel };
  trends: Record<string, Trend>; markets: SegmentTable; warnings: Warning[]; queries: QueryRecord[];
}

export interface Scan {
  metric: string; n_tests: number; n_flagged: number; multiple_comparison_note: string; flagged: SegmentRow[];
  positive: SegmentRow[]; watchlist: SegmentRow[]; watchlist_note: string; adjust_for: string | null; method: string;
}

export interface Decomposition {
  dimension: string[]; total_change_pp: number; mix_effect_pp: number; rate_effect_pp: number;
  mix_share_of_change: number | null; query_id: string;
  segments: { segment: Record<string, string>; baseline_share: number; current_share: number;
    baseline_rate: number; current_rate: number; mix_effect_pp: number; rate_effect_pp: number }[];
}

export interface Evidence {
  id: string; tool: string; title: string; statement: string;
  evidence_level: "descriptive" | "associational" | "causal"; data: Record<string, unknown>; query_ids: string[];
}
export interface Check {
  description: string; result: "supports" | "contradicts" | "neutral"; detail: string; evidence_ids: string[];
}
export type HypStatus = "supported" | "partially_supported" | "not_supported" | "inconclusive";
export interface Hypothesis {
  id: string; category: string; title: string; predicted_pattern: string; checks: Check[];
  segment_comparisons: Record<string, unknown>[]; discriminating_data: string[];
  evidence_level: "descriptive" | "associational" | "causal"; status: HypStatus; status_rule: string;
  explained_share: number | null; explained_share_basis: string | null;
}
export interface ObservedChange {
  metric: string; metric_label: string; numerator_label: string; denominator_label: string;
  baseline_rate: number; current_rate: number; baseline_n: number; current_n: number;
  abs_change_pp: number; rel_change_pct: number | null; diff_ci_pp: [number, number]; p_value: number;
  test: string; mde_pp_at_this_n: number | null; comparison_kind: "period" | "group";
  baseline_label: string; current_label: string;
  channel_adjusted_change_pp: number | null; channel_adjusted_p_value: number | null;
}
export interface Finding {
  business_question: string; metric: string; scope: string; filters: Filters; comparison_window: Record<string, string>;
  observed_change: ObservedChange; leading_hypothesis_id: string | null; supporting_evidence: string[];
  alternative_explanations: string[]; confidence: "high" | "medium" | "low"; confidence_basis: string;
  limitations: string[]; next_analysis: string[];
}
export interface ToolCall {
  id: string; tool: string; args: Record<string, unknown>; status: "ok" | "error"; error: string | null;
  evidence_ids: string[]; query_ids: string[]; duration_ms: number; requested_by: string;
}
export interface KeyPoint { text: string; evidence_ids: string[]; hypothesis_id: string | null; stance: string }
export interface Narrative {
  source: "llm" | "deterministic"; summary: string; key_points: KeyPoint[]; caveats: string[];
  validated: boolean; validation_issues: string[]; model: string | null; fallback_reason: string | null;
}
export interface ExperimentPlan {
  kind: "experiment" | "data_fix" | "monitor"; status: string; forecast_note: string; name: string;
  hypothesis_category: string; problem_statement: string; target_segment: Filters; hypothesis: string;
  intervention: string; control: string; treatment: string;
  primary_metric: { key: string; label: string; numerator: string; denominator: string };
  guardrail_metrics: { key: string | null; label: string }[]; mechanism: string; implementation_cost: string;
  risks: string[]; baseline_rate: number; pre_change_rate: number; daily_eligible_sessions: number;
  eligible_sessions_in_current_window: number; alpha: number; power: number; traffic_allocation: number;
  mde: { absolute_pp: number; relative: number; basis: string } | null; sample_size_per_arm: number | null;
  duration_days: number | null; duration_note?: string; assumptions?: string[]; decision_rule: string;
  monitoring_sample_needed?: { relative_change: number; sessions_per_window: number; days_at_current_traffic: number };
  query_id?: string; queries?: QueryRecord[];
}
export interface RelatedFinding {
  scope: string; filters: Filters; metric: string; metric_label: string; abs_change_pp: number | null;
  p_value: number | null; leading_category: string; leading_title: string; leading_status: HypStatus;
}
export interface Investigation {
  id: string; created_at: string; question: string; mode: "llm" | "deterministic";
  plan: { intent: string; metric: string; filters: Filters; rationale: string; planner: string;
    steps: { tool: string; args: Record<string, unknown>; rationale: string }[] };
  planner_notes: string[]; tool_calls: ToolCall[]; evidence: Evidence[]; hypotheses: Hypothesis[];
  finding: Finding | null; recommended_experiment: ExperimentPlan | null; narrative: Narrative;
  queries: QueryRecord[];
  extras: { related_findings?: RelatedFinding[]; duration_ms?: number; simpson?: SimpsonData;
    selection?: Record<string, unknown>; [k: string]: unknown };
  evidence_level_help: Record<string, string>;
}
export interface SimpsonData {
  dimension: string; total: { baseline_rate: number; current_rate: number; abs_change_pp: number };
  rows: { segment: string; baseline_rate: number | null; current_rate: number | null; baseline_share: number;
    current_share: number; abs_change_pp: number | null; q_value: number | null }[];
  explanation: string; query_ids?: string[]; decomposition?: Decomposition; queries?: QueryRecord[];
}
export interface ResultsAnalysis {
  source: string; outcome_type: string; metric_label: string; test: PropTest; relative_lift_pct: number | null;
  srm: { chi2: number; p_value: number; mismatch: boolean; expected_split: number };
  guardrails: { name: string; control_rate: number; treatment_rate: number; abs_change_pp: number;
    diff_ci_pp: [number, number]; max_degradation_pp: number; status: string }[];
  decision: "ship" | "iterate" | "abandon" | "investigate"; reasons: string[]; decision_rule: string;
  p_value_note: string;
  simulation?: { true_control_rate: number; true_treatment_rate: number; true_relative_lift: number; seed: number; note: string };
}
export interface ImpactScenario {
  uplift_pp: number; new_rate: number; additional_contacts_per_month: number; additional_qualified_per_month: number;
  additional_viewings_per_month: number; additional_qualified_total: number;
  modelled_value_per_month?: number; modelled_value_total?: number;
}
export interface ImpactResult {
  scenarios: Record<"low" | "base" | "high", ImpactScenario>; formulas: string[]; disclaimer: string;
  value_note: string | null; inputs: Record<string, unknown>;
}
