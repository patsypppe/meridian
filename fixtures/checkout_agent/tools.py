"""The checkout agent's tools, and the schemas the model is shown.

Tools do exactly what they say and nothing more. In particular none of them
applies the coupon policy — that judgement is the agent's, which is what makes
the policy something a prompt change can break.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read_order",
        "description": "Read an order file by id.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "read_policy",
        "description": "Read the coupon and invoicing policy document.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_coupon",
        "description": "Look up a coupon by code in the store database.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
    {
        "name": "write_invoice",
        "description": "Write an invoice for an order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "subtotal_cents": {"type": "integer"},
                "total_cents": {"type": "integer"},
                "coupon": {"type": "object"},
            },
            "required": ["order_id", "subtotal_cents", "total_cents", "coupon"],
        },
    },
    {
        "name": "update_order_status",
        "description": "Set an order's status and invoiced total.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "status": {"type": "string"},
                "total_cents": {"type": ["integer", "null"]},
            },
            "required": ["order_id", "status"],
        },
    },
    {
        "name": "write_refusal",
        "description": "Record that an order cannot be invoiced, and why.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["order_id", "reason"],
        },
    },
]


class Toolbox:
    """The tools, bound to one trial's workdir."""

    def __init__(self, workdir: str) -> None:
        self.workdir = Path(workdir)

    # -- reads -------------------------------------------------------------

    def read_order(self, order_id: int) -> dict[str, Any]:
        path = self.workdir / "orders" / f"order-{order_id}.json"
        if not path.is_file():
            return {"error": f"no order file for {order_id}"}
        parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return parsed

    def read_policy(self) -> dict[str, Any]:
        path = self.workdir / "policy.md"
        return {"policy": path.read_text(encoding="utf-8") if path.is_file() else ""}

    def query_coupon(self, code: str) -> dict[str, Any]:
        with sqlite3.connect(self.workdir / "store.db") as connection:
            row = connection.execute(
                "SELECT code, percent_off, expires_on FROM coupons WHERE code = ?", (code,)
            ).fetchone()
        if row is None:
            return {"found": False, "code": code}
        return {"found": True, "code": row[0], "percent_off": row[1], "expires_on": row[2]}

    # -- writes ------------------------------------------------------------

    def write_invoice(
        self,
        order_id: int,
        subtotal_cents: int,
        total_cents: int,
        coupon: dict[str, Any],
    ) -> dict[str, Any]:
        out = self.workdir / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"invoice-{order_id}.json").write_text(
            json.dumps(
                {
                    "order_id": order_id,
                    "subtotal_cents": subtotal_cents,
                    "total_cents": total_cents,
                    "coupon": coupon,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return {"ok": True}

    def update_order_status(
        self, order_id: int, status: str, total_cents: int | None = None
    ) -> dict[str, Any]:
        with sqlite3.connect(self.workdir / "store.db") as connection:
            connection.execute(
                "UPDATE orders SET status = ?, total_cents = ? WHERE id = ?",
                (status, total_cents, order_id),
            )
            connection.commit()
        return {"ok": True}

    def write_refusal(self, order_id: int, reason: str) -> dict[str, Any]:
        out = self.workdir / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"refusal-{order_id}.json").write_text(
            json.dumps({"order_id": order_id, "reason": reason}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return {"ok": True}

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, name, None)
        if handler is None or name not in {schema["name"] for schema in TOOL_SCHEMAS}:
            return {"error": f"unknown tool {name!r}"}
        return dict(handler(**arguments))
