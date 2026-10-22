"""Metric computations built on the approved query templates.

Each function takes a DuckDB cursor and a QueryTrace so the exact SQL behind a result
is always recorded alongside it.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

import duckdb
import pandas as pd
from scipy import stats as sps

from app.analytics import queries as Q
from app.analytics.queries import Comparison, Filters, QueryTrace, Window
from app.analytics.stats import (
    binomial_change_point,
    bh_adjust,
    compare_proportions,
    mde_for_sample_size,
    multiple_comparison_note,
    wilson_ci,
)
from app.data.schema import DIMENSIONS

MIN_SEGMENT_N = 30  # below this many denominator sessions per window a segment is not tested
WATCHLIST_P = 0.01
# Two-dimension groupings scanned by Detect: market x everything, plus the pairs where product
# effects typically interact. Keeping the grid deliberate keeps the multiple-testing burden honest.
SCAN_PAIRS = [
    ("market", "device"), ("market", "channel"), ("market", "price_band"), ("market", "property_type"),
    ("market", "intent"), ("market", "visitor_type"), ("device", "channel"), ("device", "visitor_type"),
    ("property_type", "price_band"),
]


def _seg_key(row: pd.Series, dims: list[str]) -> dict[str, str]:
    return {d: row[d] for d in dims}


def _pivot(df: pd.DataFrame, dims: list[str]) -> pd.DataFrame:
    """Turn period rows into one row per segment with baseline_* and current_* columns."""
    if df.empty:
        cols = dims + ["baseline_n", "baseline_x", "current_n", "current_x"]
        return pd.DataFrame(columns=cols)
    idx = dims if dims else None
    if idx:
        wide = df.pivot_table(index=idx, columns="period", values=["denominator", "numerator"],
                              aggfunc="sum", fill_value=0)
    else:
        wide = df.set_index("period")[["denominator", "numerator"]].T.stack().to_frame().T
        wide.columns = pd.MultiIndex.from_tuples([(a, b) for a, b in wide.columns])
    out = pd.DataFrame(index=wide.index)
    for period in ("baseline", "current"):
        out[f"{period}_n"] = wide.get(("denominator", period), 0)
        out[f"{period}_x"] = wide.get(("numerator", period), 0)
    out = out.fillna(0).astype(int).reset_index()
    if not idx:
        out = out.drop(columns=[c for c in out.columns if c == "index"])
    return out


def metric_comparison(cur: duckdb.DuckDBPyConnection, trace: QueryTrace, metric: str, filters: Filters,
                      comparison: Comparison, group_by: list[str] | None = None,
                      name: str = "metric_comparison") -> tuple[pd.DataFrame, str]:
    sql, params = Q.metric_comparison_sql(metric, filters, comparison, group_by)
    df, qid = trace.run(cur, name, sql, params)
    return _pivot(df, list(group_by or [])), qid


def overall_change(cur, trace, metric: str, filters: Filters, comparison: Comparison) -> dict[str, Any]:
    wide, qid = metric_comparison(cur, trace, metric, filters, comparison)
    if wide.empty:
        raise ValueError(f"No sessions match {filters.describe()} in the selected windows")
    r = wide.iloc[0]
    n1, x1, n2, x2 = int(r.baseline_n), int(r.baseline_x), int(r.current_n), int(r.current_x)
    if n1 == 0 or n2 == 0:
        raise ValueError(f"No denominator sessions for {Q.METRICS[metric].label} in one of the windows "
                         f"({filters.describe()})")
    test = compare_proportions(x1, n1, x2, n2)
    mde = mde_for_sample_size(max(min(x1 / n1, 0.98), 0.005), min(n1, n2)) * 100 if min(n1, n2) >= 2 else None
    return {"metric": metric, "filters": filters.active(), "test": test.to_dict(),
            "mde_pp_at_this_n": mde, "query_id": qid}


def stratified_change(strata: pd.DataFrame) -> dict[str, Any] | None:
    """Directly standardised change across strata (e.g. channels) for one segment.

    adj = Σ w_s (r1_s - r0_s) with w_s the average share of stratum s across both windows.
    Test: z = adj / SE0 where SE0 uses each stratum's pooled rate p_s (the null hypothesis of no
    within-stratum change): Var0 = Σ w_s² p_s(1-p_s)(1/n0_s + 1/n1_s).
    Interval: adj ± 1.96 SE1 with the unpooled variance Σ w_s² (r0(1-r0)/n0 + r1(1-r1)/n1).
    Strata empty in either window are dropped and the weights renormalised.
    """
    s = strata[(strata.baseline_n > 0) & (strata.current_n > 0)]
    if s.empty:
        return None
    w = (s.baseline_n / s.baseline_n.sum() + s.current_n / s.current_n.sum()) / 2
    w = w / w.sum()
    r0, r1 = s.baseline_x / s.baseline_n, s.current_x / s.current_n
    adj = float((w * (r1 - r0)).sum())
    ps = (s.baseline_x + s.current_x) / (s.baseline_n + s.current_n)
    se0 = float(((w ** 2) * ps * (1 - ps) * (1 / s.baseline_n + 1 / s.current_n)).sum() ** 0.5)
    se1 = float(((w ** 2) * (r0 * (1 - r0) / s.baseline_n + r1 * (1 - r1) / s.current_n)).sum() ** 0.5)
    z = adj / se0 if se0 > 0 else 0.0
    p = float(2 * (1 - sps.norm.cdf(abs(z)))) if se0 > 0 else 1.0
    return {"adj_change_pp": adj * 100, "adj_se_pp": se1 * 100, "adj_z": z, "adj_p_value": p,
            "adj_ci_pp": ((adj - 1.96 * se1) * 100, (adj + 1.96 * se1) * 100), "strata_used": int(len(s)),
            "strata_dropped": int(len(strata) - len(s))}


def segment_table(cur, trace, metric: str, filters: Filters, comparison: Comparison,
                  group_by: list[str], name: str = "segment_breakdown",
                  adjust_for: str | None = None) -> dict[str, Any]:
    """Per-segment baseline vs current with tests and BH-adjusted q-values.

    With `adjust_for` (e.g. "channel"), each segment also gets a change standardised over that
    dimension, which removes composition shifts in that dimension from the comparison.
    """
    if adjust_for in group_by:
        adjust_for = None
    strata_by_seg: dict[tuple, pd.DataFrame] = {}
    if adjust_for:
        fine, qid = metric_comparison(cur, trace, metric, filters, comparison, group_by + [adjust_for], name=name)
        wide = fine.groupby(group_by, as_index=False)[["baseline_n", "baseline_x", "current_n", "current_x"]].sum()
        for key, sub in fine.groupby(group_by):
            strata_by_seg[key if isinstance(key, tuple) else (key,)] = sub
    else:
        wide, qid = metric_comparison(cur, trace, metric, filters, comparison, group_by, name=name)
    rows = []
    for _, r in wide.iterrows():
        n1, x1, n2, x2 = int(r.baseline_n), int(r.baseline_x), int(r.current_n), int(r.current_x)
        row: dict[str, Any] = {"segment": _seg_key(r, group_by), "baseline_n": n1, "baseline_x": x1,
                               "current_n": n2, "current_x": x2,
                               "baseline_rate": x1 / n1 if n1 else None, "current_rate": x2 / n2 if n2 else None}
        if n1 >= MIN_SEGMENT_N and n2 >= MIN_SEGMENT_N:
            t = compare_proportions(x1, n1, x2, n2)
            row.update(abs_change_pp=t.abs_change_pp, rel_change_pct=t.rel_change_pct, p_value=t.p_value,
                       diff_ci_pp=t.diff_ci_pp, test=t.test, tested=True)
        else:
            row.update(abs_change_pp=((x2 / n2 - x1 / n1) * 100) if n1 and n2 else None, rel_change_pct=None,
                       p_value=None, diff_ci_pp=None, test=None, tested=False)
        if adjust_for and row["tested"]:
            key = tuple(r[d] for d in group_by)
            adj = stratified_change(strata_by_seg[key]) if key in strata_by_seg else None
            row.update(adj or {})
        rows.append(row)
    tested = [r for r in rows if r["tested"]]
    for r, q in zip(tested, bh_adjust([r["p_value"] for r in tested])):
        r["q_value"] = q
    adj_rows = [r for r in tested if r.get("adj_p_value") is not None]
    for r, q in zip(adj_rows, bh_adjust([r["adj_p_value"] for r in adj_rows])):
        r["adj_q_value"] = q
    for r in rows:
        r.setdefault("q_value", None)
        r.setdefault("adj_q_value", None)
        if r.get("adj_change_pp") is not None:
            r["adj_impact_sessions"] = r["adj_change_pp"] / 100 * r["current_n"]
        # Contacts "lost" vs baseline rate at current volume: a scale-aware impact measure.
        if r["baseline_rate"] is not None and r["current_rate"] is not None:
            r["impact_sessions"] = (r["current_rate"] - r["baseline_rate"]) * r["current_n"]
        else:
            r["impact_sessions"] = None
    return {"metric": metric, "group_by": group_by, "adjust_for": adjust_for, "rows": rows, "n_tests": len(tested),
            "multiple_comparison_note": multiple_comparison_note(len(tested)) if len(tested) > 1 else None,
            "min_segment_n": MIN_SEGMENT_N, "query_id": qid}


def kitagawa(wide: pd.DataFrame, dims: list[str]) -> dict[str, Any]:
    """Kitagawa decomposition of the change in an aggregate rate into mix and rate effects.

    total  = Σ w1 r1 - Σ w0 r0
    mix    = Σ (w1 - w0) (r0 + r1)/2       -- change due to segment shares
    rate   = Σ (r1 - r0) (w0 + w1)/2       -- change due to within-segment rates
    mix + rate = total exactly (no interaction term with this symmetric form).
    Segments that are empty in one period contribute their rate as 0 in that period.
    """
    n0, n1 = wide["baseline_n"].sum(), wide["current_n"].sum()
    if n0 == 0 or n1 == 0:
        raise ValueError("decomposition needs denominators in both windows")
    segs = []
    mix = rate = 0.0
    for _, r in wide.iterrows():
        w0, w1 = r.baseline_n / n0, r.current_n / n1
        r0 = r.baseline_x / r.baseline_n if r.baseline_n else 0.0
        r1 = r.current_x / r.current_n if r.current_n else 0.0
        if not r.baseline_n:
            r0 = r1
        if not r.current_n:
            r1 = r0
        m_i = (w1 - w0) * (r0 + r1) / 2
        e_i = (r1 - r0) * (w0 + w1) / 2
        mix += m_i
        rate += e_i
        segs.append({"segment": _seg_key(r, dims), "baseline_share": w0, "current_share": w1,
                     "baseline_rate": r0, "current_rate": r1, "mix_effect_pp": m_i * 100,
                     "rate_effect_pp": e_i * 100})
    total = wide["current_x"].sum() / n1 - wide["baseline_x"].sum() / n0
    return {"dimension": dims, "total_change_pp": total * 100, "mix_effect_pp": mix * 100,
            "rate_effect_pp": rate * 100,
            "mix_share_of_change": (mix / total) if abs(total) > 1e-12 else None,
            "segments": sorted(segs, key=lambda s: abs(s["mix_effect_pp"]) + abs(s["rate_effect_pp"]), reverse=True)}


def decompose(cur, trace, metric: str, filters: Filters, comparison: Comparison, dimension: str) -> dict[str, Any]:
    wide, qid = metric_comparison(cur, trace, metric, filters, comparison, [dimension], name=f"mix_by_{dimension}")
    out = kitagawa(wide, [dimension])
    out["query_id"] = qid
    return out


def standardized_gap(cur, trace, metric: str, filters: Filters, window: Window, group_dim: str,
                     group_a: str, group_b: str | None, confounder: str | list[str]) -> dict[str, Any]:
    """Compare group_a with group_b (or with every other value when group_b is None), raw and
    directly standardised to a common confounder mix.

    The standard population is the pooled confounder mix of both groups. If the gap shrinks a lot
    after standardisation, much of group_a's apparent advantage comes from *who* it attracts
    (selection/composition), not from the group itself. The within-strata gap is tested with the
    same stratified z-test used for period comparisons.
    """
    confs = [confounder] if isinstance(confounder, str) else list(confounder)
    if not confs or group_dim in confs:
        raise ValueError("need at least one confounder, different from the group dimension")
    Q._dims(confs)
    if group_a == group_b:
        raise ValueError("groups must differ")
    m = Q.METRICS[metric]
    label_b = group_b or f"not {group_a}"
    scoped = filters.merged(**{group_dim: [group_a, group_b]}) if group_b else filters.merged(**{group_dim: []})
    fsql, fparams = Q.filter_sql(scoped)
    sql = f"""
