"""Write the dashboard's bundled sample runs.

The samples are rebuilt from the per-task counts of two real runs of
`openai/gpt-oss-20b` on the checkout-agent suite (2026-10-23, via Groq). Only the
counts are real: trial order and failure details are synthetic, and each file says
so in its `detail` field. Rebuilding the full trial records would need the
original result.json files, which were not archived.

    uv run python -m benchmarks.make_web_samples
"""

from __future__ import annotations

import json
from pathlib import Path

from meridian.models.run import (
    ErrorCause,
    Outcome,
    RunResult,
    RunStatus,
    TaskResult,
    TrialResult,
)

OUT = Path(__file__).resolve().parents[1] / "web" / "public"

# task -> (passes, failures, provider-fault harness errors), 10 trials each.
LATER_RUN = {
    "expired-coupon": (0, 10, 0),
    "expired-on-bulk-order": (1, 7, 2),
    "expiry-boundary": (1, 9, 0),
    "happy-path": (1, 8, 1),
    "missing-field": (4, 6, 0),
    "stale-coupon": (1, 9, 0),
    "unknown-coupon": (1, 9, 0),
}
# The earlier run, before provider faults were attributed: they were scored `fail`.
EARLIER_RUN = {
    "expired-coupon": (4, 6, 0),
    "expired-on-bulk-order": (3, 7, 0),
    "expiry-boundary": (1, 9, 0),
    "happy-path": (1, 9, 0),
    "missing-field": (7, 3, 0),
    "stale-coupon": (0, 10, 0),
    "unknown-coupon": (1, 9, 0),
}


def build(
    run_id: str, counts: dict[str, tuple[int, int, int]], *, cost: int, ms: int, note: str
) -> RunResult:
    tasks = []
    for slug, (passes, fails, errors) in counts.items():
        outcomes = (
            [(Outcome.PASS, None)] * passes
            + [(Outcome.FAIL, None)] * fails
            + [(Outcome.HARNESS_ERROR, ErrorCause.MODEL_PROVIDER)] * errors
        )
        trials = tuple(
            TrialResult(
                run_id=run_id,
                task_slug=slug,
                trial_index=index,
                seed=index,
                outcome=outcome,
                detail="provider rate limit" if cause else "",
                error_cause=cause,
            )
            for index, (outcome, cause) in enumerate(outcomes)
        )
        tasks.append(TaskResult(task_slug=slug, trials=trials))
    return RunResult(
        run_id=run_id,
        suite_slug="checkout-agent",
        suite_version=1,
        status=RunStatus.COMPLETE,
        k=3,
        n_requested=10,
        tasks=tuple(tasks),
        excluded_task_slugs=("contamination-probe", "contamination-writer"),
        detail=note,
        duration_ms=ms,
        cost_cents=cost,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    later = build(
        "run-1792800323-a1368b",
        LATER_RUN,
        cost=3,
        ms=1_499_200,
        note=(
            "Sample reconstructed from the per-task counts of a real run "
            "(openai/gpt-oss-20b via Groq, 2026-10-23); trial order and details are synthetic."
        ),
    )
    earlier = build(
        "run-1792719876-adc0b3",
        EARLIER_RUN,
        cost=3,
        ms=1_437_200,
        note=(
            "Baseline reconstructed from the per-task counts of an earlier real run of the same "
            "model; trial order and details are synthetic."
        ),
    )
    (OUT / "sample-run.json").write_text(later.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (OUT / "sample-baseline.json").write_text(
        earlier.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"written": ["sample-run.json", "sample-baseline.json"]}))


if __name__ == "__main__":
    main()
