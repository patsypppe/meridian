"""Every grader, with a passing case and a failing case.

That is the coverage rule for this package, stated as behaviour rather than a
percentage: these are the pure functions the entire product's verdicts rest on.

The distinction the tests care about most is `fail` versus `error`. `fail` says
the agent did not produce the required state. `error` says *the assertion* is
broken. Reporting the second as the first makes a typo in a task file look like a
regression and sends whoever is on call to debug the wrong system.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from meridian.grading.graders.base import GraderError, resolve
from meridian.grading.graders.filesystem import grade_file_exists, grade_file_matches
from meridian.grading.graders.json_state import grade_json_path_equals, grade_json_path_matches
from meridian.grading.graders.process import grade_exit_code, grade_stdout_matches
from meridian.grading.graders.pytest_grader import PytestOutcome, grade_pytest
from meridian.grading.graders.sqlite import grade_sqlite_query_equals
from meridian.models.adapter import AdapterResult
from meridian.models.run import AssertionOutcome
from meridian.models.task import (
    ExitCode,
    FileExists,
    FileMatches,
    JsonPathEquals,
    JsonPathMatches,
    PytestAssertion,
    SqliteQueryEquals,
)

pytestmark = pytest.mark.unit

WORKDIR = "/work"


@pytest.fixture
def state(tmp_path: Path) -> Path:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "invoice-1042.json").write_text(
        json.dumps(
            {
                "order_id": 1042,
                "total_cents": 4999,
                "coupon": {"applied": False, "decline_reason": "coupon SUMMER20 EXPIRED"},
            }
        )
    )
    (tmp_path / "out" / "notes.txt").write_text("declined the coupon\nand charged full price\n")
    connection = sqlite3.connect(tmp_path / "store.db")
    connection.executescript(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT);"
        "INSERT INTO orders VALUES (1042, 'invoiced');"
    )
    connection.commit()
    connection.close()
    return tmp_path


# -- path resolution -----------------------------------------------------------


def test_a_path_outside_the_workdir_is_a_broken_assertion(state: Path) -> None:
    """Only the workdir is extracted; the rest is a read-only image layer."""
    with pytest.raises(GraderError, match="outside the workdir"):
        resolve(state, "/etc/passwd", WORKDIR)


# -- file_exists ---------------------------------------------------------------


def test_file_exists_passes(state: Path) -> None:
    assertion = FileExists(kind="file_exists", path="/work/out/notes.txt", should_exist=True)
    assert grade_file_exists(assertion, state, WORKDIR).outcome is AssertionOutcome.PASS


def test_file_exists_fails_when_absent(state: Path) -> None:
    assertion = FileExists(kind="file_exists", path="/work/out/nope.txt", should_exist=True)
    result = grade_file_exists(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "does not exist" in result.detail


def test_should_not_exist_passes_when_absent(state: Path) -> None:
    assertion = FileExists(kind="file_exists", path="/work/marker.txt", should_exist=False)
    assert grade_file_exists(assertion, state, WORKDIR).outcome is AssertionOutcome.PASS


def test_should_not_exist_fails_and_says_how_much_state_leaked(state: Path) -> None:
    """The contamination probe's failing direction, at the grader level."""
    (state / "marker.txt").write_text("meridian-was-here")
    assertion = FileExists(kind="file_exists", path="/work/marker.txt", should_exist=False)
    result = grade_file_exists(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "17 bytes" in result.detail


# -- file_matches --------------------------------------------------------------


def test_file_matches_passes(state: Path) -> None:
    assertion = FileMatches(kind="file_matches", path="/work/out/notes.txt", pattern="declined")
    assert grade_file_matches(assertion, state, WORKDIR).outcome is AssertionOutcome.PASS


def test_file_matches_fails_and_quotes_the_file(state: Path) -> None:
    assertion = FileMatches(kind="file_matches", path="/work/out/notes.txt", pattern="honoured")
    result = grade_file_matches(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "declined the coupon" in result.detail


def test_dotall_is_off_unless_asked_for(state: Path) -> None:
    """Defaulting DOTALL on turns a precise assertion into a loose one."""
    strict = FileMatches(kind="file_matches", path="/work/out/notes.txt", pattern="coupon.and")
    assert grade_file_matches(strict, state, WORKDIR).outcome is AssertionOutcome.FAIL

    loose = strict.model_copy(update={"dotall": True})
    assert grade_file_matches(loose, state, WORKDIR).outcome is AssertionOutcome.PASS


def test_an_uncompilable_pattern_is_a_broken_assertion(state: Path) -> None:
    assertion = FileMatches(kind="file_matches", path="/work/out/notes.txt", pattern="[")
    with pytest.raises(GraderError, match="invalid pattern"):
        grade_file_matches(assertion, state, WORKDIR)


# -- json_path_equals ----------------------------------------------------------


def test_json_path_equals_passes(state: Path) -> None:
    assertion = JsonPathEquals(
        kind="json_path_equals",
        path="/work/out/invoice-1042.json",
        json_path="$.total_cents",
        expected=4999,
    )
    assert grade_json_path_equals(assertion, state, WORKDIR).outcome is AssertionOutcome.PASS


def test_json_path_equals_names_actual_and_expected(state: Path) -> None:
    """The detail ends up in the PR comment and is often all a developer reads."""
    assertion = JsonPathEquals(
        kind="json_path_equals",
        path="/work/out/invoice-1042.json",
        json_path="$.total_cents",
        expected=3999,
    )
    result = grade_json_path_equals(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "4999" in result.detail and "3999" in result.detail


def test_key_order_and_whitespace_do_not_matter(tmp_path: Path) -> None:
    """Grading formatting rather than outcome would punish a correct agent."""
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x.json").write_text('{\n   "b": 2,\n\n  "a": {"z": 1, "y": 2}\n}\n')
    assertion = JsonPathEquals(
        kind="json_path_equals", path="/work/out/x.json", json_path="$.a", expected={"y": 2, "z": 1}
    )
    assert grade_json_path_equals(assertion, tmp_path, WORKDIR).outcome is AssertionOutcome.PASS


def test_a_missing_file_is_an_agent_failure(state: Path) -> None:
    assertion = JsonPathEquals(
        kind="json_path_equals", path="/work/out/absent.json", json_path="$.x", expected=1
    )
    result = grade_json_path_equals(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "never written" in result.detail


def test_unparseable_json_is_an_agent_failure(tmp_path: Path) -> None:
    """The agent wrote something and it is not JSON. That is the agent's problem."""
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x.json").write_text("{not json")
    assertion = JsonPathEquals(
        kind="json_path_equals", path="/work/out/x.json", json_path="$.x", expected=1
    )
    result = grade_json_path_equals(assertion, tmp_path, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "not valid JSON" in result.detail


def test_a_malformed_json_path_is_a_broken_assertion(state: Path) -> None:
    assertion = JsonPathEquals(
        kind="json_path_equals",
        path="/work/out/invoice-1042.json",
        json_path="$[[[",
        expected=1,
    )
    with pytest.raises(GraderError, match="invalid json_path"):
        grade_json_path_equals(assertion, state, WORKDIR)


# -- json_path_matches ---------------------------------------------------------


def test_json_path_matches_passes_case_insensitively(state: Path) -> None:
    assertion = JsonPathMatches(
        kind="json_path_matches",
        path="/work/out/invoice-1042.json",
        json_path="$.coupon.decline_reason",
        pattern="(?i)expired",
    )
    assert grade_json_path_matches(assertion, state, WORKDIR).outcome is AssertionOutcome.PASS


def test_json_path_matches_fails_and_shows_the_value(state: Path) -> None:
    assertion = JsonPathMatches(
        kind="json_path_matches",
        path="/work/out/invoice-1042.json",
        json_path="$.coupon.decline_reason",
        pattern="fraudulent",
    )
    result = grade_json_path_matches(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "EXPIRED" in result.detail


def test_a_path_selecting_nothing_fails_and_shows_the_document(state: Path) -> None:
    assertion = JsonPathMatches(
        kind="json_path_matches",
        path="/work/out/invoice-1042.json",
        json_path="$.coupon.absent_field",
        pattern="anything",
    )
    result = grade_json_path_matches(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "nothing at" in result.detail


# -- sqlite_query_equals -------------------------------------------------------


def test_sqlite_query_equals_passes(state: Path) -> None:
    assertion = SqliteQueryEquals(
        kind="sqlite_query_equals",
        database="/work/store.db",
        query="SELECT status FROM orders WHERE id = 1042",
        expected=(("invoiced",),),
    )
    assert grade_sqlite_query_equals(assertion, state, WORKDIR).outcome is AssertionOutcome.PASS


def test_sqlite_query_equals_names_actual_and_expected(state: Path) -> None:
    assertion = SqliteQueryEquals(
        kind="sqlite_query_equals",
        database="/work/store.db",
        query="SELECT status FROM orders WHERE id = 1042",
        expected=(("pending",),),
    )
    result = grade_sqlite_query_equals(assertion, state, WORKDIR)
    assert result.outcome is AssertionOutcome.FAIL
    assert "invoiced" in result.detail and "pending" in result.detail


def test_a_missing_database_is_an_agent_failure(state: Path) -> None:
    assertion = SqliteQueryEquals(
        kind="sqlite_query_equals",
        database="/work/absent.db",
        query="SELECT 1",
        expected=((1,),),
    )
    assert grade_sqlite_query_equals(assertion, state, WORKDIR).outcome is AssertionOutcome.FAIL


def test_a_query_that_will_not_run_is_a_broken_assertion(state: Path) -> None:
    assertion = SqliteQueryEquals(
        kind="sqlite_query_equals",
        database="/work/store.db",
        query="SELECT * FROM no_such_table",
        expected=(),
    )
    with pytest.raises(GraderError, match="query failed"):
        grade_sqlite_query_equals(assertion, state, WORKDIR)


def test_grading_cannot_alter_the_state_it_judges(state: Path) -> None:
    """A grader that mutates the evidence disagrees with its own second run."""
    assertion = SqliteQueryEquals(
        kind="sqlite_query_equals",
        database="/work/store.db",
        query="UPDATE orders SET status = 'tampered' WHERE id = 1042",
        expected=(),
    )
    with pytest.raises(GraderError):
        grade_sqlite_query_equals(assertion, state, WORKDIR)

    check = SqliteQueryEquals(
        kind="sqlite_query_equals",
        database="/work/store.db",
        query="SELECT status FROM orders WHERE id = 1042",
        expected=(("invoiced",),),
    )
    assert grade_sqlite_query_equals(check, state, WORKDIR).outcome is AssertionOutcome.PASS


# -- exit_code and stdout ------------------------------------------------------


def test_exit_code_passes() -> None:
    assertion = ExitCode(kind="exit_code", expected=0)
    result = AdapterResult(completed=True, exit_code=0)
    assert grade_exit_code(assertion, result).outcome is AssertionOutcome.PASS


def test_exit_code_fails_and_quotes_the_output() -> None:
    assertion = ExitCode(kind="exit_code", expected=0)
    result = AdapterResult(completed=False, exit_code=3, stdout_tail="the agent could not do it")
    graded = grade_exit_code(assertion, result)
    assert graded.outcome is AssertionOutcome.FAIL
    assert "could not do it" in graded.detail


def test_exit_code_against_an_adapter_that_runs_no_process_fails_clearly() -> None:
    assertion = ExitCode(kind="exit_code", expected=0)
    graded = grade_exit_code(assertion, AdapterResult(completed=True))
    assert graded.outcome is AssertionOutcome.FAIL
    assert "no exit code" in graded.detail


def test_stdout_matches_both_ways() -> None:
    result = AdapterResult(completed=True, stdout_tail="wrote invoice 1042")
    assert grade_stdout_matches("stdout", "invoice", result).outcome is AssertionOutcome.PASS
    assert grade_stdout_matches("stdout", "refusal", result).outcome is AssertionOutcome.FAIL


# -- pytest --------------------------------------------------------------------


def test_pytest_passes(state: Path) -> None:
    assertion = PytestAssertion(kind="pytest", path="/work/tests")
    outcome = PytestOutcome(exit_code=0, output="3 passed")
    graded = grade_pytest(assertion, state, WORKDIR, lambda *_: outcome)
    assert graded.outcome is AssertionOutcome.PASS


def test_pytest_fails_and_quotes_the_output(state: Path) -> None:
    assertion = PytestAssertion(kind="pytest", path="/work/tests")
    outcome = PytestOutcome(exit_code=1, output="E  assert 4999 == 3999")
    graded = grade_pytest(assertion, state, WORKDIR, lambda *_: outcome)
    assert graded.outcome is AssertionOutcome.FAIL
    assert "4999" in graded.detail


def test_collecting_no_tests_is_a_broken_assertion(state: Path) -> None:
    """A selector that matches nothing must never look like a suite that passed."""
    assertion = PytestAssertion(kind="pytest", path="/work/tests", selector="test_absent")
    outcome = PytestOutcome(exit_code=5, output="no tests ran")
    with pytest.raises(GraderError, match="proves nothing"):
        grade_pytest(assertion, state, WORKDIR, lambda *_: outcome)


def test_pytest_without_a_sandbox_errors_rather_than_passing(state: Path) -> None:
    """An assertion that cannot be evaluated has not been satisfied."""
    assertion = PytestAssertion(kind="pytest", path="/work/tests")
    with pytest.raises(GraderError, match="code execution"):
        grade_pytest(assertion, state, WORKDIR, None)
