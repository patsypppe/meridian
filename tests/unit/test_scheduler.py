"""Scheduling order and seed derivation."""

from __future__ import annotations

import asyncio

import pytest

from meridian.models.run import Outcome, TrialResult, TrialSpec
from meridian.models.task import TaskDefinition
from meridian.runtime.scheduler import derive_seed, run_tasks, trial_specs
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE

pytestmark = pytest.mark.unit


def _result(task: TaskDefinition, spec: TrialSpec) -> TrialResult:
    return TrialResult(
        run_id=spec.run_id,
        task_slug=spec.task_slug,
        trial_index=spec.trial_index,
        seed=spec.seed,
        outcome=Outcome.PASS,
    )


def test_seeds_are_deterministic() -> None:
    assert derive_seed("run", "task", 0) == derive_seed("run", "task", 0)


SIGNED_64_BIT_MAX = 2**63 - 1


def test_seeds_fit_a_signed_64_bit_column() -> None:
    """A seed that cannot be persisted is a run that cannot be reproduced."""
    for index in range(64):
        seed = derive_seed("run", "task", index)
        assert 0 <= seed <= SIGNED_64_BIT_MAX


def test_seeds_differ_across_trials_and_tasks() -> None:
    seeds = {derive_seed("run", "task", i) for i in range(10)}
    assert len(seeds) == 10
    assert derive_seed("run", "a", 0) != derive_seed("run", "b", 0)
    assert derive_seed("run-1", "a", 0) != derive_seed("run-2", "a", 0)


def test_task_adapter_overrides_the_suite_adapter() -> None:
    suite = load_suite(CHECKOUT_SUITE)
    probe = trial_specs(suite, suite.task("contamination-probe"), run_id="r", n=1)[0]
    scored = trial_specs(suite, suite.task("expired-coupon"), run_id="r", n=1)[0]

    assert probe.adapter_spec == "subprocess:probes:noop"
    assert scored.adapter_spec == suite.adapter_spec


def test_dispatch_is_task_major() -> None:
    """All n trials of one task finish before the next task starts.

    A partial run then yields complete statistics for the tasks that ran, rather
    than a partial column for every task and a usable number for none.
    """
    suite = load_suite(CHECKOUT_SUITE)
    order: list[str] = []

    async def dispatch(task: TaskDefinition, spec: TrialSpec) -> TrialResult:
        order.append(f"{task.slug}:{spec.trial_index}")
        await asyncio.sleep(0)
        return _result(task, spec)

    results = asyncio.run(
        run_tasks(dispatch, suite, suite.scored_tasks, run_id="r", n=3, concurrency=2)
    )

    assert len(results) == len(suite.scored_tasks)
    slugs_in_order = [entry.split(":")[0] for entry in order]
    # Each task's slug occupies one contiguous block.
    assert [s for i, s in enumerate(slugs_in_order) if i == 0 or s != slugs_in_order[i - 1]] == [
        t.slug for t in suite.scored_tasks
    ]


def test_halting_keeps_the_tasks_already_finished() -> None:
    suite = load_suite(CHECKOUT_SUITE)
    seen: list[str] = []

    async def dispatch(task: TaskDefinition, spec: TrialSpec) -> TrialResult:
        seen.append(task.slug)
        return _result(task, spec)

    results = asyncio.run(
        run_tasks(
            dispatch,
            suite,
            suite.scored_tasks,
            run_id="r",
            n=2,
            should_halt=lambda: len(seen) >= 2,
        )
    )

    assert len(results) == 1
    assert results[0].n == 2
