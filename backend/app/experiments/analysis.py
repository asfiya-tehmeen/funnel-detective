"""Analyse experiment results (user-supplied, simulated, or logged) against a decision rule.

Results are OBSERVED outcomes for supplied/logged data and SIMULATED outcomes for generated
data; the response always states which.
"""
from __future__ import annotations

from typing import Literal, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy import stats as sps

from app.analytics.stats import P_VALUE_NOTE, compare_proportions


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = Field(gt=0, le=100_000_000)
    conversions: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "Arm":
        if self.conversions > self.n:
            raise ValueError("conversions cannot exceed n")
        return self


class Guardrail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    control: Arm
    treatment: Arm
    max_degradation_pp: float = Field(1.0, ge=0, le=50)
    higher_is_better: bool = True


class ResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_label: str = Field("Primary metric", max_length=80)
    control: Arm
    treatment: Arm
    guardrails: list[Guardrail] = Field(default_factory=list, max_length=8)
    mde_pp: Optional[float] = Field(None, gt=0, le=50)
    alpha: float = Field(0.05, gt=0, lt=0.5)
    expected_split: float = Field(0.5, gt=0, lt=1)
    source: Literal["user_supplied", "simulated", "logged_experiment"] = "user_supplied"


def analyse(req: ResultsRequest) -> dict:
    t = compare_proportions(req.control.conversions, req.control.n, req.treatment.conversions, req.treatment.n,
                            req.alpha)
    total = req.control.n + req.treatment.n
    exp_t = total * req.expected_split
    chi2, srm_p = sps.chisquare([req.treatment.n, req.control.n], [exp_t, total - exp_t])
    srm = bool(srm_p < 0.001)
    lo, hi = t.diff_ci_pp

    guards = []
    guard_fail = guard_borderline = False
    for g in req.guardrails:
        gt = compare_proportions(g.control.conversions, g.control.n, g.treatment.conversions, g.treatment.n, req.alpha)
        glo, ghi = gt.diff_ci_pp
        worst = glo if g.higher_is_better else -ghi  # most pessimistic plausible change
        if worst < -g.max_degradation_pp:
            point_bad = (gt.abs_change_pp if g.higher_is_better else -gt.abs_change_pp) < -g.max_degradation_pp
            status = "fail" if point_bad else "inconclusive"
        else:
            status = "pass"
        guard_fail |= status == "fail"
        guard_borderline |= status == "inconclusive"
        guards.append({"name": g.name, "control_rate": gt.baseline_rate, "treatment_rate": gt.current_rate,
                       "abs_change_pp": gt.abs_change_pp, "diff_ci_pp": gt.diff_ci_pp,
                       "max_degradation_pp": g.max_degradation_pp, "status": status})

    reasons = []
    if srm:
        decision = "investigate"
        reasons.append(f"Sample-ratio mismatch (p={srm_p:.2g}): assignment or logging is broken, so the result "
                       "cannot be trusted until fixed.")
    elif lo > 0 and not guard_fail and not guard_borderline:
        decision = "ship"
        reasons.append(f"Primary metric improved by {t.abs_change_pp:+.2f}pp with a 95% CI ({lo:+.2f}, {hi:+.2f}) "
                       "excluding zero, and all guardrails passed.")
    elif hi < 0:
        decision = "abandon"
        reasons.append(f"Primary metric got worse ({t.abs_change_pp:+.2f}pp, CI {lo:+.2f} to {hi:+.2f}).")
    elif req.mde_pp is not None and hi < req.mde_pp and lo <= 0:
        decision = "abandon"
        reasons.append(f"The CI upper bound ({hi:+.2f}pp) is below the minimum effect worth shipping "
                       f"({req.mde_pp:.2f}pp): even the optimistic reading is too small.")
    else:
        decision = "iterate"
        if lo > 0:
            reasons.append("Primary metric improved, but a guardrail failed or is inconclusive.")
        else:
            reasons.append(f"The effect ({t.abs_change_pp:+.2f}pp, CI {lo:+.2f} to {hi:+.2f}) is not distinguishable "
                           "from zero; it may be real but smaller than the test could detect.")
    if guard_fail:
        reasons.append("At least one guardrail degraded beyond its margin.")

    return {
        "source": req.source,
        "outcome_type": "simulated outcome" if req.source == "simulated" else "observed outcome",
        "metric_label": req.metric_label,
        "test": t.to_dict(),
        "relative_lift_pct": t.rel_change_pct,
        "srm": {"chi2": float(chi2), "p_value": float(srm_p), "mismatch": srm,
                "expected_split": req.expected_split},
        "guardrails": guards,
        "decision": decision,
        "reasons": reasons,
        "decision_rule": ("ship: CI excludes 0 (positive), guardrails pass, no SRM · iterate: positive but "
                          "inconclusive, or a guardrail concern · abandon: significantly negative, or CI upper bound "
                          "below the MDE · investigate: sample-ratio mismatch."),
        "p_value_note": P_VALUE_NOTE,
    }


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_rate: float = Field(gt=0, lt=1)
    true_relative_lift: float = Field(ge=-0.9, le=3.0)
    n_per_arm: int = Field(gt=10, le=10_000_000)
    seed: int = 7
    mde_pp: Optional[float] = None
    metric_label: str = "Primary metric"
    guardrail_rate: Optional[float] = Field(None, gt=0, lt=1)
    guardrail_true_change_pp: float = 0.0


def simulate(req: SimulationRequest) -> dict:
    rng = np.random.default_rng(req.seed)
    p_t = min(0.999, req.baseline_rate * (1 + req.true_relative_lift))
    # Random 50/50 assignment of the total, so sample sizes vary like a real test.
    total = 2 * req.n_per_arm
    n_t = int(rng.binomial(total, 0.5))
    n_c = total - n_t
    x_c, x_t = int(rng.binomial(n_c, req.baseline_rate)), int(rng.binomial(n_t, p_t))
    guards = []
    if req.guardrail_rate:
        g_t = min(0.999, max(0.001, req.guardrail_rate + req.guardrail_true_change_pp / 100))
        guards.append(Guardrail(name="Qualified-lead rate (simulated)",
                                control=Arm(n=n_c, conversions=int(rng.binomial(n_c, req.guardrail_rate))),
                                treatment=Arm(n=n_t, conversions=int(rng.binomial(n_t, g_t)))))
    res = analyse(ResultsRequest(metric_label=req.metric_label, control=Arm(n=n_c, conversions=x_c),
                                 treatment=Arm(n=n_t, conversions=x_t), guardrails=guards, mde_pp=req.mde_pp,
                                 source="simulated"))
    res["simulation"] = {"true_control_rate": req.baseline_rate, "true_treatment_rate": p_t,
                         "true_relative_lift": req.true_relative_lift, "seed": req.seed,
                         "note": "Simulated outcome from a binomial model — not an observed result."}
    return res
