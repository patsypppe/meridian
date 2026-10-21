"""Spans are observability, never a dependency of the run."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from meridian import tracing
from meridian.config import MeridianConfig, ObservabilityConfig

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _untraced() -> Iterator[None]:
    tracing.reset()
    yield
    tracing.reset()


def test_disabled_tracing_is_a_no_op_that_still_runs_the_body() -> None:
    assert tracing.configure(ObservabilityConfig()) is None
    ran = False
    with tracing.span("meridian.trial", task_slug="t") as s:
        s.set(outcome="pass")
        ran = True
    assert ran


def test_enabled_tracing_records_names_and_attributes() -> None:
    exporter = InMemorySpanExporter()
    tracing.configure(ObservabilityConfig(enabled=True), exporter=exporter)

    with (
        tracing.span("meridian.run", run_id="run-1"),
        tracing.span("meridian.trial", task_slug="happy-path", trial_index=2) as trial,
    ):
        trial.set(outcome="pass", attempts=1)

    finished = {s.name: dict(s.attributes or {}) for s in exporter.get_finished_spans()}
    assert finished["meridian.run"] == {"run_id": "run-1"}
    assert finished["meridian.trial"] == {
        "task_slug": "happy-path",
        "trial_index": 2,
        "outcome": "pass",
        "attempts": 1,
    }


def test_a_trial_span_is_a_child_of_the_run_span() -> None:
    exporter = InMemorySpanExporter()
    tracing.configure(ObservabilityConfig(enabled=True), exporter=exporter)
    with tracing.span("meridian.run"), tracing.span("meridian.trial"):
        pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    parent = spans["meridian.trial"].parent
    assert parent is not None
    assert parent.span_id == spans["meridian.run"].context.span_id


def test_an_exporter_that_raises_never_fails_the_body() -> None:
    class Broken(InMemorySpanExporter):
        def export(self, spans):  # type: ignore[no-untyped-def,override]
            raise RuntimeError("collector is down")

    tracing.configure(ObservabilityConfig(enabled=True), exporter=Broken())
    with tracing.span("meridian.trial") as s:
        s.set(outcome="pass")
    assert SpanExportResult.SUCCESS is not None  # reached: the body survived


def test_an_exception_in_the_body_still_propagates() -> None:
    tracing.configure(ObservabilityConfig(enabled=True), exporter=InMemorySpanExporter())
    with pytest.raises(ValueError, match="agent bug"), tracing.span("meridian.trial"):
        raise ValueError("agent bug")


def test_observability_settings_never_change_the_config_hash() -> None:
    plain = MeridianConfig()
    traced = MeridianConfig(observability=ObservabilityConfig(enabled=True, endpoint="http://x"))
    assert plain.config_hash() == traced.config_hash()