SELECT CASE WHEN {group_dim} = ? THEN 'A' ELSE 'B' END AS grp, {", ".join(confs)},
  COUNT(*) FILTER (WHERE {m.denominator}) AS denominator,
  COUNT(*) FILTER (WHERE {m.numerator}) AS numerator
FROM sessions
WHERE session_date BETWEEN ? AND ?{fsql}
GROUP BY ALL
ORDER BY ALL"""
    df, qid = trace.run(cur, f"standardize_{group_dim}_by_{'_'.join(confs)}", sql,
                        [group_a, window.start, window.end, *fparams])
    if df.empty or set(df.grp) != {"A", "B"}:
        raise ValueError("both groups need data for standardisation")
    confounder = "stratum"
    df["stratum"] = df[confs].astype(str).agg(" · ".join, axis=1)
    pooled = df.groupby(confounder)["denominator"].sum()
    weights = pooled / pooled.sum()
    res: dict[str, Any] = {"group_dimension": group_dim, "group_a": group_a, "group_b": label_b,
                           "confounder": " × ".join(confs), "confounders": confs, "groups": {},
                           "standard_population": {k: float(v) for k, v in weights.items()}, "query_id": qid}
    for code, label in (("A", group_a), ("B", label_b)):
        sub = df[df.grp == code].set_index(confounder)
        n, x = int(sub["denominator"].sum()), int(sub["numerator"].sum())
        strata = {}
        std_rate = 0.0
        for c, w in weights.items():
            if c in sub.index and sub.loc[c, "denominator"] > 0:
                rc = sub.loc[c, "numerator"] / sub.loc[c, "denominator"]
                strata[c] = {"n": int(sub.loc[c, "denominator"]), "rate": float(rc),
                             "share": float(sub.loc[c, "denominator"] / n) if n else 0.0}
                std_rate += w * rc
            else:
                strata[c] = {"n": 0, "rate": None, "share": 0.0}
        res["groups"][label] = {"n": n, "x": x, "raw_rate": x / n if n else None,
                                "raw_ci": wilson_ci(x, n) if n else None,
                                "standardized_rate": float(std_rate), "strata": strata}
    a, b = res["groups"][group_a], res["groups"][label_b]
    raw_gap = (a["raw_rate"] or 0) - (b["raw_rate"] or 0)
    std_gap = a["standardized_rate"] - b["standardized_rate"]
    # Within-strata test: treat B as "baseline" and A as "current" in each confounder stratum.
    wide = df.pivot_table(index=confounder, columns="grp", values=["denominator", "numerator"],
                          aggfunc="sum", fill_value=0)
    strata_df = pd.DataFrame({"baseline_n": wide[("denominator", "B")], "baseline_x": wide[("numerator", "B")],
                              "current_n": wide[("denominator", "A")], "current_x": wide[("numerator", "A")]})
    within = stratified_change(strata_df)
    mix_diff = max(abs(a["strata"][c]["share"] - b["strata"][c]["share"]) for c in weights.index)
    res.update(raw_gap_pp=raw_gap * 100, standardized_gap_pp=std_gap * 100,
               share_of_gap_explained_by_mix=(1 - std_gap / raw_gap) if abs(raw_gap) > 1e-12 else None,
               within_strata_test=within, max_share_difference=float(mix_diff))
    return res


def trend(cur, trace, metric: str, filters: Filters, window: Window, grain: str = "day",
          split_by: str | None = None) -> dict[str, Any]:
    sql, params = Q.metric_trend_sql(metric, filters, window, grain, split_by)  # type: ignore[arg-type]
    df, qid = trace.run(cur, "metric_trend", sql, params)
    points = []
    for _, r in df.iterrows():
        n, x = int(r.denominator), int(r.numerator)
        lo, hi = wilson_ci(x, n) if n else (None, None)
        p = {"bucket": pd.Timestamp(r.bucket).date().isoformat(),
             "denominator": n, "numerator": x, "rate": x / n if n else None, "ci_low": lo, "ci_high": hi}
        if split_by:
            p[split_by] = r[split_by]
        points.append(p)
    return {"metric": metric, "grain": grain, "split_by": split_by, "points": points, "query_id": qid}


def change_point(cur, trace, metric: str, filters: Filters, window: Window) -> dict[str, Any] | None:
    t = trend(cur, trace, metric, filters, window, "day")
    pts = [p for p in t["points"]]
    cp = binomial_change_point([p["numerator"] for p in pts], [p["denominator"] for p in pts])
    if cp is None:
        return None
    cp["date"] = pts[cp["index"]]["bucket"]
    cp["query_id"] = t["query_id"]
    cp["n_days"] = len(pts)
    return cp


def funnel(cur, trace, filters: Filters, window: Window) -> dict[str, Any]:
    sql, params = Q.funnel_steps_sql(filters, window)
    df, qid = trace.run(cur, "funnel_steps", sql, params)
    r = df.iloc[0]
    steps = [
        ("search_sessions", "Searched"), ("listing_sessions", "Viewed a listing"),
        ("contact_start_sessions", "Started contact"), ("contact_sessions", "Submitted contact"),
        ("viewing_sessions", "Requested viewing"),
    ]
    out = []
    prev = None
    for key, label in steps:
        v = int(r[key])
        out.append({"key": key, "label": label, "sessions": v,
                    "step_conversion": (v / prev) if prev else None,
                    "drop_off": (1 - v / prev) if prev else None})
        prev = v
    return {"steps": out, "sessions": int(r.sessions), "save_sessions": int(r.save_sessions),
            "qualified_contacts": int(r.qualified_contacts), "query_id": qid,
            "note": "Listing sessions include sessions that landed directly on a listing without searching, "
                    "so 'Viewed a listing' can exceed what search alone would produce."}


def segment_scan(cur, trace, metric: str, filters: Filters, comparison: Comparison,
                 pairs: bool = True, top: int = 15, adjust_for: str | None = "channel") -> dict[str, Any]:
    """Test every single-dimension and (optionally) two-dimension segment; rank by BH q and impact.

    By default each segment's change is standardised over acquisition channel, so segments flagged
    here changed *within* channels rather than merely because their channel mix shifted.
    Groupings that include the adjustment dimension itself are tested unadjusted.
    """
    dims = [d for d in DIMENSIONS if d not in filters.active() or len(filters.active()[d]) > 1]
    groupings = [[d] for d in dims]
    if pairs:
        groupings += [list(p) for p in SCAN_PAIRS if p[0] in dims and p[1] in dims]
    all_rows = []
    for g in groupings:
        tbl = segment_table(cur, trace, metric, filters, comparison, g, name=f"scan_{'_x_'.join(g)}",
                            adjust_for=adjust_for)
        for r in tbl["rows"]:
            if r["tested"]:
                adjusted = r.get("adj_p_value") is not None
                r.update(group_by=g, query_id=tbl["query_id"], scan_adjusted=adjusted,
                         scan_change_pp=r["adj_change_pp"] if adjusted else r["abs_change_pp"],
                         scan_p_value=r["adj_p_value"] if adjusted else r["p_value"])
                r["scan_impact_sessions"] = r["scan_change_pp"] / 100 * r["current_n"]
                all_rows.append(r)
    qs = bh_adjust([r["scan_p_value"] for r in all_rows])
    for r, q in zip(all_rows, qs):
        r["scan_q_value"] = q
    flagged = [r for r in all_rows if r["scan_q_value"] < 0.05]
    flagged.sort(key=lambda r: r["scan_impact_sessions"])  # most negative impact first
    watchlist = sorted((r for r in all_rows if r["scan_q_value"] >= 0.05 and r["scan_p_value"] < WATCHLIST_P),
                       key=lambda r: r["scan_p_value"])
    method = (f"Per-segment change standardised over {adjust_for} (stratified z-test) where possible, otherwise a "
              "two-proportion test (Fisher when expected counts < 10). " if adjust_for else
              "Per-segment two-proportion tests (Fisher when expected counts < 10). ")
    method += (f"Benjamini-Hochberg FDR across all {len(all_rows)} tests; segments with < {MIN_SEGMENT_N} "
               "denominator sessions in either window are not tested.")
    return {"metric": metric, "n_tests": len(all_rows), "n_flagged": len(flagged),
            "multiple_comparison_note": multiple_comparison_note(len(all_rows)),
            "flagged": [r for r in flagged if r["scan_impact_sessions"] < 0][:top],
            "positive": [r for r in sorted(flagged, key=lambda r: -r["scan_impact_sessions"])
                         if r["scan_impact_sessions"] > 0][:5],
            "watchlist": watchlist[:top],
            "watchlist_note": f"Watchlist: unadjusted p < {WATCHLIST_P} but not significant after FDR correction. "
                              "Worth a targeted look, not evidence of a change on its own.",
            "adjust_for": adjust_for, "method": method}


QUALITY_INDICATORS = {
    "raw_vs_validated_gap": "Gap between raw and validated listing→contact rate (pp)",
    "failed_submit_rate": "Share of contact-start sessions with a failed contact_submitted",
    "submit_without_lead_rate": "Share of listing sessions with a contact_submitted lacking lead_id",
    "submit_before_start_rate": "Share of listing sessions with contact_submitted before any contact_started",
}


def _quality_rates(r: pd.Series) -> dict[str, float | None]:
    ls, cs = r.listing_sessions, r.contact_start_sessions
    return {
        "raw_vs_validated_gap": ((r.raw_contact_sessions - r.validated_contact_sessions) / ls * 100) if ls else None,
        "failed_submit_rate": (r.sessions_with_failed_submit / cs) if cs else None,
        "submit_without_lead_rate": (r.sessions_with_submit_without_lead / ls) if ls else None,
        "submit_before_start_rate": (r.sessions_with_submit_before_start / ls) if ls else None,
        "listing_sessions": int(ls), "contact_start_sessions": int(cs),
    }


def quality(cur, trace, filters: Filters, comparison: Comparison, group_by: list[str] | None = None) -> dict[str, Any]:
    sql, params = Q.quality_sql(filters, comparison, group_by)
    df, qid = trace.run(cur, "quality_by_segment", sql, params)
    dims = list(group_by or [])
    out = []
    keys = df[dims].drop_duplicates().to_dict("records") if dims else [{}]
    for key in keys:
        sub = df
        for d, v in key.items():
            sub = sub[sub[d] == v]
        entry: dict[str, Any] = {"segment": key}
        for period in ("baseline", "current"):
            rows = sub[sub.period == period]
            entry[period] = _quality_rates(rows.sum(numeric_only=True)) if not rows.empty else None
        out.append(entry)
    return {"group_by": dims, "segments": out, "indicators": QUALITY_INDICATORS, "query_id": qid}


def quality_warnings(cur, trace, filters: Filters, comparison: Comparison) -> list[dict[str, Any]]:
    """Automatic data-quality warnings for the overview (segment-level anomalies + global hygiene)."""
    warnings: list[dict[str, Any]] = []
    sql, params = Q.quarantine_sql()
    qdf, qid = trace.run(cur, "quarantine_summary", sql, params)
    total_q = int(qdf["events"].sum()) if not qdf.empty else 0
    if total_q:
        warnings.append({"severity": "info", "title": f"{total_q} malformed events quarantined",
                         "detail": "; ".join(f"{r.quarantine_reason}: {r.events}" for r in qdf.itertuples()),
                         "query_id": qid})
    sql, params = Q.duplicate_events_sql(filters, Window(start=comparison.baseline.start, end=comparison.current.end))
    ddf, qid = trace.run(cur, "duplicate_events", sql, params)
    if not ddf.empty:
        dup, tot = int(ddf.duplicate_events.sum()), int(ddf.events.sum())
        if dup:
            warnings.append({"severity": "info", "title": f"{dup:,} duplicate events removed ({dup / tot:.1%})",
                             "detail": "Same session, event type, listing and lead within 5 seconds; the first "
                                       "firing is kept. Session-level metrics are unaffected by duplicates.",
                             "query_id": qid})
    seg = quality(cur, trace, filters, comparison, ["market", "device"])
    for s in seg["segments"]:
        b, c = s["baseline"], s["current"]
        if not c or c["listing_sessions"] < MIN_SEGMENT_N:
            continue
        label = " / ".join(s["segment"].values())
        lead_c = c["submit_without_lead_rate"] or 0
        lead_b = (b or {}).get("submit_without_lead_rate") or 0
        if lead_c > 0.03 and lead_c > 3 * max(lead_b, 0.005):
            gap_c = c["raw_vs_validated_gap"] or 0
            warnings.append({"severity": "high", "title": f"Contact events without a lead_id in {label}",
                             "detail": f"{lead_c:.1%} of listing sessions have a contact_submitted with no lead_id "
                                       f"(baseline {lead_b:.1%}); raw listing→contact is {gap_c:.1f}pp above the "
                                       "validated rate. This pattern points to tracking, not user behaviour.",
                             "segment": s["segment"], "query_id": seg["query_id"], "kind": "tracking",
                             "affected_sessions": round(lead_c * c["listing_sessions"])})
        fc, fb = c["failed_submit_rate"] or 0, (b or {}).get("failed_submit_rate") or 0
        if c["contact_start_sessions"] >= MIN_SEGMENT_N and fc > 0.08 and fc > 2.5 * max(fb, 0.01):
            warnings.append({"severity": "high", "title": f"Failed contact submissions spiked in {label}",
                             "detail": f"{fc:.1%} of contact-start sessions hit a failed submit (baseline {fb:.1%}).",
                             "segment": s["segment"], "query_id": seg["query_id"], "kind": "failed_submit",
                             "affected_sessions": round(fc * c["contact_start_sessions"])})
    warnings.sort(key=lambda w: (w["severity"] != "high", -w.get("affected_sessions", 0)))
    return warnings
