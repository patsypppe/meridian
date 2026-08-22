"""Meridian, measured by Meridian.

A harness that cannot measure itself has no standing to measure anything else.

Three numbers matter, and the honest one is the first:

- **False-regression rate.** Run the *unchanged* agent through the gate N times
  against a fixed baseline and count the FAILs. Anything above zero means the
  gate is noisy — n is too low or the tolerance too tight — and reporting that is
  worth more than a clean number nobody can reproduce.
- **True-positive rate.** Five seeded regressions of known shape. Each one the
  gate misses is a change that would have shipped.
- **Replay fidelity.** The share of archived runs that reproduce exactly. Drift
  means an input exists the manifest does not pin.

Every regression here changes the *system under test*, never the harness or the
thresholds. A benchmark that tunes the thing it is measuring measures nothing.

The system under test is `fixtures/`, which is mounted into the trial container.
It is **not** `envs/checkout/seed/`: that is baked into the environment image and
pinned by digest, so editing it on the host changes nothing a trial ever sees.
Two regressions were written against those baked files and scored as gate misses
for it — see `RegressionNotApplied` for the guard that now makes that loud.

The benchmark records into a throwaway cassette directory, never
`fixtures/cassettes/`. Those cassettes are the pinned artifact CI replays from;
a benchmark that rewrites them would be grading itself against its own homework,
and every self-eval run would leave the tree dirty.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian.config import RunMode, load_config
from meridian.docker_client import get_client
from meridian.gate.comment import per_task_scores, stale_cassette_rate, suite_score
from meridian.gate.decide import GateInput, Verdict, decide
from meridian.grading.pipeline import grade_task
from meridian.manifest.replay import compare
from meridian.models.run import Outcome, RunResult, TrialSpec
from meridian.runtime.isolation import IsolationPolicy
from meridian.runtime.orchestrator import RunOutcome, RunRequest, execute
from meridian.runtime.trial_runner import TrialRunner, sweep_orphans
from meridian.stats.significance import paired_regression_p_value
from meridian.suites.loader import load_suite
from meridian.version import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE = REPO_ROOT / "suites" / "checkout-agent"
SUT = REPO_ROOT / "fixtures"
PLANNER = SUT / "checkout_agent" / "prompts" / "planner.md"
DEGRADED = SUT / "checkout_agent" / "prompts" / "planner.degraded.md"
TOOLS = SUT / "checkout_agent" / "tools.py"
AGENT = SUT / "checkout_agent" / "agent.py"

DEFAULT_N = 5
DEFAULT_K = 3
TOLERANCE = 0.03
SIGNIFICANCE = 0.05


@dataclass
class Seeded:
    """One seeded regression: what it changes, and how to change it back."""

    name: str
    description: str
    apply: Callable[[], Callable[[], None]]


class RegressionNotApplied(RuntimeError):
    """A seeded regression changed nothing, so the number it produces is a lie.

    This is the failure mode the benchmark is least able to notice on its own: an
    anchor drifts, the edit silently no-ops, the run scores exactly like the
    baseline, and the report records a regression the gate "missed" that was
    never applied. Every edit here asserts that it changed something.
    """


def _swap_file(path: Path, replacement: str) -> Callable[[], None]:
    original = path.read_text(encoding="utf-8")
    if replacement == original:
        raise RegressionNotApplied(f"the seeded edit to {path} changed nothing")
    path.write_text(replacement, encoding="utf-8")

    def restore() -> None:
        path.write_text(original, encoding="utf-8")

    return restore


def remove_expiry_instruction() -> Callable[[], None]:
    return _swap_file(PLANNER, DEGRADED.read_text(encoding="utf-8"))


def truncate_the_context() -> Callable[[], None]:
    """A context-window guard keeps only the last two messages of the transcript.

    The most ordinary way an agent loses its memory. The model no longer sees
    which tools it has already called, re-issues the first one every turn, and
    runs out of turns without finishing — an agent failure, not a harness one.
    """
    text = AGENT.read_text(encoding="utf-8")
    return _swap_file(
        AGENT,
        text.replace(
            '"messages": state.get("messages", []),',
            '"messages": state.get("messages", [])[-2:],',
        ),
    )


def remove_a_tool() -> Callable[[], None]:
    """`query_coupon` disappears, so no coupon can be looked up at all."""
    text = TOOLS.read_text(encoding="utf-8")
    return _swap_file(
        TOOLS,
        text.replace(
            "    def query_coupon(self, code: str) -> dict[str, Any]:",
            "    def _disabled_query_coupon(self, code: str) -> dict[str, Any]:",
        ),
    )


def starve_the_token_budget() -> Callable[[], None]:
    """Every task's max_tokens is halved to a level no task can finish within."""
    originals: dict[Path, str] = {}
    for task_file in sorted((SUITE / "tasks").glob("*.yaml")):
        originals[task_file] = task_file.read_text(encoding="utf-8")
        task_file.write_text(
            originals[task_file].replace("max_tokens: 60000", "max_tokens: 500"),
            encoding="utf-8",
        )

    def restore() -> None:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")

    return restore


