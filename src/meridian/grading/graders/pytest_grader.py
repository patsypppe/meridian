"""Grading by running a test suite against the extracted state.

The decision logic here is pure. *Executing* pytest needs a container — the test
files come from a directory the agent controlled, so running them on the host
would hand an evaluated agent code execution on the machine doing the
evaluating.

So this module takes a `PytestRunner` and the runtime supplies one. If no runner
is configured the assertion **errors** rather than passing: an assertion that
cannot be evaluated has not been satisfied.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from meridian.grading.graders.base import GraderError, failed, passed
from meridian.models.run import AssertionResult
from meridian.models.task import PytestAssertion

MAX_OUTPUT = 400

# pytest's own exit codes. 5 means "no tests ran", which is a broken assertion
# rather than a passing one — a selector that matches nothing must never look
# like a suite that passed.
PYTEST_OK = 0
PYTEST_NO_TESTS_COLLECTED = 5


@dataclass(frozen=True)
class PytestOutcome:
    exit_code: int
    output: str


class PytestRunner(Protocol):
    """Runs pytest against extracted state, in isolation from the host."""

    def __call__(self, state_dir: Path, target: str, selector: str | None) -> PytestOutcome: ...


def grade_pytest(
    assertion: PytestAssertion,
    state_dir: Path,
    workdir: str,
    runner: PytestRunner | None,
) -> AssertionResult:
    if runner is None:
        raise GraderError(
            "this task asserts with pytest, but no sandboxed pytest runner is configured. "
            "Running agent-authored tests on the host would give an evaluated agent code "
            "execution on the evaluating machine, so the assertion errors instead."
        )

    outcome = runner(state_dir, assertion.path, assertion.selector)
    tail = outcome.output[-MAX_OUTPUT:].strip()

    if outcome.exit_code == PYTEST_OK:
        return passed(assertion.kind, f"pytest passed against {assertion.path}")
    if outcome.exit_code == PYTEST_NO_TESTS_COLLECTED:
        raise GraderError(
            f"pytest collected no tests at {assertion.path}"
            + (f" matching {assertion.selector!r}" if assertion.selector else "")
            + " — the assertion selects nothing, so it proves nothing"
        )
    return failed(
        assertion.kind, f"pytest exited {outcome.exit_code} against {assertion.path}: {tail}"
    )
