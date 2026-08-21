"""SQLite assertions over the produced database.

Opened read-only through a URI so grading cannot alter the state it is judging —
a grader that mutates the evidence is a grader whose second run disagrees with
its first.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from meridian.grading.graders.base import GraderError, failed, passed, resolve
from meridian.models.run import AssertionResult
from meridian.models.task import SqliteQueryEquals


def _normalize(rows: Any) -> list[list[Any]]:
    return [list(row) for row in rows]


def grade_sqlite_query_equals(
    assertion: SqliteQueryEquals, state_dir: Path, workdir: str
) -> AssertionResult:
    database = resolve(state_dir, assertion.database, workdir)
    if not database.is_file():
        return failed(assertion.kind, f"{assertion.database} does not exist")

    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return failed(assertion.kind, f"{assertion.database} could not be opened: {exc}")

    try:
        rows = _normalize(connection.execute(assertion.query).fetchall())
    except sqlite3.Error as exc:
        # A query that will not run is a broken assertion, never an agent failure.
        raise GraderError(f"query failed against {assertion.database}: {exc}") from exc
    finally:
        connection.close()

    expected = _normalize(assertion.expected)
    if rows == expected:
        return passed(assertion.kind, f"{assertion.query} returned {expected}")
    return failed(
        assertion.kind,
        f"{assertion.query} returned {rows}, expected {expected}",
    )
