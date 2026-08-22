"""Outcome classification and the retry policy.

This is the file that protects the number Meridian reports. An agent failure
retried is a score better than reality, and nothing crashes while that happens —
so the rule gets a test with a name you would notice deleting.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from meridian.grading.graders.base import GraderError
from meridian.grading.pipeline import grade_task
from meridian.models.run import Outcome, TrialSpec
from meridian.models.suite import Suite
from meridian.models.task import FileExists, Limits, TaskDefinition
from meridian.runtime.isolation import IsolationPolicy
from meridian.runtime.trial_runner import TrialRunner
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE, REPO_ROOT, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

FIXTURES_ROOT = REPO_ROOT / "fixtures"
TIMEOUT_SECONDS = 10
KILL_GRACE_SECONDS = 5


@pytest.fixture(scope="module")
def suite() -> Suite:
    return load_suite(CHECKOUT_SUITE)


def task_running(
    base: TaskDefinition,
    target: str,
    *,
    marker: str = "done.txt",
    timeout_seconds: int | None = None,
    memory_mb: int | None = None,
) -> TaskDefinition:
    updates: dict[str, Any] = {
        "slug": f"lifecycle-{target.replace('_', '-')}",
        "adapter": f"subprocess:probes:{target}",
        "outcome_assertions": (
            FileExists(kind="file_exists", path=f"/work/out/{marker}", should_exist=True),
        ),
    }
    if timeout_seconds is not None:
        updates["limits"] = Limits(timeout_seconds=timeout_seconds, max_tokens=1000, budget_cents=1)
    if memory_mb is not None:
        environment = base.environment
        updates["environment"] = environment.model_copy(
            update={"resources": environment.resources.model_copy(update={"memory_mb": memory_mb})}
        )
    return base.model_copy(update=updates)


def make_runner(client: Any, suite: Suite, **kwargs: Any) -> TrialRunner:
    kwargs.setdefault("grader", grade_task)
    return TrialRunner(
        client,
        suite_root=suite.root,
        sut_root=FIXTURES_ROOT,
        policy=IsolationPolicy(),
        **kwargs,
    )


async def run(runner: TrialRunner, task: TaskDefinition, run_id: str) -> Any:
    spec = TrialSpec(
        run_id=run_id,
        task_slug=task.slug,
        trial_index=0,
        seed=0,
        adapter_spec=task.adapter or "subprocess:probes:noop",
    )
    return await runner.run_trial(task, spec)


async def test_a_successful_trial_passes(docker_client: Any, suite: Suite) -> None:
    task = task_running(suite.task("contamination-probe"), "touch_output")
    result = await run(make_runner(docker_client, suite), task, "life-pass")
    assert result.outcome is Outcome.PASS, result.detail
    assert result.attempts == 1


async def test_agent_failure_is_not_retried(docker_client: Any, suite: Suite) -> None:
    """The most important test in the repository.

    Retrying an agent failure produces a number better than reality while nothing
    appears to break — the exact failure mode Meridian exists to prevent.
    """
    task = task_running(suite.task("contamination-probe"), "always_fails")
    result = await run(make_runner(docker_client, suite), task, "life-agentfail")

    assert result.outcome is Outcome.FAIL, result.detail
    assert result.attempts == 1, (
        f"an agent failure was attempted {result.attempts} times; only harness errors are retryable"
    )


async def test_timeout_kills_the_container(docker_client: Any, suite: Suite) -> None:
    """Cancelling a Python future does not stop a container. The container is killed."""
    task = task_running(suite.task("contamination-probe"), "hang", timeout_seconds=TIMEOUT_SECONDS)
    started = time.monotonic()
    result = await run(make_runner(docker_client, suite), task, "life-timeout")
    elapsed = time.monotonic() - started

    assert result.outcome is Outcome.TIMEOUT, result.detail
    assert result.attempts == 1, "a timeout is a statement about the agent and is never retried"
    assert elapsed < TIMEOUT_SECONDS + KILL_GRACE_SECONDS, (
        f"the trial took {elapsed:.1f}s for a {TIMEOUT_SECONDS}s deadline; the "
        f"container was probably abandoned rather than killed"
    )
    # The autouse leak fixture asserts no container with the run label survives.


async def test_infra_fault_is_retried_once(
    docker_client: Any, suite: Suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docker.errors
    from docker.models.containers import ContainerCollection

    task = task_running(suite.task("contamination-probe"), "touch_output")
    # `client.containers` builds a fresh collection on every access, so patching
    # the instance is a no-op. The class is the seam.
    original = ContainerCollection.create
    calls = {"n": 0}

    def flaky_create(self: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise docker.errors.APIError("simulated daemon fault")
        return original(self, **kwargs)

    monkeypatch.setattr(ContainerCollection, "create", flaky_create)
    result = await run(make_runner(docker_client, suite), task, "life-infra")

    assert result.outcome is Outcome.PASS, result.detail
    assert result.attempts == 2, "an infrastructure fault retries exactly once"
    assert calls["n"] == 2


async def test_persistent_infra_fault_is_a_harness_error(
    docker_client: Any, suite: Suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    import docker.errors
    from docker.models.containers import ContainerCollection

    task = task_running(suite.task("contamination-probe"), "touch_output")
    calls = {"n": 0}

    def always_fails(self: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise docker.errors.APIError("daemon is gone")

    monkeypatch.setattr(ContainerCollection, "create", always_fails)
    result = await run(make_runner(docker_client, suite), task, "life-infra-dead")

    assert result.outcome is Outcome.HARNESS_ERROR
    assert result.attempts == 2, "retried once, then reported — never retried forever"
    assert calls["n"] == 2


async def test_an_oom_is_the_agents_failure_and_is_not_retried(
    docker_client: Any, suite: Suite
) -> None:
    """§11.4 — container OOM.

    The memory ceiling is set by the *task*, so an agent that walks into it has
    failed under the budget it was given. Retrying would hand it a second attempt
    the reported number never shows.

    The detail has to name memory. Without that an OOM is indistinguishable from
    an ordinary assertion failure, and the operator debugs the agent's logic for
    an hour before finding the real cause.
    """
    task = task_running(
        suite.task("contamination-probe"),
        "exhaust_memory",
        memory_mb=64,
        timeout_seconds=TIMEOUT_SECONDS * 3,
    )
    result = await run(make_runner(docker_client, suite), task, "life-oom")

    assert result.outcome is Outcome.FAIL, result.detail
    assert "memory" in result.detail.lower(), (
        f"an OOM reported as {result.detail!r} — the operator cannot tell this "
        f"from a logic bug in the agent"
    )
    assert result.attempts == 1, "an OOM is a statement about the agent and is never retried"


async def test_a_missing_image_is_a_harness_error_not_an_agent_failure(
    docker_client: Any, suite: Suite, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§11.4 — image pull failure.

    An image Meridian cannot obtain says nothing whatsoever about the agent.
    Counted as a failure it would look exactly like a regression, and the revert
    that follows would change nothing.
    """
    import docker.errors
    from docker.models.containers import ContainerCollection

    task = task_running(suite.task("contamination-probe"), "touch_output")
    calls = {"n": 0}

    def missing_image(self: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise docker.errors.ImageNotFound("no such image: sha256:deadbeef")

    monkeypatch.setattr(ContainerCollection, "create", missing_image)
    result = await run(make_runner(docker_client, suite), task, "life-nopull")

    assert result.outcome is Outcome.HARNESS_ERROR, result.detail
    assert "no such image" in result.detail, (
        f"classified as a harness error but the detail {result.detail!r} does not "
        f"name the image — this would pass even if the classification were incidental"
    )
    assert result.attempts == 2, "retried once, then reported — never retried forever"
    assert calls["n"] == 2


async def test_container_removed_on_grader_exception(docker_client: Any, suite: Suite) -> None:
    """A broken assertion is Meridian's fault, and never the agent's."""
    task = task_running(suite.task("contamination-probe"), "touch_output")

    def exploding_grader(*_args: Any, **_kwargs: Any) -> Any:
        raise GraderError("this assertion is broken")

    runner = make_runner(docker_client, suite, grader=exploding_grader)
    before = len(docker_client.containers.list(all=True))
    result = await run(runner, task, "life-grader")

    assert result.outcome is Outcome.HARNESS_ERROR
    assert "this assertion is broken" in result.detail
    assert result.container_removed is True
    assert len(docker_client.containers.list(all=True)) == before


async def test_an_unevaluatable_assertion_is_a_harness_error(
    docker_client: Any, suite: Suite
) -> None:
    """An assertion that cannot be evaluated has not been satisfied.

    A `pytest` assertion needs a sandboxed runner, because its test files come
    from a directory the agent controlled. Where none is configured the grader
    errors and the trial becomes a harness error — never a pass, and never a
    failure blamed on the agent.
    """
    from meridian.models.task import PytestAssertion

    task = task_running(suite.task("contamination-probe"), "touch_output").model_copy(
        update={"outcome_assertions": (PytestAssertion(kind="pytest", path="/work/tests"),)}
    )
    result = await run(make_runner(docker_client, suite), task, "life-unevaluatable")

    assert result.outcome is Outcome.HARNESS_ERROR
    assert "code execution" in result.detail


async def test_final_state_is_extracted_before_removal(docker_client: Any, suite: Suite) -> None:
    """Extraction happens inside the try and before the finally that destroys."""
    seen: dict[str, Path] = {}

    def recording_grader(task: TaskDefinition, state_dir: Path, result: Any) -> Any:
        seen["state_dir"] = state_dir
        seen["files"] = sorted(p.name for p in state_dir.rglob("*") if p.is_file())  # type: ignore[assignment]
        return grade_task(task, state_dir, result)

    task = task_running(suite.task("contamination-probe"), "touch_output")
    runner = make_runner(docker_client, suite, grader=recording_grader)
    result = await run(runner, task, "life-extract")

    assert result.outcome is Outcome.PASS, result.detail
    assert "done.txt" in seen["files"]  # the agent's output
    assert "result.json" in seen["files"]  # the adapter's own record
    assert "policy.md" in seen["files"]  # seeded from the image's read-only layer
