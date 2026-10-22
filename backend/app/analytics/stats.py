"""Deterministic statistical functions. No LLM ever computes a number shown in the product.

Conventions
-----------
* Rates are proportions in [0, 1]; "pp" means percentage points (difference x 100).
* Tests are two-sided unless stated otherwise.
* A p-value is P(data at least this extreme | no true difference, model assumptions hold).
  It is NOT the probability that a hypothesis is true; every result carries that note.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from scipy import stats as sps

P_VALUE_NOTE = (
    "A p-value is the probability of seeing a difference at least this large if there were no true "
    "difference and the test assumptions hold. It is not the probability that the hypothesis is true."
)


def _z(alpha: float, two_sided: bool = True) -> float:
    return float(sps.norm.ppf(1 - alpha / 2)) if two_sided else float(sps.norm.ppf(1 - alpha))


def wilson_ci(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (well-behaved for small n and p near 0/1)."""
    if n <= 0:
        return (0.0, 1.0)
    if x < 0 or x > n:
        raise ValueError("x must be between 0 and n")
    z = _z(alpha)
    p = x / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if x == 0 else max(0.0, centre - half)
    hi = 1.0 if x == n else min(1.0, centre + half)
    return (lo, hi)


def newcombe_diff_ci(x1: int, n1: int, x2: int, n2: int, alpha: float = 0.05) -> tuple[float, float]:
    """Newcombe hybrid score interval for p2 - p1 (method 10), built from Wilson intervals."""
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson_ci(x1, n1, alpha)
    l2, u2 = wilson_ci(x2, n2, alpha)
    d = p2 - p1
    lower = d - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2)
    upper = d + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2)
    return (lower, upper)


@dataclass
class ProportionTest:
    baseline_rate: float
    current_rate: float
    baseline_n: int
    current_n: int
    baseline_x: int
    current_x: int
    abs_change_pp: float
    rel_change_pct: float | None
    diff_ci_pp: tuple[float, float]
    baseline_ci: tuple[float, float]
    current_ci: tuple[float, float]
    test: str
    statistic: float | None
    p_value: float
    assumptions_met: bool
    assumption_note: str
    alpha: float

    @property
    def significant(self) -> bool:
        return self.p_value < self.alpha

    def to_dict(self) -> dict:
        d = asdict(self)
        d["significant"] = self.significant
        d["p_value_note"] = P_VALUE_NOTE
        return d


def compare_proportions(x1: int, n1: int, x2: int, n2: int, alpha: float = 0.05) -> ProportionTest:
    """Compare baseline (x1/n1) with current (x2/n2).

    Uses the pooled two-proportion z-test when every expected cell count is >= 10
    (normal approximation reasonable); otherwise Fisher's exact test.
    """
    if n1 <= 0 or n2 <= 0:
        raise ValueError("Both groups need at least one observation")
    for x, n in ((x1, n1), (x2, n2)):
        if x < 0 or x > n:
            raise ValueError("x must be between 0 and n")
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    expected = [n1 * pooled, n1 * (1 - pooled), n2 * pooled, n2 * (1 - pooled)]
    ok = min(expected) >= 10
    if ok:
        se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
        z = (p2 - p1) / se if se > 0 else 0.0
        p = float(2 * (1 - sps.norm.cdf(abs(z))))
        test, stat = "two-proportion z-test (pooled)", float(z)
        note = "All expected cell counts >= 10, so the normal approximation is reasonable."
    else:
        _, p = sps.fisher_exact([[x1, n1 - x1], [x2, n2 - x2]])
        test, stat = "Fisher's exact test", None
        note = (f"Smallest expected cell count is {min(expected):.1f} (< 10); used Fisher's exact test "
                "instead of the z-test.")
    lo, hi = newcombe_diff_ci(x1, n1, x2, n2, alpha)
    return ProportionTest(
        baseline_rate=p1, current_rate=p2, baseline_n=n1, current_n=n2, baseline_x=x1, current_x=x2,
        abs_change_pp=(p2 - p1) * 100,
        rel_change_pct=((p2 - p1) / p1 * 100) if p1 > 0 else None,
        diff_ci_pp=(lo * 100, hi * 100),
        baseline_ci=wilson_ci(x1, n1, alpha), current_ci=wilson_ci(x2, n2, alpha),
        test=test, statistic=stat, p_value=float(p), assumptions_met=ok, assumption_note=note, alpha=alpha,
    )


