"""Run a task's assertions against extracted final state.

The pipeline is dispatch plus error containment; the grading logic lives in the
individual graders. A grader that raises becomes an `ERROR` result, which the
trial runner escalates to a harness error — never a failure.

Graders are registered explicitly rather than discovered. An assertion kind that
exists in the schema but has no grader then fails loudly at grading time instead
of being silently skipped, and a skipped assertion is a task that passes without
being checked.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from meridian.grading.graders.base import GraderError, errored
from meridian.grading.graders.filesystem import grade_file_exists, grade_file_matches
from meridian.grading.graders.json_state import grade_json_path_equals, grade_json_path_matches
from meridian.grading.graders.process import grade_exit_code
from meridian.grading.graders.pytest_grader import PytestRunner, grade_pytest
from meridian.grading.graders.sqlite import grade_sqlite_query_equals
from meridian.models.adapter import AdapterResult
from meridian.models.run import AssertionResult
from meridian.models.task import TaskDefinition

KNOWN_KINDS = frozenset(
    {
        "file_exists",
        "file_matches",
        "json_path_equals",
        "json_path_matches",
        "sqlite_query_equals",
        "exit_code",
        "pytest",
    }
)


def grade_assertion(
    assertion: object,
    state_dir: Path,
    workdir: str,
    result: AdapterResult,
    pytest_runner: PytestRunner | None,
) -> AssertionResult:
    """Dispatch one assertion to its grader."""
    kind = getattr(assertion, "kind", "")
    match kind:
        case "file_exists":
            return grade_file_exists(assertion, state_dir, workdir)  # type: ignore[arg-type]
        case "file_matches":
            return grade_file_matches(assertion, state_dir, workdir)  # type: ignore[arg-type]
        case "json_path_equals":
            return grade_json_path_equals(assertion, state_dir, workdir)  # type: ignore[arg-type]
        case "json_path_matches":
            return grade_json_path_matches(assertion, state_dir, workdir)  # type: ignore[arg-type]
        case "sqlite_query_equals":
            return grade_sqlite_query_equals(assertion, state_dir, workdir)  # type: ignore[arg-type]
        case "exit_code":
            return grade_exit_code(assertion, result)  # type: ignore[arg-type]
        case "pytest":
            return grade_pytest(assertion, state_dir, workdir, pytest_runner)  # type: ignore[arg-type]
        case _:
            raise GraderError(f"no grader registered for assertion kind {kind!r}")


def grade_task(
    task: TaskDefinition,
    state_dir: Path,
    result: AdapterResult,
    pytest_runner: PytestRunner | None = None,
) -> Sequence[AssertionResult]:
    """Evaluate every assertion. Never raises; a broken grader becomes ERROR."""
    workdir = task.environment.workdir
    outcomes: list[AssertionResult] = []
    for assertion in task.outcome_assertions:
        try:
            outcomes.append(grade_assertion(assertion, state_dir, workdir, result, pytest_runner))
        except GraderError as exc:
            outcomes.append(errored(str(getattr(assertion, "kind", "unknown")), str(exc)))
        except Exception as exc:
            outcomes.append(
                errored(
                    str(getattr(assertion, "kind", "unknown")),
                    f"{type(exc).__name__}: {exc}",
                )
            )
    return tuple(outcomes)
