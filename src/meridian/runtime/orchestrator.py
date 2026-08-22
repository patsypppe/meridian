"""One run, start to finish.

Owns the order of operations that everything else depends on: sweep, bring up the
proxy, run task-major, tear down, sweep again. The teardown is unconditional —
an interrupted run that leaves a proxy holding a credential is worse than a run
that fails.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from meridian.config import MeridianConfig, ProxyMode, RunMode
from meridian.grading.pipeline import grade_task
from meridian.manifest.build import build_manifest
from meridian.models.manifest import Manifest, ManifestModel
from meridian.models.run import (
    AssertionResult,
    RunResult,
    RunStatus,
    TaskResult,
    TrialResult,
    TrialSpec,
)
from meridian.models.suite import Suite
from meridian.models.task import TaskDefinition
from meridian.runtime.isolation import IsolationPolicy
from meridian.runtime.proxy.cassette import CassetteStore
from meridian.runtime.proxy.session import ProxyHandle, start_proxy, stop_proxy
from meridian.runtime.pytest_runner import ContainerPytestRunner
from meridian.runtime.scheduler import run_tasks
from meridian.runtime.trial_runner import TrialRunner, sweep_orphans

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient

Progress = Callable[[str], None]


@dataclass(frozen=True)
class RunOutcome:
    """What a run produced: the numbers, and the pins that determine them."""

    result: RunResult
    manifest: Manifest


def new_run_id() -> str:
    """Sortable and unique. Timestamps are fine here — they are never hashed."""
    return f"run-{int(time.time())}-{uuid.uuid4().hex[:6]}"


@dataclass
class RunRequest:
    suite: Suite
    config: MeridianConfig
    cassette_dir: Path
    sut_root: Path | None = None
    mode: RunMode = RunMode.EXPLORATORY
    include_probes: bool = False
    run_id: str = field(default_factory=new_run_id)
    use_stub_provider: bool = True
    stub_slip_every: int | None = None
    repo_root: Path | None = None
    progress: Progress | None = None

    def tasks(self) -> list[TaskDefinition]:
        if self.include_probes:
            return list(self.suite.tasks)
        return list(self.suite.scored_tasks)


async def execute(client: DockerClient, request: RunRequest) -> RunOutcome:
    """Run a suite and return its result. Never raises for an agent failure."""
    config = request.config
    config.validate_for(request.mode)

    progress = request.progress or (lambda _message: None)
    started = time.time()
    tasks = request.tasks()

    # Reclaim anything an earlier crash left behind before adding more.
    swept = sweep_orphans(client)
    if swept:
        progress(f"swept {len(swept)} orphaned resource(s) from an earlier run")

    policy = IsolationPolicy(unsafe_shared_env=config.execution.unsafe_shared_env)
    handle: ProxyHandle | None = None
    status = RunStatus.COMPLETE
    detail = ""

    try:
        handle = start_proxy(
            client,
            run_id=request.run_id,
            mode=config.execution.proxy_mode,
            cassette_dir=request.cassette_dir,
            tasks=list(request.suite.tasks),
            run_budget_cents=config.execution.budget_cents,
            use_stub_provider=request.use_stub_provider,
            stub_slip_every=request.stub_slip_every,
        )
        progress(f"proxy up in {config.execution.proxy_mode} mode on {handle.network_name}")

        # Assertions that run agent-authored tests get their own throwaway
        # container, per task, so grading never executes that code on the host.
        def grader(task: TaskDefinition, state_dir: Path, result: Any) -> Sequence[AssertionResult]:
            return grade_task(
                task,
                state_dir,
                result,
                pytest_runner=ContainerPytestRunner(
                    client,
                    image=task.environment.snapshot,
                    run_id=request.run_id,
                    workdir=task.environment.workdir,
                ),
            )

        runner = TrialRunner(
            client,
            suite_root=request.suite.root,
            sut_root=request.sut_root,
            grader=grader,
            policy=policy,
            network_name=handle.network_name,
        )

        async def dispatch(task: TaskDefinition, spec: TrialSpec) -> TrialResult:
            return await runner.run_trial(task, spec)

        def budget_exhausted() -> bool:
            if config.execution.budget_cents is None or handle is None:
                return False
            try:
                return handle.run_cents() >= config.execution.budget_cents
            except Exception:
                # A ledger we cannot read is not a reason to halt a run.
                return False

        results: list[TaskResult] = await run_tasks(
            dispatch,
            request.suite,
            tasks,
            run_id=request.run_id,
            n=config.execution.n_trials,
            concurrency=config.execution.max_concurrent_trials,
            proxy_base_url=handle.base_url,
            progress=progress,
            should_halt=budget_exhausted,
        )

        usage = handle.usage() if handle else {}
        cost_cents = int(usage.get("run_cents", 0))
        model_ids = [str(m) for m in usage.get("models", [])]
        if len(results) < len(tasks):
            status = RunStatus.HALTED_BUDGET
            ran = {result.task_slug for result in results}
            skipped = [task.slug for task in tasks if task.slug not in ran]
            detail = (
                f"halted at the {config.execution.budget_cents}c cost cap after "
                f"{len(results)} of {len(tasks)} tasks; not run: {', '.join(skipped)}"
            )
        elif budget_exhausted():
            # Every task got a turn, but the cap was reached — which means it was
            # reached *inside* the last one. `should_halt` is only consulted
            # between tasks, so those trials were not skipped: they ran, the
            # ledger refused their model calls with a 429, the agent reported the
            # error, and they were graded `fail`.
            #
            # Left as `complete` this is the gate's worst input. It looks exactly
            # like a regression, the `halted_budget` guard in `decide()` never
            # fires, and the verdict is FAIL on what is purely a cost artifact.
            # Erring toward INCONCLUSIVE is the right direction: a run that spent
            # its budget mid-flight is not evidence about the agent.
            status = RunStatus.HALTED_BUDGET
            detail = (
                f"reached the {config.execution.budget_cents}c cost cap during the "
                f"final task; trials after that point were refused and are not "
                f"evidence about the agent"
            )
    finally:
        stop_proxy(handle, client=client)
        sweep_orphans(client, run_id=request.run_id)

    result = RunResult(
        run_id=request.run_id,
        suite_slug=request.suite.slug,
        suite_version=request.suite.version,
        status=status,
        k=config.execution.k,
        n_requested=config.execution.n_trials,
        tasks=tuple(results),
        excluded_task_slugs=tuple(t.slug for t in request.suite.excluded_tasks)
        if not request.include_probes
        else (),
        detail=detail,
        started_unix_ms=int(started * 1000),
        duration_ms=int((time.time() - started) * 1000),
        cost_cents=cost_cents,
    )

    manifest = build_manifest(
        suite=request.suite,
        config=config,
        run_id=request.run_id,
        created_unix_ms=int(started * 1000),
        sut_root=request.sut_root,
        repo_root=request.repo_root or Path.cwd(),
        cassettes=CassetteStore(request.cassette_dir),
        models=tuple(
            ManifestModel(
                model_id=model_id,
                provider="stub" if request.use_stub_provider else "anthropic",
            )
            for model_id in model_ids
        ),
        prompt_hashes=prompt_hashes(request.sut_root),
        docker_version=docker_version(client),
    )
    return RunOutcome(
        result=result.model_copy(update={"manifest_hash": manifest.manifest_hash()}),
        manifest=manifest,
    )


def prompt_hashes(sut_root: Path | None) -> dict[str, str]:
    """Hash every prompt in the system under test.

    Prompts are the most common thing a regression edits and the least likely to
    show up in a diff someone reads, so they are pinned individually rather than
    folded into the SUT's overall content hash.
    """
    from meridian.manifest.build import file_hash

    if sut_root is None or not sut_root.is_dir():
        return {}
    return {
        path.relative_to(sut_root).as_posix(): file_hash(path)
        for path in sorted(sut_root.rglob("*.md"))
        if path.is_file()
    }


def docker_version(client: DockerClient) -> str | None:
    try:
        version: str = client.version().get("Version", "")
    except Exception:
        return None
    return version or None


def default_cassette_dir(suite: Suite, root: Path | None = None) -> Path:
    """Cassettes live under a root, one directory per suite.

    `--cassettes` is always the *root*, never the suite's own directory, so the
    same flag means the same thing to `run`, `replay`, and `gate`.
    """
    base = root if root is not None else Path("fixtures") / "cassettes"
    return Path(base) / suite.slug


def proxy_mode_for(config: MeridianConfig) -> ProxyMode:
    return config.execution.proxy_mode
