"""The contamination probe, both directions, with the grader's own words kept.

Same experiment as `benchmarks/self_eval.py::measure_contamination_probe` and
`tests/integration/test_isolation.py`: run `contamination-writer` then
`contamination-probe` under the default isolation policy, then again with the
workdir volume shared across trials. Writes site/data/raw/probe.json.

    uv run python site/data/probe.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from meridian.docker_client import get_client
from meridian.grading.pipeline import grade_task
from meridian.models.run import TrialSpec
from meridian.runtime.isolation import IsolationPolicy
from meridian.runtime.trial_runner import TrialRunner, sweep_orphans
from meridian.suites.loader import load_suite

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "suites" / "checkout-agent"
SUT = ROOT / "fixtures"
OUT = ROOT / "site" / "data" / "raw" / "probe.json"


async def direction(client: object, label: str, policy: IsolationPolicy) -> dict[str, object]:
    suite = load_suite(SUITE)
    run_id = f"showcase-probe-{label}"
    runner = TrialRunner(
        client, suite_root=suite.root, sut_root=SUT, grader=grade_task, policy=policy
    )
    trials = []
    try:
        for slug in ("contamination-writer", "contamination-probe"):
            task = suite.task(slug)
            spec = TrialSpec(
                run_id=run_id,
                task_slug=slug,
                trial_index=0,
                seed=0,
                adapter_spec=task.adapter or suite.adapter_spec,
            )
            result = await runner.run_trial(task, spec)
            trials.append(
                {
                    "task": slug,
                    "outcome": str(result.outcome),
                    "detail": result.detail,
                    "container_removed": result.container_removed,
                }
            )
    finally:
        sweep_orphans(client, run_id=run_id)  # type: ignore[arg-type]
    return {
        "label": label,
        "volume_is_per_trial": policy.volume_is_per_trial,
        "trials": trials,
    }


async def main() -> None:
    client = get_client()
    report = {
        "isolated": await direction(client, "isolated", IsolationPolicy()),
        "shared": await direction(client, "shared", IsolationPolicy(unsafe_shared_env=True)),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
