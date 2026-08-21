"""The trial lifecycle — the heart of Rule 1.

The lifecycle is fixed and the ordering is load-bearing:

    resolve → create → start → materialize → wait → extract → destroy → grade

Extraction happens **before** removal and inside the same `try`. Destruction
happens in a `finally`, unconditionally, and whether it succeeded is recorded —
a leaked container is a harness error, not a silent condition. Grading happens
outside the container, against extracted state, so a compromised agent cannot
influence its own verdict.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from meridian.adapters.base import AdapterSpec, AdapterSpecError
from meridian.models.adapter import AdapterResult
from meridian.models.run import (
    AssertionOutcome,
    AssertionResult,
    Efficiency,
    Outcome,
    TrialResult,
    TrialSpec,
)
from meridian.models.task import TaskDefinition
from meridian.runtime import payload as payload_module
from meridian.runtime.isolation import (
    ISOLATED,
    RUN_LABEL,
    IsolationPolicy,
    container_kwargs,
)
from meridian.snapshots.registry import require_digest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient

# A grader takes the task and the extracted final state and returns one result
# per assertion. It never sees the container, and it never sees the transcript
# as an input to the verdict (Rule 2).
Grader = Callable[[TaskDefinition, Path, AdapterResult], Sequence[AssertionResult]]

HARNESS_RETRIES = 1
STOP_GRACE_SECONDS = 2
PAYLOAD_POLL_SECONDS = 0.05


class HarnessFault(RuntimeError):
    """Meridian could not run the trial. Retried once, then reported as such.

    Deliberately distinct from any agent-side failure. The two demand opposite
    responses — one is a page, the other is a code review — and a harness that
    conflates them reports numbers better than reality.
    """

    def __init__(self, message: str, *, container_removed: bool = True) -> None:
        super().__init__(message)
        self.container_removed = container_removed


@dataclass(frozen=True)
class ContainerPhase:
    """What the container phase established, before anything is graded."""

    timed_out: bool
    exit_code: int | None
    container_removed: bool


def _now_ms() -> int:
    return int(time.time() * 1000)


def _no_grader(
    task: TaskDefinition, state_dir: Path, result: AdapterResult
) -> Sequence[AssertionResult]:
    """Default grader: records that nothing was graded, rather than passing."""
    return tuple(
        AssertionResult(
            kind=assertion.kind,
            outcome=AssertionOutcome.ERROR,
            detail="no grader configured for this run",
        )
        for assertion in task.outcome_assertions
    )


async def _to_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(fn, *args, **kwargs)


class TrialRunner:
    """Runs one trial at a time, in its own container, and cleans up after itself."""

    def __init__(
        self,
        client: DockerClient,
        *,
        suite_root: Path,
        sut_root: Path | None = None,
        harness_root: Path | None = None,
        grader: Grader | None = None,
        policy: IsolationPolicy = ISOLATED,
        network_name: str | None = None,
    ) -> None:
        self._client = client
        self._suite_root = suite_root
        self._sut_root = sut_root
        self._harness_root = harness_root or Path(__file__).resolve().parents[1]
        self._grade = grader or _no_grader
        self._policy = policy
        self._network_name = network_name

    # -- lifecycle ---------------------------------------------------------

    async def run_trial(self, task: TaskDefinition, spec: TrialSpec) -> TrialResult:
        """Run one trial, retrying only harness faults, and only once."""
        attempt = 0
        last: TrialResult | None = None
        while attempt <= HARNESS_RETRIES:
            attempt += 1
            result = await self._attempt(task, spec, attempt)
            if result.outcome is not Outcome.HARNESS_ERROR:
                # A `fail` or a `timeout` is a statement about the agent, and
                # retrying it is how a harness reports a number better than
                # reality. This return is the enforcement of that rule.
                return result
            last = result
        assert last is not None
        return last

    async def _attempt(self, task: TaskDefinition, spec: TrialSpec, attempt: int) -> TrialResult:
        started = _now_ms()
        state_dir = Path(tempfile.mkdtemp(prefix=f"meridian-{spec.task_slug}-"))
        try:
            try:
                phase = await self._run_container(task, spec, state_dir)
            except AdapterSpecError as exc:
                return self._harness_error(spec, started, attempt, f"adapter spec: {exc}")
            except HarnessFault as exc:
                return self._harness_error(
                    spec, started, attempt, str(exc), removed=exc.container_removed
                )
            except Exception as exc:
                return self._harness_error(spec, started, attempt, f"{type(exc).__name__}: {exc}")

            return self._verdict(task, spec, phase, state_dir, started, attempt)
        finally:
            shutil.rmtree(state_dir, ignore_errors=True)

    async def _run_container(
        self, task: TaskDefinition, spec: TrialSpec, state_dir: Path
    ) -> ContainerPhase:
        """create → start → materialize → wait → extract → destroy.

        Extraction is inside the try and before destruction; destruction is in
        the finally and unconditional. Reversing either turns a timeout into an
        unexplained empty result.
        """
        digest = require_digest(task.environment.snapshot)
        AdapterSpec.parse(spec.adapter_spec)

        volume_name = self._policy.workdir_volume(spec.run_id, spec.task_slug, spec.trial_index)
        container: Any = None
        volume: Any = None
        removed = True
        try:
            volume = await self._create_volume(volume_name, spec.run_id)
            container = await self._create(task, spec, digest, volume_name)
            await _to_thread(container.start)
            await self._materialize(container, task, spec)

            timed_out, exit_code = await self._wait(container, task.limits.timeout_seconds)
            await self._extract(container, task, state_dir)
        except HarnessFault as fault:
            if container is not None:
                removed = await self._destroy(container)
                container = None
            await self._destroy_volume(volume)
            volume = None
            raise HarnessFault(str(fault), container_removed=removed) from fault
        finally:
            if container is not None:
                removed = await self._destroy(container)
            # The volume outlives the container by exactly as long as it takes to
            # extract, and not one step longer.
            await self._destroy_volume(volume)

        return ContainerPhase(timed_out=timed_out, exit_code=exit_code, container_removed=removed)

    def _verdict(
        self,
        task: TaskDefinition,
        spec: TrialSpec,
        phase: ContainerPhase,
        state_dir: Path,
        started: int,
        attempt: int,
    ) -> TrialResult:
        try:
            adapter_result = self._read_adapter_result(state_dir, task, phase.exit_code)
        except HarnessFault as exc:
            return self._harness_error(
                spec, started, attempt, str(exc), removed=phase.container_removed
            )

        if phase.timed_out:
            # Never retried: the agent had its wall clock and did not finish.
            return TrialResult(
                run_id=spec.run_id,
                task_slug=spec.task_slug,
                trial_index=spec.trial_index,
                seed=spec.seed,
                outcome=Outcome.TIMEOUT,
                detail=f"exceeded the {task.limits.timeout_seconds}s deadline",
                attempts=attempt,
                efficiency=self._efficiency(adapter_result, started),
                container_removed=phase.container_removed,
                started_unix_ms=started,
            )

        if phase.exit_code == _entrypoint_failure_code():
            return self._harness_error(
                spec,
                started,
                attempt,
                self._entrypoint_failure_detail(state_dir),
                removed=phase.container_removed,
            )

        try:
            assertions = tuple(self._grade(task, state_dir, adapter_result))
            outcome, detail = self._classify(assertions, adapter_result)
        except HarnessFault as exc:
            return self._harness_error(
                spec, started, attempt, str(exc), removed=phase.container_removed
            )
        except Exception as exc:
            # A grader that raises means *the assertion* is broken, not the
            # agent. Calling that a failure makes a broken assertion look like a
            # regression and sends you debugging the wrong system.
            return self._harness_error(
                spec,
                started,
                attempt,
                f"grader raised {type(exc).__name__}: {exc}",
                removed=phase.container_removed,
            )

        return TrialResult(
            run_id=spec.run_id,
            task_slug=spec.task_slug,
            trial_index=spec.trial_index,
            seed=spec.seed,
            outcome=outcome,
            detail=detail,
            attempts=attempt,
            assertions=assertions,
            efficiency=self._efficiency(adapter_result, started),
            container_removed=phase.container_removed,
            started_unix_ms=started,
        )

    # -- steps -------------------------------------------------------------

    async def _create_volume(self, name: str, run_id: str) -> Any:
        """Create the trial's only writable mount.

        Labelled so the orphan sweeper can reclaim it if the harness dies before
        the finally block runs.
        """
        try:
            return await _to_thread(
                self._client.volumes.create, name=name, labels={RUN_LABEL: run_id}
            )
        except Exception as exc:
            raise HarnessFault(
                f"workdir volume create failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def _destroy_volume(self, volume: Any) -> None:
        if volume is None or not self._policy.volume_is_per_trial:
            # A shared volume belongs to the run, not the trial — removing it here
            # would quietly restore the isolation the flag exists to remove.
            return
        with contextlib.suppress(Exception):
            await _to_thread(volume.remove, force=True)

    async def _create(
        self, task: TaskDefinition, spec: TrialSpec, digest: str, volume_name: str
    ) -> Any:
        kwargs = container_kwargs(
            task,
            spec.run_id,
            spec.trial_index,
            workdir_volume=volume_name,
            policy=self._policy,
            network_name=self._network_name,
        )
        kwargs["image"] = digest
        kwargs["name"] = f"meridian-{spec.task_slug}-{spec.trial_index}-{uuid.uuid4().hex[:8]}"
        try:
            return await _to_thread(self._client.containers.create, **kwargs)
        except Exception as exc:
            raise HarnessFault(f"container create failed: {type(exc).__name__}: {exc}") from exc

    def _send_payload(self, container: Any, archive: bytes) -> None:
        """Stream the payload into the container's stdin and close the write end.

        `put_archive` is not an option: Docker refuses the archive API outright
        on a read-only rootfs. stdin is a pipe, so neither the read-only rootfs
        nor the tmpfs workdir applies, and closing it is what tells the
        bootstrap's `tar -xf -` that the payload is complete.
        """
        attached = container.attach_socket(params={"stdin": 1, "stream": 1})
        raw = getattr(attached, "_sock", attached)
        # docker-py hands back a socket wrapping a live HTTPResponse. Closing the
        # socket first leaves the response flushing a closed file at GC time and
        # spraying "Exception ignored" over otherwise clean test output, so the
        # response goes first.
        response = getattr(attached, "_response", None)
        try:
            raw.sendall(archive)
            raw.shutdown(socket.SHUT_WR)
        finally:
            for closeable in (response, attached, raw):
                if closeable is not None:
                    with contextlib.suppress(Exception):
                        closeable.close()

    async def _materialize(self, container: Any, task: TaskDefinition, spec: TrialSpec) -> None:
        deadline = _now_ms() + task.limits.timeout_seconds * 1000
        try:
            archive = payload_module.build_payload(
                task,
                spec,
                suite_root=self._suite_root,
                harness_root=self._harness_root,
                sut_root=self._sut_root,
                deadline_unix_ms=deadline,
            )
            await _to_thread(self._send_payload, container, archive)
        except Exception as exc:
            raise HarnessFault(f"materializing inputs failed: {type(exc).__name__}: {exc}") from exc

    async def _wait(self, container: Any, timeout_seconds: int) -> tuple[bool, int | None]:
        """Wait for the container, killing it if the deadline passes.

        Cancelling the Python future does not stop a container: it keeps running,
        keeps holding resources, and keeps spending tokens. The container is what
        gets killed.
        """
        try:
            status = await asyncio.wait_for(
                _to_thread(container.wait), timeout=float(timeout_seconds)
            )
        except TimeoutError:
            await self._kill(container)
            return True, None
        except Exception as exc:
            raise HarnessFault(
                f"waiting on the container failed: {type(exc).__name__}: {exc}"
            ) from exc
        code = status.get("StatusCode") if isinstance(status, dict) else None
        return False, int(code) if code is not None else None

    async def _kill(self, container: Any) -> None:
        # Already dead is fine; `_destroy` still force-removes.
        with contextlib.suppress(Exception):
            await _to_thread(container.kill)

    async def _extract(self, container: Any, task: TaskDefinition, destination: Path) -> None:
        try:
            stream, _stat = await _to_thread(container.get_archive, task.environment.workdir)
            payload_module.extract_archive(stream, destination)
        except Exception as exc:
            raise HarnessFault(
                f"extracting final state failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def _destroy(self, container: Any) -> bool:
        """Remove the container unconditionally. Returns whether it worked."""
        try:
            await _to_thread(container.remove, force=True, v=True)
        except Exception:
            return False
        return True

    # -- classification ----------------------------------------------------

    def _read_adapter_result(
        self, state_dir: Path, task: TaskDefinition, exit_code: int | None
    ) -> AdapterResult:
        path = state_dir / payload_module.PAYLOAD_DIR / "result.json"
        if not path.is_file():
            return AdapterResult(
                completed=False,
                error="the agent produced no result",
                exit_code=exit_code,
            )
        try:
            result = AdapterResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HarnessFault(f"unreadable result.json: {exc}") from exc
        if result.exit_code is None and exit_code is not None:
            result = result.model_copy(update={"exit_code": exit_code})
        return result

    def _entrypoint_failure_detail(self, state_dir: Path) -> str:
        """Say *what* was misconfigured, not merely that something was."""
        from meridian.adapters.entrypoint import HARNESS_ERROR_FILE

        recorded = state_dir / payload_module.PAYLOAD_DIR / HARNESS_ERROR_FILE
        if recorded.is_file():
            return f"the entrypoint could not run: {recorded.read_text(encoding='utf-8').strip()}"
        return "the entrypoint could not run inside the container"

    def _classify(
        self, assertions: Sequence[AssertionResult], result: AdapterResult
    ) -> tuple[Outcome, str]:
        errored = [a for a in assertions if a.outcome is AssertionOutcome.ERROR]
        if errored:
            raise HarnessFault(f"assertion error: {errored[0].detail}")

        failed = [a for a in assertions if a.outcome is AssertionOutcome.FAIL]
        if failed:
            # The detail names actual versus expected, because it ends up in the
            # PR comment and is the only thing a developer reads.
            return Outcome.FAIL, "; ".join(a.detail for a in failed)

        if result.error:
            # The agent raised or gave up. Assertions may still have passed by
            # luck; they did not, or we would not be here.
            return Outcome.FAIL, f"agent error: {result.error}"

        return Outcome.PASS, ""

    def _efficiency(self, result: AdapterResult, started_ms: int) -> Efficiency:
        return Efficiency(
            turns=result.turns,
            tool_calls=len(result.tool_calls),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=_now_ms() - started_ms,
        )

    def _harness_error(
        self,
        spec: TrialSpec,
        started: int,
        attempt: int,
        detail: str,
        *,
        removed: bool = True,
    ) -> TrialResult:
        return TrialResult(
            run_id=spec.run_id,
            task_slug=spec.task_slug,
            trial_index=spec.trial_index,
            seed=spec.seed,
            outcome=Outcome.HARNESS_ERROR,
            detail=detail,
            attempts=attempt,
            container_removed=removed,
            started_unix_ms=started,
        )


def _entrypoint_failure_code() -> int:
    """The exit code the in-container entrypoint uses when it cannot run at all."""
    from meridian.adapters.entrypoint import EXIT_ENTRYPOINT_FAILED

    return EXIT_ENTRYPOINT_FAILED


def sweep_orphans(client: DockerClient, *, run_id: str | None = None) -> list[str]:
    """Remove containers and volumes a previous harness left behind.

    Written on day one because it is needed within a day of starting: a crashed
    or interrupted run leaves containers holding CPU and memory and volumes
    holding disk, and neither announces itself.
    """
    label = RUN_LABEL if run_id is None else f"{RUN_LABEL}={run_id}"
    removed: list[str] = []
    for container in client.containers.list(all=True, filters={"label": label}):
        with contextlib.suppress(Exception):
            container.remove(force=True, v=True)
            removed.append(f"container:{container.name}")
    for volume in client.volumes.list(filters={"label": label}):
        with contextlib.suppress(Exception):
            volume.remove(force=True)
            removed.append(f"volume:{volume.name}")
    return removed
