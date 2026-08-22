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

from meridian.adapters.base import AdapterSpec, AdapterSpecError
from meridian.models.adapter import AdapterResult, TrialContext

EXIT_OK = 0
EXIT_ENTRYPOINT_FAILED = 70

# Written when the entrypoint itself could not run. The harness reads it so the
# trial's detail says *what* was misconfigured rather than only that something
# was.
HARNESS_ERROR_FILE = "harness_error.txt"

# Short: this only ever runs after the agent has already failed, and the answer
# is "is anything listening", not "is it fast".
PROXY_HEALTH_TIMEOUT_SECONDS = 5.0


def build_adapter(spec: AdapterSpec) -> Any:
    """Pick the adapter implementation for a spec kind.

    Imported lazily and per-kind: the LangGraph adapter imports LangGraph, and a
    subprocess trial should not pay for — or fail on — a framework it never uses.
    """
    if spec.kind == "subprocess":
        from meridian.adapters.subprocess_adapter import SubprocessAdapter

        return SubprocessAdapter(spec)
    if spec.kind == "langgraph":
        from meridian.adapters.langgraph_adapter import LangGraphAdapter

        return LangGraphAdapter(spec)
    raise ValueError(f"no adapter implementation for kind {spec.kind!r}")


def record_harness_error(payload_dir: Path, message: str) -> int:
    """Report a Meridian misconfiguration, not an agent failure."""
    (payload_dir / HARNESS_ERROR_FILE).write_text(message, encoding="utf-8")
    (payload_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
    return EXIT_ENTRYPOINT_FAILED


def proxy_is_unreachable(base_url: str) -> bool:
    """Ask the proxy whether it is alive. Used only to explain a failure.

    This is the seam that separates "the agent failed" from "the agent was never
    given a model". It asks the proxy directly rather than matching on the text of
    the agent's error, because the wording of that error belongs to whatever
    framework raised it and will not survive an upgrade — and a classification
    rule built on someone else's error string fails silently and in the expensive
    direction.

    Unreachable means *refused or unanswered*, not "returned an error". A proxy
    that answers with a budget refusal or a cassette miss is working exactly as
    designed, and those are genuine agent-visible failures.
    """
    if not base_url:
        return False
    try:
        import httpx
    except ImportError:
        # The trial image supplies httpx, not the payload. An environment without
        # it must not turn every agent failure into an unanswerable question:
        # raising here would escape `run()`, exit EXIT_ENTRYPOINT_FAILED, and be
        # classified as a harness error — which is *retried*. Every genuine agent
        # `fail` on such an image would then get a second attempt, which is the
        # one thing the retry policy exists to forbid.
        return False

    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/healthz", timeout=PROXY_HEALTH_TIMEOUT_SECONDS
        )
    except Exception:
        # Deliberately broad. `httpx.InvalidURL` is not an `HTTPError`, so a
        # malformed base URL would otherwise crash the entrypoint rather than
        # answer the question. Anything that prevents an answer means the proxy
        # could not be confirmed alive, which is what the caller asked.
        return True
    return response.status_code >= 500


async def run(context_path: Path) -> int:
    payload_dir = context_path.parent
    raw = json.loads(context_path.read_text(encoding="utf-8"))

    # Everything up to and including resolving the adapter is configuration. A
    # failure here says the harness was set up wrong, and calling it an agent
    # failure would put a configuration mistake into the score.
    try:
        adapter_spec = AdapterSpec.parse(raw.pop("adapter_spec"))
        ctx = TrialContext.model_validate(raw)
        adapter = build_adapter(adapter_spec)
        adapter_spec.resolve_target()
    except (AdapterSpecError, ValueError) as exc:
        return record_harness_error(payload_dir, f"{type(exc).__name__}: {exc}")

    try:
        result = await adapter.invoke(ctx)
    except AdapterSpecError as exc:
        return record_harness_error(payload_dir, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # the agent blew up: an agent failure, not a harness one
        result = AdapterResult(
            completed=False,
            error=f"{type(exc).__name__}: {exc}",
            transcript_path=str(payload_dir / "transcript.json"),
        )
        (payload_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        await adapter.teardown()

    # An agent that could not reach the model has not been evaluated, so its
    # failure is Meridian's and not the agent's. Checked here rather than at the
    # exception site because an agent may *return* the failure instead of raising
    # it -- the checkout fixture catches httpx errors and reports them as an
    # AdapterResult -- and both paths must classify the same way.
    #
    # Getting this wrong is expensive in the direction that wastes a person's
    # afternoon: a dead proxy fails every trial of every task, the gate reports
    # the largest regression it has ever seen, and the developer reverts a change
    # that was never the cause.
    if result.error and proxy_is_unreachable(ctx.model_base_url):
        return record_harness_error(
            payload_dir,
            f"the model proxy at {ctx.model_base_url} did not answer its health "
            f"check, so the agent was never given a model. The agent reported: "
            f"{result.error}",
        )

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
