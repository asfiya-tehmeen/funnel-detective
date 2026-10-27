"""Live smoke test for the LLM path. Requires a real ANTHROPIC_API_KEY; makes paid API calls.

    cd backend
    export ANTHROPIC_API_KEY=...          # (PowerShell: $env:ANTHROPIC_API_KEY = "...")
    python -m scripts.llm_smoke_test [--questions 7] [--model claude-opus-5-5]

What it checks, in order, stopping early with a clear message if a step fails:
  1. Raw structured-output call: isolates request-shape problems (rejected parameter, schema feature,
     model id) from everything else. A 400 here prints the API's own message.
  2. For each example question, runs the full investigation with the real provider and reports whether the
     planner and narrator really came from the LLM, or fell back and why.

Cost (estimate): two calls per question, with the narrator call carrying the evidence ledger (roughly
10-15k input tokens). Seven questions should be on the order of a few tenths of a dollar at current
Opus-tier pricing; use --questions 1 first. The API key is never printed or stored.

Exit code 0 only if step 1 succeeds and every question produced an LLM-sourced, validated narrative.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from app.config import get_settings
from app.data.store import get_store
from app.engine.orchestrator import investigate
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError

QUESTIONS = [
    "Why did mobile contact conversion decline in Riyadh?",
    "Is the overall conversion decline real, or is the traffic mix changing?",
    "Which funnel step has the largest recoverable opportunity?",
    "Which market should we investigate first?",
    "Are users abandoning the contact flow, or is tracking broken?",
    "Why does the app convert better than the website?",
    "Which segments show a genuine change in behaviour?",
]
TRIVIAL_SCHEMA = {"type": "object", "properties": {"word": {"type": "string"}, "n": {"type": "integer"}},
                  "required": ["word", "n"], "additionalProperties": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=int, default=len(QUESTIONS), help="how many example questions to run")
    ap.add_argument("--model", default=None, help="override FD_LLM_MODEL")
    args = ap.parse_args()

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY is not set. This script needs a real key and makes paid calls; nothing was sent.")
        return 2
    settings = get_settings()
    model = args.model or settings.llm_model
    provider = AnthropicProvider(api_key=key, model=model, timeout=settings.llm_timeout_s)
    print(f"model: {model}  (key present: yes; not printed)")

    print("\n[1/2] raw structured-output call ...")
    t0 = time.perf_counter()
    try:
        out = provider.complete_json("Reply with JSON only.", "Give one common English word and the number 3.",
                                     TRIVIAL_SCHEMA, max_tokens=2000)
    except LLMError as exc:
        print(f"  FAILED: {exc}")
        print("  -> This is a request-shape or credentials problem in app/llm/anthropic_provider.py (or the key/model).")
        return 1
    print(f"  ok in {time.perf_counter() - t0:.1f}s: {out}")

    print("\n[2/2] full investigations with the live provider ...")
    cur = get_store().cursor()
    bad = 0
    for q in QUESTIONS[: args.questions]:
        t0 = time.perf_counter()
        inv = investigate(q, cur, provider=provider)
        n = inv.narrative
        planner_ok = inv.mode == "llm"
        narrator_ok = n.source == "llm" and n.validated
        bad += not (planner_ok and narrator_ok)
        print(f"- {q}")
        print(f"    planner: {'LLM' if planner_ok else 'FELL BACK'}"
              f" | narrator: {'LLM (validated)' if narrator_ok else 'FELL BACK'} | {time.perf_counter() - t0:.1f}s")
        for note in inv.planner_notes:
            if "LLM" in note:
                print(f"    planner note: {note}")
        if n.fallback_reason:
            print(f"    narrator fallback: {n.fallback_reason}")
        for issue in n.validation_issues[:5]:
            print(f"    validation issue: {issue}")
        if narrator_ok:
            print(f"    summary: {n.summary[:200]}")
    print(f"\n{len(QUESTIONS[: args.questions]) - bad}/{len(QUESTIONS[: args.questions])} fully LLM-sourced and validated.")
    print("Fallbacks are SAFE (the product still answers), but each one is a finding to look at: either the prompt, "
          "the schema, max_tokens, or the validator is too strict for real model output.")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
