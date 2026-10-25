"""The dashboard's reference vectors must match the Python statistics exactly.

`web/src/stats.test.ts` checks the TypeScript implementation against this file; this
test checks the file against Python. Between them a change to either side that moves
a number breaks the build instead of silently desynchronising the dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.export_web_vectors import OUT, vectors

pytestmark = pytest.mark.unit


def test_committed_vectors_match_the_python_statistics() -> None:
    committed = json.loads(Path(OUT).read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(vectors()))
    assert committed == fresh, (
        "web/src/__fixtures__/stats-vectors.json is stale; "
        "run `uv run python -m benchmarks.export_web_vectors`"
    )
