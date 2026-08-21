"""The adapter abstraction, proved by using it twice.

`test_same_suite_runs_on_two_adapters` is the decisive one. If it needed a branch
or a special case, the abstraction would be wrong and the fix would be the
interface, not the test.
"""

from __future__ import annotations

from typing import Any

import pytest

from meridian.grading.pipeline import grade_task
from meridian.models.run import Outcome, TrialResult, TrialSpec
from meridian.models.suite import Suite
from meridian.models.task import FileExists, TaskDefinition
from meridian.runtime.trial_runner import TrialRunner
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE, REPO_ROOT, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

FIXTURES_ROOT = REPO_ROOT / "fixtures"

# The same work, expressed for two adapters with no code in common.
ADAPTERS = {
    "langgraph": "langgraph:trivial_agent.agent:graph",
    "subprocess": "subprocess:probes:touch_output",
}


@pytest.fixture(scope="module")
def suite() -> Suite:
    return load_suite(CHECKOUT_SUITE)


def shared_task(base: TaskDefinition, adapter_spec: str) -> TaskDefinition:
    """One task definition, identical but for which adapter runs it."""
    return base.model_copy(
        update={
            "slug": "shared-task",
            "adapter": adapter_spec,
            "outcome_assertions": (
                FileExists(kind="file_exists", path="/work/out/done.txt", should_exist=True),
            ),
        }
    )


async def run_with(docker_client: Any, suite: Suite, adapter_spec: str, run_id: str) -> TrialResult:
    task = shared_task(suite.task("contamination-probe"), adapter_spec)
    runner = TrialRunner(
        docker_client,
        suite_root=suite.root,
        sut_root=FIXTURES_ROOT,
        grader=grade_task,
    )
    spec = TrialSpec(
        run_id=run_id,
        task_slug=task.slug,
        trial_index=0,
        seed=0,
        adapter_spec=adapter_spec,
    )
    return await runner.run_trial(task, spec)


def structure(result: TrialResult) -> dict[str, Any]:
    """Everything about a result that should not depend on the adapter."""
    return {
        "task_slug": result.task_slug,
        "trial_index": result.trial_index,
        "outcome": result.outcome,
        "attempts": result.attempts,
        "container_removed": result.container_removed,
        "assertions": [(a.kind, a.outcome) for a in result.assertions],
    }


async def test_same_suite_runs_on_two_adapters(docker_client: Any, suite: Suite) -> None:
    """Identical task, two adapters, structurally identical results.

    No branch, no special case. If this test ever needs one, the interface has
    leaked and the interface is what should change.
    """
    results = {
        name: await run_with(docker_client, suite, spec, f"adapters-{name}")
        for name, spec in ADAPTERS.items()
    }

    for name, result in results.items():
        assert result.outcome is Outcome.PASS, f"{name}: {result.detail}"

    assert structure(results["langgraph"]) == structure(results["subprocess"])


async def test_both_adapters_report_efficiency_without_it_affecting_the_verdict(
    docker_client: Any, suite: Suite
) -> None:
    """Rule 2: efficiency is recorded, and never decides pass or fail."""
    for name, spec in ADAPTERS.items():
        result = await run_with(docker_client, suite, spec, f"adapters-eff-{name}")
        assert result.efficiency.tool_calls >= 1, name
        assert result.efficiency.duration_ms > 0, name
        assert result.outcome is Outcome.PASS, name


async def test_a_broken_adapter_spec_is_a_harness_error(docker_client: Any, suite: Suite) -> None:
    """A misconfigured adapter is Meridian's problem, not evidence about the agent."""
    result = await run_with(
        docker_client, suite, "langgraph:no_such_module:graph", "adapters-broken"
    )
    assert result.outcome is Outcome.HARNESS_ERROR
    assert "no_such_module" in result.detail
