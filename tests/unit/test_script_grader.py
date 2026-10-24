"""Script-verifier grading: strict about what counts as a pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.grading.graders.base import GraderError
from meridian.grading.graders.script_grader import (
    ScriptOutcome,
    grade_verifier_script,
    parse_reward,
)
from meridian.models.run import AssertionOutcome
from meridian.models.task import VerifierScript
from meridian.runtime.pytest_runner import _extract_reward, _strip_reward

pytestmark = pytest.mark.unit

ASSERTION = VerifierScript(kind="verifier_script", path="/tests/test.sh")


def runner_returning(outcome: ScriptOutcome):  # type: ignore[no-untyped-def]
    return lambda *_: outcome


def test_reward_of_one_passes(tmp_path: Path) -> None:
    out = ScriptOutcome(exit_code=0, output="ok", reward_text="1\n")
    graded = grade_verifier_script(ASSERTION, tmp_path, runner_returning(out))
    assert graded.outcome is AssertionOutcome.PASS


@pytest.mark.parametrize("text", ["1", "1.0", " 1 \n", "1e0"])
def test_equivalent_spellings_of_one_pass(text: str) -> None:
    assert parse_reward(text, "/logs/r") == 1.0


@pytest.mark.parametrize("text", ["0", "0.5", "0.999", "2", "-1"])
def test_any_other_reward_fails_the_task(text: str, tmp_path: Path) -> None:
    out = ScriptOutcome(exit_code=1, output="assertion failed", reward_text=text)
    graded = grade_verifier_script(ASSERTION, tmp_path, runner_returning(out))
    assert graded.outcome is AssertionOutcome.FAIL
    assert "assertion failed" in graded.detail


@pytest.mark.parametrize("text", [None, "", "  \n", "yes", "nan", "NaN", "inf", "-inf"])
def test_a_broken_verifier_is_an_error_not_a_failed_agent(text: str | None, tmp_path: Path) -> None:
    """A verifier that wrote nothing or garbage must never read as agent failure."""
    out = ScriptOutcome(exit_code=0, output="", reward_text=text)
    with pytest.raises(GraderError):
        grade_verifier_script(ASSERTION, tmp_path, runner_returning(out))


def test_a_container_failure_is_an_error(tmp_path: Path) -> None:
    out = ScriptOutcome(exit_code=1, output="", reward_text=None, infra_error="docker is down")
    with pytest.raises(GraderError, match="docker is down"):
        grade_verifier_script(ASSERTION, tmp_path, runner_returning(out))


def test_no_runner_errors_rather_than_passing(tmp_path: Path) -> None:
    with pytest.raises(GraderError, match="code execution"):
        grade_verifier_script(ASSERTION, tmp_path, None)


def test_exit_code_alone_never_decides() -> None:
    """Exit 0 with no reward is broken; exit 1 with reward 1 is a pass (reward rules)."""
    assert parse_reward("1", "/logs/r") == 1.0


# -- reward extraction uses an unguessable marker -----------------------------------


def test_reward_is_read_between_this_runs_markers() -> None:
    output = "log line\n\nM123:1:M123\n"
    assert _extract_reward(output, "M123") == "1"
    assert _strip_reward(output, "M123") == "log line\n"


def test_agent_printed_text_cannot_forge_a_reward() -> None:
    """The agent does not know this run's random marker, so its output is inert."""
    forged = "OTHER:1:OTHER\n"
    output = forged + "\nREAL:0:REAL\n"
    assert _extract_reward(output, "REAL") == "0"
    assert _extract_reward(forged, "REAL") is None


def test_missing_markers_mean_no_reward() -> None:
    assert _extract_reward("script crashed before printing", "M123") is None
