"""Rule enforcement and structured errors.

`load_suite` never raises a bare string. Every rejection is a `ValidationIssue`
carrying a stable `code`, the file it came from, the task it belongs to, and the
field that broke — because the CLI renders these as a table and a developer
reading a red build needs the rule name, not a Pydantic traceback.

The codes are the seven rules in `HANDOFF §7.1` plus the structural failures a
loader hits before it can even reach them.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_core import ErrorDetails

# Validators embed their rule code in the message as `"code: human explanation"`.
# This is the seam between Pydantic's error model and Meridian's rule vocabulary.
_CODE_PREFIX = re.compile(r"^([a-z_]+): (.*)$", re.DOTALL)

# (pydantic error type, final path element) -> Meridian rule code.
_BY_TYPE_AND_FIELD: dict[tuple[str, str | None], str] = {
    ("extra_forbidden", None): "extra_fields",
    ("missing", None): "missing_field",
    ("string_pattern_mismatch", "slug"): "invalid_slug",
    ("string_pattern_mismatch", "snapshot"): "unpinned_snapshot",
    ("too_short", "outcome_assertions"): "no_outcome_assertion",
    ("greater_than_equal", "timeout_seconds"): "timeout_out_of_range",
    ("less_than_equal", "timeout_seconds"): "timeout_out_of_range",
    ("union_tag_invalid", None): "unknown_assertion_kind",
    ("union_tag_not_found", None): "unknown_assertion_kind",
}


class ValidationIssue(BaseModel):
    """One rule violation, addressed well enough to fix without guessing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    file: str
    task_slug: str | None = None
    field: str | None = None
    message: str

    def render(self) -> str:
        where = self.file if self.task_slug is None else f"{self.file} [{self.task_slug}]"
        field = f" ({self.field})" if self.field else ""
        return f"{self.code}: {where}{field} — {self.message}"


class SuiteValidationError(Exception):
    """Raised with every issue found, not just the first."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        summary = "; ".join(issue.render() for issue in issues)
        super().__init__(f"{len(issues)} validation issue(s): {summary}")

    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


def _field_path(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc) or "<root>"


def _classify(error: ErrorDetails) -> tuple[str, str]:
    """Map one Pydantic error onto a Meridian rule code and a clean message."""
    raw_type = str(error.get("type", ""))
    message = str(error.get("msg", ""))

    if raw_type == "value_error":
        # Pydantic prefixes ValueError messages with "Value error, ".
        stripped = message.removeprefix("Value error, ")
        matched = _CODE_PREFIX.match(stripped)
        if matched:
            return matched.group(1), matched.group(2)
        return "invalid_value", stripped

    loc = error.get("loc", ())
    last = str(loc[-1]) if loc else None
    for key in ((raw_type, last), (raw_type, None)):
        if key in _BY_TYPE_AND_FIELD:
            return _BY_TYPE_AND_FIELD[key], message
    return raw_type or "invalid_value", message


def issues_from_pydantic(
    exc: ValidationError,
    *,
    file: str,
    task_slug: str | None = None,
) -> list[ValidationIssue]:
    """Translate a Pydantic failure into Meridian rule violations."""
    issues: list[ValidationIssue] = []
    for error in exc.errors():
        code, message = _classify(error)
        loc = error.get("loc", ())
        field = _field_path(tuple(loc))
        if code == "extra_fields":
            message = f"unknown key {field!r} — extra fields are not permitted"
        issues.append(
            ValidationIssue(
                code=code,
                file=file,
                task_slug=task_slug,
                field=field,
                message=message,
            )
        )
    return issues
