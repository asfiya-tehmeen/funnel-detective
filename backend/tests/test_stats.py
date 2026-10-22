import math

import pytest
from scipy import stats as sps

from app.analytics.stats import (
    bh_adjust, binomial_change_point, compare_proportions, holm_adjust, mde_for_sample_size, newcombe_diff_ci,
    power_for, sample_size_two_proportions, wilson_ci,
)


def test_wilson_ci_known_value():
    # Reference: Wilson interval for 10/100 at 95% is (0.0552, 0.1744).
    lo, hi = wilson_ci(10, 100)
    assert lo == pytest.approx(0.0552, abs=1e-4)
    assert hi == pytest.approx(0.1744, abs=1e-4)


def test_wilson_ci_edges():
    lo, hi = wilson_ci(0, 50)
    assert lo == 0.0 and 0 < hi < 0.1
    lo, hi = wilson_ci(50, 50)
    assert hi == 1.0 and 0.9 < lo < 1
    assert wilson_ci(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson_ci(11, 10)


def test_two_proportion_z_matches_manual():
    t = compare_proportions(100, 1000, 130, 1000)
    p = (100 + 130) / 2000
    se = math.sqrt(p * (1 - p) * (2 / 1000))
    z = (0.13 - 0.10) / se
    assert t.statistic == pytest.approx(z)
    assert t.p_value == pytest.approx(2 * (1 - sps.norm.cdf(abs(z))))
    assert t.abs_change_pp == pytest.approx(3.0)
    assert t.rel_change_pct == pytest.approx(30.0)
    assert t.test.startswith("two-proportion z-test")
    assert t.assumptions_met
    lo, hi = t.diff_ci_pp
    assert lo < 3.0 < hi


def test_small_counts_use_fisher():
    t = compare_proportions(1, 20, 6, 20)
    assert t.test == "Fisher's exact test"
    assert not t.assumptions_met
    _, p = sps.fisher_exact([[1, 19], [6, 14]])
    assert t.p_value == pytest.approx(p)


def test_compare_rejects_bad_input():
    with pytest.raises(ValueError):
        compare_proportions(5, 0, 1, 10)
    with pytest.raises(ValueError):
        compare_proportions(11, 10, 1, 10)


def test_newcombe_contains_point_estimate():
    lo, hi = newcombe_diff_ci(50, 500, 80, 500)
    assert lo < 0.06 < hi


def test_holm_and_bh():
    p = [0.01, 0.04, 0.03, 0.005]
    holm = holm_adjust(p)
    assert holm == pytest.approx([0.03, 0.06, 0.06, 0.02])
    bh = bh_adjust(p)
    assert bh == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert bh_adjust([]) == []
    # Adjusted values never below raw p-values.
    assert all(a >= r for a, r in zip(bh, p))


def test_sample_size_known_value():
    # Classic case: p0=10%, p1=12%, α=0.05 two-sided, 80% power -> ~3,841 per arm.
    n = sample_size_two_proportions(0.10, 0.02)
    assert 3800 <= n <= 3900
    # Halving the MDE roughly quadruples n.
    assert sample_size_two_proportions(0.10, 0.01) == pytest.approx(4 * n, rel=0.08)


def test_sample_size_validation():
    for args in [(0, 0.01), (1, 0.01), (0.1, 0), (0.95, 0.1)]:
        with pytest.raises(ValueError):
            sample_size_two_proportions(*args)


def test_mde_is_inverse_of_sample_size():
    n = sample_size_two_proportions(0.08, 0.01)
    assert mde_for_sample_size(0.08, n) == pytest.approx(0.01, rel=0.02)
    assert power_for(0.08, 0.09, n) == pytest.approx(0.8, abs=0.02)


def test_change_point_finds_break():
    xs = [10] * 20 + [5] * 20
    ns = [100] * 40
    cp = binomial_change_point(xs, ns)
    assert cp["index"] == 20
    assert cp["rate_before"] == pytest.approx(0.10)
    assert cp["rate_after"] == pytest.approx(0.05)
    flat = binomial_change_point([10] * 40, [100] * 40)
    assert flat["llr"] == pytest.approx(0.0, abs=1e-9)
    assert binomial_change_point([1, 2], [10, 10]) is None


def test_p_value_note_present():
    d = compare_proportions(10, 100, 20, 100).to_dict()
    assert "not the probability that the hypothesis is true" in d["p_value_note"]
