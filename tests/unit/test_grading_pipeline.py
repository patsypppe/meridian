"""The pipeline: dispatch, and containment of broken graders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian.grading.pipeline import KNOWN_KINDS, grade_task
from meridian.models.adapter import AdapterResult
from meridian.models.run import AssertionOutcome
from meridian.models.task import FileExists, JsonPathEquals, PytestAssertion, TaskDefinition
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE

pytestmark = pytest.mark.unit

RESULT = AdapterResult(completed=True, exit_code=0)


@pytest.fixture(scope="module")
def task() -> TaskDefinition:
    return load_suite(CHECKOUT_SUITE).task("expired-coupon")


def test_every_assertion_kind_in_the_schema_has_a_grader(task: TaskDefinition) -> None:
    """A kind with no grader is a task that could pass without being checked."""
    from meridian.models.task import OutcomeAssertion

    schema_kinds = {
        member.model_fields["kind"].annotation.__args__[0]  # type: ignore[union-attr]
        for member in OutcomeAssertion.__origin__.__args__  # type: ignore[attr-defined]
    }
    assert schema_kinds == set(KNOWN_KINDS)


def test_a_broken_grader_becomes_error_not_failure(task: TaskDefinition, tmp_path: Path) -> None:
    broken = task.model_copy(
        update={
            "outcome_assertions": (
                JsonPathEquals(
                    kind="json_path_equals", path="/etc/shadow", json_path="$.x", expected=1
                ),
            )
        }
    )
    results = grade_task(broken, tmp_path, RESULT)
    assert [r.outcome for r in results] == [AssertionOutcome.ERROR]
    assert "outside the workdir" in results[0].detail


def test_one_broken_assertion_does_not_hide_the_others(
    task: TaskDefinition, tmp_path: Path
) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "invoice-1042.json").write_text(json.dumps({"total_cents": 4999}))
    mixed = task.model_copy(
        update={
            "outcome_assertions": (
                JsonPathEquals(
                    kind="json_path_equals",
                    path="/work/out/invoice-1042.json",
                    json_path="$.total_cents",
                    expected=4999,
                ),
                PytestAssertion(kind="pytest", path="/work/tests"),
                FileExists(kind="file_exists", path="/work/out/absent", should_exist=True),
            )
        }
    )
    outcomes = [r.outcome for r in grade_task(mixed, tmp_path, RESULT)]
    assert outcomes == [
        AssertionOutcome.PASS,
        AssertionOutcome.ERROR,
        AssertionOutcome.FAIL,
    ]


def test_grading_never_raises(task: TaskDefinition, tmp_path: Path) -> None:
    """The pipeline contains failures so the runner can classify them."""
    assert grade_task(task, tmp_path, RESULT)
