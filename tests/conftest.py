"""Shared fixtures.

The leak check is autouse and repo-wide on purpose: a container that outlives its
trial keeps consuming resources and tokens, and it is far cheaper to catch the
leak on the day it is introduced than to find it later by wondering why the
machine is slow.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_SUITE = REPO_ROOT / "suites" / "checkout-agent"
CHECKOUT_ENV = REPO_ROOT / "envs" / "checkout"

RUN_LABEL = "meridian.run"


def docker_available() -> bool:
    try:
        from meridian.docker_client import get_client

        get_client()
    except Exception:
        return False
    return True


requires_docker = pytest.mark.skipif(
    not docker_available(),
    reason="the Docker daemon is not reachable; integration tests need it",
)


@pytest.fixture(scope="session")
def docker_client() -> Any:
    from meridian.docker_client import get_client

    return get_client()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def _labelled_resources() -> tuple[list[Any], list[Any]]:
    """Every container and volume carrying a Meridian run label."""
    try:
        from meridian.docker_client import get_client

        client = get_client()
    except Exception:
        return [], []
    containers: list[Any] = client.containers.list(all=True, filters={"label": RUN_LABEL})
    volumes: list[Any] = client.volumes.list(filters={"label": RUN_LABEL})
    return containers, volumes


@pytest.fixture(autouse=True)
def no_leaked_trial_containers(request: pytest.FixtureRequest) -> Iterator[None]:
    """Assert no container carrying a run label survives the test.

    Only meaningful for Docker-backed tests, so it is a cheap no-op elsewhere.
    """
    is_docker_test = any(
        request.node.get_closest_marker(marker) for marker in ("integration", "e2e")
    )
    if not is_docker_test:
        yield
        return

    before_containers, before_volumes = _labelled_resources()
    seen_containers = {c.id for c in before_containers}
    seen_volumes = {v.name for v in before_volumes}

    yield

    after_containers, after_volumes = _labelled_resources()
    leaked_containers = [c for c in after_containers if c.id not in seen_containers]
    leaked_volumes = [v for v in after_volumes if v.name not in seen_volumes]
    if not leaked_containers and not leaked_volumes:
        return

    described = [f"container {c.name}({c.status})" for c in leaked_containers]
    described += [f"volume {v.name}" for v in leaked_volumes]
    for container in leaked_containers:
        with contextlib.suppress(Exception):
            container.remove(force=True, v=True)
    for volume in leaked_volumes:
        with contextlib.suppress(Exception):
            volume.remove(force=True)
    pytest.fail(f"{len(described)} resource(s) leaked: {', '.join(described)}")
