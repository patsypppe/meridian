"""Task definition — the primary user-authored artifact.

Everything here is validated on load and frozen afterwards. The error codes
raised by validators are stable strings prefixed onto the message (`"code: …"`)
so `suites.validate` can map a Pydantic failure back to the rule in
`HANDOFF §7.1` that rejected it, and the CLI can render a table naming the file,
the task, and the rule.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG_PATTERN = r"^[a-z0-9-]{3,48}$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"

MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 3600

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Provenance(BaseModel):
    """Why this task exists — `MD-FR-02`.

    Required, with `synthetic_reason` as the escape hatch for tasks no real trace
    motivated. A task whose origin nobody remembers is how a suite rots: it
    cannot be judged still-relevant, so it is never retired, so it accumulates.
    """

    model_config = _FROZEN

    trace_id: str | None = None
    session_id: str | None = None
    failure_code: str | None = None
    synthetic_reason: str | None = None

    @model_validator(mode="after")
    def _requires_an_origin(self) -> Provenance:
        if not self.trace_id and not self.synthetic_reason:
            raise ValueError(
                "provenance_required: provenance needs either a trace_id (this task "
                "came from a real failure) or a synthetic_reason (it did not, and "
                "here is why it exists anyway)"
            )
        return self


class Resources(BaseModel):
    """Per-trial resource ceiling.

    `cpus` is stored as a decimal **string**, not a float: the task definition is
    canonically hashed and floats are not stable across platforms
    (`HANDOFF §8.5`). YAML authors still write `cpus: 2.0`; the validator
    normalizes.
    """

    model_config = _FROZEN

    cpus: str = "2.0"
    memory_mb: int = Field(default=2048, ge=64, le=65536)
    pids: int = Field(default=256, ge=16, le=8192)

    @field_validator("cpus", mode="before")
    @classmethod
    def _normalize_cpus(cls, value: Any) -> str:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"invalid_resources: cpus {value!r} is not a number") from exc
        if parsed <= 0 or parsed > 64:
            raise ValueError(f"invalid_resources: cpus {value!r} outside (0, 64]")
        return format(parsed.normalize(), "f")

    @property
    def nano_cpus(self) -> int:
        """Docker's `nano_cpus`, derived exactly from the decimal string."""
        return int(Decimal(self.cpus) * 1_000_000_000)


class Environment(BaseModel):
    """The snapshot a trial starts from — `MD-FR-08`.

    `snapshot` is a digest and never a tag. A tag is a mutable pointer; pinning
    one means a run recorded last week cannot be reproduced today, which defeats
    Rule 4 without anything appearing to break.
    """

    model_config = _FROZEN

    snapshot: str = Field(pattern=DIGEST_PATTERN)
    workdir: str = "/work"
    network: Literal["none", "proxy-only"] = "none"
    resources: Resources = Resources()

    @field_validator("workdir")
    @classmethod
    def _absolute_workdir(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"invalid_workdir: workdir {value!r} must be absolute")
        return value.rstrip("/") or "/"


class InputFile(BaseModel):
    """One seed file copied into the trial container before it starts."""

    model_config = _FROZEN

    src: str
    dest: str

    @field_validator("dest")
    @classmethod
    def _absolute_dest(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"invalid_input: dest {value!r} must be absolute")
        return value


class TaskInput(BaseModel):
    """What the agent is given: a prompt, and files materialized into the container."""

    model_config = _FROZEN

    prompt_file: str
    files: tuple[InputFile, ...] = ()


class Limits(BaseModel):
    """Hard ceilings. `budget_cents` is an integer because it gets hashed."""

    model_config = _FROZEN

    timeout_seconds: int = Field(
        default=300,
        ge=MIN_TIMEOUT_SECONDS,
        le=MAX_TIMEOUT_SECONDS,
    )
    max_tokens: int = Field(default=60_000, ge=1)
    budget_cents: int = Field(default=25, ge=1)


# --- Outcome assertions -------------------------------------------------------
# Rule 2: assertions run against the FINAL STATE of the environment. There is no
# assertion kind that inspects the agent's trajectory, and adding one would be a
# product change, not an implementation detail.


