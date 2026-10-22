"""Business-impact model. Every output is a modelled estimate from explicit, editable inputs.

additional_contacts   = eligible_sessions × absolute uplift (pp / 100)
additional_qualified  = additional_contacts × qualified-lead rate
additional_viewings   = additional_contacts × viewing rate
modelled_value        = additional_qualified × value per qualified contact   (only if a value is supplied)
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligible_sessions_per_month: float = Field(gt=0, le=1e9)
    baseline_rate: float = Field(gt=0, lt=1)
    uplift_pp_low: float = Field(ge=-50, le=50)
    uplift_pp_base: float = Field(ge=-50, le=50)
    uplift_pp_high: float = Field(ge=-50, le=50)
    qualified_rate: float = Field(gt=0, le=1)
    viewing_rate: float = Field(ge=0, le=1)
    value_per_qualified_contact: Optional[float] = Field(None, ge=0, le=1e7)
    value_currency: str = Field("AED", max_length=8)
    value_source: Optional[str] = Field(None, max_length=200)
    months: int = Field(12, ge=1, le=36)

    @model_validator(mode="after")
    def _ordered(self) -> "ImpactRequest":
        if not (self.uplift_pp_low <= self.uplift_pp_base <= self.uplift_pp_high):
            raise ValueError("uplift scenarios must satisfy low ≤ base ≤ high")
        if not 0 < self.baseline_rate + self.uplift_pp_high / 100 <= 1:
            raise ValueError("baseline + high uplift must stay within (0, 1]")
        if self.baseline_rate + self.uplift_pp_low / 100 < 0:
            raise ValueError("baseline + low uplift cannot be negative")
        return self


def estimate(req: ImpactRequest) -> dict:
    scenarios = {}
    for name, up in (("low", req.uplift_pp_low), ("base", req.uplift_pp_base), ("high", req.uplift_pp_high)):
        contacts_m = req.eligible_sessions_per_month * up / 100
        qualified_m = contacts_m * req.qualified_rate
        viewings_m = contacts_m * req.viewing_rate
        s = {"uplift_pp": up, "new_rate": req.baseline_rate + up / 100,
             "additional_contacts_per_month": contacts_m, "additional_qualified_per_month": qualified_m,
             "additional_viewings_per_month": viewings_m,
             "additional_qualified_total": qualified_m * req.months}
        if req.value_per_qualified_contact is not None:
            s["modelled_value_per_month"] = qualified_m * req.value_per_qualified_contact
            s["modelled_value_total"] = qualified_m * req.value_per_qualified_contact * req.months
        scenarios[name] = s
    return {
        "scenarios": scenarios,
        "inputs": req.model_dump(),
        "formulas": [
            "additional contacts = eligible sessions × uplift (pp) / 100",
            "additional qualified contacts = additional contacts × qualified-lead rate",
            "additional viewings = additional contacts × viewing rate",
            "modelled value = additional qualified contacts × value per qualified contact",
        ],
        "disclaimer": ("Modelled estimates from the assumptions shown — not realised revenue and not a guaranteed "
                       "outcome. Uplift scenarios should come from experiment results or a documented forecast."),
        "value_note": (None if req.value_per_qualified_contact is not None else
                       "No value per qualified contact supplied, so no monetary estimate is shown. Enter one with "
                       "its source (e.g. finance's average agent revenue per qualified lead)."),
    }
