"""The gate decision rule.

The most heavily tested function in the repository, because it is the only one
whose output someone acts on without reading anything else.

Two properties get the most attention: that a gate never fails for a reason that
is not about the agent, and that it never fails on its first run. Both are how a
gate gets disabled in practice.
"""

from __future__ import annotations

import pytest

from meridian.gate.decide import GateInput, Verdict, decide, flipped_tasks

pytestmark = pytest.mark.unit


def always_significant(baseline: object, head: object) -> float:
    return 0.001


def never_significant(baseline: object, head: object) -> float:
    return 0.42


def gate(**overrides: object) -> GateInput:
    defaults: dict[str, object] = {
        "baseline_suite_passhat_k": 0.90,
        "head_suite_passhat_k": 0.90,
        "per_task_baseline": {"a": 1.0, "b": 0.8},
        "per_task_head": {"a": 1.0, "b": 0.8},
        "tolerance": 0.02,
    }
    return GateInput.model_validate({**defaults, **overrides})


# -- the happy paths -----------------------------------------------------------


def test_pass_within_tolerance() -> None:
    verdict, reason = decide(gate(head_suite_passhat_k=0.885), always_significant)
    assert verdict is Verdict.PASS
    assert "within the" in reason


def test_an_improvement_passes() -> None:
    verdict, reason = decide(gate(head_suite_passhat_k=0.99), always_significant)
    assert verdict is Verdict.PASS
    assert "+0.090" in reason


def test_pass_when_no_baseline_exists() -> None:
    """A gate that fails on its first run gets disabled on its second day."""
    verdict, reason = decide(gate(baseline_suite_passhat_k=None), always_significant)
    assert verdict is Verdict.PASS
    assert "no baseline" in reason


# -- failing ------------------------------------------------------------------


def test_fail_beyond_tolerance_with_significance() -> None:
    verdict, reason = decide(gate(head_suite_passhat_k=0.50), always_significant)
    assert verdict is Verdict.FAIL
    assert "0.400" in reason and "p=0.001" in reason


def test_pass_with_warning_beyond_tolerance_without_significance() -> None:
    verdict, reason = decide(gate(head_suite_passhat_k=0.50), never_significant)
    assert verdict is Verdict.PASS_WITH_WARNING
    assert "not distinguishable from noise" in reason


def test_fail_beyond_tolerance_when_significance_is_not_required() -> None:
    verdict, reason = decide(
        gate(head_suite_passhat_k=0.50, require_significance=False), never_significant
    )
    assert verdict is Verdict.FAIL
    assert "beyond the" in reason


def test_significance_is_not_consulted_within_tolerance() -> None:
    """A p-value function that raises proves the guard order."""

    def explode(baseline: object, head: object) -> float:
        raise AssertionError("significance must not be computed within tolerance")

    assert decide(gate(head_suite_passhat_k=0.895), explode)[0] is Verdict.PASS


# -- not evidence -------------------------------------------------------------


def test_inconclusive_on_halted_budget() -> None:
    verdict, reason = decide(
        gate(run_status="halted_budget", head_suite_passhat_k=0.1), always_significant
    )
    assert verdict is Verdict.INCONCLUSIVE
    assert "cost cap" in reason


def test_inconclusive_on_excess_harness_errors() -> None:
    verdict, reason = decide(
        gate(harness_error_rate=0.20, head_suite_passhat_k=0.1), always_significant
    )
    assert verdict is Verdict.INCONCLUSIVE
    assert "did not execute" in reason


def test_inconclusive_when_the_cassettes_predate_the_agent() -> None:
    """Stale recordings look exactly like a regression and are not one.

    A FAIL here would send someone to debug a change that is fine. The gate says
    "re-record" instead.
    """
    verdict, reason = decide(
        gate(stale_cassette_rate=0.60, head_suite_passhat_k=0.0), always_significant
    )
    assert verdict is Verdict.INCONCLUSIVE
    assert "predate this agent" in reason
    assert "--proxy-mode record" in reason


def test_a_few_stale_trials_do_not_derail_the_comparison() -> None:
    verdict, _ = decide(
        gate(stale_cassette_rate=0.05, head_suite_passhat_k=0.90), always_significant
    )
    assert verdict is Verdict.PASS


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_status": "halted_budget"},
        {"harness_error_rate": 0.20},
        {"stale_cassette_rate": 0.60},
    ],
    ids=["halted", "harness-errors", "stale-cassettes"],
)
def test_fail_on_inconclusive_flips_every_inconclusive_case(overrides: dict[str, object]) -> None:
    verdict, _ = decide(gate(fail_on_inconclusive=True, **overrides), always_significant)
    assert verdict is Verdict.FAIL


def test_a_halted_run_is_inconclusive_even_when_it_looks_fine() -> None:
    """Guard order: "is this evidence?" is asked before "is it good news?"."""
    verdict, _ = decide(
        gate(run_status="halted_budget", head_suite_passhat_k=1.0), always_significant
    )
    assert verdict is Verdict.INCONCLUSIVE


# -- the exit-code contract ---------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "blocks"),
    [
        (Verdict.PASS, False),
        (Verdict.PASS_WITH_WARNING, False),
        (Verdict.INCONCLUSIVE, False),
        (Verdict.FAIL, True),
    ],
)
def test_only_fail_blocks_a_merge(verdict: Verdict, blocks: bool) -> None:
    """CI must never confuse "the agent got worse" with "the tool crashed"."""
    assert verdict.blocks_merge is blocks


# -- flipped tasks ------------------------------------------------------------


def test_broken_tasks_are_the_ones_that_crossed_one() -> None:
    flipped = flipped_tasks({"a": 1.0, "b": 0.6, "c": 1.0}, {"a": 0.0, "b": 0.4, "c": 1.0})
    assert flipped.broken == ("a",)
    assert [slug for slug, _, _ in flipped.regressed] == ["a", "b"]


def test_fixed_tasks_are_reported_too() -> None:
    flipped = flipped_tasks({"a": 0.4}, {"a": 1.0})
    assert flipped.fixed == ("a",)
    assert flipped.broken == ()


def test_a_partial_regression_is_not_called_broken() -> None:
    """ "Broken" means it used to work every time. 0.6 → 0.4 is a regression, not that."""
    flipped = flipped_tasks({"a": 0.6}, {"a": 0.4})
    assert flipped.broken == ()
    assert flipped.regressed == (("a", 0.6, 0.4),)


def test_task_set_changes_are_named_rather_than_hidden() -> None:
    """A silently shrinking task set is how a gate stops working unnoticed."""
    flipped = flipped_tasks({"kept": 1.0, "withdrawn": 1.0}, {"kept": 1.0, "added": 1.0})
    assert flipped.only_in_baseline == ("withdrawn",)
    assert flipped.only_in_head == ("added",)


def test_an_unchanged_suite_reports_nothing_moved() -> None:
    flipped = flipped_tasks({"a": 1.0, "b": 0.4}, {"a": 1.0, "b": 0.4})
    assert not flipped.anything_moved