def break_the_invoice_rounding() -> Callable[[], None]:
    """`write_invoice` rounds the total down to a whole dollar before writing it.

    The model computes the right number and the tool layer corrupts it on the way
    to disk, so the invoice and the order row disagree. Grading final state is
    what catches this; grading the trajectory would show a flawless one.
    """
    text = TOOLS.read_text(encoding="utf-8")
    return _swap_file(
        TOOLS,
        text.replace(
            '                    "total_cents": total_cents,',
            '                    "total_cents": (total_cents // 100) * 100,',
        ),
    )


REGRESSIONS = [
    Seeded(
        "remove-expiry-instruction",
        "the planner no longer tells the agent to check coupon expiry",
        remove_expiry_instruction,
    ),
    Seeded(
        "truncate-the-context",
        "a context-window guard drops all but the last two messages",
        truncate_the_context,
    ),
    Seeded(
        "remove-a-tool",
        "query_coupon is gone, so no coupon can be looked up",
        remove_a_tool,
    ),
    Seeded(
        "starve-the-token-budget",
        "max_tokens drops to a level no task can finish within",
        starve_the_token_budget,
    ),
    Seeded(
        "break-the-invoice-rounding",
        "write_invoice rounds the total down to a whole dollar",
        break_the_invoice_rounding,
    ),
]


@contextlib.contextmanager
def applied(regression: Seeded) -> Iterator[None]:
    restore = regression.apply()
    try:
        yield
    finally:
        restore()


@dataclass
class Bench:
    """A run configuration, and the one directory the benchmark may write to."""

    client: Any
    cassettes: Path
    n: int
    k: int

    def request(self, run_id: str, *, proxy_mode: str = "record") -> RunRequest:
        suite = load_suite(SUITE)
        config = load_config(
            overrides={"execution": {"n_trials": self.n, "k": self.k, "proxy_mode": proxy_mode}}
        )
        return RunRequest(
            suite=suite,
            config=config,
            cassette_dir=self.cassettes / suite.slug,
            sut_root=SUT,
            mode=RunMode.GATE,
            run_id=run_id,
            repo_root=REPO_ROOT,
        )

    async def run(self, run_id: str, *, proxy_mode: str = "record") -> RunOutcome:
        return await execute(self.client, self.request(run_id, proxy_mode=proxy_mode))

    def verdict(self, baseline: RunResult, head: RunResult) -> tuple[Verdict, str]:
        return decide(
            GateInput(
                baseline_suite_passhat_k=suite_score(baseline),
                head_suite_passhat_k=suite_score(head),
                per_task_baseline=per_task_scores(baseline),
                per_task_head=per_task_scores(head),
                harness_error_rate=head.harness_error_rate,
                run_status=str(head.status),
                tolerance=TOLERANCE,
                require_significance=True,
                significance_level=SIGNIFICANCE,
                stale_cassette_rate=stale_cassette_rate(head),
            ),
            paired_regression_p_value,
        )


async def measure_false_regressions(
    bench: Bench, baseline: RunResult, *, runs: int
) -> dict[str, Any]:
    """Gate the *unchanged* agent repeatedly. Every FAIL here is noise."""
    false_fails = 0
    durations: list[int] = []
    costs: list[int] = []
    for index in range(runs):
        outcome = await bench.run(f"self-eval-stable-{index}")
        durations.append(outcome.result.duration_ms)
        costs.append(outcome.result.cost_cents)
        verdict, _ = bench.verdict(baseline, outcome.result)
        if verdict is Verdict.FAIL:
            false_fails += 1
        print(f"  stable run {index + 1}/{runs}: {verdict}", file=sys.stderr)
    return {
        "runs": runs,
        "false_fails": false_fails,
        "rate": round(false_fails / runs, 4) if runs else 0.0,
        "_durations": durations,
        "_costs": costs,
    }


