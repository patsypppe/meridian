"""The PR comment.

This is the only Meridian output most people will ever read, so it is ordered by
what a reviewer needs: the verdict, then which tasks flipped, then why they
failed in the grader's own words, and only then the numbers and the pins.

A failing check that does not explain itself gets disabled within a week.
"""

from __future__ import annotations

import math
from statistics import NormalDist

from meridian.gate.decide import FlippedTasks, Verdict
from meridian.models.run import RunResult
from meridian.runtime.secrets import redact
from meridian.stats.passk import pass_hat_k
from meridian.stats.power import (
    DEFAULT_ALPHA,
    DEFAULT_POWER,
    minimum_detectable_effect,
    paired_differences,
    required_tasks,
)

BADGE = {
    Verdict.PASS: "✅ **PASS**",
    Verdict.PASS_WITH_WARNING: "⚠️ **PASS (with warning)**",
    Verdict.FAIL: "❌ **FAIL**",
    Verdict.INCONCLUSIVE: "🟡 **INCONCLUSIVE**",
}

MAX_FAILURE_LINES = 8

# The same one-sided z-sum `power.py` uses, recovered rather than restated so the
# two cannot drift into quoting different arithmetic at the reader.
_Z_SUM = NormalDist().inv_cdf(1.0 - DEFAULT_ALPHA) + NormalDist().inv_cdf(DEFAULT_POWER)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _task_table(baseline: dict[str, float], head: dict[str, float], k: int) -> list[str]:
    slugs = sorted(set(baseline) | set(head))
    lines = [f"| task | baseline pass^{k} | head pass^{k} | |", "|---|---|---|---|"]
    for slug in slugs:
        before = baseline.get(slug)
        after = head.get(slug)
        if before is None:
            marker = "new"
        elif after is None:
            marker = "**absent from head**"
        elif after < before - 1e-9:
            marker = "🔻 regressed"
        elif after > before + 1e-9:
            marker = "🔺 improved"
        else:
            marker = ""
        lines.append(f"| `{slug}` | {_fmt(before)} | {_fmt(after)} | {marker} |")
    return lines


