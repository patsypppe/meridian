"""Small-sample intervals, checked against published table values."""

from __future__ import annotations

import math

import pytest

from meridian.stats.intervals import t_interval, t_inv_cdf, wilson_interval

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("df", "expected"),
    [(1, 12.7062), (2, 4.3027), (4, 2.7764), (6, 2.4469), (9, 2.2622), (29, 2.0452)],
)
def test_t_quantile_matches_standard_tables(df: int, expected: float) -> None:
    """Two-sided 95% critical values from any statistics textbook table."""
    assert t_inv_cdf(0.975, df) == pytest.approx(expected, abs=5e-4)


def test_t_quantile_is_symmetric_and_centered() -> None:
    assert t_inv_cdf(0.5, 5) == 0.0
    assert t_inv_cdf(0.025, 5) == pytest.approx(-t_inv_cdf(0.975, 5))


def test_t_quantile_approaches_the_normal_for_large_df() -> None:
    assert t_inv_cdf(0.975, 10_000) == pytest.approx(1.96, abs=2e-3)


@pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.5])
def test_t_quantile_rejects_bad_probabilities(p: float) -> None:
    with pytest.raises(ValueError, match="0 < p < 1"):
        t_inv_cdf(p, 5)


def test_t_interval_matches_a_hand_computation() -> None:
    # mean 3, sample sd sqrt(2.5), se sqrt(0.5), t(0.975, 4) = 2.7764
    lo, hi = t_interval([1, 2, 3, 4, 5])
    half = 2.7764451 * math.sqrt(0.5)
    assert lo == pytest.approx(3 - half, abs=1e-4)
    assert hi == pytest.approx(3 + half, abs=1e-4)


def test_t_interval_is_wider_than_normal_at_small_m() -> None:
    values = [0.0, 0.1, 0.3, 0.2, 0.0, 0.4, 0.1]
    lo, hi = t_interval(values)
    mean = sum(values) / len(values)
    se = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1) / len(values))
    assert (hi - lo) / 2 > 1.96 * se


def test_t_interval_needs_two_values() -> None:
    with pytest.raises(ValueError, match="at least two"):
        t_interval([0.5])


@pytest.mark.parametrize(
    ("successes", "n", "lower", "upper"),
    [(0, 10, 0.0, 0.2775), (5, 10, 0.2366, 0.7634), (10, 10, 0.7225, 1.0), (1, 10, 0.0179, 0.4041)],
)
def test_wilson_matches_known_values(successes: int, n: int, lower: float, upper: float) -> None:
    lo, hi = wilson_interval(successes, n)
    assert lo == pytest.approx(lower, abs=5e-4)
    assert hi == pytest.approx(upper, abs=5e-4)


@pytest.mark.parametrize("successes", [0, 1, 7, 10])
def test_wilson_stays_inside_the_unit_interval(successes: int) -> None:
    lo, hi = wilson_interval(successes, 10)
    assert 0.0 <= lo <= successes / 10 <= hi <= 1.0


def test_wilson_at_zero_successes_is_not_a_point() -> None:
    """The normal interval collapses to [0, 0] here; Wilson does not."""
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and hi > 0.2


def test_wilson_handles_extreme_levels_without_raising() -> None:
    lo, hi = wilson_interval(3, 10, level=1.0 - 1e-16)
    assert 0.0 <= lo <= hi <= 1.0


def test_wilson_validates_inputs() -> None:
    with pytest.raises(ValueError, match="successes"):
        wilson_interval(11, 10)
    with pytest.raises(ValueError, match="level"):
        wilson_interval(1, 10, level=1.0)
    assert wilson_interval(0, 0) == (0.0, 0.0)