async def measure_true_positives(bench: Bench, baseline: RunResult) -> dict[str, Any]:
    """Each seeded regression the gate misses is a change that would have shipped."""
    detected: list[str] = []
    missed: list[dict[str, str]] = []
    for regression in REGRESSIONS:
        with applied(regression):
            outcome = await bench.run(f"self-eval-{regression.name}")
        verdict, reason = bench.verdict(baseline, outcome.result)
        caught = verdict in (Verdict.FAIL, Verdict.INCONCLUSIVE)
        print(f"  {regression.name}: {verdict}", file=sys.stderr)
        if caught:
            detected.append(regression.name)
        else:
            missed.append({"name": regression.name, "verdict": str(verdict), "reason": reason})
    total = len(REGRESSIONS)
    return {
        "seeded_regressions": total,
        "detected": len(detected),
        "rate": round(len(detected) / total, 4) if total else 0.0,
        "missed": missed,
    }


async def measure_replay_fidelity(bench: Bench, *, manifests: int) -> dict[str, Any]:
    """Drift means an input exists that the manifest does not pin."""
    exact = 0
    diffs: list[str] = []
    for index in range(manifests):
        run_id = f"self-eval-replay-{index}"
        recorded = await bench.run(run_id)
        replayed = await bench.run(run_id, proxy_mode="replay")
        # No expected_hash: comparing a manifest against its own hash verifies
        # nothing, and this number is published.
        report = compare(recorded.manifest, recorded.result, replayed.result)
        if report.exact:
            exact += 1
        else:
            diffs.append(report.render())
        print(f"  replay {index + 1}/{manifests}: fidelity {report.fidelity:.3f}", file=sys.stderr)
    return {
        "manifests": manifests,
        "exact": exact,
        "rate": round(exact / manifests, 4) if manifests else 0.0,
        "diffs": diffs,
    }


async def measure_contamination_probe(client: Any) -> dict[str, str]:
    """Both directions. A probe that cannot fail proves nothing."""
    suite = load_suite(SUITE)
    results: dict[str, str] = {}

    for label, policy, run_id in (
        ("with_isolation", IsolationPolicy(), "self-eval-probe-iso"),
        ("without_isolation", IsolationPolicy(unsafe_shared_env=True), "self-eval-probe-unsafe"),
    ):
        runner = TrialRunner(
            client, suite_root=suite.root, sut_root=SUT, grader=grade_task, policy=policy
        )
        outcome = Outcome.PASS
        for slug in ("contamination-writer", "contamination-probe"):
            task = suite.task(slug)
            spec = TrialSpec(
                run_id=run_id,
                task_slug=slug,
                trial_index=0,
                seed=0,
                adapter_spec=task.adapter or suite.adapter_spec,
            )
            outcome = (await runner.run_trial(task, spec)).outcome
        results[label] = str(outcome)
        sweep_orphans(client, run_id=run_id)
    return results


async def main_async(args: argparse.Namespace, workspace: Path) -> int:
    client = get_client()
    sweep_orphans(client)
    started = time.time()
    bench = Bench(client=client, cassettes=workspace / "cassettes", n=args.n, k=args.k)

    print("baseline…", file=sys.stderr)
    baseline = await bench.run("self-eval-baseline")

    print("false-regression rate…", file=sys.stderr)
    false_regressions = await measure_false_regressions(
        bench, baseline.result, runs=args.stable_runs
    )
    durations = [baseline.result.duration_ms, *false_regressions.pop("_durations")]
    costs = [baseline.result.cost_cents, *false_regressions.pop("_costs")]

    print("true-positive rate…", file=sys.stderr)
    true_positives = await measure_true_positives(bench, baseline.result)

    print("replay fidelity…", file=sys.stderr)
    fidelity = await measure_replay_fidelity(bench, manifests=args.replays)

    print("contamination probe…", file=sys.stderr)
    probe = await measure_contamination_probe(client)

    ordered = sorted(durations)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "meridian_version": __version__,
        "config": {"n": args.n, "k": args.k, "tolerance": TOLERANCE},
        "baseline_suite_pass_hat_k": round(suite_score(baseline.result), 4),
        "false_regression_rate": false_regressions,
        "true_positive_rate": true_positives,
        "replay_fidelity": fidelity,
        "run_duration_ms": {
            "mean": int(statistics.fmean(ordered)) if ordered else 0,
            "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0,
        },
        "run_cost_cents": {"mean": int(statistics.fmean(costs)) if costs else 0},
        "contamination_probe": probe,
        "total_duration_s": int(time.time() - started),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure Meridian with Meridian.")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "benchmarks" / "self-eval.json")
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument(
        "--stable-runs",
        type=int,
        default=30,
        help="Gate runs of the unchanged agent, for the false-regression rate.",
    )
    parser.add_argument("--replays", type=int, default=5)
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix="meridian-self-eval-"))
    try:
        return asyncio.run(main_async(args, workspace))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
