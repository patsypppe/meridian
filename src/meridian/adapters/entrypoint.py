"""The in-container entrypoint. PID 1's child; the harness never runs this.

Reads a serialized `TrialContext`, resolves the adapter spec, runs the agent,
and writes `result.json` and `transcript.json` next to the payload. It exits 0
whenever it *ran the agent to a conclusion* — including a conclusion the agent
did not like — because the trial's verdict is decided outside, by grading the
final state. A non-zero exit here means the entrypoint itself could not run,
which is a harness error.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from meridian.adapters.base import AdapterSpec
from meridian.models.adapter import AdapterResult, TrialContext

EXIT_OK = 0
EXIT_ENTRYPOINT_FAILED = 70


def build_adapter(spec: AdapterSpec) -> Any:
    """Pick the adapter implementation for a spec kind.

    Imported lazily and per-kind: the LangGraph adapter imports LangGraph, and a
    subprocess trial should not pay for — or fail on — a framework it never uses.
    """
    if spec.kind == "subprocess":
        from meridian.adapters.subprocess_adapter import SubprocessAdapter

        return SubprocessAdapter(spec)
    # The LangGraph branch lands with WP-5. Until then a langgraph spec parses
    # cleanly and fails loudly here rather than half-working.
    raise ValueError(f"no adapter implementation for kind {spec.kind!r}")


async def run(context_path: Path) -> int:
    payload_dir = context_path.parent
    raw = json.loads(context_path.read_text(encoding="utf-8"))
    adapter_spec = AdapterSpec.parse(raw.pop("adapter_spec"))
    ctx = TrialContext.model_validate(raw)

    adapter = build_adapter(adapter_spec)
    try:
        result = await adapter.invoke(ctx)
    except Exception as exc:  # the agent blew up: an agent failure, not a harness one
        result = AdapterResult(
            completed=False,
            error=f"{type(exc).__name__}: {exc}",
            transcript_path=str(payload_dir / "transcript.json"),
        )
        (payload_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        await adapter.teardown()

    (payload_dir / "result.json").write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    transcript = payload_dir / "transcript.json"
    if not transcript.exists():
        transcript.write_text(
            json.dumps({"task": ctx.task_slug, "trial": ctx.trial_index, "turns": result.turns}),
            encoding="utf-8",
        )
    return EXIT_OK


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: entrypoint.py <context.json>", file=sys.stderr)
        return EXIT_ENTRYPOINT_FAILED
    try:
        return asyncio.run(run(Path(argv[1])))
    except Exception:
        # The entrypoint itself failed. This is Meridian's problem, and the
        # non-zero exit is what tells the runner to classify it as one.
        traceback.print_exc()
        return EXIT_ENTRYPOINT_FAILED


if __name__ == "__main__":  # pragma: no cover - exercised inside the container
    raise SystemExit(main(sys.argv))
