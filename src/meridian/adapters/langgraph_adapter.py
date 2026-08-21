"""The LangGraph reference adapter.

The contract is framework-shaped, never domain-shaped: the target is a compiled
graph invoked with `{"context": <the trial context>}`, and its final state is
read for a small, fixed set of keys. The adapter knows nothing about checkouts,
coupons, or invoices, and adding a second LangGraph agent requires no change
here.

This module is the only place in the repository that imports `langgraph`, and it
runs **inside the trial container**. The harness process never imports it.
"""

from __future__ import annotations

import time
from typing import Any

from meridian.adapters.base import AdapterSpec, AdapterSpecError
from meridian.models.adapter import AdapterCapabilities, AdapterResult, ToolCall, TrialContext

# LangGraph counts every node visit. A turn is two visits (act, tools) plus the
# fixed plan/finish nodes, so this bounds a graph at roughly 60 turns — well past
# any agent's own turn cap, so its recursion guard never fires first and gets
# misread as an agent error.
RECURSION_LIMIT = 128


class LangGraphAdapter:
    """Invokes a compiled LangGraph graph and reports what it did."""

    def __init__(self, spec: AdapterSpec) -> None:
        self._spec = spec

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=f"langgraph:{self._spec.module}",
            supports_streaming=False,
            supports_tools=True,
        )

    async def invoke(self, ctx: TrialContext) -> AdapterResult:
        target = self._spec.resolve_target()
        if not hasattr(target, "ainvoke") and not hasattr(target, "invoke"):
            raise AdapterSpecError(
                f"{self._spec} must resolve to a compiled LangGraph graph; "
                f"{type(target).__name__} has neither ainvoke nor invoke"
            )

        started = time.time()
        payload = {"context": ctx.model_dump()}
        config = {"recursion_limit": RECURSION_LIMIT}

        if hasattr(target, "ainvoke"):
            final: dict[str, Any] = await target.ainvoke(payload, config=config)
        else:  # pragma: no cover - synchronous graphs are legal but unused here
            final = target.invoke(payload, config=config)

        duration_ms = int((time.time() - started) * 1000)
        recorded = list(final.get("tool_calls") or [])
        return AdapterResult(
            completed=bool(final.get("completed")),
            turns=int(final.get("turns", 0)),
            tool_calls=tuple(
                ToolCall(
                    name=str(entry.get("name", "unknown")),
                    started_unix_ms=int(started * 1000),
                    duration_ms=duration_ms // max(1, len(recorded)),
                    ok=bool(entry.get("ok", False)),
                )
                for entry in recorded
            ),
            input_tokens=int(final.get("input_tokens", 0)),
            output_tokens=int(final.get("output_tokens", 0)),
            transcript_path=f"{ctx.workdir}/.meridian/transcript.json",
            error=final.get("error"),
        )

    async def teardown(self) -> None:
        return None
