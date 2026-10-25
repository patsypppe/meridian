"""Retry policy: pure functions plus the loop that uses them."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import httpx
import pytest

from meridian.runtime.proxy import retry
from meridian.runtime.proxy import server as server_module

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [("3", 3.0), ("2.5", 2.5), (" 0 ", 0.0), (None, None), ("", None), ("soon", None)],
)
def test_parse_retry_after_seconds(value: str | None, expected: float | None) -> None:
    assert retry.parse_retry_after(value) == expected


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "-5", "1e999"])
def test_parse_retry_after_rejects_untrustworthy_values(value: str) -> None:
    """A hostile header must never reach asyncio.sleep(nan) and crash the proxy."""
    assert retry.parse_retry_after(value) is None


def test_parse_retry_after_http_date() -> None:
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
    assert retry.parse_retry_after("Fri, 23 Oct 2026 12:00:30 GMT", now=now) == 30.0
    assert retry.parse_retry_after("Fri, 23 Oct 2026 11:59:00 GMT", now=now) is None


def test_backoff_with_server_hint_waits_at_least_the_hint() -> None:
    assert retry.backoff_seconds(0, 4.0, rng=lambda: 0.0) == 4.0
    assert 4.0 < retry.backoff_seconds(0, 4.0, rng=lambda: 1.0) <= 4.4 + 1e-9


def test_backoff_without_hint_is_full_jitter_and_bounded() -> None:
    assert retry.backoff_seconds(3, None, rng=lambda: 0.5) == 4.0  # 1 * 2**3 * 0.5
    assert retry.backoff_seconds(30, None, rng=lambda: 1.0) == retry.MAX_BACKOFF_SECONDS
    assert retry.backoff_seconds(0, 9999.0, rng=lambda: 0.0) == retry.MAX_BACKOFF_SECONDS


def test_backoff_never_returns_nan_or_negative() -> None:
    for attempt in range(10):
        value = retry.backoff_seconds(attempt, None, rng=lambda: 0.37)
        assert math.isfinite(value) and value >= 0


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
def test_retryable_statuses(status: int) -> None:
    assert retry.is_retryable_status(status)


@pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(status: int) -> None:
    assert not retry.is_retryable_status(status)


async def _no_sleep(_: float) -> None:
    return None


async def test_transport_errors_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(server_module.asyncio, "sleep", _no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await server_module._post_with_retries(client, "http://x/", {}, {})
    assert response.status_code == 200 and calls["n"] == 3


async def test_nan_retry_after_does_not_crash_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "nan"})
        return httpx.Response(200, json={})

    monkeypatch.setattr(server_module.asyncio, "sleep", _no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await server_module._post_with_retries(client, "http://x/", {}, {})
    assert response.status_code == 200


async def test_persistent_failure_is_bounded_and_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    monkeypatch.setattr(server_module.asyncio, "sleep", _no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await server_module._post_with_retries(client, "http://x/", {}, {})
    assert response.status_code == 503
    assert calls["n"] == retry.MAX_ATTEMPTS


async def test_persistent_transport_error_raises_after_the_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(server_module.asyncio, "sleep", _no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            await server_module._post_with_retries(client, "http://x/", {}, {})


async def test_client_errors_are_returned_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    monkeypatch.setattr(server_module.asyncio, "sleep", _no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await server_module._post_with_retries(client, "http://x/", {}, {})
    assert response.status_code == 404 and calls["n"] == 1


# -- accounting for time spent backing off ----------------------------------------------


def test_retry_stats_accumulate_waits() -> None:
    stats = retry.RetryStats()
    stats.record_wait(1.5)
    stats.record_wait(0.25)
    assert (stats.retries, stats.waited_ms) == (2, 1750)


async def test_waiting_is_recorded_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, json={})

    monkeypatch.setattr(server_module.asyncio, "sleep", _no_sleep)
    stats = retry.RetryStats()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await server_module._post_with_retries(client, "http://x/", {}, {}, stats)
    assert stats.retries == 2
    # Retry-After of 2s, plus up to 10% jitter, twice
    assert 4000 <= stats.waited_ms <= 4400


def test_the_ledger_reports_upstream_wait_per_trial() -> None:
    from meridian.runtime.proxy.budget import BudgetLedger

    ledger = BudgetLedger()
    ledger.usage_for("happy-path", 0)
    ledger.record_upstream_wait("happy-path", 0, retries=2, waited_ms=4000)
    ledger.record_upstream_wait("happy-path", 0, retries=1, waited_ms=1000)
    entry = ledger.snapshot()["trials"]["happy-path:0"]  # type: ignore[index]
    assert entry["upstream_retries"] == 3 and entry["upstream_wait_ms"] == 5000


async def test_waits_are_reported_before_the_sleep_so_a_killed_call_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trial killed mid-backoff must still have been told about the time it lost."""
    reported: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "30"})

    class Killed(Exception):
        pass

    async def killed_while_sleeping(_: float) -> None:
        raise Killed

    monkeypatch.setattr(server_module.asyncio, "sleep", killed_while_sleeping)
    stats = retry.RetryStats(on_wait=reported.append)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Killed):
            await server_module._post_with_retries(client, "http://x/", {}, {}, stats)
    assert len(reported) == 1 and reported[0] >= 30.0
