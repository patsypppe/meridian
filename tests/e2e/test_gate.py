"""The gate, end to end, including its exit-code contract.

The decision rule is unit tested exhaustively; what this file proves is that the
whole pipeline produces a verdict a CI job can act on, and that a regression in a
prompt reaches that verdict.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, requires_docker

pytestmark = [pytest.mark.e2e, requires_docker]

PLANNER = REPO_ROOT / "fixtures" / "checkout_agent" / "prompts" / "planner.md"
DEGRADED = REPO_ROOT / "fixtures" / "checkout_agent" / "prompts" / "planner.degraded.md"

EXIT_OK = 0
EXIT_GATE_FAIL = 1
EXIT_HARNESS_ERROR = 2


def meridian(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "meridian.cli", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
    )


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep archived runs out of the developer's working tree."""
    monkeypatch.setenv("MERIDIAN_RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


def test_only_gate_returns_non_zero_for_a_product_verdict() -> None:
    """The exit-code contract from HANDOFF §8.4.

    CI must never confuse "the agent got worse" with "the tool crashed", because
    the response to each is completely different.
    """
    # A suite that does not exist is a harness error, not a verdict.
    broken = meridian("run", "--suite", "./suites/does-not-exist")
    assert broken.returncode == EXIT_HARNESS_ERROR

    # `suite publish` never returns 1, whatever it finds.
    published = meridian("suite", "publish", "./suites/checkout-agent")
    assert published.returncode == EXIT_OK

    invalid = meridian("suite", "publish", "./tests/fixtures/invalid-suites/unpinned-snapshot")
    assert invalid.returncode == EXIT_HARNESS_ERROR


def test_version_and_help_are_free_of_side_effects() -> None:
    assert meridian("version").returncode == EXIT_OK
    assert meridian("--help").returncode == EXIT_OK


@pytest.mark.slow
def test_a_degraded_prompt_fails_the_gate_and_a_revert_passes(
    docker_client: object, tmp_path: Path
) -> None:
    """The deliverable, as a test.

    Runs the gate against the committed baseline twice: once with the degraded
    planner in place, once with it restored.
    """
    original = PLANNER.read_text(encoding="utf-8")
    comment = tmp_path / "comment.md"

    try:
        PLANNER.write_text(DEGRADED.read_text(encoding="utf-8"), encoding="utf-8")
        failing = meridian(
            "gate",
            "--suite",
            "./suites/checkout-agent",
            "--baseline-ref",
            "HEAD",
            "--n",
            "5",
            "--k",
            "3",
            "--tolerance",
            "0.03",
            "--comment-file",
            str(comment),
        )
    finally:
        PLANNER.write_text(original, encoding="utf-8")

    assert failing.returncode == EXIT_GATE_FAIL, failing.stderr[-2000:]
    body = comment.read_text(encoding="utf-8")
    assert "**FAIL**" in body
    assert "expired-coupon" in body
    # The grader's own words, which is what tells a developer what to do.
    assert "expected 4999" in body

    passing = meridian(
        "gate",
        "--suite",
        "./suites/checkout-agent",
        "--baseline-ref",
        "HEAD",
        "--n",
        "5",
        "--k",
        "3",
        "--tolerance",
        "0.03",
        "--comment-file",
        str(comment),
    )
    assert passing.returncode == EXIT_OK, passing.stderr[-2000:]
    assert "**PASS**" in comment.read_text(encoding="utf-8")
