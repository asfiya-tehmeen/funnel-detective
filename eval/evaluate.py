"""Evaluate the investigation engine against hidden synthetic scenarios AND against a world with none.

    cd backend && python ../eval/evaluate.py [--json report.json] [--skip-null]
    cd backend && python ../eval/evaluate.py --seeds 101-110      # robustness over seeds

Two questions are graded, because recall alone is not enough:

1. RECALL (planted world): does the engine find the planted mechanisms A-E and explain them correctly?
2. FALSE ALARMS: does it stay quiet when nothing is wrong?
   * Planted-world controls: eight segments with no planted behavioural, technical or tracking effect
     (Cairo and Abu Dhabi, by market and by market x device).
   * Null world: a dataset generated with every planted effect switched off (`generate(scenarios=False)`).
     Every investigation there is a false-alarm opportunity; any hypothesis other than random variation
     reported as 'supported' is counted.

How it stays honest
-------------------
* The engine runs exactly as the product runs it (same orchestrator, deterministic mode).
  Nothing in app/ reads this file or ground_truth.json.
* Ground truth is only used here, after the engine has answered, to grade the answer.
* Limits: all worlds come from the same generator family, so more seeds measure sampling variability,
  not robustness to differently-structured data. Some decision thresholds were set while looking at
  seeds 1-8 (see the README); seeds 101-110 were not used for tuning.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.analytics import metrics as M  # noqa: E402
from app.analytics.queries import Comparison, Filters, QueryTrace  # noqa: E402
from app.data.generator import generate  # noqa: E402
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

# Segments with no planted technical, tracking or behavioural effect. (Cairo and Abu Dhabi do share the
# real channel-mix shift of scenario B, so 'traffic_composition' is allowed there and not counted.)
CONTROL_MARKETS = ["Cairo", "Abu Dhabi"]
CONTROL_DEVICES = ["mobile web", "app", "desktop"]
CONTROL_QUESTIONS = [f"Why did contact conversion change in {m}?" for m in CONTROL_MARKETS] + [
    f"Why did contact conversion change in {m} on {d}?" for m in CONTROL_MARKETS for d in CONTROL_DEVICES]
CONTROL_FALSE_ALARM = {"technical_regression", "instrumentation_issue", "behavioral_change"}

# The null world has no effects at all, so every non-noise explanation is a false alarm.
NULL_FALSE_ALARM = {"technical_regression", "instrumentation_issue", "behavioral_change", "traffic_composition"}
NULL_QUESTIONS = [
    "Why did mobile contact conversion decline in Riyadh?",
    "Is the overall conversion decline real, or is the traffic mix changing?",
    "Which funnel step has the largest recoverable opportunity?",
    "Which market should we investigate first?",
    "Are users abandoning the contact flow, or is tracking broken?",
    "Which segments show a genuine change in behaviour?",
] + [f"Why did contact conversion change in {m}?" for m in ["Dubai", "Riyadh", "Cairo", "Abu Dhabi", "Jeddah"]] + [
    f"Why did contact conversion change for {d} users?" for d in ["mobile web", "app", "desktop"]]


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
    """Recall on the planted world plus the planted-world negative controls."""
    cur = store.cursor()
    t0 = time.perf_counter()
    results = [grade(n, q, investigate(q, cur), seg, exp) for n, q, seg, exp in CASES]
    controls = []
    for q in CONTROL_QUESTIONS:
        inv = investigate(q, cur)
        bad = sorted({h.category for h in inv.hypotheses if h.category in CONTROL_FALSE_ALARM and h.status == "supported"})
        controls.append({"question": q, "leading": inv.hypotheses[0].category, "status": inv.hypotheses[0].status,
                         "false_alarm": bool(bad), "false_alarm_categories": bad})
    return {"results": results, "negative_controls": controls, "unprompted_scan": unprompted_scan(cur),
            "duration_s": round(time.perf_counter() - t0, 1)}


def evaluate_null_world(store: DataStore) -> dict:
    """False-alarm measurement on a world where no effect was planted."""
    cur = store.cursor()
    t0 = time.perf_counter()
    findings = 0
    by_cat: Counter = Counter()
    alarms = []
    confident_wrong = 0  # primary findings with medium/high confidence whose lead is not noise
    for q in NULL_QUESTIONS:
        inv = investigate(q, cur)
        items = [(inv.finding.scope, inv.finding.metric, [(h.category, h.status) for h in inv.hypotheses], True)]
        items += [(r["scope"], r["metric"], [(h["category"], h["status"]) for h in r["hypotheses"]], False)
                  for r in inv.extras.get("related_findings", [])]
        for scope, metric, hyps, primary in items:
            findings += 1
            for cat, status in hyps:
                if status == "supported" and cat in NULL_FALSE_ALARM:
                    by_cat[cat] += 1
                    alarms.append({"question": q, "scope": scope, "metric": metric, "category": cat, "primary": primary})
        lead = inv.hypotheses[0]
        if inv.finding.confidence in ("medium", "high") and lead.category != "random_variation" and lead.status == "supported":
            confident_wrong += 1
    return {"investigations": len(NULL_QUESTIONS), "findings_examined": findings, "false_alarms": sum(by_cat.values()),
            "false_alarms_by_category": dict(by_cat), "confident_wrong_primary": confident_wrong, "alarms": alarms,
            "duration_s": round(time.perf_counter() - t0, 1)}


def make_store(seed: int, scenarios: bool) -> DataStore:
    tmp = Path(tempfile.mkdtemp()) / "events.parquet"
    generate(seed=seed, scenarios=scenarios).to_parquet(tmp, index=False)
    return DataStore(tmp)


def multi_seed(seeds: list[int], include_null: bool = True) -> int:
    tally: dict[str, int] = {}
    ctrl_ok = ctrl_total = ctrl_partial = 0
    null_alarms = Counter()
    null_clean = null_confident = 0
    for seed in seeds:
        rep = evaluate_store(make_store(seed, True))
        for r in rep["results"]:
            tally[r["scenario"]] = tally.get(r["scenario"], 0) + int(r["explained"])
        c_ok = sum(not c["false_alarm"] for c in rep["negative_controls"])
        ctrl_ok += c_ok
        ctrl_total += len(rep["negative_controls"])
        partial_lead = sum(c["leading"] in CONTROL_FALSE_ALARM for c in rep["negative_controls"])
        ctrl_partial += partial_lead
        line = (f"seed {seed}: {sum(r['explained'] for r in rep['results'])}/{len(rep['results'])} explained, "
                f"controls {c_ok}/{len(rep['negative_controls'])} clean ({partial_lead} lead with a partial non-noise claim)")
        if include_null:
            nrep = evaluate_null_world(make_store(seed, False))
            null_alarms.update(nrep["false_alarms_by_category"])
            null_clean += int(nrep["false_alarms"] == 0)
            null_confident += nrep["confident_wrong_primary"]
            line += (f" | null world: {nrep['false_alarms']} false alarms in {nrep['findings_examined']} findings"
                     + (f" {nrep['false_alarms_by_category']}" if nrep["false_alarms"] else ""))
        print(line, flush=True)
    print()
    print("Recall per scenario across", len(seeds), "seeds")
    for k, v in tally.items():
        print(f"  {k:<45} {v}/{len(seeds)}")
    print(f"  {'planted-world controls without false alarm':<45} {ctrl_ok}/{ctrl_total}")
    print(f"  {'(info) controls leading with a partial claim':<45} {ctrl_partial}/{ctrl_total}")
    if include_null:
        print(f"  {'null worlds with zero false alarms':<45} {null_clean}/{len(seeds)}")
        print(f"  {'null-world false alarms by category':<45} {dict(null_alarms) or 'none'}")
        print(f"  {'null-world confident (medium/high) wrong leads':<45} {null_confident}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, help="write the full report to this file")
    ap.add_argument("--seed", type=int, help="regenerate the planted dataset with this seed (temp file) and evaluate it")
    ap.add_argument("--seeds", help="robustness run over several seeds, e.g. 1-8 or 101-110")
    ap.add_argument("--skip-null", action="store_true", help="skip the null-world false-alarm evaluation")
    ap.add_argument("--null-seeds", default="1,2", help="comma-separated seeds for the null-world check in a default run")
    args = ap.parse_args()
    if args.seeds:
        a, _, b = args.seeds.partition("-")
        return multi_seed(list(range(int(a), int(b or a) + 1)), include_null=not args.skip_null)
    if args.seed is not None:
        TRUTH["seed"] = args.seed
        store = make_store(args.seed, True)
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
        flag = "FALSE ALARM" if c["false_alarm"] else "OK"
        extra = f" [{', '.join(c['false_alarm_categories'])}]" if c["false_alarm"] else ""
        print(f"[{flag:>27}] control: {c['question']} → {c['leading']} ({c['status']}){extra}")
    print("-" * 72)
    for k, v in scan["flagged_in"].items():
        print(f"Unprompted scan flags {k}: {', '.join(v) if v else 'not flagged'}")
    n_pass = sum(r["explained"] for r in results)
    n_ctrl = sum(not c["false_alarm"] for c in controls)
    ok = n_pass == len(results) and n_ctrl == len(controls)
    n_partial = sum(c["leading"] in CONTROL_FALSE_ALARM for c in controls)
    print("=" * 72 + f"\n{n_pass}/{len(results)} scenario checks explained correctly · "
          f"{n_ctrl}/{len(controls)} controls without false alarms "
          f"(info: {n_partial} lead with a partial non-noise claim) · {report['duration_s']}s")

    if not args.skip_null:
        nulls = []
        for s in [int(x) for x in args.null_seeds.split(",") if x.strip()]:
            nrep = evaluate_null_world(make_store(s, False))
            nrep["seed"] = s
            nulls.append(nrep)
            print(f"null world (seed {s}, no planted effects): {nrep['false_alarms']} false 'supported' claims in "
                  f"{nrep['findings_examined']} findings from {nrep['investigations']} investigations"
                  + (f" {nrep['false_alarms_by_category']}" if nrep["false_alarms"] else "")
                  + f" · {nrep['confident_wrong_primary']} confident wrong leads · {nrep['duration_s']}s")
            for a in nrep["alarms"][:5]:
                print(f"    ! {a['category']} on {a['scope']} ({a['metric']}) via “{a['question'][:50]}”")
        report["null_world"] = nulls
        ok = ok and all(n["false_alarms"] == 0 for n in nulls)
    print()
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
