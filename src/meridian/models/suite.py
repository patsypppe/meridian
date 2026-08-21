"""Suite — a versioned, content-hashed collection of tasks."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from meridian.hashing import content_hash
from meridian.models.task import SLUG_PATTERN, TaskDefinition


class SuiteHeader(BaseModel):
    """The `suite.yaml` file itself. Tasks are discovered from `tasks/*.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN)
    version: int = Field(ge=1)
    description: str = ""
    adapter: str
    tags: tuple[str, ...] = ()


class Suite(BaseModel):
    """A loaded, validated suite.

    `content_hash` covers the header and every task definition, and nothing else
    — not the filesystem path, not load order, not mtimes. Two checkouts of the
    same commit therefore hash identically, which is what makes the manifest's
    `suite_content_hash` worth recording.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    header: SuiteHeader
    tasks: tuple[TaskDefinition, ...]
    root: Path

    @property
    def slug(self) -> str:
        return self.header.slug

    @property
    def version(self) -> int:
        return self.header.version

    @property
    def adapter_spec(self) -> str:
        return self.header.adapter

    @property
    def scored_tasks(self) -> tuple[TaskDefinition, ...]:
        """Tasks that contribute to the suite-level statistic.

        Harness probes and non-active tasks are excluded here and reported
        separately, so an aggregate never silently shrinks (`HANDOFF §8.1`).
        """
        return tuple(t for t in self.tasks if t.counts_toward_suite_score)

    @property
    def excluded_tasks(self) -> tuple[TaskDefinition, ...]:
        return tuple(t for t in self.tasks if not t.counts_toward_suite_score)

    def task(self, slug: str) -> TaskDefinition:
        for candidate in self.tasks:
            if candidate.slug == slug:
                return candidate
        raise KeyError(f"suite {self.slug!r} has no task {slug!r}")

    def content_hash(self) -> str:
        return content_hash(
            {
                "header": self.header.model_dump(mode="json"),
                "tasks": [t.model_dump(mode="json") for t in self.tasks],
            }
        )
