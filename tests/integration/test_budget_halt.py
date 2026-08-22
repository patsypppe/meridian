"""The cost governor.

The property under test is not "the run stops". It is that a run which stops
early still *reports* — completely, for the tasks that finished, and visibly
partial for the ones that did not. A cost cap that discards the work already paid
for is worse than no cap at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from meridian.config import load_config
from meridian.models.run import RunStatus
from meridian.runtime.orchestrator import RunRequest, execute
from meridian.runtime.proxy.budget import BudgetLedger, TrialLimits
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE, REPO_ROOT, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

CASSETTES_ROOT = REPO_ROOT / "fixtures" / "cassettes"
SUT = REPO_ROOT / "fixtures"

# One task of the reference suite costs roughly 7c at n=2. A cap below that
# stops the run after the first task.
TIGHT_BUDGET_CENTS = 4


def request_with(budget_cents: int | None, run_id: str) -> RunRequest:
    suite = load_suite(CHECKOUT_SUITE)
    config = load_config(
        overrides={
            "execution": {
                "n_trials": 2,
                "k": 2,
                "proxy_mode": "replay",
                "budget_cents": budget_cents,
            }
        }
    )
    return RunRequest(
        suite=suite,
        config=config,
        cassette_dir=CASSETTES_ROOT / suite.slug,
        sut_root=SUT,
        run_id=run_id,
        repo_root=REPO_ROOT,
    )


async def test_a_run_halts_at_its_cost_cap(docker_client: Any) -> None:
    outcome = await execute(docker_client, request_with(TIGHT_BUDGET_CENTS, "budget-halt"))
    result = outcome.result

    assert result.status is RunStatus.HALTED_BUDGET
    assert "cost cap" in result.detail
    assert result.cost_cents >= TIGHT_BUDGET_CENTS


async def test_a_halted_run_still_reports_the_tasks_that_finished(
    docker_client: Any,
) -> None:
    """Complete statistics for what ran, and the rest named explicitly."""
    outcome = await execute(docker_client, request_with(TIGHT_BUDGET_CENTS, "budget-partial"))
    result = outcome.result
    suite = load_suite(CHECKOUT_SUITE)

    assert 0 < len(result.tasks) < len(suite.scored_tasks)
    # Every task that ran has all of its trials — task-major dispatch means a
    # halt never leaves a task half-measured.
    for task in result.tasks:
        assert len(task.trials) == 2
        assert task.n == 2

    ran = {task.task_slug for task in result.tasks}
    for slug in (t.slug for t in suite.scored_tasks):
        if slug not in ran:
            assert slug in result.detail


async def test_a_halted_run_still_writes_a_manifest(docker_client: Any) -> None:
    """A partial run has to stay reproducible, or it cannot be investigated."""
    outcome = await execute(docker_client, request_with(TIGHT_BUDGET_CENTS, "budget-manifest"))
    assert outcome.manifest.manifest_hash().startswith("sha256:")
    assert outcome.result.manifest_hash == outcome.manifest.manifest_hash()


async def test_no_cap_means_the_whole_suite_runs(docker_client: Any) -> None:
    outcome = await execute(docker_client, request_with(None, "budget-none"))
    suite = load_suite(CHECKOUT_SUITE)
    assert outcome.result.status is RunStatus.COMPLETE
    assert len(outcome.result.tasks) == len(suite.scored_tasks)


@pytest.mark.unit
def test_the_ledger_refuses_a_call_once_the_run_cap_is_reached() -> None:
    """Enforced in the proxy too, so a single task cannot blow the whole cap."""
    ledger = BudgetLedger(
        limits={"t": TrialLimits(max_tokens=10**9, budget_cents=10**6)},
        run_budget_cents=1,
    )
    ledger.charge("t", 0, model="claude-opus-5", input_tokens=5_000_000, output_tokens=0)
    assert ledger.run_budget_exhausted()
