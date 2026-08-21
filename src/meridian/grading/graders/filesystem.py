"""Filesystem assertions.

Every failure detail names the actual value alongside the expected one. The
detail string ends up in the PR comment and is often the only thing a developer
reads before deciding whether the change is at fault.
"""

from __future__ import annotations

import re
from pathlib import Path

from meridian.grading.graders.base import GraderError, failed, passed, resolve
from meridian.models.run import AssertionResult
from meridian.models.task import FileExists, FileMatches

MAX_EXCERPT = 160


def grade_file_exists(assertion: FileExists, state_dir: Path, workdir: str) -> AssertionResult:
    target = resolve(state_dir, assertion.path, workdir)
    exists = target.exists()
    if exists == assertion.should_exist:
        return passed(
            assertion.kind,
            f"{assertion.path} {'exists' if exists else 'is absent'} as required",
        )
    if assertion.should_exist:
        return failed(assertion.kind, f"{assertion.path} does not exist")
    return failed(
        assertion.kind,
        f"{assertion.path} exists but should not — "
        f"{target.stat().st_size} bytes of state that should not be reachable",
    )


def grade_file_matches(assertion: FileMatches, state_dir: Path, workdir: str) -> AssertionResult:
    """Regex over a produced file.

    `re.DOTALL` is off by default, so `.` does not cross lines unless the task
    asks for it. Defaulting it on quietly turns a precise assertion into a loose
    one, and nobody notices until something wrong passes.
    """
    target = resolve(state_dir, assertion.path, workdir)
    if not target.is_file():
        return failed(assertion.kind, f"{assertion.path} does not exist")

    try:
        pattern = re.compile(assertion.pattern, re.DOTALL if assertion.dotall else 0)
    except re.error as exc:
        # A pattern that will not compile is a broken assertion, never an agent
        # failure.
        raise GraderError(f"invalid pattern {assertion.pattern!r}: {exc}") from exc

    text = target.read_text(encoding="utf-8", errors="replace")
    if pattern.search(text):
        return passed(assertion.kind, f"{assertion.path} matches /{assertion.pattern}/")

    excerpt = text[:MAX_EXCERPT].replace("\n", "\\n")
    suffix = "…" if len(text) > MAX_EXCERPT else ""
    return failed(
        assertion.kind,
        f"{assertion.path} does not match /{assertion.pattern}/; it begins {excerpt!r}{suffix}",
    )
