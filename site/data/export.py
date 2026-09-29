"""Turn the raw run artifacts into the one JSON file the showcase page reads.

Every number is computed with Meridian's own functions — `pass_hat_k`,
`per_task_scores`, `decide`, `paired_regression_p_value`,
`minimum_detectable_effect`, `required_tasks` — never re-implemented here, and
each gate verdict is recomputed from the archived runs and checked against the
PR comment the gate itself wrote. A mismatch aborts the export.

    uv run python site/data/export.py      # after site/data/regenerate.sh
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from meridian.config import load_config
from meridian.gate.comment import (
    _Z_SUM,
    paired_suite_scores,
    per_task_scores,
    stale_cassette_rate,
    suite_score,
)
from meridian.gate.decide import GateInput, Verdict, decide, flipped_tasks
from meridian.manifest import store as run_store
from meridian.models.run import RunResult
from meridian.stats.bootstrap import bootstrap_ci
from meridian.stats.passk import pass_at_k, pass_hat_k
from meridian.stats.power import (
    DEFAULT_ALPHA,
    DEFAULT_POWER,
    MIN_TASKS_FOR_POWER,
    minimum_detectable_effect,
    paired_differences,
    required_tasks,
)
from meridian.stats.significance import paired_regression_p_value
from meridian.version import __version__

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "site" / "data" / "raw"
OUT = ROOT / "site" / "data" / "showcase.json"
SELF_EVAL = ROOT / "benchmarks" / "self-eval.json"

TOLERANCE = 0.03
SIGNIFICANCE = 0.05

GATES = {
    "degraded-prompt": {
        "title": "Planner prompt loses one sentence",
        "change": "fixtures/checkout_agent/prompts/planner.md replaced by planner.degraded.md "
        "— the sentence telling the agent to check coupon expiry is removed (PR #12).",
    },
    "invoice-rounding": {
        "title": "Invoice total rounded down to a dollar",
        "change": "write_invoice in fixtures/checkout_agent/tools.py writes "
        "(total_cents // 100) * 100 — the same seeded bug the self-eval misses.",
    },
    "unchanged": {
        "title": "No change at all",
        "change": "The agent exactly as committed, gated against its own baseline.",
    },
}

BADGES = {
    "✅ **PASS**": Verdict.PASS,
    "⚠️ **PASS (with warning)**": Verdict.PASS_WITH_WARNING,
    "❌ **FAIL**": Verdict.FAIL,
    "🟡 **INCONCLUSIVE**": Verdict.INCONCLUSIVE,
}


def r(value: float | None, places: int = 6) -> float | None:
    return None if value is None else round(value, places)


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def trial_grid(run: RunResult) -> list[dict[str, Any]]:
    rows = []
    for task in run.tasks:
        failing = [a for t in task.trials for a in t.assertions if a.outcome != "pass"]
        rows.append(
            {
                "task": task.task_slug,
                "n": task.n,
                "c": task.c,
                "trials": [
                    {
                        "index": t.trial_index,
                        "outcome": str(t.outcome),
                        "detail": t.detail.split(";")[0].strip(),
                        "tool_calls": t.efficiency.tool_calls,
                        "turns": t.efficiency.turns,
                        "duration_ms": t.efficiency.duration_ms,
                    }
                    for t in sorted(task.trials, key=lambda t: t.trial_index)
                ],
                "pass_at_k": r(pass_at_k(task.n, task.c, run.k)),
                "pass_hat_k": r(pass_hat_k(task.n, task.c, run.k)),
                "mean": r(task.c / task.n if task.n else 0.0),
                "failed_assertions": len(failing),
            }
        )
    return rows


def reference_run() -> dict[str, Any]:
    run = RunResult.model_validate_json((RAW / "run.json").read_text(encoding="utf-8"))
    text = (RAW / "run.txt").read_text(encoding="utf-8")
    manifest = re.search(r"manifest: (sha256:[0-9a-f]+)", text)
    scored = [t for t in run.tasks if t.task_slug not in run.excluded_task_slugs]
    lo, hi = bootstrap_ci([(t.n, t.c, run.k) for t in scored], pass_hat_k, iterations=4000)
    return {
        "run_id": run.run_id,
        "status": str(run.status),
        "n": run.n_requested,
        "k": run.k,
        "tasks": trial_grid(run),
        "suite_pass_hat_k": r(suite_score(run)),
        "suite_mean": r(sum(t.c for t in scored) / sum(t.n for t in scored)),
        "ci95": [r(lo), r(hi)],
        "harness_error_rate": run.harness_error_rate,
        "duration_ms": run.duration_ms,
        "cost_cents": run.cost_cents,
        "manifest": manifest.group(1) if manifest else None,
        "excluded": list(run.excluded_task_slugs),
        "table_text": text.split("\n\nfailing trials:")[0].strip(),
    }


def bootstrap_distribution(differences: list[float], seed: int, iterations: int) -> dict[str, Any]:
    """The resampled means behind the p-value, as exact values and counts.

    Mirrors `paired_regression_p_value` step for step (same seed, same draw
    shape) so this is the distribution that decided the verdict. Over a handful
    of tasks the resampled mean takes only a few distinct values, so they are
    exported exactly rather than binned — a bin straddling zero would blur the
    one boundary the p-value is about.
    """
    values = np.array(differences, dtype=float)
    rng = np.random.default_rng(seed)
    index = rng.integers(0, len(values), size=(iterations, len(values)))
    means = values[index].mean(axis=1)
    groups: dict[float, dict[str, Any]] = {}
    for mean in means:
        key = round(float(mean), 9)
        group = groups.setdefault(key, {"value": key, "count": 0, "at_or_above_zero": True})
        group["count"] += 1
        group["at_or_above_zero"] = group["at_or_above_zero"] and bool(mean >= 0)
    return {
        "iterations": iterations,
        "seed": seed,
        "values": sorted(groups.values(), key=lambda g: g["value"]),
        "share_at_or_above_zero": float((means >= 0).mean()),
        "observed_mean": float(values.mean()),
    }


def parse_comment(text: str) -> dict[str, Any]:
    head = re.search(r"head run `([^`]+)`", text)
    base = re.search(r"baseline run `([^`]+)`", text)
    manifest = re.search(r"manifest `([^`]+)`", text)
    verdict_line = next(line for line in text.splitlines() if line.startswith(tuple(BADGES)))
    badge = next(b for b in BADGES if verdict_line.startswith(b))
    sensitivity = next(
        (line for line in text.splitlines() if line.startswith("**Sensitivity.**")), ""
    )
    failures = re.findall(r"^- `([^`]+)` — (.+)$", text, flags=re.MULTILINE)
    if not (head and base):
        raise SystemExit("gate comment is missing its provenance line")
    return {
        "head_run": head.group(1),
        "baseline_run": base.group(1),
        "manifest_prefix": manifest.group(1) if manifest else None,
        "verdict": BADGES[badge],
        "reason": verdict_line[len(badge) :].lstrip(" —").strip(),
        "sensitivity_text": sensitivity.replace("**", ""),
        "failures": [{"task": t, "detail": d} for t, d in failures],
    }


def gate(name: str, seed: int, iterations: int) -> dict[str, Any]:
    comment_path = RAW / f"gate-{name}.md"
    comment = comment_path.read_text(encoding="utf-8")
    parsed = parse_comment(comment)
    baseline = run_store.load_result(parsed["baseline_run"], root=ROOT / "runs")
    head = run_store.load_result(parsed["head_run"], root=ROOT / "runs")

    base_scores = per_task_scores(baseline)
    head_scores = per_task_scores(head)
    base_suite, head_suite = paired_suite_scores(base_scores, head_scores)
    assert base_suite is not None

    def p_fn(b: dict[str, float], h: dict[str, float]) -> float:
        return paired_regression_p_value(b, h, iterations=iterations, seed=seed)

    gate_input = GateInput(
        baseline_suite_passhat_k=base_suite,
        head_suite_passhat_k=head_suite,
        per_task_baseline=base_scores,
        per_task_head=head_scores,
        harness_error_rate=head.harness_error_rate,
        run_status=str(head.status),
        tolerance=TOLERANCE,
        require_significance=True,
        significance_level=SIGNIFICANCE,
        stale_cassette_rate=stale_cassette_rate(head),
    )
    verdict, reason = decide(gate_input, p_fn)
    if verdict is not parsed["verdict"] or reason != parsed["reason"]:
        raise SystemExit(
            f"{name}: recomputed verdict {verdict} ({reason!r}) disagrees with the "
            f"gate's own comment {parsed['verdict']} ({parsed['reason']!r})"
        )

    differences = paired_differences(base_scores, head_scores)
    mde = minimum_detectable_effect(differences)
    spread = None if mde is None else mde * math.sqrt(len(differences)) / _Z_SUM
    # Same condition the PR comment uses before quoting a task count.
    needed = None
    if mde is not None and spread is not None and mde > TOLERANCE:
        needed = required_tasks(spread, TOLERANCE)
    drop = base_suite - head_suite
    p_value = p_fn(base_scores, head_scores) if drop > 0 else None
    flipped = flipped_tasks(base_scores, head_scores)
    histogram = bootstrap_distribution(differences, seed, iterations) if drop > 0 else None
    if histogram is not None and p_value is not None:
        assert abs(histogram["share_at_or_above_zero"] - p_value) < 1e-12

    slugs = sorted(set(base_scores) & set(head_scores))
    return {
        "name": name,
        **GATES[name],
        "verdict": str(verdict),
        "blocks_merge": verdict.blocks_merge,
        "reason": reason,
        "baseline_run": baseline.run_id,
        "head_run": head.run_id,
        "manifest_prefix": parsed["manifest_prefix"],
        "tasks": [
            {
                "task": s,
                "baseline": r(base_scores[s]),
                "head": r(head_scores[s]),
                "baseline_c": baseline.task(s).c,
                "head_c": head.task(s).c,
                "n": head.task(s).n,
            }
            for s in slugs
        ],
        "baseline_suite": r(base_suite),
        "head_suite": r(head_suite),
        "drop": r(drop),
        "tolerance": TOLERANCE,
        "significance_level": SIGNIFICANCE,
        "p_value": r(p_value),
        "differences": [r(d) for d in differences],
        "spread": r(spread),
        "mde": r(mde),
        "required_tasks_for_tolerance": needed,
        "comparable_tasks": len(differences),
        "flipped": {
            "broken": list(flipped.broken),
            "now_always_fails": list(flipped.now_always_fails),
            "fixed": list(flipped.fixed),
        },
        "bootstrap": histogram,
        "sensitivity_text": parsed["sensitivity_text"],
        "failures": parsed["failures"],
        "head_cost_cents": head.cost_cents,
        "head_duration_ms": head.duration_ms,
        "harness_error_rate": gate_input.harness_error_rate,
        "max_harness_error_rate": gate_input.max_harness_error_rate,
        "stale_cassette_rate": gate_input.stale_cassette_rate,
        "max_stale_cassette_rate": gate_input.max_stale_cassette_rate,
        "run_status": gate_input.run_status,
        "comment_markdown": comment,
    }


def main() -> None:
    config = load_config(overrides={})
    seed = config.stats.bootstrap_seed
    iterations = config.stats.bootstrap_iterations
    self_eval = json.loads(SELF_EVAL.read_text(encoding="utf-8"))
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": git_head(),
        "meridian_version": __version__,
        "stats": {
            "alpha": DEFAULT_ALPHA,
            "power": DEFAULT_POWER,
            "z_sum": _Z_SUM,
            "min_tasks_for_power": MIN_TASKS_FOR_POWER,
            "bootstrap_seed": seed,
            "bootstrap_iterations": iterations,
        },
        "run": reference_run(),
        "gates": [gate(name, seed, iterations) for name in GATES],
        "probe": json.loads((RAW / "probe.json").read_text(encoding="utf-8")),
        "audit": json.loads((RAW / "audit.json").read_text(encoding="utf-8")),
        "self_eval": {"source": "benchmarks/self-eval.json", **self_eval},
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
