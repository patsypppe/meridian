"""Run, trial, and outcome models.

The outcome taxonomy is the load-bearing part of this file. Getting it wrong
invalidates every number Meridian reports, and it goes wrong in one specific
direction: classifying an agent failure as something retryable, which produces
results better than reality while nothing appears to break.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Outcome(StrEnum):
    """How a trial ended.

    `PASS`, `FAIL`, and `TIMEOUT` are statements about the **agent** and are
    never retried — retrying an agent failure is how a harness reports numbers
    better than reality, which is the exact thing this product exists to prevent.

    `HARNESS_ERROR` is a statement about **Meridian** and is retried once.
    """

    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    HARNESS_ERROR = "harness_error"

    @property
    def is_retryable(self) -> bool:
        return self is Outcome.HARNESS_ERROR

    @property
    def counts_as_success(self) -> bool:
        return self is Outcome.PASS


class ErrorCause(StrEnum):
    """Why a trial was a `harness_error`: which part of the system failed.

    One undifferentiated bucket hides the difference between "the provider is
    rate limiting us" and "the grader is broken", which demand different fixes.
    Only meaningful when the outcome is `HARNESS_ERROR`.
    """

    SANDBOX = "sandbox"  # container, volume or archive operations failed
    ADAPTER_CONFIG = "adapter_config"  # the adapter spec was invalid
    ENTRYPOINT = "entrypoint"  # the in-container runtime produced no usable result
    GRADER = "grader"  # an assertion or grader raised: the check is broken
    MODEL_PROVIDER = "model_provider"  # the provider failed after the proxy's retries
    INTERNAL = "internal"  # anything else; a Meridian bug until shown otherwise


class AssertionOutcome(StrEnum):
    """One assertion's verdict.

    `ERROR` means the assertion itself is broken, not the agent. It escalates to
    a `HARNESS_ERROR` trial. Confusing "my assertion is broken" with "the agent
    is broken" is how a suite quietly becomes meaningless.
    """

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class AssertionResult(BaseModel):
    model_config = _FROZEN

    kind: str
    outcome: AssertionOutcome
    detail: str


class Efficiency(BaseModel):
    """Secondary signals. Recorded and reported; never pass/fail (Rule 2)."""

    model_config = _FROZEN

    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0


class TrialResult(BaseModel):
    """One trial, start to finish."""

    model_config = _FROZEN

    run_id: str
    task_slug: str
    trial_index: int
    seed: int
    outcome: Outcome
    detail: str = ""
    attempts: int = 1
    assertions: tuple[AssertionResult, ...] = ()
    efficiency: Efficiency = Efficiency()
    container_removed: bool = True
    cassette_hash: str | None = None
    started_unix_ms: int = 0
    # Set exactly when `outcome` is HARNESS_ERROR.
    error_cause: ErrorCause | None = None

    @model_validator(mode="after")
    def _cause_matches_outcome(self) -> TrialResult:
        if (self.outcome is Outcome.HARNESS_ERROR) != (self.error_cause is not None):
            raise ValueError(
                "error_cause is required for a harness_error and forbidden otherwise: "
                f"outcome={self.outcome}, error_cause={self.error_cause}"
            )
        return self

    @property
    def passed(self) -> bool:
        return self.outcome.counts_as_success


class TaskResult(BaseModel):
    """Every trial of one task, plus the counts the statistics engine needs."""

    model_config = _FROZEN

    task_slug: str
    trials: tuple[TrialResult, ...]

    @property
    def n(self) -> int:
        """Trials that produced a verdict about the agent.

        Harness errors are excluded from the denominator: a trial Meridian could
        not run is not evidence about the agent either way, and counting it as a
        failure would make an infrastructure blip look like a regression.
        """
        return sum(1 for t in self.trials if t.outcome is not Outcome.HARNESS_ERROR)

    @property
    def c(self) -> int:
        return sum(1 for t in self.trials if t.passed)

    @property
    def harness_errors(self) -> int:
        return sum(1 for t in self.trials if t.outcome is Outcome.HARNESS_ERROR)


class RunStatus(StrEnum):
    COMPLETE = "complete"
    HALTED_BUDGET = "halted_budget"
    ABORTED = "aborted"


class RunResult(BaseModel):
    """A whole run. Partial runs are first-class: a halted run still reports."""

    model_config = _FROZEN

    run_id: str
    suite_slug: str
    suite_version: int
    status: RunStatus
    k: int
    n_requested: int
    tasks: tuple[TaskResult, ...]
    excluded_task_slugs: tuple[str, ...] = ()
    detail: str = ""
    started_unix_ms: int = 0
    duration_ms: int = 0
    manifest_hash: str | None = None
    cost_cents: int = 0

    @property
    def total_trials(self) -> int:
        return sum(len(t.trials) for t in self.tasks)

    @property
    def harness_error_rate(self) -> float:
        """Fraction of attempted trials Meridian itself could not complete."""
        total = self.total_trials
        if total == 0:
            return 0.0
        return sum(t.harness_errors for t in self.tasks) / total

    @property
    def harness_errors_by_cause(self) -> dict[str, int]:
        """Harness errors counted per cause, most frequent first, for reports."""
        counts: dict[str, int] = {}
        for task in self.tasks:
            for trial in task.trials:
                if trial.error_cause is not None:
                    key = str(trial.error_cause)
                    counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    @property
    def harness_error_summary(self) -> str:
        """`4.3% (model_provider: 3)` -- the rate, and what the errors were."""
        rate = f"{self.harness_error_rate:.1%}"
        causes = ", ".join(f"{cause}: {n}" for cause, n in self.harness_errors_by_cause.items())
        return f"{rate} ({causes})" if causes else rate

    def task(self, slug: str) -> TaskResult:
        for candidate in self.tasks:
            if candidate.task_slug == slug:
                return candidate
        raise KeyError(f"run {self.run_id} has no task {slug!r}")


class TrialSpec(BaseModel):
    """Everything the runner needs to execute one trial."""

    model_config = _FROZEN

    run_id: str
    task_slug: str
    trial_index: int
    seed: int
    adapter_spec: str
    proxy_base_url: str = ""
    max_tokens: int = Field(default=60_000, ge=1)
