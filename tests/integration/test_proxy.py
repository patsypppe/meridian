"""The proxy, in place: topology, credentials, and replay.

These tests use the real container topology because that is where the security
property lives. A unit test can show the trial's environment dict is empty; only
this can show the credential is in the proxy and the trial still cannot reach the
internet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from meridian.config import ProxyMode
from meridian.grading.pipeline import grade_task
from meridian.models.run import Outcome, TrialSpec
from meridian.models.suite import Suite
from meridian.models.task import FileExists, TaskDefinition
from meridian.runtime.proxy.cassette import CassetteStore, request_key
from meridian.runtime.proxy.session import start_proxy, stop_proxy
from meridian.runtime.secrets import assert_absent_from
from meridian.runtime.trial_runner import TrialRunner
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE, REPO_ROOT, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

FIXTURES_ROOT = REPO_ROOT / "fixtures"
FAKE_KEY = "sk-ant-fake-key-for-topology-tests-only"


@pytest.fixture(scope="module")
def suite() -> Suite:
    return load_suite(CHECKOUT_SUITE)


@pytest.fixture
def proxy(docker_client: Any, suite: Suite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A running proxy in record mode, so it holds a credential."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    handle = start_proxy(
        docker_client,
        run_id="proxytest",
        mode=ProxyMode.RECORD,
        cassette_dir=tmp_path / "cassettes",
        tasks=list(suite.tasks),
        upstream_base_url="http://127.0.0.1:9",  # never reached; these tests do not call upstream
    )
    try:
        yield handle
    finally:
        stop_proxy(handle, client=docker_client)


def probe_task(base: TaskDefinition, target: str, marker: str) -> TaskDefinition:
    return base.model_copy(
        update={
            "slug": f"proxy-{marker}",
            "adapter": f"subprocess:probes:{target}",
            "environment": base.environment.model_copy(update={"network": "proxy-only"}),
            "outcome_assertions": (
                FileExists(kind="file_exists", path=f"/work/out/{marker}", should_exist=True),
            ),
        }
    )


async def run_probe(
    docker_client: Any,
    suite: Suite,
    task: TaskDefinition,
    proxy: Any,
    *,
    capture: dict[str, Any] | None = None,
) -> Any:
    def grader(t: TaskDefinition, state_dir: Path, result: Any) -> Any:
        if capture is not None:
            capture["state_dir"] = state_dir
            env_file = state_dir / "out" / "env.txt"
            capture["env"] = env_file.read_text().splitlines() if env_file.is_file() else []
        return grade_task(t, state_dir, result)

    runner = TrialRunner(
        docker_client,
        suite_root=suite.root,
        sut_root=FIXTURES_ROOT,
        grader=grader,
        network_name=proxy.network_name,
    )
    spec = TrialSpec(
        run_id="proxytest",
        task_slug=task.slug,
        trial_index=0,
        seed=0,
        adapter_spec=task.adapter or "subprocess:probes:noop",
        proxy_base_url=proxy.base_url,
    )
    return await runner.run_trial(task, spec)


async def test_no_credentials_in_container_env(
    docker_client: Any, suite: Suite, proxy: Any
) -> None:
    """The proxy holds the key. The trial's environment does not contain it."""
    captured: dict[str, Any] = {}
    task = probe_task(suite.task("contamination-probe"), "dump_env", "env.txt")
    result = await run_probe(docker_client, suite, task, proxy, capture=captured)

    assert result.outcome is Outcome.PASS, result.detail
    assert captured["env"], "the probe wrote no environment at all"
    assert_absent_from(captured["env"], context="the trial container")
    assert not any(FAKE_KEY in line for line in captured["env"])

    # And the credential really is in the proxy — otherwise the test above passes
    # trivially against a proxy that has no key either.
    proxy_env = proxy.container.attrs["Config"]["Env"]
    assert any(entry.startswith("ANTHROPIC_API_KEY=") for entry in proxy_env)


async def test_the_trial_can_reach_the_proxy(docker_client: Any, suite: Suite, proxy: Any) -> None:
    task = probe_task(suite.task("contamination-probe"), "reach_proxy", "proxy-reachable")
    result = await run_probe(docker_client, suite, task, proxy)
    assert result.outcome is Outcome.PASS, result.detail


async def test_the_trial_cannot_reach_the_internet(
    docker_client: Any, suite: Suite, proxy: Any
) -> None:
    """No route, rather than a blocked route."""
    task = probe_task(suite.task("contamination-probe"), "reach_internet", "internet-unreachable")
    result = await run_probe(docker_client, suite, task, proxy)
    assert result.outcome is Outcome.PASS, result.detail


