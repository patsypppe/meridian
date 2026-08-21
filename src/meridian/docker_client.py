"""One place that talks to the Docker daemon.

Every failure to reach the daemon is a **harness error**, never a product
verdict. Keeping the construction here means that distinction is made once
rather than re-derived at each call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient


class DockerUnavailableError(RuntimeError):
    """The daemon could not be reached. Retryable once, then a harness error."""


def get_client() -> DockerClient:
    """Return a Docker client, or raise a harness error explaining what to do."""
    import docker

    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        raise DockerUnavailableError(
            "cannot reach the Docker daemon. Meridian runs every trial in its own "
            "container, so there is no degraded mode to fall back to — start Docker "
            "and retry."
        ) from exc
    return client
