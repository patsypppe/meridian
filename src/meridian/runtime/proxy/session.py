"""Bringing the proxy and the trial network up and down around a run.

The topology is the security control, so it is worth stating plainly:

    trial container ──▶ meridian-trial-<run>  (internal: true, no route out)
                                  │
                              proxy container ──▶ upstream provider
                                  │
                          published port ──▶ the harness, for usage

The proxy is attached to **two** networks and the trial to **one**. The trial has
no route to the internet — not a blocked route, no route — while the proxy has
the egress it needs to record. Nothing about that depends on a filter being
configured correctly.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from meridian.config import ProxyMode
from meridian.models.task import TaskDefinition
from meridian.runtime.isolation import RUN_LABEL
from meridian.runtime.secrets import has_credential, load_credential

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient

PROXY_ALIAS = "proxy"
PROXY_PORT = 8080
PROXY_IMAGE_TAG = "meridian-proxy:dev"

STUB_PORT = 9000


def stub_container_name(run_id: str) -> str:
    return f"meridian-provider-{run_id}"


def stub_upstream_url(run_id: str) -> str:
    """Addressed by container name rather than a network alias.

    `containers.create` takes no alias argument, and Docker's embedded DNS
    resolves container names on a user-defined network anyway — so a name is one
    fewer moving part than creating the container and re-attaching it.
    """
    return f"http://{stub_container_name(run_id)}:{STUB_PORT}"


HEALTH_TIMEOUT_SECONDS = 30.0
HEALTH_POLL_SECONDS = 0.2


class ProxyStartError(RuntimeError):
    """The proxy could not be started. A harness error — no degraded mode."""


@dataclass
class ProxyHandle:
    """A running proxy and the network its trials share with it."""

    base_url: str
    network_name: str
    container: Any
    host_url: str
    upstream_network_name: str | None = None
    upstream_container: Any = None

    def usage(self) -> dict[str, Any]:
        response = httpx.get(f"{self.host_url}/v1/usage", timeout=10.0)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload

    def run_cents(self) -> int:
        return int(self.usage().get("run_cents", 0))


def limits_payload(tasks: list[TaskDefinition]) -> str:
    """Per-task ceilings, taken from the task definitions.

    Serialized here and handed to the proxy at start, because a ceiling the agent
    could raise is not a ceiling.
    """
    return json.dumps(
        {
            task.slug: {
                "max_tokens": task.limits.max_tokens,
                "budget_cents": task.limits.budget_cents,
            }
            for task in tasks
        },
        sort_keys=True,
    )


def start_stub_provider(
    client: DockerClient,
    *,
    run_id: str,
    image: str = PROXY_IMAGE_TAG,
    slip_every: int | None = None,
) -> tuple[Any, str]:
    """Start the deterministic stand-in provider on its own private network.

    It gets a network the *trial* is not on, so the agent cannot reach it
    directly and bypass the proxy's ledger and recording. The stub is a fixture,
    not a threat — but a bypass that exists is a bypass someone eventually takes.
    """
    network_name = f"meridian-upstream-{run_id}"
    environment = {"MERIDIAN_STUB_PORT": str(STUB_PORT)}
    if slip_every is not None:
        environment["MERIDIAN_STUB_SLIP_EVERY"] = str(slip_every)

    network = client.networks.create(
        network_name, driver="bridge", internal=True, labels={RUN_LABEL: run_id}
    )
    container = client.containers.create(
        image=image,
        name=stub_container_name(run_id),
        entrypoint=["python3", "-m", "meridian.runtime.proxy.stub_provider"],
        environment=environment,
        labels={RUN_LABEL: run_id, "meridian.role": "stub-provider"},
        detach=True,
        network=network_name,
    )
    container.start()
    network.reload()
    return container, network_name


def start_proxy(
    client: DockerClient,
    *,
    run_id: str,
    mode: ProxyMode,
    cassette_dir: Path,
    tasks: list[TaskDefinition],
    image: str = PROXY_IMAGE_TAG,
    run_budget_cents: int | None = None,
    upstream_base_url: str | None = None,
    use_stub_provider: bool = False,
    stub_slip_every: int | None = None,
) -> ProxyHandle:
    """Start the proxy and the internal trial network for one run."""
    network_name = f"meridian-trial-{run_id}"
    # Resolved before the environment is built: the proxy learns where upstream
    # is from its environment, so deciding afterwards would silently leave it
    # pointed at the real provider.
    if use_stub_provider:
        upstream_base_url = stub_upstream_url(run_id)
    cassette_dir = Path(cassette_dir).resolve()
    cassette_dir.mkdir(parents=True, exist_ok=True)

    environment: dict[str, str] = {
        "MERIDIAN_PROXY_MODE": str(mode),
        "MERIDIAN_CASSETTE_DIR": "/cassettes",
        "MERIDIAN_LIMITS": limits_payload(tasks),
    }
    if run_budget_cents is not None:
        environment["MERIDIAN_RUN_BUDGET_CENTS"] = str(run_budget_cents)
    if upstream_base_url:
        environment["MERIDIAN_UPSTREAM_BASE_URL"] = upstream_base_url

    if mode is not ProxyMode.REPLAY and not use_stub_provider:
        # The credential exists here and in no other container. Replay never
        # needs one, which is why CI runs offline and the demo works on a plane.
        if not has_credential():
            raise ProxyStartError(
                f"proxy mode {mode} calls the provider, but no credential is set. "
                f"Replay mode needs none."
            )
        credential = load_credential()
        environment[credential.env_var] = credential.value

    network = None
    container = None
    stub_container = None
    stub_network_name = None
    try:
        if use_stub_provider and mode is not ProxyMode.REPLAY:
            stub_container, stub_network_name = start_stub_provider(
                client, run_id=run_id, image=image, slip_every=stub_slip_every
            )
        network = client.networks.create(
            network_name,
            driver="bridge",
            # No route to anywhere. The trial's isolation from the internet is a
            # property of this flag, not of a filter.
            internal=True,
            labels={RUN_LABEL: run_id},
        )
        container = client.containers.create(
            image=image,
            name=f"meridian-proxy-{run_id}",
            environment=environment,
            labels={RUN_LABEL: run_id, "meridian.role": "proxy"},
            volumes={
                str(cassette_dir): {
                    "bind": "/cassettes",
                    # Read-only in replay: a replaying run must not be able to
                    # quietly rewrite the recording it is being judged against.
                    "mode": "ro" if mode is ProxyMode.REPLAY else "rw",
                }
            },
            ports={f"{PROXY_PORT}/tcp": None},
            detach=True,
        )
        container.start()
        network.connect(container, aliases=[PROXY_ALIAS])
        if stub_network_name is not None:
            client.networks.get(stub_network_name).connect(container)
        container.reload()

        host_url = _published_url(container)
        _await_health(host_url, container)
        if stub_container is not None:
            # Checked through the proxy, because the stub sits on an internal
            # network the host cannot reach. Skipping this turns "the upstream
            # never started" into "every trial failed", with nothing said about
            # why.
            _await_upstream(host_url, stub_container)
    except Exception as exc:
        stop_proxy(
            ProxyHandle("", network_name, container, "", stub_network_name, stub_container),
            client=client,
        )
        raise ProxyStartError(f"starting the proxy failed: {type(exc).__name__}: {exc}") from exc

    return ProxyHandle(
        base_url=f"http://{PROXY_ALIAS}:{PROXY_PORT}",
        network_name=network_name,
        container=container,
        host_url=host_url,
        upstream_network_name=stub_network_name,
        upstream_container=stub_container,
    )


def _published_url(container: Any) -> str:
    ports = container.attrs["NetworkSettings"]["Ports"] or {}
    bindings = ports.get(f"{PROXY_PORT}/tcp") or []
    if not bindings:
        raise ProxyStartError("the proxy published no host port; usage cannot be read")
    return f"http://127.0.0.1:{bindings[0]['HostPort']}"


def _await_health(host_url: str, container: Any) -> None:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{host_url}/healthz", timeout=2.0)
            if response.status_code == 200:
                return
        except Exception as exc:
            last = exc
        time.sleep(HEALTH_POLL_SECONDS)
    logs = container.logs(tail=40).decode("utf-8", errors="replace") if container else ""
    raise ProxyStartError(f"proxy never became healthy ({last}); logs:\n{logs}")


def _await_upstream(host_url: str, stub_container: Any) -> None:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{host_url}/healthz/upstream", timeout=5.0)
            if response.status_code == 200:
                return
            last = response.text[:300]
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(HEALTH_POLL_SECONDS)
    logs = stub_container.logs(tail=40).decode("utf-8", errors="replace")
    raise ProxyStartError(
        f"the proxy could not reach its upstream provider ({last}); provider logs:\n{logs}"
    )


def stop_proxy(handle: ProxyHandle | None, *, client: DockerClient) -> None:
    """Tear down the proxy and its network, unconditionally and quietly."""
    if handle is None:
        return
    for container in (handle.container, handle.upstream_container):
        if container is not None:
            with contextlib.suppress(Exception):
                container.remove(force=True, v=True)
    for network in (handle.network_name, handle.upstream_network_name):
        if network:
            with contextlib.suppress(Exception):
                client.networks.get(network).remove()
