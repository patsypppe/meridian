"""pass@k and pass^k.

Both are combinatorial estimators over sampling **without replacement** from the
n trials actually run. Neither is `(c/n)**k`, which is a different estimator that
would disagree with the published definition and make the README indefensible.

    pass@k = 1 - C(n-c, k) / C(n, k)     at least one of k sampled trials passes
    pass^k = C(c, k)     / C(n, k)       all k sampled trials pass

The row worth internalising: an agent passing 4 of 5 trials has pass^3 = 0.4.
Eighty percent looks acceptable; a 40% chance that three consecutive attempts all
work does not.
"""

from __future__ import annotations

from math import comb


def _validate(n: int, c: int, k: int) -> None:
    if not (0 <= c <= n) or not (1 <= k <= n):
        raise ValueError(f"invalid n={n} c={c} k={k}")


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that at least one of k trials drawn without replacement passes."""
    _validate(n, c, k)
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Probability that ALL k trials drawn without replacement pass.

    This is the number a business can plan against: it answers "if a user runs
    this task k times, will it work every time?", which a mean score cannot.
    """
    _validate(n, c, k)
    if c < k:
        return 0.0
    return comb(c, k) / comb(n, k)


def suite_pass_hat_k(per_task: list[tuple[int, int]], k: int) -> float:
    """Unweighted mean of per-task pass^k.

    Unweighted on purpose. Weighting by trial count lets a task that happened to
    run more trials silently dominate the suite score.
    """
    if not per_task:
        return 0.0
    return sum(pass_hat_k(n, c, min(k, n)) for n, c in per_task) / len(per_task)
