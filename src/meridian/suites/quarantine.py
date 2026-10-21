"""Flaky-task quarantine (MD-CAP-05).

A task whose pass rate swings from run to run carries no signal about the agent
and a lot of noise about the suite. Quarantine takes it out of the *aggregate*;
it never hides it. The CLI names every quarantined task beneath the table,
because a task silently dropped from the aggregate is how a suite starts
reporting a better number than reality.

Pure: it is handed rates, never a database or a directory. Reading history is
the orchestrator's job.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.models.run import RunResult
from meridian.stats.variance import pass_rate_variance

__all__ = [
    "DEFAULT_MIN_RUNS",
    "DEFAULT_THRESHOLD",
    "pass_rates_by_task",
    "quarantined_slugs",
    "should_quarantine",
]

DEFAULT_THRESHOLD = 0.25
DEFAULT_MIN_RUNS = 3


def should_quarantine(
    rates: Sequence[float],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_runs: int = DEFAULT_MIN_RUNS,
) -> bool:
    """True when the last `min_runs` rates vary by more than `threshold`.

    Fewer than `min_runs` observations never quarantine, however wild: a task is
    not condemned on thin evidence.
    """
    if len(rates) < min_runs:
        return False
    return pass_rate_variance(rates[-min_runs:]) > threshold


def pass_rates_by_task(runs: Sequence[RunResult]) -> dict[str, list[float]]:
    """Per-task pass rate for each run, oldest first.

    A run in which a task produced no gradeable trial contributes nothing for it:
    a harness error is not a pass rate of zero.
    """
    rates: dict[str, list[float]] = {}
    for run in runs:
        for task in run.tasks:
            if task.n > 0:
                rates.setdefault(task.task_slug, []).append(task.c / task.n)
    return rates


def quarantined_slugs(
    history: Sequence[RunResult],
    current: Sequence[RunResult] = (),
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_runs: int = DEFAULT_MIN_RUNS,
) -> tuple[str, ...]:
    """Tasks to quarantine, judged over prior runs followed by the current one."""
    by_task: Mapping[str, list[float]] = pass_rates_by_task([*history, *current])
    return tuple(
        sorted(
            slug
            for slug, rates in by_task.items()
            if should_quarantine(rates, threshold=threshold, min_runs=min_runs)
        )
    )
