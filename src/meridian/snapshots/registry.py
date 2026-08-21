"""Digest resolution and pinning checks — `MD-FR-08`.

A tag is a mutable pointer. Pinning one means a run recorded last week cannot be
reproduced today, and nothing appears to break while that happens. Every path
that reaches a container image goes through `require_digest` first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

SNAPSHOT_REF_FILE = "snapshot-ref.json"


class UnpinnedReferenceError(ValueError):
    """Raised when a mutable tag reaches a context that requires a digest."""

    def __init__(self, reference: str) -> None:
        super().__init__(
            f"{reference!r} is a tag, not a digest. Snapshots are pinned by digest so a "
            f"historical run stays reproducible; resolve it with "
            f"`meridian snapshot build` first."
        )
        self.reference = reference


def is_digest(reference: str) -> bool:
    return DIGEST_RE.fullmatch(reference) is not None


def require_digest(reference: str) -> str:
    """Return `reference` unchanged, or raise if it is not a digest."""
    if not is_digest(reference):
        raise UnpinnedReferenceError(reference)
    return reference


def resolve_digest(client: DockerClient, reference: str) -> str:
    """Resolve any image reference to a digest, pulling by digest if absent.

    A digest that is already local is returned untouched — this never silently
    upgrades a pin.
    """
    if is_digest(reference):
        try:
            client.images.get(reference)
        except Exception:  # ImageNotFound and transport errors alike mean "not here yet"
            client.images.pull(reference)
        return reference

    image = client.images.get(reference)
    if image.id is None:  # pragma: no cover - the daemon always sets an id
        raise UnpinnedReferenceError(reference)
    return require_digest(str(image.id))


def read_snapshot_ref(env_path: str | Path) -> str | None:
    """Read the digest most recently built for this environment, if any."""
    path = Path(env_path) / SNAPSHOT_REF_FILE
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = payload.get("digest")
    return str(digest) if digest else None


def write_snapshot_ref(env_path: str | Path, *, digest: str, tag: str, platform: str) -> Path:
    """Record the digest just built, so CI can hand it to `snapshot sync`.

    This file is the seam that makes the architecture mismatch survivable: the
    committed task pins an arm64 image ID, CI rebuilds for amd64, writes the new
    digest here, and rewrites the suite before running.
    """
    path = Path(env_path) / SNAPSHOT_REF_FILE
    path.write_text(
        json.dumps(
            {"digest": require_digest(digest), "tag": tag, "platform": platform},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
