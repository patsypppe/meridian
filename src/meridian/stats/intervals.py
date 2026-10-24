"""Confidence intervals that stay honest at small sample sizes.

Meridian suites are small: with a handful of tasks, an interval from a
percentile bootstrap undercovers and a normal approximation is too narrow. Two
intervals that behave at small m:

- `t_interval`: Student-t over per-task scores, with `tasks - 1` degrees of
  freedom. Wider than the bootstrap at small m, which is the honest direction.
- `wilson_interval`: Wilson score interval for a binomial proportion. Always
  within [0, 1] and well calibrated near 0 and 1, where the normal interval
  collapses to a point.

Pure arithmetic: no I/O, no third-party dependencies.

Provenance. `t_inv_cdf`, `_reg_inc_beta` and `_beta_continued_fraction` are
adapted from Inspect (https://github.com/UKGovernmentBEIS/inspect_ai),
`src/inspect_ai/scorer/_metrics/std.py` at commit
7c2dcccbebc6747f310a70eeba1fa4063fe060db, Copyright (c) 2024 UK AI Security
Institute, MIT License (full text in THIRD_PARTY_NOTICES.md). The Wilson
formula follows Inspect's `ci_wilson`.

Changes from the originals: renamed `_t_inv_cdf` to the public `t_inv_cdf`;
added type annotations to satisfy `mypy --strict`; `ci_wilson`'s metric-framework
plumbing (Inspect's `SampleScore`, `ValueToFloat`, cluster metadata) is replaced
by plain functions over numbers, and its clustered (Korn-Graubard) variant is not
ported; the bound is returned as exactly 0 (no successes) or exactly 1 (all
successes), where the arithmetic would otherwise leave float noise of about 1e-17.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist


def t_inv_cdf(p: float, df: int) -> float:
    """Student-t inverse CDF, dependency-free.

    Exact to bisection precision via the regularized incomplete beta
    function (for t >= 0, `F(t) = 1 - I_x(df/2, 1/2) / 2` with
    `x = df / (df + t^2)`), rather than a Cornish-Fisher-style series --
    the series is least accurate at exactly the small `df` this exists
    to serve. Called once per metric evaluation, so speed is irrelevant.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"t quantile requires 0 < p < 1, got {p}")
    if df < 1:
        raise ValueError(f"t quantile requires df >= 1, got {df}")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_inv_cdf(1.0 - p, df)

    def cdf(t: float) -> float:
        x = df / (df + t * t)
        return 1.0 - 0.5 * _reg_inc_beta(df / 2.0, 0.5, x)

    # bracket the quantile, then bisect
    hi = 1.0
    while cdf(hi) < p:
        hi *= 2.0
    lo = 0.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _reg_inc_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b) (Numerical Recipes 6.4)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(ln_front)
    # continued fraction converges fast for x < (a + 1) / (a + b + 2);
    # otherwise use the symmetry I_x(a, b) = 1 - I_(1-x)(b, a)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    else:
        return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's continued fraction for the incomplete beta function."""
    max_iterations = 200
    epsilon = 3e-16
    tiny = 1e-300

    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        # even step
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        # odd step
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def _validate_level(level: float) -> float:
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be in the open interval (0, 1), got {level}")
    return (1.0 - level) / 2.0


def t_interval(values: Sequence[float], level: float = 0.95) -> tuple[float, float]:
    """Two-sided Student-t interval for the mean of `values`.

    Each value should be one independent unit -- for Meridian, one task's score,
    so the degrees of freedom are `tasks - 1`. Unclamped: callers that need [0, 1]
    clamp for display.
    """
    tail = _validate_level(level)
    n = len(values)
    if n < 2:
        raise ValueError("a t interval needs at least two values")
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    standard_error = math.sqrt(variance / n)
    # `1 - tail` rounds to 1.0 for levels within one ulp of 1; degrade to the
    # largest float below 1 (a huge but finite critical value) rather than raise.
    critical = t_inv_cdf(min(1.0 - tail, math.nextafter(1.0, 0.0)), n - 1)
    return mean - critical * standard_error, mean + critical * standard_error


def wilson_interval(successes: float, n: int, level: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion, within [0, 1].

    `successes` may be fractional (e.g. a partial credit), in which case the
    interval is conservative rather than misleadingly narrow.
    """
    tail = _validate_level(level)
    if n <= 0:
        return 0.0, 0.0
    if not 0.0 <= successes <= n:
        raise ValueError(f"successes must be in [0, {n}], got {successes}")
    # compute z from the lower tail: 1 - tail can round to exactly 1.0 for
    # levels within one ulp of 1, which inv_cdf rejects
    z = -NormalDist().inv_cdf(tail)
    p_hat = successes / n
    denominator = 1.0 + z * z / n
    center = (p_hat + z * z / (2.0 * n)) / denominator
    half_width = z * math.sqrt(p_hat * (1.0 - p_hat) / n + z * z / (4.0 * n * n)) / denominator
    lower = 0.0 if successes == 0 else max(center - half_width, 0.0)
    upper = 1.0 if successes == n else min(center + half_width, 1.0)
    return lower, upper
