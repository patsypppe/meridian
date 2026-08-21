"""Run a task's assertions against extracted final state.

The pipeline is dispatch plus error containment; the grading logic lives in the
individual graders. A grader that raises becomes an `ERROR` result, which the
trial runner escalates to a harness error — never a failure.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from meridian.grading.graders.base import GraderError, errored
from meridian.grading.graders.filesystem import grade_file_exists
from meridian.models.adapter import AdapterResult
from meridian.models.run import AssertionResult
from meridian.models.task import TaskDefinition

# kind -> grader. Registered explicitly rather than discovered, so an assertion
# kind that exists in the schema but has no grader fails loudly at grading time
# instead of being silently skipped — a skipped assertion is a task that passes
# without being checked.
GRADERS: dict[str, Callable[[Any, Path, str, AdapterResult], AssertionResult]] = {
    "file_exists": lambda a, state, workdir, _result: grade_file_exists(a, state, workdir),
}


def grade_task(
    task: TaskDefinition,
    state_dir: Path,
    result: AdapterResult,
) -> Sequence[AssertionResult]:
    """Evaluate every assertion. Never raises; a broken grader becomes ERROR."""
    workdir = task.environment.workdir
    outcomes: list[AssertionResult] = []
    for assertion in task.outcome_assertions:
        grader = GRADERS.get(assertion.kind)
        if grader is None:
            outcomes.append(
                errored(
                    assertion.kind,
                    f"no grader registered for assertion kind {assertion.kind!r}",
                )
            )
            continue
        try:
            outcomes.append(grader(assertion, state_dir, workdir, result))
        except GraderError as exc:
            outcomes.append(errored(assertion.kind, str(exc)))
        except Exception as exc:
            outcomes.append(errored(assertion.kind, f"{type(exc).__name__}: {exc}"))
    return tuple(outcomes)
