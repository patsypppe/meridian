"""Filesystem assertions.

Every failure detail names the actual value alongside the expected one. The
detail string ends up in the PR comment and is often the only thing a developer
reads before deciding whether the change is at fault.
"""

from __future__ import annotations

from pathlib import Path

from meridian.grading.graders.base import failed, passed, resolve
from meridian.models.run import AssertionResult
from meridian.models.task import FileExists


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