def holm_adjust(pvalues: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values (controls family-wise error rate)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def bh_adjust(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values / q-values (controls false discovery rate)."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i], reverse=True)
    adjusted = [0.0] * m
    running = 1.0
    for idx, i in enumerate(order):
        rank = m - idx
        running = min(running, pvalues[i] * m / rank)
        adjusted[i] = min(1.0, running)
    return adjusted


def multiple_comparison_note(m: int, alpha: float = 0.05) -> str:
    expected_fp = m * alpha
    return (f"{m} segment comparisons were tested. Even with no real changes, about {expected_fp:.1f} would "
            f"look 'significant' at alpha={alpha} by chance, so the Benjamini-Hochberg q-value (FDR) is used "
            "to rank segments and raw p-values should not be read in isolation.")


def sample_size_two_proportions(p0: float, mde_abs: float, alpha: float = 0.05, power: float = 0.8,
                                two_sided: bool = True) -> int:
    """Per-arm sample size to detect an absolute difference `mde_abs` from baseline p0.

    n = (z_a * sqrt(2 p̄ q̄) + z_b * sqrt(p0 q0 + p1 q1))^2 / (p1 - p0)^2
    """
    if not 0 < p0 < 1:
        raise ValueError("baseline rate must be between 0 and 1 (exclusive)")
    if mde_abs == 0:
        raise ValueError("minimum detectable effect must be non-zero")
    p1 = p0 + mde_abs
    if not 0 < p1 < 1:
        raise ValueError("baseline + MDE must stay between 0 and 1")
    if not (0 < alpha < 1 and 0 < power < 1):
        raise ValueError("alpha and power must be between 0 and 1")
    za, zb = _z(alpha, two_sided), float(sps.norm.ppf(power))
    pbar = (p0 + p1) / 2
    num = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p0 * (1 - p0) + p1 * (1 - p1))) ** 2
    return int(math.ceil(num / (p1 - p0) ** 2))


def mde_for_sample_size(p0: float, n_per_arm: int, alpha: float = 0.05, power: float = 0.8,
                        two_sided: bool = True) -> float:
    """Smallest absolute uplift detectable with n_per_arm (solved numerically, positive direction)."""
    if n_per_arm < 2:
        raise ValueError("need at least 2 observations per arm")
    lo, hi = 1e-6, 1 - p0 - 1e-6
    if sample_size_two_proportions(p0, hi, alpha, power, two_sided) > n_per_arm:
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2
        if sample_size_two_proportions(p0, mid, alpha, power, two_sided) > n_per_arm:
            lo = mid
        else:
            hi = mid
    return hi


def power_for(p0: float, p1: float, n_per_arm: int, alpha: float = 0.05) -> float:
    """Approximate power of a two-sided two-proportion z-test."""
    if n_per_arm <= 0:
        return 0.0
    za = _z(alpha)
    pbar = (p0 + p1) / 2
    se0 = math.sqrt(2 * pbar * (1 - pbar) / n_per_arm)
    se1 = math.sqrt((p0 * (1 - p0) + p1 * (1 - p1)) / n_per_arm)
    if se1 == 0:
        return 1.0
    d = abs(p1 - p0)
    return float(sps.norm.cdf((d - za * se0) / se1))


def binomial_change_point(xs: list[int], ns: list[int], min_segment: int = 5) -> dict | None:
    """Single change point in a daily binomial series via maximum likelihood-ratio split.

    Returns the split index (first day of the 'after' regime), rates before/after and the
    log-likelihood ratio. The LLR is a maximum over candidate splits, so the chi-square
    approximation overstates significance; we report it as a strength-of-break indicator only.
    """
    def ll(x: int, n: int) -> float:
        if n == 0 or x == 0 or x == n:
            return 0.0
        p = x / n
        return x * math.log(p) + (n - x) * math.log(1 - p)

    k = len(xs)
    if k < 2 * min_segment or sum(ns) == 0:
        return None
    total = ll(sum(xs), sum(ns))
    best = None
    for split in range(min_segment, k - min_segment + 1):
        xb, nb = sum(xs[:split]), sum(ns[:split])
        xa, na = sum(xs[split:]), sum(ns[split:])
        if nb == 0 or na == 0:
            continue
        llr = ll(xb, nb) + ll(xa, na) - total
        if best is None or llr > best["llr"]:
            best = {"index": split, "llr": llr, "rate_before": xb / nb, "rate_after": xa / na,
                    "n_before": nb, "n_after": na}
    return best
