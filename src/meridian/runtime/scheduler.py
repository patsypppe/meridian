"""Bounded-concurrency dispatch, task-major and trial-minor.

All *n* trials of task A run before task B starts. That ordering is not an
implementation detail: it means an interrupted or budget-halted run still yields
*complete* statistics for the tasks that finished, rather than a partial column
for every task and a usable number for none.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence

from meridian.models.run import TaskResult, TrialResult, TrialSpec
from meridian.models.suite import Suite
from meridian.models.task import TaskDefinition

TrialDispatch = Callable[[TaskDefinition, TrialSpec], Awaitable[TrialResult]]

# Progress is a callback so the scheduler never decides where output goes;
# the CLI sends it to stderr, tests send it nowhere.
ProgressHook = Callable[[str], None]


def derive_seed(run_id: str, task_slug: str, trial_index: int) -> int:
    """A deterministic per-trial seed, recorded in the manifest.

    Derived rather than random so that replaying a manifest reproduces the same
    seeds without having to store each one separately — and stored anyway,
    because a derivation that changes silently is worse than a stored value.

    Bounded to 63 bits so it fits a signed 64-bit column.
    """
    digest = hashlib.sha256(f"{run_id}:{task_slug}:{trial_index}".encode()).digest()
    # 63 bits, not 64: a seed has to survive a round trip through a signed
    # 64-bit column, and an unsigned 64-bit value does not. Discovered the way
    # these things usually are — a run that computed fine and then would not
    # persist.
    return int.from_bytes(digest[:8], "big") >> 1


def trial_specs(
    suite: Suite,
    task: TaskDefinition,
    *,
    run_id: str,
    n: int,
    proxy_base_url: str = "",
) -> list[TrialSpec]:
    return [
        TrialSpec(
            run_id=run_id,
            task_slug=task.slug,
            trial_index=index,
            seed=derive_seed(run_id, task.slug, index),
            adapter_spec=task.adapter or suite.adapter_spec,
            proxy_base_url=proxy_base_url,
            max_tokens=task.limits.max_tokens,
        )
        for index in range(n)
    ]


async def run_task(
    dispatch: TrialDispatch,
    task: TaskDefinition,
    specs: Sequence[TrialSpec],
    *,
    concurrency: int,
    progress: ProgressHook | None = None,
) -> TaskResult:
    """Run every trial of one task, bounded by a semaphore."""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(spec: TrialSpec) -> TrialResult:
        async with semaphore:
            result = await dispatch(task, spec)
            if progress is not None:
                progress(f"{task.slug} trial {spec.trial_index}: {result.outcome}")
            return result

    trials = await asyncio.gather(*(one(spec) for spec in specs))
    return TaskResult(task_slug=task.slug, trials=tuple(trials))


async def run_tasks(
    dispatch: TrialDispatch,
    suite: Suite,
    tasks: Sequence[TaskDefinition],
    *,
    run_id: str,
    n: int,
    concurrency: int = 4,
    proxy_base_url: str = "",
    progress: ProgressHook | None = None,
    should_halt: Callable[[], bool] | None = None,
) -> list[TaskResult]:
    """Run each task to completion in order, stopping cleanly when asked to.

    `should_halt` is how the cost governor stops a run without abandoning the
    tasks already finished — a halted run still reports.
    """
    results: list[TaskResult] = []
    for task in tasks:
        if should_halt is not None and should_halt():
            if progress is not None:
                progress(f"halting before {task.slug}")
            break
        specs = trial_specs(suite, task, run_id=run_id, n=n, proxy_base_url=proxy_base_url)
        results.append(
            await run_task(dispatch, task, specs, concurrency=concurrency, progress=progress)
        )
    return results
