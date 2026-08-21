"""The paired one-sided test, and its honest limits."""

from __future__ import annotations

import pytest

from meridian.stats.significance import (
    overlapping_slugs,
    paired_regression_p_value,
    unmatched_slugs,
)

pytestmark = pytest.mark.unit


def uniform(slugs: list[str], value: float) -> dict[str, float]:
    return dict.fromkeys(slugs, value)


SEVEN = [f"task-{i}" for i in range(7)]


def test_identical_runs_are_not_significant() -> None:
    scores = uniform(SEVEN, 0.8)
    assert paired_regression_p_value(scores, dict(scores)) == 1.0


def test_an_improvement_is_never_a_regression() -> None:
    assert paired_regression_p_value(uniform(SEVEN, 0.5), uniform(SEVEN, 0.9)) == 1.0


def test_large_uniform_regression_is_significant() -> None:
    """Every task drops. There is no resample that looks fine."""
    p = paired_regression_p_value(uniform(SEVEN, 1.0), uniform(SEVEN, 0.2), iterations=4000)
    assert p == 0.0


def test_the_seeded_regression_is_significant() -> None:
    """Three of seven tasks break — the shipped fixture's actual shape."""
    baseline = dict.fromkeys(SEVEN, 0.4)
    head = dict(baseline)
    for slug in SEVEN[:3]:
        head[slug] = 0.0

    p = paired_regression_p_value(baseline, head, iterations=8000)
    assert p < 0.05, p


def test_a_single_task_regression_in_a_small_suite_is_not_significant() -> None:
    """The honest limit, pinned rather than hidden.

    A paired bootstrap has very little power against a regression confined to a
    small share of tasks: most resamples miss the broken task entirely. With one
    task of three broken, p is around 0.30. That is what "three tasks is not much
    evidence" looks like — not a bug.

    Meridian reports flipped tasks regardless of the verdict, and
    `require_significance` can be turned off for small suites.
    """
    baseline = uniform(["a", "b", "c"], 1.0)
    head = {"a": 0.0, "b": 1.0, "c": 1.0}

    p = paired_regression_p_value(baseline, head, iterations=8000)
    assert 0.2 < p < 0.4, p


def test_the_p_value_is_reproducible_from_its_seed() -> None:
    baseline = uniform(SEVEN, 1.0)
    head = {**baseline, "task-0": 0.0, "task-1": 0.2}
    assert paired_regression_p_value(baseline, head, iterations=2000, seed=3) == (
        paired_regression_p_value(baseline, head, iterations=2000, seed=3)
    )


def test_a_disjoint_task_set_is_refused() -> None:
    """Comparing different task sets produces a number that means nothing."""
    with pytest.raises(ValueError, match="nothing to compare"):
        paired_regression_p_value({"a": 1.0}, {"b": 1.0})


def test_only_overlapping_tasks_are_compared() -> None:
    baseline = {"shared": 1.0, "only-baseline": 1.0}
    head = {"shared": 1.0, "only-head": 0.0}
    assert overlapping_slugs(baseline, head) == ["shared"]


def test_unmatched_tasks_are_reported_so_they_cannot_vanish_quietly() -> None:
    """A silently shrinking task set is how a gate stops working unnoticed."""
    baseline = {"shared": 1.0, "withdrawn": 1.0}
    head = {"shared": 1.0, "brand-new": 1.0}
    assert unmatched_slugs(baseline, head) == (["withdrawn"], ["brand-new"])
