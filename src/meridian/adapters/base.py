"""Adapter spec strings: `kind:module:attribute`.

`langgraph:checkout_agent.agent:graph` says *which adapter* to use, *which
module* holds the system under test, and *which attribute* of it to invoke. The
harness only ever parses this; it resolves it inside the container.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

KNOWN_KINDS = ("langgraph", "subprocess")


class AdapterSpecError(ValueError):
    """A malformed or unknown adapter spec. A configuration error, not a verdict."""


@dataclass(frozen=True)
class AdapterSpec:
    """A parsed `kind:module:attribute` triple."""

    kind: str
    module: str
    attribute: str

    @classmethod
    def parse(cls, raw: str) -> AdapterSpec:
        parts = raw.split(":")
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise AdapterSpecError(
                f"adapter spec {raw!r} must be 'kind:module:attribute', "
                f"e.g. 'langgraph:checkout_agent.agent:graph'"
            )
        kind, module, attribute = (part.strip() for part in parts)
        if kind not in KNOWN_KINDS:
            raise AdapterSpecError(
                f"unknown adapter kind {kind!r}; Meridian ships {', '.join(KNOWN_KINDS)}"
            )
        return cls(kind=kind, module=module, attribute=attribute)

    def __str__(self) -> str:
        return f"{self.kind}:{self.module}:{self.attribute}"

    def resolve_target(self) -> Any:
        """Import the module and return the attribute. Container-side only."""
        try:
            module = importlib.import_module(self.module)
        except ImportError as exc:
            raise AdapterSpecError(
                f"cannot import {self.module!r} for adapter spec {self}: {exc}"
            ) from exc
        try:
            return getattr(module, self.attribute)
        except AttributeError as exc:
            raise AdapterSpecError(
                f"module {self.module!r} has no attribute {self.attribute!r}"
            ) from exc
