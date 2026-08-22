"""Minimum detectable effect — how small a regression the gate could have seen.

The property that matters most here is the round trip: an MDE and a required task
count are the same equation read in opposite directions, so if they disagree one
of them is wrong and a PR comment is quoting a number nobody checked.
"""

from __future__ import annotations

import math

import pytest

from meridian.stats.power import (
    MIN_TASKS_FOR_POWER,
    minimum_detectable_effect,
    paired_differences,
    required_tasks,
)

pytestmark = pytest.mark.unit

# One-sided, alpha=0.05, power=0.80.
Z_SUM = 1.6448536269514722 + 0.8416212335729143


def test_it_matches_the_closed_form() -> None:
    """delta = (z_alpha + z_beta) * s / sqrt(n), computed by hand."""
    differences = [-0.1, -0.2, -0.3, -0.4]
    # Sample stdev of the four values, Bessel-corrected.
    mean = sum(differences) / 4
    spread = math.sqrt(sum((d - mean) ** 2 for d in differences) / 3)
    expected = Z_SUM * spread / math.sqrt(4)

    result = minimum_detectable_effect(differences)
    assert result is not None
    assert result == pytest.approx(expected)


def test_more_tasks_detect_smaller_regressions() -> None:
    """The whole argument for growing a suite, as an assertion."""
    small = minimum_detectable_effect([-0.1, 0.0, 0.1] * 2)
    large = minimum_detectable_effect([-0.1, 0.0, 0.1] * 20)
    assert small is not None and large is not None
    assert large < small


def test_noisier_tasks_detect_only_larger_regressions() -> None:
    tight = minimum_detectable_effect([-0.01, 0.0, 0.01, 0.0, -0.01])
    noisy = minimum_detectable_effect([-0.9, 0.0, 0.9, 0.0, -0.9])
    assert tight is not None and noisy is not None
    assert noisy > tight


def test_demanding_more_power_demands_a_larger_effect() -> None:
    differences = [-0.1, 0.0, 0.1, -0.2, 0.05]
    at_80 = minimum_detectable_effect(differences, power=0.80)
    at_95 = minimum_detectable_effect(differences, power=0.95)
    assert at_80 is not None and at_95 is not None
    assert at_95 > at_80


# -- the two ways of not knowing ---------------------------------------------


@pytest.mark.parametrize("n", range(MIN_TASKS_FOR_POWER))
def test_too_few_tasks_reports_nothing_rather_than_a_number(n: int) -> None:
    """`None`, never 0.0 — those read as opposite claims about sensitivity."""
    assert minimum_detectable_effect([0.1] * n) is None


def test_identical_movement_everywhere_reports_nothing() -> None:
    """Zero spread would divide out to 0.0, which reads as infinite sensitivity."""
    assert minimum_detectable_effect([-0.2, -0.2, -0.2, -0.2]) is None


# -- the round trip ----------------------------------------------------------


def test_the_mde_and_the_required_task_count_are_the_same_equation() -> None:
    """Detecting exactly the MDE should require exactly the tasks you have."""
    differences = [-0.3, 0.1, 0.0, -0.15, 0.25, -0.05, 0.2]
    mde = minimum_detectable_effect(differences)
    assert mde is not None

    mean = sum(differences) / len(differences)
    spread = math.sqrt(sum((d - mean) ** 2 for d in differences) / (len(differences) - 1))

    # ceil, so it lands on the task count itself rather than one below it.
    assert required_tasks(spread, mde) == len(differences)


def test_halving_the_effect_quadruples_the_tasks() -> None:
    """Sample size goes as 1/delta^2; a linear intuition under-orders by 4x."""
    assert required_tasks(0.3, 0.05) == pytest.approx(required_tasks(0.3, 0.1) * 4, rel=0.02)


@pytest.mark.parametrize(("spread", "effect"), [(0.3, 0.0), (0.3, -0.1), (0.0, 0.1), (-1.0, 0.1)])
def test_required_tasks_refuses_impossible_inputs(spread: float, effect: float) -> None:
    with pytest.raises(ValueError):
        required_tasks(spread, effect)


# -- pairing -----------------------------------------------------------------


def test_differences_are_taken_only_over_tasks_in_both_runs() -> None:
    """A task on one side only has no pair, and pairing it with nothing is a lie."""
    baseline = {"a": 0.5, "b": 0.5, "gone": 0.9}
    head = {"a": 0.4, "b": 0.6, "new": 0.1}
    assert paired_differences(baseline, head) == pytest.approx([-0.1, 0.1])


def test_meridians_own_seven_task_suite_cannot_resolve_its_known_miss() -> None:
    """The published 4/5 true-positive result, explained as a number.

    The seeded rounding bug moves two of seven tasks and leaves five untouched.
    That spread over seven tasks puts the MDE well above the 0.114 drop it
    produced — which is precisely why the gate returned pass_with_warning, and
    why the fix is more tasks rather than a looser threshold.
    """
    # Five tasks unmoved, two dropping hard: the shape of that regression.
    differences = [0.0, 0.0, 0.0, 0.0, 0.0, -0.4, -0.4]
    mde = minimum_detectable_effect(differences)
    assert mde is not None
    assert mde > 0.114, (
        f"MDE {mde:.3f} — if this ever drops below the observed 0.114 the "
        f"published explanation for the miss no longer holds"
    )


def test_a_last_ulp_spread_is_not_mistaken_for_real_sensitivity() -> None:
    """`pass^k` values are ratios of binomials, so equal moves differ in the last bit.

    An exact `== 0.0` check let a spread of ~1e-17 through, and the note then
    claimed regressions smaller than 0.000 would be missed — i.e. unlimited
    sensitivity, the exact claim this guard exists to prevent.
    """
    baseline = {"t1": 0.2857142857142857, "t2": 0.7142857142857143, "t3": 0.2857142857142857}
    head = {"t1": 0.047619047619047616, "t2": 0.47619047619047616, "t3": 0.047619047619047616}
    differences = paired_differences(baseline, head)

    assert len(set(differences)) > 1, "the differences must genuinely differ in the last ulp"
    assert minimum_detectable_effect(differences) is None


def test_a_genuinely_small_but_real_spread_is_still_estimated() -> None:
    """The epsilon must not swallow a real, if tiny, difference between tasks."""
    mde = minimum_detectable_effect([-0.10, -0.11, -0.12, -0.13])
    assert mde is not None and mde > 0
