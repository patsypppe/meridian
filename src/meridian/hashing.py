"""Canonical JSON and content hashing — one implementation, used everywhere.

`HANDOFF §8.5` locates these functions in `manifest/build.py`. They live here
instead so that `suites/`, `config.py`, and the proxy cassette can all hash
without importing the manifest package (which depends on the run models, which
depend on the task models — a cycle). `manifest.build` re-exports both names, so
the documented import path still resolves.

The float rule is enforced, not documented. Floats are not stable across
platforms and a drifting hash silently invalidates every historical manifest, so
`canonical_json` refuses to serialize one and names the path where it found it.
Costs are integer cents, durations integer milliseconds, probabilities decimal
strings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["FloatInHashedStructureError", "canonical_json", "content_hash"]


class FloatInHashedStructureError(TypeError):
    """Raised when a float reaches a structure that is about to be hashed."""

    def __init__(self, path: str, value: float) -> None:
        super().__init__(
            f"float {value!r} at {path}: floats are not stable across platforms and "
            f"must not appear in a hashed structure. Use an integer (cents, "
            f"milliseconds) or a decimal string."
        )
        self.path = path
        self.value = value


def _reject_floats(obj: Any, path: str = "$") -> None:
    """Walk a structure and raise on the first float found.

    `bool` is a subclass of `int` and is fine; only genuine floats are rejected.
    """
    if isinstance(obj, float):
        raise FloatInHashedStructureError(path, obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            _reject_floats(value, f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            _reject_floats(value, f"{path}[{index}]")


def canonical_json(obj: Any) -> bytes:
    """Serialize deterministically: sorted keys, no whitespace, UTF-8, no floats."""
    _reject_floats(obj)
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(obj: Any) -> str:
    """`sha256:`-prefixed hash of the canonical serialization of `obj`."""
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()
