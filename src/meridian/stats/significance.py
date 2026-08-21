"""Is the regression real, or is it noise?

The gate compares head against a baseline **over the same task set**, so the test
is paired: the unit of analysis is the per-task difference, not the two suite
scores. The null hypothesis is "head is not worse".

One property is worth stating loudly because it decides how the gate is
configured: **a paired bootstrap has very little power against a regression
confined to a small share of tasks.** If one task of three breaks completely, the
p-value is about 0.30 — the bootstrap frequently resamples a set that misses the
broken task entirely. That is not a bug in the test; it is what "three tasks is
not much evidence" actually looks like. Meridian reports flipped tasks regardless
of the verdict, and `require_significance` can be turned off for small suites.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

DEFAULT_ITERATIONS = 10_000

NOT_A_REGRESSION = 1.0


def overlapping_slugs(baseline: Mapping[str, float], head: Mapping[str, float]) -> list[str]:
    return sorted(set(baseline) & set(head))


def unmatched_slugs(
    baseline: Mapping[str, float], head: Mapping[str, float]
) -> tuple[list[str], list[str]]:
    """Tasks present on only one side.

    Returned so the report can name them. A silently shrinking task set is how a
    gate stops working without anyone noticing.
    """
    return (
        sorted(set(baseline) - set(head)),
        sorted(set(head) - set(baseline)),
    )


def paired_regression_p_value(
    baseline: Mapping[str, float],
    head: Mapping[str, float],
    iterations: int = DEFAULT_ITERATIONS,
    seed: int = 0,
) -> float:
    """One-sided p-value for the hypothesis that head regressed against baseline."""
    slugs = overlapping_slugs(baseline, head)
    if not slugs:
        raise ValueError(
            "no tasks appear in both runs; there is nothing to compare, and comparing "
            "different task sets would produce a number that means nothing"
        )

    differences = np.array([head[slug] - baseline[slug] for slug in slugs], dtype=float)
    observed = float(differences.mean())
    if observed >= 0:
        # Head is not worse on average. There is no regression to test for.
        return NOT_A_REGRESSION

    rng = np.random.default_rng(seed)
    index = rng.integers(0, len(differences), size=(iterations, len(differences)))
    resampled = differences[index].mean(axis=1)
    # The fraction of resamples showing no regression: evidence *against* the
    # observed drop being real.
    return float((resampled >= 0).mean())
