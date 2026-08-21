"""The adapter contract — deliberately narrow.

This is the abstraction that keeps Meridian framework-agnostic, so nothing in
this file may know what a graph, a chain, or a run loop is. If a framework type
appears here, the abstraction has leaked and every future adapter inherits the
leak.

These models also run **inside** the trial container, so this module imports
nothing beyond Pydantic.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class TrialContext(BaseModel):
    """Everything an agent is told about the trial it is running in."""

    model_config = _FROZEN

    run_id: str
    task_slug: str
    trial_index: int
    seed: int
    workdir: str
    prompt: str
    # Points at the Meridian proxy, never at a provider. The container holds no
    # credential; the proxy attaches it.
    model_base_url: str
    max_tokens: int
    deadline_unix_ms: int


class ToolCall(BaseModel):
    model_config = _FROZEN

    name: str
    started_unix_ms: int
    duration_ms: int
    ok: bool


class AdapterResult(BaseModel):
    """What the agent did. Never what it *achieved* — that is the grader's job."""

    model_config = _FROZEN

    completed: bool
    turns: int = 0
    tool_calls: Sequence[ToolCall] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    transcript_path: str = ""
    # An agent-level error: the agent tried and failed. NOT a harness error, and
    # therefore never retried.
    error: str | None = None
    exit_code: int | None = None
    stdout_tail: str = ""


class AdapterCapabilities(BaseModel):
    model_config = _FROZEN

    name: str
    supports_streaming: bool
    supports_tools: bool


@runtime_checkable
class AgentAdapter(Protocol):
    """The whole interface. Three methods; anything more is framework leakage."""

    def capabilities(self) -> AdapterCapabilities: ...

    async def invoke(self, ctx: TrialContext) -> AdapterResult: ...

    async def teardown(self) -> None: ...