def failure_details(run: RunResult, limit: int = MAX_FAILURE_LINES) -> list[str]:
    """Why the failing trials failed, in the grader's words.

    Redacted on the way out: a comment is public, and a secret is only secret
    until it is printed once.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for task in run.tasks:
        for trial in task.trials:
            if trial.passed or not trial.detail:
                continue
            first = trial.detail.split(";")[0].strip()
            key = f"{task.task_slug}:{first}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- `{task.task_slug}` — {redact(first)}")
            if len(lines) >= limit:
                return lines
    return lines


def sensitivity_note(
    baseline: dict[str, float], head: dict[str, float], *, target: float
) -> list[str]:
    """State what this run could and could not have seen.

    A verdict without this is half a sentence. `PASS` from a suite too small to
    resolve the regression you are worried about is not evidence that nothing
    broke — and a reviewer has no way to tell those apart from the badge alone.

    The second half is the actionable one: the remedy for an insensitive gate is
    a number of tasks to write, not a threshold to loosen.
    """
    differences = paired_differences(baseline, head)
    mde = minimum_detectable_effect(differences)
    if mde is None:
        return [
            f"**Sensitivity.** Too few comparable tasks ({len(differences)}) to estimate "
            f"what this run could detect. Treat the verdict as directional.",
            "",
        ]

    note = (
        f"**Sensitivity.** This run could reliably detect a suite-level drop of "
        f"**{mde:.3f}** or larger ({DEFAULT_POWER:.0%} power, one-sided "
        f"alpha={DEFAULT_ALPHA}). A real regression smaller than that would most "
        f"likely have passed."
    )
    if mde > target:
        spread = mde * math.sqrt(len(differences)) / _Z_SUM
        needed = required_tasks(spread, target)
        note += (
            f" Resolving the configured tolerance of {target:.3f} would take about "
            f"**{needed} comparable tasks**; this comparison has {len(differences)}."
        )
    return [note, ""]


def render(
    *,
    verdict: Verdict,
    reason: str,
    flipped: FlippedTasks,
    head: RunResult,
    baseline_scores: dict[str, float],
    head_scores: dict[str, float],
    baseline_suite: float | None,
    head_suite: float,
    baseline_run_id: str | None,
    manifest_hash: str | None = None,
    tolerance: float = 0.02,
) -> str:
    """Render the comment body."""
    k = head.k
    lines: list[str] = [
        "## Meridian gate",
        "",
        f"{BADGE[verdict]} — {reason}",
        "",
    ]

    if flipped.broken:
        # The headline. A task that used to work every time and now does not is
        # what a reviewer is here to find out.
        names = ", ".join(f"`{slug}`" for slug in flipped.broken)
        lines += [f"**Broken by this change** (used to pass every trial): {names}", ""]
    if flipped.now_always_fails:
        names = ", ".join(f"`{slug}`" for slug in flipped.now_always_fails)
        lines += [f"**Now fails every trial:** {names}", ""]
    if flipped.fixed:
        names = ", ".join(f"`{slug}`" for slug in flipped.fixed)
        lines += [f"**Fixed by this change:** {names}", ""]
    if flipped.only_in_baseline or flipped.only_in_head:
        # Named so a shrinking task set cannot go unnoticed.
        if flipped.only_in_baseline:
            gone = ", ".join(f"`{slug}`" for slug in flipped.only_in_baseline)
            lines.append(f"**Not run on head** (excluded from the comparison): {gone}")
        if flipped.only_in_head:
            new = ", ".join(f"`{slug}`" for slug in flipped.only_in_head)
            lines.append(f"**New in head** (excluded from the comparison): {new}")
        lines.append("")

    details = failure_details(head)
    if details:
        lines += ["<details><summary>Failing trials in this run</summary>", ""]
        lines += details
        lines += ["", "</details>", ""]

    lines += _task_table(baseline_scores, head_scores, k)
    lines += [
        "",
        "| | baseline | head |",
        "|---|---|---|",
        f"| suite pass^{k} | {_fmt(baseline_suite)} | {_fmt(head_suite)} |",
        "",
    ]
    lines += sensitivity_note(baseline_scores, head_scores, target=tolerance)
    lines += [
        f"`n={head.n_requested}` · `k={k}` · status `{head.status}` · "
        f"harness errors {head.harness_error_rate:.1%} · cost {head.cost_cents}c",
        "",
    ]

    provenance = [f"head run `{head.run_id}`"]
    if baseline_run_id:
        provenance.append(f"baseline run `{baseline_run_id}`")
    if manifest_hash:
        provenance.append(f"manifest `{manifest_hash[:23]}…`")
    lines.append(
        "<sub>"
        + " · ".join(provenance)
        + f" · reproduce with `meridian replay {head.run_id}`</sub>"
    )
    return "\n".join(lines)


def per_task_scores(run: RunResult) -> dict[str, float]:
    """Per-task pass^k, over tasks that actually produced k trials to draw from.

    A task with fewer than `k` scoreable trials is **excluded**, not clamped.
    Clamping silently substitutes a different statistic under the same label:
    a task that lost three of five trials to harness errors and passed the
    remaining two was reported as `pass^3 = 1.000`, which is a `pass^2` wearing
    the wrong name, and it lands in the suite aggregate as a perfect score.

    Excluding is safe here because the gate already names every task present on
    only one side — "Not run on head (excluded from the comparison)" — so a task
    dropping out is visible rather than silent. A comparison that quietly changes
    which statistic it is comparing is the worse failure.
    """
    return {
        task.task_slug: pass_hat_k(task.n, task.c, run.k)
        for task in run.tasks
        if task.n >= run.k
    }


def suite_score(run: RunResult) -> float:
    scores = per_task_scores(run)
    return sum(scores.values()) / len(scores) if scores else 0.0


def stale_cassette_rate(run: RunResult) -> float:
    """Share of trials that failed because the recording predates the agent.

    Those failures look exactly like a regression and are not one, so the gate
    needs to be able to tell them apart.
    """
    total = run.total_trials
    if total == 0:
        return 0.0
    stale = sum(
        1
        for task in run.tasks
        for trial in task.trials
        if not trial.passed and "cassette_miss" in trial.detail
    )
    return stale / total
