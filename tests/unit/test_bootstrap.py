"""Cluster bootstrap over tasks."""

from __future__ import annotations

import numpy as np
import pytest

from meridian.stats.bootstrap import bootstrap_ci, per_task_values, suite_mean
from meridian.stats.passk import pass_hat_k

pytestmark = pytest.mark.unit


def naive_trial_level_ci(
    per_task: list[tuple[int, int, int]],
    iterations: int = 10_000,
    seed: int = 0,
) -> tuple[float, float]:
    """The wrong implementation, written here so the test can compare against it.

    Pools every trial and resamples individual trials, treating correlated
    observations as independent.
    """
    trials = [1.0] * sum(c for _n, c, _k in per_task)
    trials += [0.0] * sum(n - c for n, c, _k in per_task)
    values = np.array(trials, dtype=float)
    rng = np.random.default_rng(seed)
    index = rng.integers(0, len(values), size=(iterations, len(values)))
    means = values[index].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def test_resamples_tasks_not_trials() -> None:
    """Task-level resampling must give the wider interval.

    Trials within a task are correlated; pooling them and resampling individually
    treats correlated observations as independent and produces an interval that
    is far too narrow. A gate built on the narrow one is overconfident and
    eventually wrong in a way nobody can explain.
    """
    # Tasks that disagree sharply with each other — the between-task variation a
    # trial-level bootstrap is blind to.
    per_task = [(10, 10, 3), (10, 10, 3), (10, 0, 3), (10, 0, 3), (10, 10, 3)]

    task_lo, task_hi = bootstrap_ci(per_task, pass_hat_k, iterations=4000)
    trial_lo, trial_hi = naive_trial_level_ci(per_task, iterations=4000)

    task_width = task_hi - task_lo
    trial_width = trial_hi - trial_lo
    assert task_width > 2 * trial_width, (
        f"task-level width {task_width:.3f} is not meaningfully wider than trial-level "
        f"{trial_width:.3f}; the clustering is not being respected"
    )
    # Stated absolutely too, so the test still means something if both widths
    # move: a trial-level interval on this data claims the suite sits within
    # roughly +/-0.15 of 0.6, when in truth three tasks are at 1.0 and two at 0.0.
    assert trial_width < 0.35
    assert task_width > 0.7


def test_the_interval_contains_the_point_estimate() -> None:
    per_task = [(5, 4, 3), (5, 5, 3), (5, 3, 3), (5, 5, 3)]
    lo, hi = bootstrap_ci(per_task, pass_hat_k, iterations=4000)
    mean = suite_mean(per_task, pass_hat_k)
    assert lo <= mean <= hi


def test_the_interval_is_reproducible_from_its_seed() -> None:
    """The seed goes in the manifest, so an interval is reproducible."""
    per_task = [(5, 4, 3), (5, 2, 3), (5, 5, 3)]
    assert bootstrap_ci(per_task, pass_hat_k, iterations=2000, seed=7) == bootstrap_ci(
        per_task, pass_hat_k, iterations=2000, seed=7
    )


def test_different_seeds_move_the_interval() -> None:
    # Six tasks rather than three: with three, the 2.5th and 97.5th percentiles
    # land on the extreme resamples for any seed and the intervals coincide.
    per_task = [(5, 4, 3), (5, 2, 3), (5, 5, 3), (5, 3, 3), (5, 5, 3), (5, 1, 3)]
    assert bootstrap_ci(per_task, pass_hat_k, iterations=2000, seed=1) != bootstrap_ci(
        per_task, pass_hat_k, iterations=2000, seed=2
    )


def test_a_unanimous_suite_has_a_degenerate_interval() -> None:
    lo, hi = bootstrap_ci([(5, 5, 3)] * 4, pass_hat_k, iterations=1000)
    assert (lo, hi) == (1.0, 1.0)


def test_one_task_reports_its_value_rather_than_false_certainty() -> None:
    """One task carries no information about between-task variation."""
    lo, hi = bootstrap_ci([(5, 4, 3)], pass_hat_k, iterations=1000)
    assert lo == hi == pytest.approx(0.4)


def test_no_tasks_is_an_error_not_an_empty_interval() -> None:
    with pytest.raises(ValueError, match="nothing to bootstrap"):
        bootstrap_ci([], pass_hat_k)


def test_tasks_with_no_gradeable_trials_are_dropped() -> None:
    """A task Meridian could not run is not evidence about the agent."""
    assert per_task_values([(0, 0, 3), (5, 5, 3)], pass_hat_k) == [1.0]
