"""Flaky-task quarantine: judged on thin evidence never, on wild evidence always."""

from __future__ import annotations

import pytest

from meridian.models.run import Outcome, RunResult, RunStatus, TaskResult, TrialResult
from meridian.report import table
from meridian.stats.variance import pass_rate_variance
from meridian.suites.quarantine import quarantined_slugs, should_quarantine

pytestmark = pytest.mark.unit


def _trials(slug: str, passed: int, failed: int, errors: int = 0) -> TaskResult:
    outcomes = [Outcome.PASS] * passed + [Outcome.FAIL] * failed + [Outcome.HARNESS_ERROR] * errors
    return TaskResult(
        task_slug=slug,
        trials=tuple(
            TrialResult(run_id="r", task_slug=slug, trial_index=i, seed=i, outcome=o)
            for i, o in enumerate(outcomes)
        ),
    )


def _run(run_id: str, *tasks: TaskResult, quarantined: tuple[str, ...] = ()) -> RunResult:
    return RunResult(
        run_id=run_id,
        suite_slug="s",
        suite_version=1,
        status=RunStatus.COMPLETE,
        k=3,
        n_requested=5,
        tasks=tasks,
        quarantined_task_slugs=quarantined,
    )


def test_variance_is_the_sample_estimator() -> None:
    assert pass_rate_variance([0.0, 1.0, 0.0]) == pytest.approx(1 / 3)


def test_variance_refuses_a_single_observation() -> None:
    with pytest.raises(ValueError):
        pass_rate_variance([1.0])


def test_a_stable_task_is_not_quarantined() -> None:
    assert not should_quarantine([1.0, 1.0, 1.0], threshold=0.25)
    assert not should_quarantine([0.8, 0.8, 0.6], threshold=0.25)


def test_a_task_that_alternates_is_quarantined() -> None:
    assert should_quarantine([0.0, 1.0, 0.0], threshold=0.25)


def test_two_runs_never_quarantine_however_wild() -> None:
    assert not should_quarantine([0.0, 1.0], threshold=0.0)


def test_only_the_most_recent_window_counts() -> None:
    assert not should_quarantine([0.0, 1.0, 0.0, 1.0, 1.0, 1.0], threshold=0.25)


def test_a_harness_error_run_contributes_no_pass_rate() -> None:
    history = [
        _run("a", _trials("t", 5, 0)),
        _run("b", _trials("t", 0, 0, errors=5)),
    ]
    current = [_run("c", _trials("t", 0, 5))]
    # Two real observations only: not enough evidence, however different they are.
    assert quarantined_slugs(history, current) == ()


def test_quarantined_slugs_names_the_flaky_task_and_not_the_stable_one() -> None:
    history = [
        _run("a", _trials("flaky", 0, 5), _trials("steady", 5, 0)),
        _run("b", _trials("flaky", 5, 0), _trials("steady", 5, 0)),
    ]
    current = [_run("c", _trials("flaky", 0, 5), _trials("steady", 5, 0))]
    assert quarantined_slugs(history, current) == ("flaky",)


def test_a_quarantined_task_is_out_of_the_aggregate_and_named_in_the_table() -> None:
    steady = _trials("steady", 5, 0)
    flaky = _trials("flaky", 3, 2)
    plain = table.render(_run("c", steady, flaky))
    marked = table.render(_run("c", steady, flaky, quarantined=("flaky",)))

    assert "QUARANTINED" in marked
    assert "quarantined (variance > 0.25 across 3 runs): flaky" in marked
    assert "QUARANTINED" not in plain
    # With the flaky task out, the aggregate is the steady task alone: 1.00.
    assert "suite pass^3" in marked
    assert marked.split("suite pass^3")[1].split()[0] == "1.00"
    assert plain.split("suite pass^3")[1].split()[0] != "1.00"
