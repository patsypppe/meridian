"""Confidence intervals by cluster bootstrap over **tasks**.

Trials within a task are correlated — same prompt, same environment, same failure
mode. Trials across tasks are not. Resampling trials therefore treats correlated
observations as independent and produces intervals that are far too narrow, which
makes the gate overconfident and eventually wrong in a way nobody can explain.

So the resampling unit is the task. This is the single most common statistical
error in eval tooling, and `test_resamples_tasks_not_trials` pins it by computing
both and asserting the task-level interval is the wider one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

# (n, c, k) for one task.
TaskCounts = tuple[int, int, int]
Statistic = Callable[[int, int, int], float]

DEFAULT_ITERATIONS = 10_000
DEFAULT_ALPHA = 0.05


def per_task_values(per_task: Sequence[TaskCounts], stat_fn: Statistic) -> list[float]:
    """The statistic for each task, with k clamped to the trials that ran.

    Clamping matters: a task that lost trials to harness errors would otherwise
    raise rather than contribute what it can.
    """
    return [stat_fn(n, c, min(k, n)) for n, c, k in per_task if n > 0]


def bootstrap_ci(
    per_task: Sequence[TaskCounts],
    stat_fn: Statistic,
    iterations: int = DEFAULT_ITERATIONS,
    alpha: float = DEFAULT_ALPHA,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile interval for the suite-level statistic, resampling TASKS.

    The seed is recorded in the manifest, so an interval is reproducible rather
    than merely repeatable-ish.
    """
    values = np.array(per_task_values(per_task, stat_fn), dtype=float)
    m = len(values)
    if m == 0:
        raise ValueError("no tasks with gradeable trials; there is nothing to bootstrap")
    if m == 1:
        # One task carries no information about between-task variation. Saying so
        # is better than reporting a zero-width interval that looks certain.
        value = float(values[0])
        return value, value

    rng = np.random.default_rng(seed)
    index = rng.integers(0, m, size=(iterations, m))
    means = values[index].mean(axis=1)
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return lo, hi


def suite_mean(per_task: Sequence[TaskCounts], stat_fn: Statistic) -> float:
    """Unweighted mean across tasks — every task counts once."""
    values = per_task_values(per_task, stat_fn)
    return sum(values) / len(values) if values else 0.0
