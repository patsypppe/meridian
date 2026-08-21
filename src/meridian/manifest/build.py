"""Assemble a manifest, and hash it canonically.

`canonical_json` and `content_hash` are re-exported here because `HANDOFF §8.5`
names this module as their home. They are implemented in `meridian.hashing` so
that `suites`, `config`, and the proxy can hash without importing the manifest
package — which would be a cycle.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.config import MeridianConfig
from meridian.hashing import canonical_json, content_hash
from meridian.models.manifest import Manifest, ManifestModel, ManifestTask, ManifestTrial
from meridian.models.suite import Suite
from meridian.runtime.scheduler import derive_seed
from meridian.version import __version__

if TYPE_CHECKING:  # pragma: no cover - typing only
    from meridian.runtime.proxy.cassette import CassetteStore

__all__ = ["build_manifest", "canonical_json", "content_hash", "git_sha"]

LOCK_FILE = "uv.lock"


def git_sha(path: Path | None = None) -> str | None:
    """The commit a directory is at, or None outside a repository."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(path) if path else None,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except Exception:
        return None
    return completed.stdout.strip() or None


def directory_hash(root: Path) -> str:
    """Content hash of a directory tree, by relative path.

    Used for the system under test. Its git SHA is recorded too, but a SHA says
    nothing about uncommitted edits — and an uncommitted edit to a prompt is
    precisely the change a gate exists to catch.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def environment_facts(repo_root: Path | None = None) -> dict[str, str]:
    """Facts about the machine that could plausibly change a result."""
    facts = {
        "python": platform.python_version(),
        "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
        "implementation": sys.implementation.name,
    }
    if repo_root is not None:
        lock = repo_root / LOCK_FILE
        if lock.is_file():
            # The harness version alone is not enough: the same version with a
            # different dependency set is a different harness.
            facts["uv_lock"] = file_hash(lock)
    return facts


def build_manifest(
    *,
    suite: Suite,
    config: MeridianConfig,
    run_id: str,
    created_unix_ms: int,
    sut_root: Path | None = None,
    repo_root: Path | None = None,
    cassettes: CassetteStore | None = None,
    models: tuple[ManifestModel, ...] = (),
    prompt_hashes: dict[str, str] | None = None,
    docker_version: str | None = None,
) -> Manifest:
    """Pin everything this run depended on."""
    tasks = tuple(
        ManifestTask(
            slug=task.slug,
            definition_hash=task.definition_hash(),
            snapshot_digest=task.environment.snapshot,
        )
        for task in suite.tasks
    )

    trials: list[ManifestTrial] = []
    for task in suite.tasks:
        cassette = cassettes.get(task.slug) if cassettes is not None else None
        for index in range(config.execution.n_trials):
            trials.append(
                ManifestTrial(
                    task_slug=task.slug,
                    trial_index=index,
                    seed=derive_seed(run_id, task.slug, index),
                    cassette_hash=cassette.trial_hash(index) if cassette else None,
                )
            )

    env = environment_facts(repo_root)
    if docker_version:
        env["docker"] = docker_version

    return Manifest(
        meridian_version=__version__,
        created_unix_ms=created_unix_ms,
        commit_sha=git_sha(repo_root),
        suite_slug=suite.slug,
        suite_version=suite.version,
        suite_content_hash=suite.content_hash(),
        config_hash=config.config_hash(),
        adapter_spec=suite.adapter_spec,
        sut_commit_sha=git_sha(sut_root),
        sut_content_hash=directory_hash(sut_root) if sut_root and sut_root.is_dir() else None,
        n_trials=config.execution.n_trials,
        k=config.execution.k,
        bootstrap_seed=config.stats.bootstrap_seed,
        proxy_mode=str(config.execution.proxy_mode),
        tasks=tasks,
        trials=tuple(trials),
        models=models,
        prompt_hashes=prompt_hashes or {},
        env=env,
    )