class _Assertion(BaseModel):
    model_config = _FROZEN


class FileExists(_Assertion):
    kind: Literal["file_exists"]
    path: str
    should_exist: bool = True


class FileMatches(_Assertion):
    kind: Literal["file_matches"]
    path: str
    pattern: str
    dotall: bool = False


class JsonPathEquals(_Assertion):
    kind: Literal["json_path_equals"]
    path: str
    json_path: str
    expected: Any


class JsonPathMatches(_Assertion):
    kind: Literal["json_path_matches"]
    path: str
    json_path: str
    pattern: str


class SqliteQueryEquals(_Assertion):
    kind: Literal["sqlite_query_equals"]
    database: str
    query: str
    expected: tuple[tuple[Any, ...], ...]


class ExitCode(_Assertion):
    kind: Literal["exit_code"]
    expected: int


class PytestAssertion(_Assertion):
    kind: Literal["pytest"]
    path: str
    selector: str | None = None


OutcomeAssertion = Annotated[
    FileExists
    | FileMatches
    | JsonPathEquals
    | JsonPathMatches
    | SqliteQueryEquals
    | ExitCode
    | PytestAssertion,
    Field(discriminator="kind"),
]


class EfficiencyExpectations(BaseModel):
    """Secondary signals — recorded, reported, and never pass/fail (Rule 2)."""

    model_config = _FROZEN

    max_tool_calls: int | None = None
    max_turns: int | None = None


class TaskDefinition(BaseModel):
    """One task. Frozen, fully validated, and canonically hashable."""

    model_config = _FROZEN

    slug: str = Field(pattern=SLUG_PATTERN)
    description: str = ""
    provenance: Provenance
    environment: Environment
    input: TaskInput
    limits: Limits = Limits()
    outcome_assertions: tuple[OutcomeAssertion, ...] = Field(min_length=1)
    efficiency_expectations: EfficiencyExpectations = EfficiencyExpectations()
    tags: tuple[str, ...] = ()

    # `state` drives suite aggregation: only `active` tasks enter the suite-level
    # pass^k. `quarantined` and `withdrawn` are excluded and listed separately, so
    # a shrinking task set is visible rather than silent (HANDOFF §8.1).
    state: Literal["active", "quarantined", "withdrawn"] = "active"

    # A task may override the suite's adapter. The contamination pair needs a
    # deterministic non-LLM runner: it measures isolation, and routing it through
    # a model would make the probe's result depend on the model's mood.
    adapter: str | None = None

    # `role` separates the harness's own self-tests from the eval set. The
    # contamination pair (HANDOFF §11.3) must never contribute to a reported
    # score — it measures Meridian, not the agent.
    role: Literal["eval", "harness_probe"] = "eval"

    @property
    def counts_toward_suite_score(self) -> bool:
        return self.state == "active" and self.role == "eval"

    @model_validator(mode="after")
    def _inputs_land_inside_the_workdir(self) -> TaskDefinition:
        """Seed files must land under the workdir.

        The rootfs is read-only and the workdir is the only writable mount, so a
        `dest` outside it fails at container start with a permission error that
        looks like an agent problem. Catching it at load time turns a confusing
        runtime failure into an obvious authoring mistake.
        """
        workdir = self.environment.workdir.rstrip("/")
        for seed in self.input.files:
            if not (seed.dest == workdir or seed.dest.startswith(f"{workdir}/")):
                raise ValueError(
                    f"input_outside_workdir: {seed.dest!r} is not under the workdir "
                    f"{workdir!r}; the rest of the filesystem is read-only"
                )
        return self

    def referenced_files(self) -> tuple[str, ...]:
        """Every suite-relative path this task depends on, for existence checks."""
        return (self.input.prompt_file, *(f.src for f in self.input.files))

    def definition_hash(self) -> str:
        """Content hash of the task, used in the manifest to pin exactly this task."""
        from meridian.hashing import content_hash

        return content_hash(self.model_dump(mode="json"))


def is_pinned_digest(reference: str) -> bool:
    """True when `reference` is a digest rather than a mutable tag."""
    return re.fullmatch(DIGEST_PATTERN, reference) is not None
