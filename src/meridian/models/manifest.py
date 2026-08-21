"""The run manifest — Rule 4, as a data structure.

A manifest pins every input a run depended on. If something influences a result
and is not in here, the reproducibility claim has a hole in it, and the hole is
invisible until a replay disagrees and nobody can say why.

`manifest_hash` deliberately excludes `created_unix_ms`. A hash that changes
because time passed identifies nothing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from meridian.hashing import content_hash

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ManifestTask(BaseModel):
    model_config = _FROZEN

    slug: str
    definition_hash: str
    snapshot_digest: str


class ManifestTrial(BaseModel):
    model_config = _FROZEN

    task_slug: str
    trial_index: int
    seed: int
    cassette_hash: str | None = None


class ManifestModel(BaseModel):
    model_config = _FROZEN

    model_id: str
    provider: str


class Manifest(BaseModel):
    """Everything needed to re-materialize a run."""

    model_config = _FROZEN

    meridian_version: str
    created_unix_ms: int
    commit_sha: str | None = None
    suite_slug: str
    suite_version: int
    suite_content_hash: str
    config_hash: str
    adapter_spec: str
    sut_commit_sha: str | None = None
    sut_content_hash: str | None = None
    n_trials: int
    k: int
    bootstrap_seed: int = 0
    proxy_mode: str = "replay"
    tasks: tuple[ManifestTask, ...] = ()
    trials: tuple[ManifestTrial, ...] = ()
    models: tuple[ManifestModel, ...] = ()

    # `HANDOFF §7.3` hangs prompt hashes off each model. They live on the
    # manifest instead, because a prompt is a property of the system under test
    # rather than of the model, and a run whose model was never reached -- a
    # cassette miss, a budget refusal -- still has to pin its prompts. That is
    # precisely the failing-gate case the manifest most needs to explain.
    prompt_hashes: dict[str, str] = {}
    env: dict[str, str] = {}

    def hashable(self) -> dict[str, object]:
        """The manifest minus anything that varies without the run varying.

        Only `created_unix_ms` is dropped. Everything else is either an input or
        a fact about the machine, and both belong in the identity of a run.
        """
        payload = self.model_dump(mode="json")
        payload.pop("created_unix_ms", None)
        return payload

    def manifest_hash(self) -> str:
        return content_hash(self.hashable())

    def task(self, slug: str) -> ManifestTask:
        for candidate in self.tasks:
            if candidate.slug == slug:
                return candidate
        raise KeyError(f"manifest has no task {slug!r}")

    def seeds_for(self, slug: str) -> dict[int, int]:
        return {t.trial_index: t.seed for t in self.trials if t.task_slug == slug}
