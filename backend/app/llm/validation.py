"""Guards applied to every LLM output before it reaches the user.

1. Schema: output must parse into the Pydantic model (extra fields rejected).
2. Grounding: every key point must cite evidence ids that exist.
3. Numbers: every number in the narrative must match a number present in the cited/available evidence
   (within rounding tolerance). Unmatched numbers are treated as fabricated.
4. Causal language: words like "caused" or "proves" are rejected unless causal (experimental) evidence is cited.
5. Consistency: a key point may not claim support for a hypothesis the engine marked not_supported.
"""
from __future__ import annotations

import re
from typing import Iterable

from pydantic import ValidationError

from app.engine.schemas import Evidence, Hypothesis, NarrativeDraft

NUMBER_RE = re.compile(r"(?<![\w.])[-+−]?\d+(?:[.,]\d+)*(?:\.\d+)?")
CAUSAL_RE = re.compile(r"\b(caused|causes|causing|proves?|proven|because of|due to|drove|drives|driven by|led to|"
                       r"resulted in|responsible for)\b", re.IGNORECASE)
TRIVIAL_NUMBERS = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 95.0, 100.0}
YEAR_RANGE = range(2020, 2031)


def _numbers(text: str) -> list[float]:
    out = []
    for m in NUMBER_RE.finditer(text):
        s = m.group().replace("−", "-").replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            continue
    return out


def _evidence_numbers(evidence: Iterable[Evidence]) -> set[float]:
    nums: set[float] = set()
    for e in evidence:
        for n in _numbers(e.statement) + _numbers(e.title):
            nums.add(round(abs(n), 4))
    return nums


def _matches(n: float, pool: set[float]) -> bool:
    a = abs(n)
    if a in TRIVIAL_NUMBERS or int(a) in YEAR_RANGE:
        return True
    for p in pool:
        if abs(a - p) <= max(0.051, 0.006 * max(a, p)):  # rounding to 1 decimal, or 0.6% relative
            return True
    return False


def number_pool(evidence: Iterable[Evidence], hypotheses: Iterable[Hypothesis], extra_texts: Iterable[str] = ()) -> set[float]:
    """Numbers the engine computed: evidence statements plus hypothesis check details (also deterministic)."""
    pool = _evidence_numbers(evidence)
    for h in hypotheses:
        for c in h.checks:
            pool.update(round(abs(n), 4) for n in _numbers(c.detail))
    for t in extra_texts:
        pool.update(round(abs(n), 4) for n in _numbers(t))
    return pool


def validate_narrative(raw: dict, evidence: list[Evidence], hypotheses: list[Hypothesis],
                       extra_texts: Iterable[str] = ()) -> tuple[NarrativeDraft | None, list[str]]:
    issues: list[str] = []
    try:
        draft = NarrativeDraft.model_validate(raw)
    except ValidationError as exc:
        return None, [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()][:5]
    ev_by_id = {e.id: e for e in evidence}
    hyp_by_id = {h.id: h for h in hypotheses}
    pool = number_pool(evidence, hypotheses, extra_texts)
    has_causal = any(e.evidence_level == "causal" for e in evidence)

    texts = [("summary", draft.summary)] + [(f"key_point[{i}]", kp.text) for i, kp in enumerate(draft.key_points)] \
        + [(f"caveat[{i}]", c) for i, c in enumerate(draft.caveats)]
    for i, kp in enumerate(draft.key_points):
        missing = [eid for eid in kp.evidence_ids if eid not in ev_by_id]
        if missing:
            issues.append(f"key_point[{i}] cites unknown evidence {missing}")
        if kp.hypothesis_id is not None:
            h = hyp_by_id.get(kp.hypothesis_id)
            if h is None:
                issues.append(f"key_point[{i}] references unknown hypothesis {kp.hypothesis_id}")
            elif kp.stance == "supports" and h.status == "not_supported":
                issues.append(f"key_point[{i}] claims support for {h.id} ({h.category}), which the evidence marks "
                              "not supported")
            elif kp.stance == "contradicts" and h.status == "supported":
                issues.append(f"key_point[{i}] claims {h.id} is contradicted, but the evidence marks it supported")
    for where, text in texts:
        bad = [n for n in _numbers(text) if not _matches(n, pool)]
        if bad:
            issues.append(f"{where} contains numbers not found in the evidence: {bad[:5]}")
        if not has_causal:
            m = CAUSAL_RE.search(text)
            if m:
                issues.append(f"{where} uses causal language ('{m.group()}') without experimental evidence")
    return (draft if not issues else None), issues
