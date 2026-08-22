"""The manifest check inside a replay report.

`ReplayReport.exact` is published — it is the `replay_fidelity` number in the
README and in `benchmarks/self-eval.json`. It used to include a manifest check
that both callers invoked as `manifest.manifest_hash() == manifest.manifest_hash()`,
so the published number covered a check that never ran.

These tests exist so that cannot happen again quietly: the check must be able to
fail.
"""

from __future__ import annotations

import pytest

from meridian.manifest.replay import verify_manifest
from meridian.models.manifest import Manifest

pytestmark = pytest.mark.unit

GOLDEN = "tests/fixtures/golden-manifest.json"


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    from pathlib import Path

    return Manifest.model_validate_json(
        (Path(__file__).resolve().parents[1] / "fixtures/golden-manifest.json").read_text(
            encoding="utf-8"
        )
    )


def test_a_matching_hash_verifies(manifest: Manifest) -> None:
    assert verify_manifest(manifest, manifest.manifest_hash())


def test_a_mismatched_hash_is_caught(manifest: Manifest) -> None:
    """The case the old call site could never reach."""
    assert not verify_manifest(manifest, "sha256:" + "0" * 64)


def test_no_expectation_is_not_a_failure(manifest: Manifest) -> None:
    """Replaying a run archived before hashes were persisted is not a mismatch."""
    assert verify_manifest(manifest, None)


def test_an_edited_manifest_no_longer_matches_its_recorded_hash(manifest: Manifest) -> None:
    """The property the check exists for: the file changed after it was archived."""
    recorded = manifest.manifest_hash()
    tampered = manifest.model_copy(update={"suite_slug": "something-else"})
    assert not verify_manifest(tampered, recorded)
