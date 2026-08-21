"""One run, start to finish.

Owns the order of operations that everything else depends on: sweep, bring up the
proxy, run task-major, tear down, sweep again. The teardown is unconditional —
an interrupted run that leaves a proxy holding a credential is worse than a run
that fails.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.config import MeridianConfig, ProxyMode, RunMode
from meridian.grading.pipeline import grade_task
from meridian.models.run import RunResult, RunStatus, TaskResult, TrialResult, TrialSpec
from meridian.models.suite import Suite
from meridian.models.task import TaskDefinition
from meridian.runtime.isolation import IsolationPolicy
from meridian.runtime.proxy.session import ProxyHandle, start_proxy, stop_proxy
from meridian.runtime.scheduler import run_tasks
from meridian.runtime.trial_runner import TrialRunner, sweep_orphans

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient

Progress = Callable[[str], None]


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
    progress: Progress | None = None

    def tasks(self) -> list[TaskDefinition]:
        if self.include_probes:
            return list(self.suite.tasks)
        return list(self.suite.scored_tasks)


async def execute(client: DockerClient, request: RunRequest) -> RunResult:
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

        runner = TrialRunner(
            client,
            suite_root=request.suite.root,
            sut_root=request.sut_root,
            grader=grade_task,
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

        cost_cents = handle.run_cents() if handle else 0
        if len(results) < len(tasks):
            status = RunStatus.HALTED_BUDGET
            ran = {result.task_slug for result in results}
            skipped = [task.slug for task in tasks if task.slug not in ran]
            detail = (
                f"halted at the {config.execution.budget_cents}c cost cap after "
                f"{len(results)} of {len(tasks)} tasks; not run: {', '.join(skipped)}"
            )
    finally:
        stop_proxy(handle, client=client)
        sweep_orphans(client, run_id=request.run_id)

    return RunResult(
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


def default_cassette_dir(suite: Suite, root: Path | None = None) -> Path:
    base = root or Path("fixtures") / "cassettes"
    return Path(base) / suite.slug


def proxy_mode_for(config: MeridianConfig) -> ProxyMode:
    return config.execution.proxy_mode