async def test_the_trial_network_is_internal(docker_client: Any, proxy: Any) -> None:
    network = docker_client.networks.get(proxy.network_name)
    assert network.attrs["Internal"] is True


async def test_the_proxy_reports_usage_to_the_harness(proxy: Any) -> None:
    """The proxy bridges two networks so the harness can read the ledger."""
    assert proxy.usage()["run_cents"] == 0
    assert httpx.get(f"{proxy.host_url}/healthz", timeout=5).json()["mode"] == "record"


async def test_replay_mode_needs_no_credential(
    docker_client: Any, suite: Suite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI runs offline. A proxy that demanded a key in replay would break that."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    cassettes = tmp_path / "cassettes"
    store = CassetteStore(cassettes)
    cassette = store.get("happy-path")
    body = {"model": "claude-sonnet-5", "max_tokens": 8, "messages": []}
    cassette.record(
        0,
        key=request_key(body),
        response={
            "type": "message",
            "content": [],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
        input_tokens=1,
        output_tokens=1,
    )
    store.save(cassette)

    handle = start_proxy(
        docker_client,
        run_id="proxyreplay",
        mode=ProxyMode.REPLAY,
        cassette_dir=cassettes,
        tasks=list(suite.tasks),
    )
    try:
        assert not any(
            entry.startswith(("ANTHROPIC_API_KEY=", "OPENAI_API_KEY="))
            for entry in handle.container.attrs["Config"]["Env"]
        )
        response = httpx.post(
            f"{handle.host_url}/v1/messages",
            json=body,
            headers={"x-meridian-task": "happy-path", "x-meridian-trial": "0"},
            timeout=10,
        )
        assert response.status_code == 200

        miss = httpx.post(
            f"{handle.host_url}/v1/messages",
            json=body | {"max_tokens": 9999},
            headers={"x-meridian-task": "happy-path", "x-meridian-trial": "0"},
            timeout=10,
        )
        assert miss.status_code == 424, "replay must fail closed rather than call upstream"
    finally:
        stop_proxy(handle, client=docker_client)


async def test_replay_mounts_cassettes_read_only(
    docker_client: Any, suite: Suite, tmp_path: Path
) -> None:
    """A replaying run must not rewrite the recording it is judged against."""
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    handle = start_proxy(
        docker_client,
        run_id="proxyro",
        mode=ProxyMode.REPLAY,
        cassette_dir=cassettes,
        tasks=list(suite.tasks),
    )
    try:
        mounts = handle.container.attrs["Mounts"]
        cassette_mount = next(m for m in mounts if m["Destination"] == "/cassettes")
        assert cassette_mount["RW"] is False
    finally:
        stop_proxy(handle, client=docker_client)


async def test_limits_come_from_the_task_not_the_agent(suite: Suite, proxy: Any) -> None:
    """A ceiling the thing being measured can raise is not a ceiling."""
    env = {
        e.split("=", 1)[0]: e.split("=", 1)[1]
        for e in proxy.container.attrs["Config"]["Env"]
        if "=" in e
    }
    limits = json.loads(env["MERIDIAN_LIMITS"])
    assert limits["expired-coupon"]["max_tokens"] == suite.task("expired-coupon").limits.max_tokens
    assert (
        limits["expired-coupon"]["budget_cents"] == suite.task("expired-coupon").limits.budget_cents
    )


async def test_an_unreachable_proxy_is_a_harness_error_not_an_agent_failure(
    docker_client: Any, suite: Suite, proxy: Any
) -> None:
    """§11.4 — proxy unreachable.

    This is the misclassification that costs the most. The agent cannot reach the
    model, so it fails every trial of every task, and the gate reports the largest
    regression it has ever seen. A developer reverts a change that was never the
    cause, the revert does not help, and the harness has spent their afternoon.

    An agent that cannot reach the model has not been evaluated at all, so the
    trial is a harness error and the run says it executed nothing rather than
    reporting a regression it did not measure.
    """
    task = suite.task("happy-path")
    runner = TrialRunner(
        docker_client,
        suite_root=suite.root,
        sut_root=FIXTURES_ROOT,
        grader=grade_task,
        network_name=proxy.network_name,
    )
    spec = TrialSpec(
        run_id="proxytest",
        task_slug=task.slug,
        trial_index=0,
        seed=0,
        adapter_spec=task.adapter or suite.adapter_spec,
        # Port 9 (discard) inside the container: nothing is listening, so every
        # model call is refused rather than merely slow.
        proxy_base_url="http://127.0.0.1:9",
    )
    result = await runner.run_trial(task, spec)

    assert result.outcome is Outcome.HARNESS_ERROR, (
        f"a trial that could never reach the model was reported as "
        f"{result.outcome} — this is a harness outage being scored as a regression"
    )
    assert "proxy" in result.detail.lower()
