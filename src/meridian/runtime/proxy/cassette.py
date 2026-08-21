"""Recorded model interactions.

A cassette is an **ordered tape per trial**, not a request→response map. That
distinction is the whole design:

A map keyed only by request hash collapses the variance the harness exists to
measure. Two trials of the same task issue byte-identical first requests; a map
would serve both the same response, every recorded run would become
deterministic, and pass^k would only ever be 0 or 1. An ordered tape replays
trial 3 exactly as trial 3 happened, so recorded non-determinism survives replay
and pass^k means what it says.

Replay **fails closed**: an unrecorded or out-of-order request is an error, never
a live call. A cassette that silently falls through to the network is a cassette
that does not guarantee anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from meridian.hashing import content_hash

CASSETTE_VERSION = 1

# Stripped before hashing a request. Every one of these varies between otherwise
# identical calls, and leaving any of them in means every replay misses — the
# failure looks like a broken cassette rather than a bad key.
VOLATILE_REQUEST_FIELDS = (
    "request_id",
    "idempotency_key",
    "metadata",
    "timestamp",
    "created_at",
    "user",
)


class CassetteMiss(LookupError):
    """A replayed request was not recorded, or arrived out of order."""


def request_key(body: dict[str, Any]) -> str:
    """Hash a request after stripping volatile fields."""
    stripped = {k: v for k, v in body.items() if k not in VOLATILE_REQUEST_FIELDS}
    return content_hash(stripped)


class Cassette:
    """One task's recorded interactions, one tape per trial index."""

    def __init__(self, task_slug: str, trials: dict[str, list[dict[str, Any]]] | None = None):
        self.task_slug = task_slug
        self.trials: dict[str, list[dict[str, Any]]] = trials or {}
        self._cursors: dict[str, int] = {}

    # -- recording ---------------------------------------------------------

    def record(
        self,
        trial_index: int,
        *,
        key: str,
        response: dict[str, Any],
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        tape = self.trials.setdefault(str(trial_index), [])
        tape.append(
            {
                "request_hash": key,
                "response": response,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
            }
        )

    # -- replay ------------------------------------------------------------

    def replay(self, trial_index: int, key: str) -> dict[str, Any]:
        """Return the next recorded response for this trial, or fail closed."""
        tape_id = str(trial_index)
        tape = self.trials.get(tape_id)
        if tape is None:
            raise CassetteMiss(
                f"no recording for {self.task_slug} trial {trial_index}; "
                f"recorded trials are {sorted(self.trials, key=int)}"
            )
        cursor = self._cursors.get(tape_id, 0)
        if cursor >= len(tape):
            raise CassetteMiss(
                f"{self.task_slug} trial {trial_index} made {cursor + 1} model calls but only "
                f"{len(tape)} were recorded; the agent's behaviour changed since recording"
            )
        entry = tape[cursor]
        if entry["request_hash"] != key:
            raise CassetteMiss(
                f"{self.task_slug} trial {trial_index} call {cursor} sent a request hashing to "
                f"{key[:19]}… but {entry['request_hash'][:19]}… was recorded; the prompt or "
                f"model configuration changed"
            )
        self._cursors[tape_id] = cursor + 1
        return entry

    def rewind(self, trial_index: int | None = None) -> None:
        if trial_index is None:
            self._cursors.clear()
        else:
            self._cursors.pop(str(trial_index), None)

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CASSETTE_VERSION,
            "task": self.task_slug,
            "trials": {k: self.trials[k] for k in sorted(self.trials, key=int)},
        }

    def content_hash(self) -> str:
        """Pinned in the manifest, per trial, so a swapped cassette is visible."""
        return content_hash(self.to_dict())

    def trial_hash(self, trial_index: int) -> str:
        return content_hash(self.trials.get(str(trial_index), []))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Cassette:
        version = payload.get("version")
        if version != CASSETTE_VERSION:
            raise ValueError(
                f"cassette version {version!r} is not {CASSETTE_VERSION}; re-record rather "
                f"than guessing at the older format"
            )
        return cls(str(payload["task"]), dict(payload.get("trials", {})))


class CassetteStore:
    """A directory of cassettes, one JSON file per task."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._loaded: dict[str, Cassette] = {}

    def path_for(self, task_slug: str) -> Path:
        return self.root / f"{task_slug}.json"

    def get(self, task_slug: str) -> Cassette:
        if task_slug in self._loaded:
            return self._loaded[task_slug]
        path = self.path_for(task_slug)
        if path.is_file():
            cassette = Cassette.from_dict(json.loads(path.read_text(encoding="utf-8")))
        else:
            cassette = Cassette(task_slug)
        self._loaded[task_slug] = cassette
        return cassette

    def save(self, cassette: Cassette) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(cassette.task_slug)
        path.write_text(
            json.dumps(cassette.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def save_all(self) -> list[Path]:
        return [self.save(cassette) for cassette in self._loaded.values()]
