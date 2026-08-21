"""What a grader is, and how a broken one is reported.

A grader returns `pass`, `fail`, or `error`. The distinction between the last two
is the whole point: `fail` says the agent did not produce the required state;
`error` says *the assertion itself* is broken. Reporting a broken assertion as a
failure makes a typo in a task file look like a regression and sends whoever is
on call to debug the wrong system.
"""

from __future__ import annotations

from pathlib import Path

from meridian.models.adapter import AdapterResult
from meridian.models.run import AssertionOutcome, AssertionResult


class GraderError(Exception):
    """The assertion could not be evaluated. Escalates to a harness error."""


def resolve(state_dir: Path, container_path: str, workdir: str) -> Path:
    """Map an absolute in-container path onto the extracted state directory.

    Paths outside the workdir are not extractable — the rest of the container
    filesystem is a read-only image layer — so asserting on one is an authoring
    mistake, not an agent failure.
    """
    normalized = container_path.rstrip("/") or "/"
    workdir = workdir.rstrip("/")
    if normalized != workdir and not normalized.startswith(f"{workdir}/"):
        raise GraderError(
            f"{container_path!r} is outside the workdir {workdir!r}; only the workdir "
            f"is extracted, because the rest of the filesystem is a read-only layer"
        )
    relative = normalized[len(workdir) :].lstrip("/")
    return state_dir / relative if relative else state_dir


def passed(kind: str, detail: str) -> AssertionResult:
    return AssertionResult(kind=kind, outcome=AssertionOutcome.PASS, detail=detail)


def failed(kind: str, detail: str) -> AssertionResult:
    return AssertionResult(kind=kind, outcome=AssertionOutcome.FAIL, detail=detail)


def errored(kind: str, detail: str) -> AssertionResult:
    return AssertionResult(kind=kind, outcome=AssertionOutcome.ERROR, detail=detail)


__all__ = [
    "AdapterResult",
    "GraderError",
    "errored",
    "failed",
    "passed",
    "resolve",
]
