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
) -> ProxyHandle:
    """Start the proxy and the internal trial network for one run."""
    network_name = f"meridian-trial-{run_id}"
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

    if mode is not ProxyMode.REPLAY:
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
    try:
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
        container.reload()

        host_url = _published_url(container)
        _await_health(host_url, container)
    except Exception as exc:
        stop_proxy(ProxyHandle("", network_name, container, ""), client=client)
        raise ProxyStartError(f"starting the proxy failed: {type(exc).__name__}: {exc}") from exc

    return ProxyHandle(
        base_url=f"http://{PROXY_ALIAS}:{PROXY_PORT}",
        network_name=network_name,
        container=container,
        host_url=host_url,
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


def stop_proxy(handle: ProxyHandle | None, *, client: DockerClient) -> None:
    """Tear down the proxy and its network, unconditionally and quietly."""
    if handle is None:
        return
    if handle.container is not None:
        with contextlib.suppress(Exception):
            handle.container.remove(force=True, v=True)
    with contextlib.suppress(Exception):
        client.networks.get(handle.network_name).remove()
