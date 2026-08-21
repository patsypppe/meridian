"""Replay fidelity, end to end.

Replay is a correctness check on the harness, so this test runs the real thing:
record a run, archive it, re-materialize it from the manifest alone, and compare
per-task pass^k. Anything less than 1.0 means an input exists that the manifest
does not pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meridian.config import load_config
from meridian.manifest import store as run_store
from meridian.manifest.replay import compare, verify_images_available
from meridian.runtime.orchestrator import RunRequest, execute
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE, REPO_ROOT, requires_docker

pytestmark = [pytest.mark.e2e, requires_docker]

CASSETTES = REPO_ROOT / "fixtures" / "cassettes" / "checkout-agent"
SUT = REPO_ROOT / "fixtures"

N_TRIALS = 3
K = 2


def request_for(run_id: str, tmp_path: Path) -> RunRequest:
    suite = load_suite(CHECKOUT_SUITE)
    config = load_config(
        overrides={"execution": {"n_trials": N_TRIALS, "k": K, "proxy_mode": "replay"}}
    )
    return RunRequest(
        suite=suite,
        config=config,
        cassette_dir=CASSETTES,
        sut_root=SUT,
        run_id=run_id,
        repo_root=REPO_ROOT,
    )


async def test_a_run_replays_exactly(docker_client: Any, tmp_path: Path) -> None:
    original = await execute(docker_client, request_for("e2e-replay", tmp_path))
    run_store.save(original.manifest, original.result, root=tmp_path)

    manifest = run_store.load_manifest("e2e-replay", root=tmp_path)
    recorded = run_store.load_result("e2e-replay", root=tmp_path)
    verify_images_available(docker_client, manifest)

    # Re-materialized from the manifest, with the same run id so the derived
    # seeds match.
    replayed = await execute(docker_client, request_for(manifest_run_id(manifest), tmp_path))
    report = compare(manifest, recorded, replayed.result, expected_hash=manifest.manifest_hash())

    assert report.manifest_hash_matches
    assert report.missing == []
    assert report.fidelity == 1.0, report.render()
    assert report.exact


def manifest_run_id(manifest: Any) -> str:
    return "e2e-replay"


async def test_the_manifest_pins_everything_the_run_used(
    docker_client: Any, tmp_path: Path
) -> None:
    outcome = await execute(docker_client, request_for("e2e-pins", tmp_path))
    manifest = outcome.manifest
    suite = load_suite(CHECKOUT_SUITE)

    assert manifest.suite_content_hash == suite.content_hash()
    assert {t.slug for t in manifest.tasks} == {t.slug for t in suite.tasks}
    assert all(t.snapshot_digest.startswith("sha256:") for t in manifest.tasks)
    assert len(manifest.trials) == len(suite.tasks) * N_TRIALS
    assert all(t.cassette_hash for t in manifest.trials if t.task_slug in {"happy-path"})
    assert manifest.sut_content_hash is not None
    assert manifest.env["uv_lock"].startswith("sha256:")
    assert manifest.models, "no model was recorded, so the manifest does not pin one"
    assert any("planner.md" in key for key in manifest.prompt_hashes)


async def test_a_changed_prompt_changes_the_manifest(
    docker_client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression a gate exists to catch must be visible in the pins."""
    before = (await execute(docker_client, request_for("e2e-prompt-a", tmp_path))).manifest

    planner = SUT / "checkout_agent" / "prompts" / "planner.md"
    original = planner.read_text(encoding="utf-8")
    degraded = (SUT / "checkout_agent" / "prompts" / "planner.degraded.md").read_text(
        encoding="utf-8"
    )
    planner.write_text(degraded, encoding="utf-8")
    try:
        after = (await execute(docker_client, request_for("e2e-prompt-b", tmp_path))).manifest
    finally:
        planner.write_text(original, encoding="utf-8")

    assert before.sut_content_hash != after.sut_content_hash
    # Pinned even though the degraded run never reached the model: its requests
    # miss the cassette, so no model is recorded, and the prompts are exactly
    # what someone reading the failing run needs to see.
    assert before.prompt_hashes != after.prompt_hashes
    assert after.models == ()
