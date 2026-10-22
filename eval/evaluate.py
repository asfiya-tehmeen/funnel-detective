"""Evaluate the investigation engine against the hidden synthetic scenarios.

    cd backend && python ../eval/evaluate.py [--json report.json]

How it stays honest
-------------------
* The engine is run exactly as the product runs it (same orchestrator, deterministic mode) on
  generic business questions and on an unprompted segment scan. Nothing in app/ reads this file
  or ground_truth.json.
* Ground truth is only used here, after the engine has answered, to grade the answer.
* Each scenario is graded on detection (did the engine look at the right segment?) and
  explanation (did the leading, 'supported' explanation match the planted mechanism?).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.analytics import metrics as M  # noqa: E402
from app.analytics.queries import Comparison, Filters, QueryTrace  # noqa: E402
from app.data.store import DataStore  # noqa: E402
from app.engine.orchestrator import investigate  # noqa: E402

TRUTH = json.loads((ROOT / "eval" / "ground_truth.json").read_text(encoding="utf-8"))
SC = TRUTH["scenarios"]


CASES = [
    ("A_mobile_contact_regression", "Why did mobile contact conversion decline in Riyadh?",
     SC["A_mobile_contact_regression"]["segment"], "technical_regression"),
    ("A_mobile_contact_regression (unprompted)", "Which funnel step has the largest recoverable opportunity?",
     SC["A_mobile_contact_regression"]["segment"], "technical_regression"),
    ("B_channel_mix_shift", "Is the overall conversion decline real, or is the traffic mix changing?",
     None, "traffic_composition"),
    ("C_app_selection_effect", "Why does the app convert better than the website?",
     SC["C_app_selection_effect"]["segment"], "selection_effect"),
    ("D_jeddah_app_tracking (unprompted)", "Are users abandoning the contact flow, or is tracking broken?",
     SC["D_jeddah_app_tracking"]["segment"], "instrumentation_issue"),
    ("E_dubai_luxury_behaviour", "Why did contact starts fall for luxury listings in Dubai?",
     SC["E_dubai_luxury_behaviour"]["segment"], "behavioral_change"),
    ("E_dubai_luxury_behaviour (unprompted)", "Which segments show a genuine change in behaviour?",
     SC["E_dubai_luxury_behaviour"]["segment"], "behavioral_change"),
]
CONTROL_QUESTIONS = [
    "Why did contact conversion change in Abu Dhabi on desktop?",
    "Why did contact conversion change in Cairo?",
]


def scopes(inv) -> list[tuple[dict, str, str]]:
    """All (filters, leading category, status) the investigation reported, primary first."""
    out = [(inv.finding.filters, inv.hypotheses[0].category, inv.hypotheses[0].status)] if inv.finding else []
    out += [(r["filters"], r["leading_category"], r["leading_status"]) for r in inv.extras.get("related_findings", [])]
    return out


def seg_match(filters: dict, segment: dict) -> bool:
    return all(filters.get(k) == [v] for k, v in segment.items())


def grade(name: str, question: str, inv, segment: dict | None, expected: str) -> dict:
    found = scopes(inv)
    if segment:
        hit = next(((f, c, s) for f, c, s in found if seg_match(f, segment)), None)
    else:
        hit = found[0] if found else None
    detected = hit is not None
    explained = bool(hit and hit[1] == expected and hit[2] in ("supported", "partially_supported"))
    strong = bool(hit and hit[1] == expected and hit[2] == "supported")
    return {"scenario": name, "question": question, "expected": expected, "segment": segment,
            "detected": detected, "explained": explained, "explanation_fully_supported": strong,
            "engine_answer": {"filters": hit[0], "leading": hit[1], "status": hit[2]} if hit else None,
            "all_scopes": [{"filters": f, "leading": c, "status": s} for f, c, s in found],
            "confidence": inv.finding.confidence if inv.finding else None}


def unprompted_scan(cur) -> dict:
    """Which planted segments does Detect flag without being asked about them?"""
    comp = Comparison()
    flagged = {}
    for metric in ("listing_to_contact_start", "contact_start_to_submit", "listing_to_contact", "listing_to_contact_raw"):
        res = M.segment_scan(cur, QueryTrace(), metric, Filters(), comp)
        flagged[metric] = [r["segment"] for r in res["flagged"]] + [r["segment"] for r in res["positive"]]
        flagged[metric + " (watchlist)"] = [r["segment"] for r in res["watchlist"]]
    out = {}
    for key, seg in [("A_mobile_contact_regression", SC["A_mobile_contact_regression"]["segment"]),
                     ("E_dubai_luxury_behaviour", SC["E_dubai_luxury_behaviour"]["segment"]),
                     ("D_jeddah_app_tracking", SC["D_jeddah_app_tracking"]["segment"])]:
        out[key] = [m for m, segs in flagged.items() if seg in segs]
    return {"flagged_in": out, "n_flagged": {k: len(v) for k, v in flagged.items()}}


def evaluate_store(store: DataStore) -> dict:
    cur = store.cursor()
    t0 = time.perf_counter()
    results = [grade(n, q, investigate(q, cur), seg, exp) for n, q, seg, exp in CASES]
    controls = []
    for q in CONTROL_QUESTIONS:
        inv = investigate(q, cur)
        bad = [h.category for h in inv.hypotheses if h.category in ("technical_regression", "instrumentation_issue")
               and h.status == "supported"]
        controls.append({"question": q, "leading": inv.hypotheses[0].category, "status": inv.hypotheses[0].status,
                         "false_alarm": bool(bad), "false_alarm_categories": bad})
    return {"results": results, "negative_controls": controls, "unprompted_scan": unprompted_scan(cur),
            "duration_s": round(time.perf_counter() - t0, 1)}


def multi_seed(seeds: list[int]) -> int:
    import tempfile
    from app.data.generator import generate
    tally: dict[str, int] = {}
    ctrl_ok = 0
    for seed in seeds:
        tmp = Path(tempfile.mkdtemp()) / "events.parquet"
        generate(seed=seed).to_parquet(tmp, index=False)
        rep = evaluate_store(DataStore(tmp))
        for r in rep["results"]:
            tally[r["scenario"]] = tally.get(r["scenario"], 0) + int(r["explained"])
        ok = all(not c["false_alarm"] for c in rep["negative_controls"])
        ctrl_ok += ok
        print(f"seed {seed}: {sum(r['explained'] for r in rep['results'])}/{len(rep['results'])} explained, "
              f"controls {'ok' if ok else 'FALSE ALARM'}")
    print()
    print("Per-scenario success across", len(seeds), "seeds")
    for k, v in tally.items():
        print(f"  {k:<45} {v}/{len(seeds)}")
    print(f"  {'negative controls without false alarms':<45} {ctrl_ok}/{len(seeds)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, help="write the full report to this file")
    ap.add_argument("--seed", type=int, help="regenerate the dataset with this seed (to a temp file) and evaluate it")
    ap.add_argument("--seeds", help="robustness run over several seeds, e.g. 1-8 or 101-110")
    args = ap.parse_args()
    if args.seeds:
        a, _, b = args.seeds.partition("-")
        return multi_seed(list(range(int(a), int(b or a) + 1)))
    if args.seed is not None:
        import tempfile
        from app.data.generator import generate
        tmp = Path(tempfile.mkdtemp()) / "events.parquet"
        generate(seed=args.seed).to_parquet(tmp, index=False)
        TRUTH["seed"] = args.seed
        store = DataStore(tmp)
    else:
        store = DataStore()
    rep = evaluate_store(store)
    results, controls = rep["results"], rep["negative_controls"]
    scan = rep["unprompted_scan"]
    report = {"seed": TRUTH["seed"], **rep}

    print(f"\nFunnel Detective — scenario evaluation (seed {TRUTH['seed']})\n" + "=" * 72)
    for r in results:
        mark = "PASS" if r["explained"] else ("DETECTED, WRONG EXPLANATION" if r["detected"] else "MISSED")
        ans = r["engine_answer"]
        print(f"[{mark:>27}] {r['scenario']}")
        print(f"{'':30}Q: {r['question']}")
        print(f"{'':30}expected {r['expected']} → engine: "
              + (f"{ans['leading']} ({ans['status']}) on {ans['filters'] or 'all traffic'}" if ans else "segment not examined"))
    print("-" * 72)
    for c in controls:
        print(f"[{'FALSE ALARM' if c['false_alarm'] else 'OK':>27}] control: {c['question']} → {c['leading']} ({c['status']})")
    print("-" * 72)
    for k, v in scan["flagged_in"].items():
        print(f"Unprompted scan flags {k}: {', '.join(v) if v else 'not flagged'}")
    n_pass = sum(r["explained"] for r in results)
    n_ctrl = sum(not c["false_alarm"] for c in controls)
    print("=" * 72 + f"\n{n_pass}/{len(results)} scenario checks explained correctly · "
          f"{n_ctrl}/{len(controls)} controls without false alarms · {report['duration_s']}s\n")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0 if n_pass == len(results) and n_ctrl == len(controls) else 1


if __name__ == "__main__":
    sys.exit(main())
