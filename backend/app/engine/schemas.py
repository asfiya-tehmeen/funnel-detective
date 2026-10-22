"""Typed schemas for evidence, hypotheses, findings, plans and investigations.

Separation of concerns that the UI relies on:
  * Evidence.statement is generated deterministically from computed numbers.
  * Narrative.* is interpretation (LLM or template) and is validated against the evidence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

EvidenceLevel = Literal["descriptive", "associational", "causal"]
HypothesisCategory = Literal[
    "technical_regression", "traffic_composition", "behavioral_change",
    "instrumentation_issue", "random_variation", "selection_effect",
]
HypothesisStatus = Literal["supported", "partially_supported", "not_supported", "inconclusive"]
Intent = Literal[
    "explain_change", "mix_vs_rate", "opportunity", "prioritize_markets",
    "abandon_vs_tracking", "segment_advantage", "scan_changes",
]

EVIDENCE_LEVEL_HELP = {
    "descriptive": "What the data shows (rates, counts, intervals). Says nothing about why.",
    "associational": "A pattern that co-occurs with a segment, time or version. Consistent with an explanation "
                     "but does not establish cause.",
    "causal": "Comes from a randomized experiment, so differences can be attributed to the treatment "
              "(within the experiment's population and period).",
}


class Evidence(BaseModel):
    id: str
    tool: str
    title: str
    statement: str
    evidence_level: EvidenceLevel
    data: dict[str, Any] = {}
    query_ids: list[str] = []


class Check(BaseModel):
    """One test of a hypothesis' predicted pattern against evidence."""
    description: str
    result: Literal["supports", "contradicts", "neutral"]
    detail: str
    evidence_ids: list[str] = []


class Hypothesis(BaseModel):
    id: str
    category: HypothesisCategory
    title: str
    predicted_pattern: str
    checks: list[Check]
    segment_comparisons: list[dict[str, Any]] = []
    discriminating_data: list[str]
    evidence_level: EvidenceLevel
    status: HypothesisStatus
    status_rule: str
    explained_share: Optional[float] = None
    explained_share_basis: Optional[str] = None

    @property
    def n_support(self) -> int:
        return sum(c.result == "supports" for c in self.checks)

    @property
    def n_contra(self) -> int:
        return sum(c.result == "contradicts" for c in self.checks)


class ObservedChange(BaseModel):
    metric: str
    metric_label: str
    numerator_label: str
    denominator_label: str
    baseline_rate: float
    current_rate: float
    baseline_n: int
    current_n: int
    abs_change_pp: float
    rel_change_pct: Optional[float]
    diff_ci_pp: tuple[float, float]
    p_value: float
    test: str
    mde_pp_at_this_n: Optional[float]
    comparison_kind: Literal["period", "group"] = "period"
    baseline_label: str = "Baseline"
    current_label: str = "Current"
    channel_adjusted_change_pp: Optional[float] = None
    channel_adjusted_p_value: Optional[float] = None


class Finding(BaseModel):
    business_question: str
    metric: str
    scope: str
    filters: dict[str, list[str]] = {}
    comparison_window: dict[str, str]
    observed_change: ObservedChange
    leading_hypothesis_id: Optional[str]
    supporting_evidence: list[str]
    alternative_explanations: list[str]
    confidence: Literal["high", "medium", "low"]
    confidence_basis: str
    limitations: list[str]
    next_analysis: list[str]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    args: dict[str, Any] = {}
    rationale: str = ""


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    metric: str
    filters: dict[str, list[str]] = {}
    rationale: str
    steps: list[PlanStep] = []
    planner: Literal["llm", "deterministic"] = "deterministic"


class ToolCall(BaseModel):
    id: str
    tool: str
    args: dict[str, Any]
    status: Literal["ok", "error"]
    error: Optional[str] = None
    evidence_ids: list[str] = []
    query_ids: list[str] = []
    duration_ms: float
    requested_by: Literal["planner", "hypothesis_battery", "workflow"] = "workflow"


class KeyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_ids: list[str] = Field(min_length=1)
    hypothesis_id: Optional[str] = None
    stance: Literal["supports", "contradicts", "context"] = "context"


class NarrativeDraft(BaseModel):
    """Schema the LLM must return. Validated before display."""
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=1200)
    key_points: list[KeyPoint] = Field(min_length=1, max_length=8)
    caveats: list[str] = Field(default_factory=list, max_length=6)


class Narrative(BaseModel):
    source: Literal["llm", "deterministic"]
    summary: str
    key_points: list[KeyPoint]
    caveats: list[str]
    validated: bool
    validation_issues: list[str] = []
    model: Optional[str] = None
    fallback_reason: Optional[str] = None


class Investigation(BaseModel):
    id: str
    created_at: datetime
    question: str
    mode: Literal["llm", "deterministic"]
    plan: InvestigationPlan
    planner_notes: list[str] = []
    tool_calls: list[ToolCall]
    evidence: list[Evidence]
    hypotheses: list[Hypothesis]
    finding: Optional[Finding]
    recommended_experiment: Optional[dict[str, Any]] = None
    narrative: Narrative
    queries: list[dict[str, Any]]
    extras: dict[str, Any] = {}
    evidence_level_help: dict[str, str] = EVIDENCE_LEVEL_HELP
