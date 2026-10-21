"""Variance of a task's pass rate across runs.

Sample variance (n-1), not population variance. Three observations is the
smallest history the quarantine rule accepts, and at that size the population
estimator understates the spread: a task that alternates 0.0 / 1.0 / 0.0 has a
population variance of 0.222 but a sample variance of 0.333, and the second is
the honest statement about how little three points can pin down.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["pass_rate_variance"]


def pass_rate_variance(rates: Sequence[float]) -> float:
    """Unbiased sample variance of per-run pass rates. Needs at least two."""
    if len(rates) < 2:
        raise ValueError(f"variance needs at least two observations, got {len(rates)}")
    mean = sum(rates) / len(rates)
    return sum((r - mean) ** 2 for r in rates) / (len(rates) - 1)
