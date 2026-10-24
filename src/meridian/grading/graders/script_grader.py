"""Grading by a benchmark-supplied verifier script that writes a reward.

Several benchmark ecosystems (Harbor and Terminal-Bench among them) verify a task
by running a shell script that writes a number to a reward file. This grader
supplies the decision half; running the script is the runner's job, in a
throwaway container with no network.

The decision is deliberately strict:

- the reward must be exactly 1 to pass; anything else the script legitimately
  reports is a failure of the task;
- a missing, empty, non-numeric or non-finite reward is a *broken verifier*, not a
  failed agent, and raises `GraderError` so the trial becomes a harness error.
  A verifier that crashed must never read as an agent that failed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from meridian.grading.graders.base import GraderError, failed, passed
from meridian.models.run import AssertionResult
from meridian.models.task import VerifierScript

MAX_OUTPUT = 1200
PASSING_REWARD = 1.0


@dataclass(frozen=True)
class ScriptOutcome:
    exit_code: int
    output: str
    reward_text: str | None
    # Set when the grading container itself failed, as opposed to the script.
    infra_error: str | None = None


class ScriptRunner(Protocol):
    """Runs a verifier script against extracted state, in isolation from the host."""

    def __call__(self, state_dir: Path, script_path: str, reward_path: str) -> ScriptOutcome: ...


def parse_reward(text: str | None, reward_path: str) -> float:
    if text is None or not text.strip():
        raise GraderError(f"the verifier script wrote no reward to {reward_path}")
    try:
        value = float(text.strip())
    except ValueError as exc:
        raise GraderError(
            f"the reward in {reward_path} is not a number: {text.strip()[:80]!r}"
        ) from exc
    if not math.isfinite(value):
        raise GraderError(f"the reward in {reward_path} is not finite: {text.strip()[:80]!r}")
    return value


def grade_verifier_script(
    assertion: VerifierScript,
    state_dir: Path,
    runner: ScriptRunner | None,
) -> AssertionResult:
    if runner is None:
        raise GraderError(
            "this task verifies with a script, but no sandboxed script runner is configured. "
            "Running a verifier on the host would give an evaluated agent code execution "
            "on the evaluating machine, so the assertion errors instead."
        )
    outcome = runner(state_dir, assertion.path, assertion.reward_path)
    if outcome.infra_error is not None:
        raise GraderError(f"the grading container failed: {outcome.infra_error}")

    reward = parse_reward(outcome.reward_text, assertion.reward_path)
    if reward == PASSING_REWARD:
        return passed(assertion.kind, f"{assertion.path} reported reward {reward:g}")
    tail = outcome.output[-MAX_OUTPUT:].strip()
    return failed(
        assertion.kind,
        f"{assertion.path} reported reward {reward:g} (exit {outcome.exit_code}): {tail}",
    )
