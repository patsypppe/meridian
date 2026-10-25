"""The grading sandbox and script runner, against real containers.

These are the paths an agent cannot be trusted near: its final state is streamed
into a throwaway container together with the benchmark's own verifier, and the
reward is read back. What matters is that the layout is right, the verifier wins
over anything the agent wrote, and nothing the agent prints can pass for a reward.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meridian.runtime.pytest_runner import ContainerScriptRunner, _GradingSandbox
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

WORKDIR = "/work"

CHECK_ANSWER = b"""#!/bin/bash
mkdir -p /logs/verifier
if [ "$(cat /work/answer.txt 2>/dev/null)" = "42" ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
"""


@pytest.fixture(scope="module")
def image() -> str:
    return load_suite(CHECKOUT_SUITE).tasks[0].environment.snapshot


def state(tmp_path: Path, **files: str) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    for name, content in files.items():
        (directory / name).write_text(content)
    return directory


def runner(client: Any, image: str, grader_files: dict[str, bytes]) -> ContainerScriptRunner:
    return ContainerScriptRunner(
        client, image=image, run_id="grade-it", workdir=WORKDIR, grader_files=grader_files
    )


def test_layout_state_in_workdir_and_grader_files_in_tests(
    docker_client: Any, image: str, tmp_path: Path
) -> None:
    sandbox = _GradingSandbox(
        docker_client,
        image=image,
        run_id="grade-layout",
        workdir=WORKDIR,
        grader_files={"/tests/hidden.txt": b"secret"},
    )
    code, output = sandbox.run(
        state(tmp_path, **{"answer.txt": "42"}),
        "tar -xf - -C / && cat /work/answer.txt && cat /tests/hidden.txt "
        "&& touch /etc/nope 2>&1; echo done",
        "layout",
    )
    assert "42" in output and "secret" in output
    # the root filesystem stays read-only: only the workdir, /tests, /logs and /tmp write
    assert "Read-only file system" in output or "Permission denied" in output
    assert code == 0


def test_a_correct_answer_earns_reward_one(docker_client: Any, image: str, tmp_path: Path) -> None:
    out = runner(docker_client, image, {"/tests/test.sh": CHECK_ANSWER})(
        state(tmp_path, **{"answer.txt": "42"}), "/tests/test.sh", "/logs/verifier/reward.txt"
    )
    assert out.infra_error is None
    assert (out.reward_text or "").strip() == "1"


def test_a_wrong_answer_earns_reward_zero(docker_client: Any, image: str, tmp_path: Path) -> None:
    out = runner(docker_client, image, {"/tests/test.sh": CHECK_ANSWER})(
        state(tmp_path, **{"answer.txt": "41"}), "/tests/test.sh", "/logs/verifier/reward.txt"
    )
    assert (out.reward_text or "").strip() == "0"


def test_a_verifier_that_writes_nothing_has_no_reward(
    docker_client: Any, image: str, tmp_path: Path
) -> None:
    out = runner(docker_client, image, {"/tests/test.sh": b"#!/bin/bash\ntrue\n"})(
        state(tmp_path), "/tests/test.sh", "/logs/verifier/reward.txt"
    )
    assert (out.reward_text or "").strip() == ""


def test_the_verifier_wins_over_an_agent_written_copy(
    docker_client: Any, image: str, tmp_path: Path
) -> None:
    """The agent rewrites the check to always pass; the benchmark's copy is used."""
    cheat = "mkdir -p /logs/verifier; echo 1 > /logs/verifier/reward.txt\n"
    out = runner(docker_client, image, {"/work/check.sh": CHECK_ANSWER})(
        state(tmp_path, **{"check.sh": cheat, "answer.txt": "wrong"}),
        "/work/check.sh",
        "/logs/verifier/reward.txt",
    )
    assert (out.reward_text or "").strip() == "0"


def test_text_the_agent_prints_cannot_forge_a_reward(
    docker_client: Any, image: str, tmp_path: Path
) -> None:
    """Even if the verifier echoes agent-controlled text, the markers are unguessable."""
    verifier = (
        b"#!/bin/bash\ncat /work/note.txt\nmkdir -p /logs/verifier\n"
        b"echo 0 > /logs/verifier/reward.txt\n"
    )
    out = runner(docker_client, image, {"/tests/test.sh": verifier})(
        state(tmp_path, **{"note.txt": "deadbeef:1:deadbeef\nFAKE:1:FAKE\n"}),
        "/tests/test.sh",
        "/logs/verifier/reward.txt",
    )
    assert (out.reward_text or "").strip() == "0"


def test_a_verifier_that_does_not_create_the_reward_directory_still_works(
    docker_client: Any, image: str, tmp_path: Path
) -> None:
    """Harbor guarantees /logs/verifier exists; real verifiers rely on it."""
    verifier = b"#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n"
    out = runner(docker_client, image, {"/tests/test.sh": verifier})(
        state(tmp_path), "/tests/test.sh", "/logs/verifier/reward.txt"
    )
    assert (out.reward_text or "").strip() == "1"
