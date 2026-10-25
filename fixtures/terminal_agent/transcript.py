"""An append-only transcript of what the agent did, written as it goes.

Written line by line and flushed immediately, so that when the harness kills the
agent at its deadline the record of what it was doing survives. The harness reads
the tail of this file into a timeout verdict.

Standard library only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

TRANSCRIPT_RELATIVE = ".meridian/transcript.jsonl"
FIELD_LIMIT = 400


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > FIELD_LIMIT:
        return value[:FIELD_LIMIT] + f"... [{len(value) - FIELD_LIMIT} more]"
    return value


def record(workdir: str, event: str, **fields: Any) -> None:
    """Append one event. Never raises: a transcript must not be able to fail a trial."""
    line = {"t": round(time.time(), 2), "event": event}
    line.update({key: _clip(value) for key, value in fields.items()})
    try:
        path = Path(workdir) / TRANSCRIPT_RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
            handle.flush()
    except OSError:
        pass
