"""Canonical hashing — the float rule is enforced, not documented."""

from __future__ import annotations

import pytest

from meridian.hashing import FloatInHashedStructureError, canonical_json, content_hash


@pytest.mark.unit
def test_key_order_does_not_affect_the_hash() -> None:
    assert content_hash({"b": 1, "a": 2}) == content_hash({"a": 2, "b": 1})


@pytest.mark.unit
def test_serialization_is_compact_and_sorted() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'


@pytest.mark.unit
def test_unicode_is_preserved_not_escaped() -> None:
    assert canonical_json({"k": "café"}) == '{"k":"café"}'.encode()


@pytest.mark.unit
def test_bools_are_allowed_because_they_are_not_floats() -> None:
    assert canonical_json({"ok": True}) == b'{"ok":true}'


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected_path"),
    [
        ({"cost": 1.5}, "$.cost"),
        ({"nested": {"p": 0.5}}, "$.nested.p"),
        ({"rows": [1, 2.0]}, "$.rows[1]"),
    ],
)
def test_floats_are_refused_with_their_path(payload: dict[str, object], expected_path: str) -> None:
    with pytest.raises(FloatInHashedStructureError) as excinfo:
        canonical_json(payload)
    assert excinfo.value.path == expected_path


@pytest.mark.unit
def test_hash_is_prefixed_and_hex() -> None:
    digest = content_hash({"a": 1})
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64
