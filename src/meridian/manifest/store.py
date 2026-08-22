"""Where runs are kept on disk.

A directory per run, holding the manifest and the result. This is what makes
`meridian replay <run-id>` and the gate's baseline lookup possible without a
database, and it is deliberately boring: a JSON file you can read is worth more
during an incident than a schema you have to query.
"""

from __future__ import annotations

import json
from pathlib import Path

from meridian.models.manifest import Manifest
from meridian.models.run import RunResult

RUNS_DIR = Path("runs")
MANIFEST_FILE = "manifest.json"
RESULT_FILE = "result.json"


class RunNotFoundError(LookupError):
    """No archived run with that id."""


def run_dir(run_id: str, root: Path | None = None) -> Path:
    return (root or RUNS_DIR) / run_id


def save(manifest: Manifest, result: RunResult, *, root: Path | None = None) -> Path:
    directory = run_dir(result.run_id, root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MANIFEST_FILE).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    (directory / RESULT_FILE).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return directory


def load_manifest(run_id: str, *, root: Path | None = None) -> Manifest:
    path = run_dir(run_id, root) / MANIFEST_FILE
    if not path.is_file():
        raise RunNotFoundError(f"no manifest for run {run_id!r} at {path}")
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_result(run_id: str, *, root: Path | None = None) -> RunResult:
    path = run_dir(run_id, root) / RESULT_FILE
    if not path.is_file():
        raise RunNotFoundError(f"no result for run {run_id!r} at {path}")
    return RunResult.model_validate_json(path.read_text(encoding="utf-8"))


def list_runs(root: Path | None = None) -> list[str]:
    """Archived run ids, oldest first. Run ids sort chronologically by design."""
    directory = root or RUNS_DIR
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if (p / MANIFEST_FILE).is_file())


def find_by_commit(
    commit_sha: str,
    *,
    root: Path | None = None,
    n_trials: int | None = None,
    k: int | None = None,
    suite_content_hash: str | None = None,
) -> str | None:
    """The most recent archived run at a commit that is *comparable* to this one.

    Matching the commit is not enough. An archived run at n=3, k=2 compared
    against a head run at n=5, k=3 produces a difference that is entirely an
    artefact of the configuration — and it looks exactly like a regression. Same
    for a run of a different version of the suite.

    Returning None is a normal answer, not an error: the first run on a branch
    has no baseline, and a gate that fails for that reason gets disabled the
    next day.
    """
    for run_id in reversed(list_runs(root)):
        manifest = load_manifest(run_id, root=root)
        if manifest.commit_sha != commit_sha:
            continue
        if n_trials is not None and manifest.n_trials != n_trials:
            continue
        if k is not None and manifest.k != k:
            continue
        if suite_content_hash is not None and manifest.suite_content_hash != suite_content_hash:
            continue
        return run_id
    return None


def summarize(run_id: str, *, root: Path | None = None) -> dict[str, object]:
    manifest = load_manifest(run_id, root=root)
    return {
        "run_id": run_id,
        "manifest_hash": manifest.manifest_hash(),
        "suite": f"{manifest.suite_slug} v{manifest.suite_version}",
        "commit": manifest.commit_sha,
        "n": manifest.n_trials,
        "k": manifest.k,
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
