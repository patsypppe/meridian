"""Re-materialize a run from its manifest and check that it still reproduces.

Replay is a correctness check on the harness itself, not a convenience. Any drift
means something outside the manifest is influencing results, and finding out what
is the most valuable debugging session this project affords.

Fidelity is measured per task on pass^k, because that is the number the gate acts
on. A run that reproduces its trial ordering but not its pass^k has not
reproduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.models.manifest import Manifest
from meridian.models.run import RunResult
from meridian.stats.passk import pass_hat_k

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient


class ReplayError(RuntimeError):
    """The run could not be re-materialized. Distinct from a fidelity failure."""


@dataclass(frozen=True)
class TaskFidelity:
    task_slug: str
    recorded: float
    replayed: float

    @property
    def matches(self) -> bool:
        return abs(self.recorded - self.replayed) < 1e-9


@dataclass
class ReplayReport:
    run_id: str
    manifest_hash_matches: bool
    tasks: list[TaskFidelity] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def fidelity(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(1 for t in self.tasks if t.matches) / len(self.tasks)

    @property
    def exact(self) -> bool:
        return self.fidelity == 1.0 and not self.missing and self.manifest_hash_matches

    def render(self) -> str:
        lines = [f"replay {self.run_id}"]
        lines.append(f"manifest hash: {'matches' if self.manifest_hash_matches else 'CHANGED'}")
        lines.append("")
        lines.append(f"{'task':<24} {'recorded':>10} {'replayed':>10}   ")
        lines.append("-" * 54)
        for task in self.tasks:
            marker = "ok" if task.matches else "DRIFT"
            lines.append(
                f"{task.task_slug:<24} {task.recorded:>10.3f} {task.replayed:>10.3f}   {marker}"
            )
        lines.append("-" * 54)
        lines.append(f"replay fidelity: {self.fidelity:.3f}")
        if self.missing:
            lines.append(f"tasks absent from the replay: {', '.join(self.missing)}")
        return "\n".join(lines)


def recorded_pass_hat_k(result: RunResult) -> dict[str, float]:
    return {
        task.task_slug: pass_hat_k(task.n, task.c, min(result.k, task.n))
        for task in result.tasks
        if task.n > 0
    }


def verify_manifest(manifest: Manifest, expected_hash: str | None) -> bool:
    """Whether the manifest still hashes to what it did when it was written."""
    return expected_hash is None or manifest.manifest_hash() == expected_hash


def verify_images_available(client: DockerClient, manifest: Manifest) -> None:
    """Fail early and clearly when a pinned snapshot is gone.

    A missing image cannot be worked around: a replay against a *different*
    environment is not a replay.
    """
    missing: list[str] = []
    for task in manifest.tasks:
        try:
            client.images.get(task.snapshot_digest)
        except Exception:
            missing.append(f"{task.slug} → {task.snapshot_digest}")
    if missing:
        raise ReplayError(
            "these pinned snapshots are not available locally, so the run cannot be "
            "re-materialized: " + "; ".join(missing)
        )


def compare(
    manifest: Manifest,
    recorded: RunResult,
    replayed: RunResult,
    *,
    expected_hash: str | None = None,
) -> ReplayReport:
    """Build the fidelity report from a recorded run and its replay."""
    before = recorded_pass_hat_k(recorded)
    after = recorded_pass_hat_k(replayed)

    report = ReplayReport(
        run_id=recorded.run_id,
        manifest_hash_matches=verify_manifest(manifest, expected_hash),
        missing=sorted(set(before) - set(after)),
    )
    for slug in sorted(set(before) & set(after)):
        report.tasks.append(TaskFidelity(slug, before[slug], after[slug]))
    return report


def cassette_dir_for(manifest: Manifest, root: Path) -> Path:
    return root / manifest.suite_slug
