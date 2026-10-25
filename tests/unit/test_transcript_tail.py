"""A timeout verdict quotes what the agent was last doing."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.runtime.trial_runner import (
    MAX_THROTTLE_EXTENSION_FACTOR,
    TRANSCRIPT_RELATIVE,
    _with_transcript,
    effective_deadline_seconds,
    transcript_tail,
)

pytestmark = pytest.mark.unit


def write(state: Path, text: str) -> None:
    path = state / TRANSCRIPT_RELATIVE
    path.parent.mkdir(parents=True)
    path.write_text(text)


def test_no_transcript_means_no_extra_detail(tmp_path: Path) -> None:
    assert transcript_tail(tmp_path) == ""
    assert _with_transcript("exceeded the 120s deadline", tmp_path) == "exceeded the 120s deadline"


def test_the_tail_is_quoted_in_the_verdict(tmp_path: Path) -> None:
    write(tmp_path, '{"event": "command", "command": "apt-get install jq"}\n')
    detail = _with_transcript("exceeded the 120s deadline", tmp_path)
    assert detail.startswith("exceeded the 120s deadline; last agent activity:")
    assert "apt-get install jq" in detail


def test_a_long_transcript_keeps_the_end_on_a_line_boundary(tmp_path: Path) -> None:
    lines = [f'{{"event": "command", "n": {i}}}' for i in range(500)]
    write(tmp_path, "\n".join(lines) + "\n")
    tail = transcript_tail(tmp_path, limit=300)
    assert len(tail) <= 300
    assert tail.splitlines()[-1] == '{"event": "command", "n": 499}'
    assert all(line.startswith("{") and line.endswith("}") for line in tail.splitlines())


# -- the deadline moves with provider throttling, within bounds ----------------------


def test_no_throttling_leaves_the_deadline_alone() -> None:
    assert effective_deadline_seconds(120, 0) == 120


def test_throttling_extends_the_deadline_by_the_time_lost() -> None:
    assert effective_deadline_seconds(120, 45) == 165


def test_the_extension_is_capped_so_a_dead_provider_cannot_hang_a_trial() -> None:
    assert effective_deadline_seconds(120, 10_000) == 120 + 120 * MAX_THROTTLE_EXTENSION_FACTOR


def test_a_negative_reading_never_shortens_the_deadline() -> None:
    assert effective_deadline_seconds(120, -30) == 120
