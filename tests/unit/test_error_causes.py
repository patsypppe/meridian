"""Error causes and provider-fault attribution."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from meridian.config import ProxyMode
from meridian.models.run import (
    ErrorCause,
    Outcome,
    RunResult,
    RunStatus,
    TaskResult,
    TrialResult,
)
from meridian.runtime.proxy import retry
from meridian.runtime.proxy import server as server_module
from meridian.runtime.proxy.budget import BudgetLedger
from meridian.runtime.proxy.server import ProxyConfig, create_app
from meridian.runtime.trial_runner import UpstreamState, attribute_provider_fault

pytestmark = pytest.mark.unit


def trial(outcome: Outcome, cause: ErrorCause | None = None, detail: str = "") -> TrialResult:
    return TrialResult(
        run_id="r",
        task_slug="happy-path",
        trial_index=0,
        seed=1,
        outcome=outcome,
        detail=detail,
        error_cause=cause,
    )


# -- the invariant: a cause exactly when it is a harness error ---------------------


def test_harness_error_requires_a_cause() -> None:
    with pytest.raises(ValidationError, match="error_cause"):
        trial(Outcome.HARNESS_ERROR)


@pytest.mark.parametrize("outcome", [Outcome.PASS, Outcome.FAIL, Outcome.TIMEOUT])
def test_agent_outcomes_forbid_a_cause(outcome: Outcome) -> None:
    with pytest.raises(ValidationError, match="error_cause"):
        trial(outcome, ErrorCause.INTERNAL)


# -- attribution: narrow on purpose ------------------------------------------------

BEFORE = UpstreamState(failures=0)


def test_a_failed_trial_whose_last_call_failed_is_a_provider_fault() -> None:
    after = UpstreamState(failures=2, last_failed=True, kind="provider")
    out = attribute_provider_fault(trial(Outcome.FAIL, detail="agent error"), BEFORE, after)
    assert out.outcome is Outcome.HARNESS_ERROR
    assert out.error_cause is ErrorCause.MODEL_PROVIDER
    assert "original verdict fail" in out.detail


def test_a_timeout_during_provider_failure_is_a_provider_fault() -> None:
    after = UpstreamState(failures=1, last_failed=True, kind="provider")
    assert attribute_provider_fault(trial(Outcome.TIMEOUT), BEFORE, after).error_cause is (
        ErrorCause.MODEL_PROVIDER
    )


def test_a_proxy_side_failure_is_internal_not_provider() -> None:
    after = UpstreamState(failures=1, last_failed=True, kind="proxy")
    assert attribute_provider_fault(trial(Outcome.FAIL), BEFORE, after).error_cause is (
        ErrorCause.INTERNAL
    )


def test_a_pass_always_stands() -> None:
    after = UpstreamState(failures=5, last_failed=True, kind="provider")
    original = trial(Outcome.PASS)
    assert attribute_provider_fault(original, BEFORE, after) is original


def test_an_agent_that_recovered_and_then_failed_keeps_its_fail() -> None:
    """The provider hiccuped, the agent carried on, then failed on its merits."""
    after = UpstreamState(failures=1, last_failed=False, kind="provider")
    original = trial(Outcome.FAIL)
    assert attribute_provider_fault(original, BEFORE, after) is original


def test_failures_from_an_earlier_attempt_do_not_count() -> None:
    """A retry is judged on its own attempt: the counter must have risen."""
    state = UpstreamState(failures=3, last_failed=True, kind="provider")
    original = trial(Outcome.FAIL)
    assert attribute_provider_fault(original, state, state) is original


def test_unknown_upstream_state_never_reclassifies() -> None:
    after = UpstreamState(failures=1, last_failed=True, kind="provider")
    original = trial(Outcome.FAIL)
    assert attribute_provider_fault(original, None, after) is original


# -- classification of the upstream exception --------------------------------------


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://x")
    return httpx.HTTPStatusError("e", request=request, response=httpx.Response(code))


@pytest.mark.parametrize("code", [401, 403, 404, 429, 500, 502, 503, 529])
def test_provider_status_failures_are_provider_faults(code: int) -> None:
    assert retry.classify_upstream_failure(_status_error(code)) == "provider"


@pytest.mark.parametrize("code", [400, 413, 422])
def test_rejected_requests_are_the_agents_not_the_providers(code: int) -> None:
    assert retry.classify_upstream_failure(_status_error(code)) is None


def test_transport_errors_are_provider_faults_and_bugs_are_proxy_faults() -> None:
    assert retry.classify_upstream_failure(httpx.ConnectError("x")) == "provider"
    assert retry.classify_upstream_failure(KeyError("boom")) == "proxy"


# -- the proxy keeps the per-trial record ------------------------------------------


def test_ledger_tracks_upstream_health_per_trial() -> None:
    ledger = BudgetLedger()
    ledger.record_upstream_failure("happy-path", 0, "provider")
    ledger.record_upstream_failure("happy-path", 0, "provider")
    ledger.usage_for("happy-path", 0)
    entry = ledger.snapshot()["trials"]["happy-path:0"]  # type: ignore[index]
    assert entry["upstream_failures"] == 2 and entry["last_upstream_failed"] is True
    ledger.record_upstream_success("happy-path", 0)
    entry = ledger.snapshot()["trials"]["happy-path:0"]  # type: ignore[index]
    assert entry["upstream_failures"] == 2 and entry["last_upstream_failed"] is False


async def test_proxy_records_an_exhausted_upstream_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def always_down(*_: object, **__: object) -> dict[str, object]:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(server_module, "_call_upstream", always_down)
    app = create_app(ProxyConfig(mode=ProxyMode.RECORD, cassette_dir=tmp_path, limits={}))
    headers = {"x-meridian-task": "happy-path", "x-meridian-trial": "0"}
    body = {"model": "m", "max_tokens": 8, "messages": [{"role": "user", "content": "x"}]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy"
    ) as client:
        assert (await client.post("/v1/messages", json=body, headers=headers)).status_code == 502
        usage = (await client.get("/v1/usage")).json()
    ledger = app.state.proxy.ledger
    health = ledger.upstream["happy-path:0"]
    assert (health.failures, health.last_failed, health.last_kind) == (1, True, "provider")
    assert "usage" in usage or "trials" in usage


# -- reporting ---------------------------------------------------------------------


def _run(*trials: TrialResult) -> RunResult:
    return RunResult(
        run_id="r",
        suite_slug="s",
        suite_version=1,
        status=RunStatus.COMPLETE,
        k=1,
        n_requested=len(trials),
        tasks=(TaskResult(task_slug="happy-path", trials=trials),),
    )


def test_summary_breaks_harness_errors_down_by_cause() -> None:
    run = _run(
        trial(Outcome.PASS),
        trial(Outcome.HARNESS_ERROR, ErrorCause.MODEL_PROVIDER),
        trial(Outcome.HARNESS_ERROR, ErrorCause.MODEL_PROVIDER),
        trial(Outcome.HARNESS_ERROR, ErrorCause.GRADER),
    )
    assert run.harness_errors_by_cause == {"model_provider": 2, "grader": 1}
    assert run.harness_error_summary == "75.0% (model_provider: 2, grader: 1)"


def test_summary_without_errors_is_just_the_rate() -> None:
    assert _run(trial(Outcome.PASS)).harness_error_summary == "0.0%"
