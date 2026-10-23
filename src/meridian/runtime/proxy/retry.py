"""Retry policy for transient provider failures, kept pure so it is testable.

Retrying here is not retrying an agent failure: the agent never sees the rate
limit and its request is unchanged. Without it, a provider's 429 is scored as the
agent failing.

Design notes, from reading how Inspect and Harbor handle the same problem:

- A malformed or hostile `Retry-After` must never crash the retry loop, so the
  header is parsed defensively and NaN, infinity and negatives are rejected.
- Backoff has jitter, otherwise N parallel trials that hit a limit together retry
  together and hit it again.
- The number of attempts is bounded. Unbounded retry turns an outage into a hang.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

# 408 request timeout, 429 rate limit, 529 provider overloaded, and every 5xx.
RETRYABLE_EXTRA_STATUSES = frozenset({408, 429, 529})

MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 90.0
# A server-provided wait gets up to this much extra, so retries spread out.
RETRY_AFTER_JITTER = 0.1

# Connection resets, DNS failures, read/write/connect timeouts, protocol errors.
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (httpx.TransportError,)


def is_retryable_status(status: int) -> bool:
    return status in RETRYABLE_EXTRA_STATUSES or 500 <= status <= 599


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait, or None if the header is absent or unusable.

    Accepts delta-seconds (`"3"`, `"2.5"`) and an HTTP-date. Anything non-finite or
    negative is rejected rather than clamped: a value that cannot be trusted is not
    a hint worth following.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def backoff_seconds(
    attempt: int,
    retry_after: float | None = None,
    *,
    rng: Callable[[], float],
) -> float:
    """How long to wait before attempt `attempt + 1` (attempt starts at 0).

    With a server hint, wait at least that long plus a little jitter. Without one,
    use full-jitter exponential backoff.
    """
    if retry_after is not None:
        wait = retry_after * (1.0 + RETRY_AFTER_JITTER * rng())
    else:
        ceiling = BASE_BACKOFF_SECONDS * (2.0**attempt)
        wait = ceiling * rng()
    return min(max(wait, 0.0), MAX_BACKOFF_SECONDS)


def retry_after_from(headers: Mapping[str, str]) -> float | None:
    return parse_retry_after(headers.get("retry-after"))
