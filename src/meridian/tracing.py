"""OpenTelemetry spans for runs and trials (MD-PRD-001 §10.1).

Off by default, and when off it costs one attribute lookup. Two properties are
deliberate and both are about trust:

* An export failure never fails a trial. An observability feature that can fail
  a run is worse than none, so every call into the SDK is wrapped and a broken
  collector is a missing span, nothing more.
* Spans live outside the manifest. Durations and timestamps are floats and
  nanoseconds, and nothing here is read back by anything hashed (Rule 4).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

    from meridian.config import ObservabilityConfig

__all__ = ["configure", "reset", "span"]

_provider: TracerProvider | None = None


def configure(
    config: ObservabilityConfig, *, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Enable tracing if the config asks for it. Returns the provider, or None.

    `exporter` exists for tests, which hand in an in-memory one. Otherwise spans
    go to `config.endpoint` over OTLP/HTTP when that exporter is installed.
    """
    global _provider
    if not config.enabled:
        return None
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": config.service_name}))
        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        elif config.endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found,unused-ignore]
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=config.endpoint))
            )
        else:
            return None
    except Exception:
        # No exporter installed, or the SDK is unhappy: run untraced.
        return None
    _provider = provider
    return provider


def reset() -> None:
    """Return to the no-op state. Tests use this; production never needs to."""
    global _provider
    _provider = None


class _Span:
    """A span handle whose every method is safe to call, traced or not."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def set(self, **attributes: str | int | bool) -> None:
        if self._inner is None:
            return
        with contextlib.suppress(Exception):
            for key, value in attributes.items():
                self._inner.set_attribute(key, value)


@contextlib.contextmanager
def span(name: str, **attributes: str | int | bool) -> Iterator[_Span]:
    """Open a span named `name`. A no-op unless `configure` enabled tracing."""
    provider = _provider
    if provider is None:
        yield _Span(None)
        return

    manager: Any = None
    inner: Any = None
    with contextlib.suppress(Exception):
        manager = provider.get_tracer("meridian").start_as_current_span(name)
        inner = manager.__enter__()
    handle = _Span(inner)
    handle.set(**attributes)
    try:
        yield handle
    finally:
        if manager is not None:
            with contextlib.suppress(Exception):
                manager.__exit__(None, None, None)
