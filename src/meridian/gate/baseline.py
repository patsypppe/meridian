"""Resolving what to compare against.

Two ways, in order of preference:

1. An **archived run** at the baseline commit. Free, and exactly the numbers that
   commit produced.
2. A **git worktree** at the baseline commit, run now. Costs a second run, but it
   is what CI has to do — a fresh runner has no archive.

If neither is available the answer is "no baseline", which is a `PASS`. A gate
that fails on its first run gets disabled on its second day.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class BaselineError(RuntimeError):
    """The baseline could not be materialized. A harness error, never a verdict."""


@dataclass(frozen=True)
class Baseline:
    """Where the baseline came from, for the comment's provenance line."""

    run_id: str | None
    source: str


def merge_base(repo_root: Path, ref: str) -> str | None:
    """The commit this branch diverged from, or None if it cannot be determined.

    `fetch-depth: 0` in CI is not optional: merge-base resolution silently returns
    nothing on a shallow clone, and the gate then quietly has no baseline forever.
    """
    for command in (["git", "merge-base", "HEAD", ref], ["git", "rev-parse", ref]):
        try:
            completed = subprocess.run(
                command, cwd=repo_root, capture_output=True, text=True, check=True, timeout=30
            )
        except Exception:
            continue
        sha = completed.stdout.strip()
        if sha:
            return sha
    return None


@contextlib.contextmanager
def worktree_at(repo_root: Path, sha: str) -> Iterator[Path]:
    """A detached checkout of `sha`, removed afterwards.

    The working tree stays untouched: the gate must never leave a developer's
    checkout in a different state than it found it.
    """
    directory = Path(tempfile.mkdtemp(prefix="meridian-baseline-"))
    checkout = directory / "tree"
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(checkout), sha],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise BaselineError(
            f"could not check out baseline {sha[:12]}: {exc.stderr.strip() or exc}. "
            f"In CI this usually means a shallow clone — set fetch-depth: 0."
        ) from exc

    try:
        yield checkout
    finally:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(checkout)],
                cwd=repo_root,
                capture_output=True,
                timeout=60,
            )
        shutil.rmtree(directory, ignore_errors=True)
