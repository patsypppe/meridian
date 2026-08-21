"""A trivial adapter that runs an external process.

It exists to prove the interface is not LangGraph-shaped. If the abstraction
were leaking, this adapter could not satisfy it without special-casing, and the
`test_same_suite_runs_on_two_adapters` test would need a branch — which is the
signal to fix the interface, not the test.

The target attribute is a callable `(context) -> Sequence[str]` returning argv,
so the thing being evaluated does not have to be Python at all.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

from meridian.adapters.base import AdapterSpec, AdapterSpecError
from meridian.models.adapter import AdapterCapabilities, AdapterResult, ToolCall, TrialContext

STDOUT_TAIL_BYTES = 4096


class SubprocessAdapter:
    """Runs an argv produced by the adapter target."""

    def __init__(self, spec: AdapterSpec) -> None:
        self._spec = spec

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=f"subprocess:{self._spec.module}",
            supports_streaming=False,
            supports_tools=False,
        )

    async def invoke(self, ctx: TrialContext) -> AdapterResult:
        target = self._spec.resolve_target()
        if not callable(target):
            raise AdapterSpecError(
                f"{self._spec} must resolve to a callable returning argv, got {type(target)}"
            )
        argv = target(ctx)
        if not isinstance(argv, Sequence) or isinstance(argv, str) or not argv:
            raise AdapterSpecError(
                f"{self._spec} returned {argv!r}; expected a non-empty argv list"
            )

        started = time.time()
        process = await asyncio.create_subprocess_exec(
            *[str(part) for part in argv],
            cwd=ctx.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        duration_ms = int((time.time() - started) * 1000)
        tail = stdout[-STDOUT_TAIL_BYTES:].decode("utf-8", errors="replace")

        # A non-zero exit is an *agent* failure: the thing under evaluation ran
        # and did not succeed. It is never a harness error and never retried.
        return AdapterResult(
            completed=process.returncode == 0,
            turns=1,
            tool_calls=(
                ToolCall(
                    name=str(argv[0]),
                    started_unix_ms=int(started * 1000),
                    duration_ms=duration_ms,
                    ok=process.returncode == 0,
                ),
            ),
            transcript_path=f"{ctx.workdir}/.meridian/transcript.json",
            error=None if process.returncode == 0 else f"exit code {process.returncode}",
            exit_code=process.returncode,
            stdout_tail=tail,
        )

    async def teardown(self) -> None:
        return None
