"""Assertions over what the process did, rather than what it wrote.

Still outcome grading (Rule 2): an exit code is a fact about the final state of
the run, not a step in the agent's reasoning. Nothing here inspects *how* the
agent reached that exit code.
"""

from __future__ import annotations

import re

from meridian.grading.graders.base import GraderError, failed, passed
from meridian.models.adapter import AdapterResult
from meridian.models.run import AssertionResult
from meridian.models.task import ExitCode

MAX_TAIL = 240


def grade_exit_code(assertion: ExitCode, result: AdapterResult) -> AssertionResult:
    if result.exit_code is None:
        return failed(
            assertion.kind,
            "the adapter reported no exit code; this assertion only applies to adapters "
            "that run a process",
        )
    if result.exit_code == assertion.expected:
        return passed(assertion.kind, f"exited {assertion.expected}")
    tail = result.stdout_tail[-MAX_TAIL:].strip()
    detail = f"exited {result.exit_code}, expected {assertion.expected}"
    return failed(assertion.kind, f"{detail}; last output: {tail!r}" if tail else detail)


def grade_stdout_matches(kind: str, pattern: str, result: AdapterResult) -> AssertionResult:
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise GraderError(f"invalid pattern {pattern!r}: {exc}") from exc
    if compiled.search(result.stdout_tail):
        return passed(kind, f"output matches /{pattern}/")
    return failed(
        kind,
        f"output does not match /{pattern}/; last output: {result.stdout_tail[-MAX_TAIL:]!r}",
    )
