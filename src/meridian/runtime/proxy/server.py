"""The proxy itself: a small, boring ASGI app.

It speaks enough of the Anthropic messages API that an adapter needs only a
`base_url` override. It is deliberately not a general-purpose proxy — it accepts
one endpoint, and everything else 404s, because an eval agent has no legitimate
reason to reach anything else.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from meridian.config import ProxyMode
from meridian.runtime.proxy.budget import BudgetExceeded, BudgetLedger, TrialLimits
from meridian.runtime.proxy.cassette import CassetteMiss, CassetteStore, request_key
from meridian.runtime.secrets import ProviderCredential, load_credential

TASK_HEADER = "x-meridian-task"
TRIAL_HEADER = "x-meridian-trial"

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

UPSTREAM_TIMEOUT_SECONDS = 120.0


@dataclass
class ProxyConfig:
    mode: ProxyMode
    cassette_dir: Path
    limits: dict[str, TrialLimits]
    run_budget_cents: int | None = None
    upstream_base_url: str = ANTHROPIC_BASE_URL
    credential: ProviderCredential | None = None


class ProxyState:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.cassettes = CassetteStore(config.cassette_dir)
        self.ledger = BudgetLedger(limits=config.limits, run_budget_cents=config.run_budget_cents)


def _trial_identity(request: Request) -> tuple[str, int]:
    task = request.headers.get(TASK_HEADER, "unknown")
    try:
        trial = int(request.headers.get(TRIAL_HEADER, "0"))
    except ValueError:
        trial = 0
    return task, trial


def _usage_from(response_body: dict[str, Any]) -> tuple[int, int]:
    usage = response_body.get("usage") or {}
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


async def _call_upstream(state: ProxyState, body: dict[str, Any]) -> dict[str, Any]:
    credential = state.config.credential or load_credential()
    headers = {
        credential.header_name: credential.header_value,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{state.config.upstream_base_url}/v1/messages", json=body, headers=headers
        )
    response.raise_for_status()
    parsed: dict[str, Any] = response.json()
    return parsed


async def messages(request: Request) -> Response:
    state: ProxyState = request.app.state.proxy
    task_slug, trial_index = _trial_identity(request)

    try:
        body = json.loads(await request.body())
    except json.JSONDecodeError as exc:
        return JSONResponse(
            {"type": "error", "error": {"type": "invalid_request", "message": str(exc)}},
            status_code=400,
        )

    try:
        state.ledger.check_before(task_slug, trial_index)
    except BudgetExceeded as exc:
        # 429 with a structured body. The agent sees a failed call and the trial
        # becomes a `fail` — it did not achieve the required state within its
        # budget. It is emphatically not a harness error.
        return JSONResponse(exc.as_payload(), status_code=429)

    key = request_key(body)
    cassette = state.cassettes.get(task_slug)
    model = str(body.get("model", "unknown"))

    if state.config.mode is ProxyMode.REPLAY:
        try:
            entry = cassette.replay(trial_index, key)
        except CassetteMiss as exc:
            # Fails closed. A cassette that falls through to the network on a
            # miss guarantees nothing, and the miss would only surface later as
            # an unexplained cost or an unreproducible result.
            return JSONResponse(
                {
                    "type": "error",
                    "error": {
                        "type": "cassette_miss",
                        "message": str(exc),
                        "mode": "replay",
                    },
                },
                status_code=424,
            )
        state.ledger.charge(
            task_slug,
            trial_index,
            model=model,
            input_tokens=int(entry["input_tokens"]),
            output_tokens=int(entry["output_tokens"]),
        )
        return JSONResponse(entry["response"])

    try:
        response_body = await _call_upstream(state, body)
    except Exception as exc:
        return JSONResponse(
            {"type": "error", "error": {"type": "upstream_error", "message": str(exc)}},
            status_code=502,
        )

    input_tokens, output_tokens = _usage_from(response_body)
    state.ledger.charge(
        task_slug,
        trial_index,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    if state.config.mode is ProxyMode.RECORD:
        cassette.record(
            trial_index,
            key=key,
            response=response_body,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        state.cassettes.save(cassette)

    return JSONResponse(response_body)


async def healthz(request: Request) -> Response:
    state: ProxyState = request.app.state.proxy
    return JSONResponse({"ok": True, "mode": str(state.config.mode)})


async def usage(request: Request) -> Response:
    """Run usage, for the cost governor and the run report."""
    state: ProxyState = request.app.state.proxy
    return JSONResponse(state.ledger.snapshot())


def create_app(config: ProxyConfig) -> Starlette:
    app = Starlette(
        routes=[
            Route("/v1/messages", messages, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
            Route("/v1/usage", usage, methods=["GET"]),
        ]
    )
    app.state.proxy = ProxyState(config)
    return app


def config_from_env(env: dict[str, str] | None = None) -> ProxyConfig:
    """Build the proxy's configuration from its container environment.

    Limits arrive from the harness, not from the agent: a ceiling the thing being
    measured can raise is not a ceiling.
    """
    environment = dict(os.environ if env is None else env)
    raw_limits = json.loads(environment.get("MERIDIAN_LIMITS", "{}"))
    limits = {
        slug: TrialLimits(
            max_tokens=int(entry["max_tokens"]), budget_cents=int(entry["budget_cents"])
        )
        for slug, entry in raw_limits.items()
    }
    run_budget = environment.get("MERIDIAN_RUN_BUDGET_CENTS")
    return ProxyConfig(
        mode=ProxyMode(environment.get("MERIDIAN_PROXY_MODE", "replay")),
        cassette_dir=Path(environment.get("MERIDIAN_CASSETTE_DIR", "/cassettes")),
        limits=limits,
        run_budget_cents=int(run_budget) if run_budget else None,
        upstream_base_url=environment.get("MERIDIAN_UPSTREAM_BASE_URL", ANTHROPIC_BASE_URL),
    )


def main() -> None:  # pragma: no cover - the container's entry point
    import uvicorn

    uvicorn.run(
        create_app(config_from_env()),
        # Binds broadly because the container sits on an internal network whose
        # only other member is the trial.
        host="0.0.0.0",
        port=int(os.environ.get("MERIDIAN_PROXY_PORT", "8080")),
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
