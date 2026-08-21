"""pass@k and pass^k, against the worked table from HANDOFF §8.1.

This table exists to catch one specific wrong implementation: `(c/n)**k`. It is a
different estimator, it disagrees with the published definition, and it would
make the README indefensible. Row `n=5, c=4, k=3` is the one that catches it —
the correct answer is 0.4 and `(4/5)**3` is 0.512.
"""

from __future__ import annotations

import pytest

from meridian.stats.passk import pass_at_k, pass_hat_k, suite_pass_hat_k

pytestmark = pytest.mark.unit

# n, c, k, pass@k, pass^k, why
TABLE = [
    (5, 5, 3, 1.0, 1.0, "perfect"),
    (5, 0, 3, 0.0, 0.0, "total failure"),
    (5, 4, 1, 0.8, 0.8, "k=1 collapses both to c/n"),
    (5, 4, 3, 1.0, 0.4, "C(4,3)/C(5,3) = 4/10 — the headline number"),
    (5, 3, 3, 1.0, 0.1, "C(3,3)/C(5,3) = 1/10"),
    (5, 2, 3, 0.9, 0.0, "c < k"),
    (10, 8, 5, 1.0, 56 / 252, "C(8,5)/C(10,5)"),
]


@pytest.mark.parametrize(
    ("n", "c", "k", "expected_at_k", "expected_hat_k", "why"),
    TABLE,
    ids=[row[5] for row in TABLE],
)
def test_the_worked_table(
    n: int, c: int, k: int, expected_at_k: float, expected_hat_k: float, why: str
) -> None:
    assert pass_at_k(n, c, k) == pytest.approx(expected_at_k), why
    assert pass_hat_k(n, c, k) == pytest.approx(expected_hat_k), why


def test_pass_hat_k_is_not_the_naive_power() -> None:
    """The specific wrong implementation this suite exists to catch."""
    assert pass_hat_k(5, 4, 3) == pytest.approx(0.4)
    assert pytest.approx(0.512) == (4 / 5) ** 3
    assert pass_hat_k(5, 4, 3) != pytest.approx((4 / 5) ** 3)


def test_the_headline_sentence_is_true() -> None:
    """An agent passing 4 of 5 trials has a pass^3 of 0.4.

    Eighty percent looks acceptable. A 40% chance that three consecutive attempts
    all work does not. This is the sentence the README is built around.
    """
    assert pass_hat_k(5, 4, 3) == pytest.approx(0.4)


@pytest.mark.parametrize("k", [1, 2, 3, 4, 5])
def test_pass_at_k_is_never_below_pass_hat_k(k: int) -> None:
    for c in range(6):
        assert pass_at_k(5, c, k) >= pass_hat_k(5, c, k) - 1e-12


def test_both_statistics_increase_with_successes() -> None:
    at_k = [pass_at_k(10, c, 3) for c in range(11)]
    hat_k = [pass_hat_k(10, c, 3) for c in range(11)]
    assert at_k == sorted(at_k)
    assert hat_k == sorted(hat_k)


@pytest.mark.parametrize(
    ("n", "c", "k"),
    [(5, 6, 3), (5, -1, 3), (5, 3, 0), (5, 3, 6), (0, 0, 1)],
)
def test_impossible_inputs_are_refused(n: int, c: int, k: int) -> None:
    with pytest.raises(ValueError, match="invalid"):
        pass_hat_k(n, c, k)
    with pytest.raises(ValueError, match="invalid"):
        pass_at_k(n, c, k)


def test_suite_score_is_unweighted() -> None:
    """Weighting by trial count lets one task silently dominate the suite."""
    # One task with many trials and a poor score, one with few and a perfect one.
    unweighted = suite_pass_hat_k([(100, 50), (5, 5)], 3)
    weighted = (pass_hat_k(100, 50, 3) * 100 + pass_hat_k(5, 5, 3) * 5) / 105

    assert unweighted == pytest.approx((pass_hat_k(100, 50, 3) + 1.0) / 2)
    assert unweighted > weighted


def test_suite_score_clamps_k_to_the_trials_that_ran() -> None:
    """A task that lost trials to harness errors contributes what it can."""
    assert suite_pass_hat_k([(2, 2)], 5) == pytest.approx(1.0)


def test_an_empty_suite_scores_zero_rather_than_raising() -> None:
    assert suite_pass_hat_k([], 3) == 0.0
