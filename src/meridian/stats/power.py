"""How small a regression could this run have seen at all?

Every gate verdict is two claims, and harnesses normally publish only the first:

    PASS — head 0.72 vs baseline 0.75, p=0.31
    ...and this run could not have detected a regression smaller than 14 points.

The second sentence is the one that decides whether the first means anything. A
suite of seven tasks has very little power against a regression confined to two
of them — `significance.py` says so in its own docstring, where nobody reading a
PR comment will ever see it. A PASS from an underpowered run is not evidence that
nothing broke; it is the absence of evidence either way, and a gate that cannot
tell those apart trains people to trust it exactly when it is least reliable.

The estimator is the standard inversion of the paired-difference sample size
formula (Miller, *Adding Error Bars to Evals*, arXiv:2411.00640, §"power
analysis"). For a one-sided paired test over `n` tasks with per-task difference
standard deviation `s`:

    n = (z_alpha + z_beta)^2 * s^2 / delta^2   ⇒   delta = (z_alpha + z_beta) * s / sqrt(n)

`delta` is the **minimum detectable effect**: the smallest true regression this
run would catch, at the stated power, given the variance it actually observed.

Two honesty notes, because an MDE quoted without them is its own kind of lie:

- It is computed from the *observed* spread of per-task differences, so it is an
  estimate with its own uncertainty, and a very small task set estimates `s`
  badly. Below `MIN_TASKS_FOR_POWER` tasks it is not reported at all rather than
  reported precisely.
- Unlike the bootstrap in `significance.py`, this is a normal approximation. It
  is used to *describe* sensitivity, never to decide the verdict — the gate's
  decision stays with the paired bootstrap, which makes no such assumption.

Pure: no I/O, no Docker, per the layout rule in `CLAUDE.md`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import NormalDist

__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_POWER",
    "MIN_TASKS_FOR_POWER",
    "minimum_detectable_effect",
    "paired_differences",
    "required_tasks",
]

DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.80

# Below this, the standard deviation of the differences is estimated from so few
# points that the resulting MDE is noise quoted to three decimals. Saying "this
# suite is too small to answer that" is the more useful output.
MIN_TASKS_FOR_POWER = 3


def paired_differences(baseline: Mapping[str, float], head: Mapping[str, float]) -> list[float]:
    """Per-task head-minus-baseline, over tasks present in both runs."""
    return [head[slug] - baseline[slug] for slug in sorted(set(baseline) & set(head))]


def _z(alpha: float, power: float) -> float:
    """`z_alpha + z_beta` for a one-sided test.

    One-sided because the gate's hypothesis is one-sided: it asks whether head
    regressed, and an improvement is never a regression.
    """
    normal = NormalDist()
    return normal.inv_cdf(1.0 - alpha) + normal.inv_cdf(power)


def _stdev(differences: Sequence[float]) -> float:
    """Sample standard deviation with Bessel's correction.

    Written out rather than taken from `statistics` so the `n - 1` is visible:
    with a handful of tasks the difference between dividing by `n` and by `n - 1`
    is large, and it biases the MDE in the flattering direction.
    """
    n = len(differences)
    mean = sum(differences) / n
    variance = sum((d - mean) ** 2 for d in differences) / (n - 1)
    return math.sqrt(variance)


def minimum_detectable_effect(
    differences: Sequence[float],
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
) -> float | None:
    """The smallest regression this run could have caught, or `None` if unknowable.

    `None` means "this suite cannot answer that question" — too few tasks to
    estimate the spread. It is deliberately not `0.0`, which would read as
    infinite sensitivity: the two mean opposite things and a caller that confuses
    them publishes the opposite of the truth.
    """
    n = len(differences)
    if n < MIN_TASKS_FOR_POWER:
        return None
    spread = _stdev(differences)
    # Not `== 0.0`. `pass^k` values are ratios of binomial coefficients, so tasks
    # that moved by *exactly* the same amount routinely differ in the last ulp and
    # leave a spread around 1e-17 — which sails past an equality check and renders
    # as "regressions smaller than about 0.000 would have gone unnoticed", i.e. a
    # claim of unlimited sensitivity. That is the precise thing this guard exists
    # to prevent, so it is scaled to the size of the differences rather than
    # compared against zero.
    scale = max((abs(d) for d in differences), default=0.0)
    if spread <= 1e-12 * max(scale, 1.0):
        # Every task moved by the same amount. Real when a change lands uniformly,
        # and there is genuinely no spread to estimate sensitivity from.
        return None
    return _z(alpha, power) * spread / math.sqrt(n)


def required_tasks(
    spread: float,
    effect: float,
    *,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_POWER,
) -> int:
    """How many tasks it would take to detect `effect`, at the observed spread.

    The actionable half of an MDE. "This run could not see a regression smaller
    than 14 points" prompts the obvious question, and the answer is a number of
    tasks to write rather than a threshold to loosen.
    """
    if effect <= 0:
        raise ValueError(f"effect must be positive, got {effect}")
    if spread <= 0:
        raise ValueError(f"spread must be positive, got {spread}")
    exact = (_z(alpha, power) * spread / effect) ** 2
    # Rounded before ceiling. Asked for exactly the effect a run *did* detect,
    # the arithmetic lands a few ulps above the whole number it should be, and a
    # bare ceil then answers "write one more task than you already have" — which
    # is wrong, and wrong in the way that makes a person distrust the number.
    return math.ceil(round(exact, 9))
