"""Rule 1, enforced rather than asserted.

Every test here would still pass if isolation were merely *configured*. What
makes the set meaningful is `test_contamination_probe_fails_without_isolation`:
a probe that cannot fail proves nothing, so the failing direction is asserted
alongside the passing one.
"""

from __future__ import annotations

import pytest

from meridian.grading.pipeline import grade_task
from meridian.models.run import Outcome, TrialSpec
from meridian.models.suite import Suite
from meridian.models.task import FileExists, TaskDefinition
from meridian.runtime.isolation import IsolationPolicy
from meridian.runtime.trial_runner import TrialRunner, sweep_orphans
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE, REPO_ROOT, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

FIXTURES_ROOT = REPO_ROOT / "fixtures"


@pytest.fixture(scope="module")
def suite() -> Suite:
    return load_suite(CHECKOUT_SUITE)


def probe_task(base: TaskDefinition, *, target: str, marker: str) -> TaskDefinition:
    """Derive a single-assertion probe task from the contamination template.

    Building these in the test rather than adding them to the shipped suite keeps
    the suite a description of the agent under evaluation, not of Meridian.
    """
    return base.model_copy(
        update={
            "slug": f"probe-{marker.replace('/', '-')}",
            "adapter": f"subprocess:probes:{target}",
            "outcome_assertions": (
                FileExists(kind="file_exists", path=f"/work/out/{marker}", should_exist=True),
            ),
        }
    )


def runner_for(
    docker_client: object, suite: Suite, *, policy: IsolationPolicy | None = None
) -> TrialRunner:
    return TrialRunner(
        docker_client,  # type: ignore[arg-type]
        suite_root=suite.root,
        sut_root=FIXTURES_ROOT,
        grader=grade_task,
        policy=policy or IsolationPolicy(),
    )


async def run_one(
    runner: TrialRunner, task: TaskDefinition, *, run_id: str, trial_index: int = 0
) -> object:
    spec = TrialSpec(
        run_id=run_id,
        task_slug=task.slug,
        trial_index=trial_index,
        seed=trial_index,
        adapter_spec=task.adapter or "subprocess:probes:noop",
    )
    return await runner.run_trial(task, spec)


async def test_container_is_non_root(docker_client: object, suite: Suite) -> None:
    task = probe_task(suite.task("contamination-probe"), target="report_uid", marker="uid-ok")
    result = await run_one(runner_for(docker_client, suite), task, run_id="iso-uid")
    assert result.outcome is Outcome.PASS, result.detail  # type: ignore[attr-defined]


async def test_rootfs_is_read_only(docker_client: object, suite: Suite) -> None:
    task = probe_task(
        suite.task("contamination-probe"),
        target="probe_readonly_rootfs",
        marker="rootfs-readonly",
    )
    result = await run_one(runner_for(docker_client, suite), task, run_id="iso-ro")
    assert result.outcome is Outcome.PASS, result.detail  # type: ignore[attr-defined]


async def test_no_network_by_default(docker_client: object, suite: Suite) -> None:
    task = probe_task(
        suite.task("contamination-probe"), target="probe_no_network", marker="no-network"
    )
    result = await run_one(runner_for(docker_client, suite), task, run_id="iso-net")
    assert result.outcome is Outcome.PASS, result.detail  # type: ignore[attr-defined]


async def test_contamination_probe_passes_with_isolation(
    docker_client: object, suite: Suite
) -> None:
    runner = runner_for(docker_client, suite)
    writer = await run_one(runner, suite.task("contamination-writer"), run_id="contam-iso")
    probe = await run_one(runner, suite.task("contamination-probe"), run_id="contam-iso")

    assert writer.outcome is Outcome.PASS, writer.detail  # type: ignore[attr-defined]
    assert probe.outcome is Outcome.PASS, probe.detail  # type: ignore[attr-defined]


async def test_contamination_probe_fails_without_isolation(
    docker_client: object, suite: Suite
) -> None:
    """The failing direction. Without this the probe proves nothing."""
    policy = IsolationPolicy(unsafe_shared_env=True)
    runner = runner_for(docker_client, suite, policy=policy)
    run_id = "contam-unsafe"
    try:
        writer = await run_one(runner, suite.task("contamination-writer"), run_id=run_id)
        probe = await run_one(runner, suite.task("contamination-probe"), run_id=run_id)

        assert writer.outcome is Outcome.PASS, writer.detail  # type: ignore[attr-defined]
        assert probe.outcome is Outcome.FAIL, (  # type: ignore[attr-defined]
            "the probe passed with isolation disabled, which means it cannot fail "
            "and therefore proves nothing"
        )
        assert "marker.txt" in probe.detail  # type: ignore[attr-defined]
    finally:
        # The shared volume belongs to the run, so the run has to clean it up.
        sweep_orphans(docker_client, run_id=run_id)  # type: ignore[arg-type]


async def test_isolated_trials_get_distinct_volumes(docker_client: object, suite: Suite) -> None:
    """The mechanism behind the probe, asserted directly."""
    policy = IsolationPolicy()
    names = {policy.workdir_volume("run", "task", index) for index in range(5)}
    assert len(names) == 5

    shared = IsolationPolicy(unsafe_shared_env=True)
    shared_names = {shared.workdir_volume("run", "task", index) for index in range(5)}
    assert len(shared_names) == 1
