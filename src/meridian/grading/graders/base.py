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

    **The containment check is a security boundary, not tidiness.** A task file
    is attacker-controlled in the case Meridian is built for: a pull request
    changes a task, CI runs the gate on it, and the grader's failure detail is
    posted to the PR as a comment. A prefix test alone is not enough —
    ``/work/../../etc/hosts`` starts with ``/work/`` and still escapes once
    joined — so the resolved path is checked against the state directory itself,
    the same way `payload.extract_archive` checks tar members.
    """
    normalized = container_path.rstrip("/") or "/"
    workdir = workdir.rstrip("/")
    if normalized != workdir and not normalized.startswith(f"{workdir}/"):
        raise GraderError(
            f"{container_path!r} is outside the workdir {workdir!r}; only the workdir "
            f"is extracted, because the rest of the filesystem is a read-only layer"
        )
    relative = normalized[len(workdir) :].lstrip("/")
    target = state_dir / relative if relative else state_dir
    # `resolve()` collapses `..` and follows symlinks, so this catches both a
    # traversal written into the task and one an agent planted in the workdir it
    # controls. Never render the resolved path back to the caller: on a hit it
    # names a host path, and that detail is bound for a public PR comment.
    if not target.resolve().is_relative_to(state_dir.resolve()):
        raise GraderError(
            f"{container_path!r} escapes the workdir {workdir!r} once its path "
            f"segments are applied; an assertion may only read state the trial "
            f"produced"
        )
    return target


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
