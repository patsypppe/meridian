"""A deterministic stand-in for a model provider.

**Why this exists, stated plainly.** Meridian's cassettes have to be recorded
against *something*. Recording against a real provider costs money, needs a
credential, and produces a recording nobody else can regenerate. This stub
produces the same wire shape — Anthropic messages with tool use — from the
conversation alone, so the entire pipeline (record → replay → gate → self-eval)
runs at zero cost and reproduces on any machine.

It is **not** a model and never pretends to be one. What it does faithfully
reproduce is the property the harness measures:

- It reads the system prompt. If the planner's expiry instruction is missing, it
  applies an expired coupon — exactly the seeded regression, arising from the
  prompt rather than from a flag.
- It slips on a schedule, so a task has genuine trial-to-trial variance and
  pass^k is a number worth reporting rather than 0 or 1.

Swap `MERIDIAN_UPSTREAM_BASE_URL` to a real provider and re-record; nothing
downstream changes.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# The planner sentence whose removal is the seeded regression.
EXPIRY_INSTRUCTION = re.compile(r"expire", re.IGNORECASE)

# One trial in this many "slips" — the agent forgets its last step. Set to 0 to
# make the stub perfectly deterministic.
DEFAULT_SLIP_EVERY = 5

INPUT_TOKENS_PER_TURN = 420
OUTPUT_TOKENS_PER_TURN = 95


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the tools have returned so far, keyed by tool name.

    A real model reads its own transcript; so does this. That keeps the stub
    honest about what information is actually available at each turn.
    """
    seen: dict[str, Any] = {}
    pending: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                pending.append(str(block.get("name")))
            elif block.get("type") == "tool_result" and pending:
                name = pending.pop(0)
                try:
                    seen[name] = json.loads(block.get("content", "{}"))
                except json.JSONDecodeError:
                    seen[name] = {}
    return seen


def _called(messages: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            names.extend(
                str(block.get("name"))
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
    return names


def _order_id(messages: list[dict[str, Any]]) -> int:
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            match = re.search(r"\b(\d{3,})\b", content)
            if match:
                return int(match.group(1))
    return 0


def _tool_use(tool_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": arguments}


def _message(content: list[dict[str, Any]], *, stop_reason: str) -> dict[str, Any]:
    return {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": "meridian-stub",
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": INPUT_TOKENS_PER_TURN,
            "output_tokens": OUTPUT_TOKENS_PER_TURN,
        },
    }


def _discount(subtotal_cents: int, percent_off: int) -> int:
    """Nearest cent, ties away from zero — the policy's wording, in integers."""
    return (subtotal_cents * percent_off * 2 + 100) // 200


def decide(
    system: str, messages: list[dict[str, Any]], *, trial_index: int, slip_every: int
) -> dict[str, Any]:
    """Produce the next assistant turn from the conversation so far."""
    called = _called(messages)
    results = _tool_results(messages)
    order_id = _order_id(messages)
    checks_expiry = bool(EXPIRY_INSTRUCTION.search(system))

    if "read_order" not in called:
        return _message(
            [_tool_use("t1", "read_order", {"order_id": order_id})], stop_reason="tool_use"
        )

    order = results.get("read_order") or {}

    # An order with no line items cannot be invoiced.
    if not order.get("items"):
        if "write_refusal" not in called:
            return _message(
                [
                    _tool_use(
                        "t2",
                        "write_refusal",
                        {
                            "order_id": order_id,
                            "reason": "the order is missing its line items, so there is "
                            "nothing to invoice",
                        },
                    )
                ],
                stop_reason="tool_use",
            )
        return _message(
            [{"type": "text", "text": f"Order {order_id} cannot be invoiced; refusal recorded."}],
            stop_reason="end_turn",
        )

    if "read_policy" not in called:
        return _message([_tool_use("t3", "read_policy", {})], stop_reason="tool_use")

    coupon_code = order.get("coupon_code")
    if coupon_code and "query_coupon" not in called:
        return _message(
            [_tool_use("t4", "query_coupon", {"code": coupon_code})], stop_reason="tool_use"
        )

    subtotal = sum(int(item["qty"]) * int(item["unit_cents"]) for item in order["items"])
    coupon_row = results.get("query_coupon") or {}

    applied = False
    decline_reason: str | None = None
    total = subtotal

    if not coupon_code:
        decline_reason = "no coupon was supplied"
    elif not coupon_row.get("found"):
        decline_reason = f"coupon {coupon_code} does not exist"
    else:
        expired = str(coupon_row["expires_on"]) < str(order.get("as_of", ""))
        if expired and checks_expiry:
            decline_reason = (
                f"coupon {coupon_code} expired on {coupon_row['expires_on']}, "
                f"before the order date {order.get('as_of')}"
            )
        else:
            # Without the expiry instruction in the planner, the expiry check is
            # simply never made — the regression is an omission, not a mistake.
            applied = True
            total = subtotal - _discount(subtotal, int(coupon_row["percent_off"]))

    coupon_block: dict[str, Any] = {"code": coupon_code, "applied": applied}
    if applied:
        coupon_block["percent_off"] = int(coupon_row["percent_off"])
    else:
        coupon_block["decline_reason"] = decline_reason

    if "write_invoice" not in called:
        return _message(
            [
                _tool_use(
                    "t5",
                    "write_invoice",
                    {
                        "order_id": order_id,
                        "subtotal_cents": subtotal,
                        "total_cents": total,
                        "coupon": coupon_block,
                    },
                )
            ],
            stop_reason="tool_use",
        )

    if "update_order_status" not in called:
        # The slip: the invoice is written and the order row is never updated.
        # A plausible, narrow failure — the shape real agents actually produce.
        if slip_every and trial_index % slip_every == slip_every - 1:
            return _message(
                [{"type": "text", "text": f"Invoice for order {order_id} written."}],
                stop_reason="end_turn",
            )
        return _message(
            [
                _tool_use(
                    "t6",
                    "update_order_status",
                    {"order_id": order_id, "status": "invoiced", "total_cents": total},
                )
            ],
            stop_reason="tool_use",
        )

    return _message(
        [{"type": "text", "text": f"Order {order_id} invoiced at {total} cents."}],
        stop_reason="end_turn",
    )


async def messages_endpoint(request: Request) -> Response:
    body = json.loads(await request.body())
    trial_index = int(request.headers.get("x-meridian-trial", "0"))
    slip_every = int(os.environ.get("MERIDIAN_STUB_SLIP_EVERY", DEFAULT_SLIP_EVERY))
    return JSONResponse(
        decide(
            str(body.get("system", "")),
            list(body.get("messages", [])),
            trial_index=trial_index,
            slip_every=slip_every,
        )
    )


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True, "provider": "meridian-stub"})


def create_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/v1/messages", messages_endpoint, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


def main() -> None:  # pragma: no cover - the container's entry point
    import uvicorn

    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ.get("MERIDIAN_STUB_PORT", "9000")),
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
