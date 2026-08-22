"""The gate decision rule.

A pure function. It takes numbers and configuration and returns a verdict; it
touches nothing else, and it is the most heavily tested function in the
repository — because it is the only one whose output someone acts on without
reading anything else.

The ordering of the checks is the design. Every guard that fires before the
comparison exists to answer the same question: *is this run evidence at all?* A
run that halted early, that Meridian largely failed to execute, or that was
judged against recordings predating the agent is not evidence, and reporting a
regression from it would be worse than reporting nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

PValueFn = Callable[[Mapping[str, float], Mapping[str, float]], float]


class Verdict(StrEnum):
    PASS = "pass"
    PASS_WITH_WARNING = "pass_with_warning"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"

    @property
    def blocks_merge(self) -> bool:
        return self is Verdict.FAIL


class GateInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_suite_passhat_k: float | None
    head_suite_passhat_k: float
    per_task_baseline: dict[str, float] = {}
    per_task_head: dict[str, float] = {}
    harness_error_rate: float = 0.0
    run_status: str = "complete"
    tolerance: float = 0.02
    require_significance: bool = True
    significance_level: float = 0.05
    fail_on_inconclusive: bool = False
    max_harness_error_rate: float = 0.05

    # The share of head trials that failed because the recording predates the
    # agent rather than because the agent got worse. Distinguishing the two is
    # the difference between "your change broke something" and "re-record".
    stale_cassette_rate: float = 0.0
    max_stale_cassette_rate: float = 0.10


def _inconclusive(g: GateInput, reason: str) -> tuple[Verdict, str]:
    return (Verdict.FAIL if g.fail_on_inconclusive else Verdict.INCONCLUSIVE, reason)


def decide(g: GateInput, p_value_fn: PValueFn) -> tuple[Verdict, str]:
    """Return the verdict and the sentence that explains it."""
    if g.run_status == "halted_budget":
        return _inconclusive(g, "run halted at its cost cap before completing")

    if g.harness_error_rate > g.max_harness_error_rate:
        return _inconclusive(
            g,
            f"harness error rate {g.harness_error_rate:.1%} exceeds "
            f"{g.max_harness_error_rate:.1%}; too much of this run did not execute "
            f"to draw a conclusion from it",
        )

    if g.stale_cassette_rate > g.max_stale_cassette_rate:
        # Replaying a *changed* agent against recordings made from the previous
        # one produces failures that look exactly like a regression and are not
        # one. Saying so is far more useful than a FAIL nobody can reproduce.
        return _inconclusive(
            g,
            f"{g.stale_cassette_rate:.1%} of trials missed their recording: these "
            f"cassettes predate this agent. Re-record, or gate with "
            f"--proxy-mode record",
        )

    if g.baseline_suite_passhat_k is None:
        # A gate that fails on its first run gets disabled on its second day.
        return Verdict.PASS, "no baseline for this commit; recording head as the new baseline"

    drop = g.baseline_suite_passhat_k - g.head_suite_passhat_k
    if drop <= g.tolerance:
        return (
            Verdict.PASS,
            f"suite pass^k moved {-drop:+.3f} "
            f"({g.baseline_suite_passhat_k:.3f} → {g.head_suite_passhat_k:.3f}), "
            f"within the {g.tolerance:.3f} tolerance",
        )

    if g.require_significance:
        p = p_value_fn(g.per_task_baseline, g.per_task_head)
        if p > g.significance_level:
            return (
                Verdict.PASS_WITH_WARNING,
                f"suite pass^k dropped {drop:.3f} but p={p:.3f} > "
                f"{g.significance_level:.3f}; the drop is not distinguishable from noise "
                f"across this task set",
            )
        return (
            Verdict.FAIL,
            f"suite pass^k dropped {drop:.3f} "
            f"({g.baseline_suite_passhat_k:.3f} → {g.head_suite_passhat_k:.3f}), p={p:.3f}",
        )

    return (
        Verdict.FAIL,
        f"suite pass^k dropped {drop:.3f} "
        f"({g.baseline_suite_passhat_k:.3f} → {g.head_suite_passhat_k:.3f}), "
        f"beyond the {g.tolerance:.3f} tolerance",
    )


class FlippedTasks(BaseModel):
    """Tasks that crossed the line, reported regardless of the verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Crossed 1.0 downward: it used to work every time and now does not.
    broken: tuple[str, ...] = ()
    # Reached zero: it used to work sometimes and now never does. At least as
    # loud a signal as the 1.0 crossing, and `HANDOFF §8.4` does not name it —
    # a task going 0.4 -> 0.0 would otherwise regress in the table with nothing
    # in the headline, which is the line most reviewers read.
    now_always_fails: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()
    regressed: tuple[tuple[str, float, float], ...] = ()
    improved: tuple[tuple[str, float, float], ...] = ()
    only_in_baseline: tuple[str, ...] = ()
    only_in_head: tuple[str, ...] = ()

    @property
    def anything_moved(self) -> bool:
        return bool(self.regressed or self.improved or self.only_in_baseline or self.only_in_head)


def flipped_tasks(
    baseline: Mapping[str, float],
    head: Mapping[str, float],
    *,
    epsilon: float = 1e-9,
) -> FlippedTasks:
    """Which tasks changed, and how.

    `broken` and `fixed` are the 1.0 boundary crossings — a task that used to
    work every time and now does not, or the reverse. `now_always_fails` catches
    the other loud case: a task that used to work *sometimes* and now never does.

    These lists are the content of the PR comment and the thing a reviewer
    actually reads.
    """
    broken: list[str] = []
    now_always_fails: list[str] = []
    fixed: list[str] = []
    regressed: list[tuple[str, float, float]] = []
    improved: list[tuple[str, float, float]] = []

    for slug in sorted(set(baseline) & set(head)):
        before, after = baseline[slug], head[slug]
        if after < before - epsilon:
            regressed.append((slug, before, after))
            if before >= 1.0 - epsilon:
                broken.append(slug)
            elif after <= epsilon:
                now_always_fails.append(slug)
        elif after > before + epsilon:
            improved.append((slug, before, after))
            if after >= 1.0 - epsilon:
                fixed.append(slug)

    return FlippedTasks(
        broken=tuple(broken),
        now_always_fails=tuple(now_always_fails),
        fixed=tuple(fixed),
        regressed=tuple(regressed),
        improved=tuple(improved),
        # Named explicitly: a silently shrinking task set is how a gate stops
        # working without anyone noticing.
        only_in_baseline=tuple(sorted(set(baseline) - set(head))),
        only_in_head=tuple(sorted(set(head) - set(baseline))),
    )
