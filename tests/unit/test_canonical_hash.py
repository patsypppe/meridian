"""Canonical hashing and the golden manifest.

`test_the_golden_manifest_hash_has_not_moved` is **intentionally brittle**. That
is its job. Every historical manifest hash is only verifiable while this function
produces the same bytes for the same inputs, so a change here silently
invalidates the entire archive.

If this test fails and the change was deliberate, the archive is not
retroactively fixable — bump a hash-version field and record why, rather than
updating the golden value and moving on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian.hashing import FloatInHashedStructureError, canonical_json, content_hash
from meridian.manifest.build import canonical_json as reexported_canonical
from meridian.manifest.build import content_hash as reexported_hash
from meridian.models.manifest import Manifest

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIXTURES / "golden-manifest.json"
GOLDEN_HASH = FIXTURES / "golden-manifest-hash.txt"


@pytest.fixture(scope="module")
def golden() -> Manifest:
    return Manifest.model_validate_json(GOLDEN.read_text(encoding="utf-8"))


def test_the_golden_manifest_hash_has_not_moved(golden: Manifest) -> None:
    """Deliberately brittle. See the module docstring before changing it."""
    assert golden.manifest_hash() == GOLDEN_HASH.read_text(encoding="utf-8").strip()


def test_hashing_is_re_exported_where_the_handoff_says_it_lives() -> None:
    assert reexported_canonical is canonical_json
    assert reexported_hash is content_hash


def test_the_hash_ignores_when_the_run_happened(golden: Manifest) -> None:
    """A hash that changes because time passed identifies nothing."""
    later = golden.model_copy(update={"created_unix_ms": golden.created_unix_ms + 86_400_000})
    assert later.manifest_hash() == golden.manifest_hash()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("suite_content_hash", "sha256:" + "ab" * 32),
        ("config_hash", "sha256:" + "cd" * 32),
        ("adapter_spec", "subprocess:probes:noop"),
        ("sut_content_hash", "sha256:" + "ef" * 32),
        ("n_trials", 7),
        ("k", 2),
        ("bootstrap_seed", 42),
        ("proxy_mode", "record"),
        ("meridian_version", "0.2.0"),
        ("commit_sha", "1111111111111111111111111111111111111111"),
    ],
)
def test_every_pinned_input_changes_the_hash(golden: Manifest, field: str, value: object) -> None:
    """If changing an input does not change the hash, it was never really pinned."""
    assert golden.model_copy(update={field: value}).manifest_hash() != golden.manifest_hash()


def test_the_environment_is_part_of_the_identity(golden: Manifest) -> None:
    """Same code, different dependency set, is a different harness."""
    other = golden.model_copy(update={"env": {**golden.env, "uv_lock": "sha256:" + "00" * 32}})
    assert other.manifest_hash() != golden.manifest_hash()


def test_trial_seeds_are_pinned(golden: Manifest) -> None:
    changed = list(golden.trials)
    changed[0] = changed[0].model_copy(update={"seed": 999_999})
    assert golden.model_copy(update={"trials": tuple(changed)}).manifest_hash() != (
        golden.manifest_hash()
    )


def test_cassette_hashes_are_pinned(golden: Manifest) -> None:
    """A swapped recording must be visible, not silent."""
    changed = list(golden.trials)
    changed[0] = changed[0].model_copy(update={"cassette_hash": "sha256:" + "fe" * 32})
    assert golden.model_copy(update={"trials": tuple(changed)}).manifest_hash() != (
        golden.manifest_hash()
    )


def test_the_golden_manifest_contains_no_floats() -> None:
    """The rule the whole hashing scheme depends on, checked on real data."""
    canonical_json(json.loads(GOLDEN.read_text(encoding="utf-8")))


def test_a_float_anywhere_in_a_manifest_is_refused(golden: Manifest) -> None:
    payload = golden.hashable()
    payload["env"] = {"drift": 0.5}  # type: ignore[index]
    with pytest.raises(FloatInHashedStructureError):
        content_hash(payload)


def test_prompt_hashes_are_pinned_on_the_manifest_not_the_model(golden: Manifest) -> None:
    """A run whose model was never reached still has to pin its prompts.

    A cassette miss or a budget refusal produces no model record at all, and that
    is exactly the failing run whose prompts someone needs to see.
    """
    assert golden.prompt_hashes
    other = golden.model_copy(update={"prompt_hashes": {"p.md": "sha256:" + "00" * 32}})
    assert other.manifest_hash() != golden.manifest_hash()


def test_seeds_are_recoverable_from_the_manifest(golden: Manifest) -> None:
    """Replay reproduces seeds by lookup, not by re-deriving them."""
    assert golden.seeds_for("happy-path") == {0: 1000, 1: 1001}
