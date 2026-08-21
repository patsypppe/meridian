"""Snapshot management — MD-FR-08.

The property under test is not "docker build works". It is that an environment
resolves to a *digest*, that the digest is stable for an unchanged Dockerfile,
and that a tag is refused everywhere a pin is required.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from meridian.snapshots.build import build_snapshot, rewrite_suite_snapshots
from meridian.snapshots.registry import (
    is_digest,
    read_snapshot_ref,
    resolve_digest,
)
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_ENV, CHECKOUT_SUITE, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


def test_build_is_reproducible(docker_client: object, tmp_path: Path) -> None:
    """Two builds of an unchanged Dockerfile pin the same digest."""
    first = build_snapshot(docker_client, CHECKOUT_ENV, tag="checkout:test")  # type: ignore[arg-type]
    second = build_snapshot(docker_client, CHECKOUT_ENV, tag="checkout:test")  # type: ignore[arg-type]
    assert is_digest(first)
    assert first == second


def test_resolve_digest_maps_a_tag_to_a_digest(docker_client: object) -> None:
    build_snapshot(docker_client, CHECKOUT_ENV, tag="checkout:test")  # type: ignore[arg-type]
    resolved = resolve_digest(docker_client, "checkout:test")  # type: ignore[arg-type]
    assert is_digest(resolved)


def test_a_digest_resolves_to_itself(docker_client: object) -> None:
    digest = build_snapshot(docker_client, CHECKOUT_ENV, tag="checkout:test")  # type: ignore[arg-type]
    # Resolving a pin must never silently upgrade it.
    assert resolve_digest(docker_client, digest) == digest  # type: ignore[arg-type]


def test_write_ref_records_the_digest(docker_client: object, tmp_path: Path) -> None:
    env_copy = tmp_path / "checkout"
    shutil.copytree(CHECKOUT_ENV, env_copy)
    digest = build_snapshot(
        docker_client,  # type: ignore[arg-type]
        env_copy,
        tag="checkout:reftest",
        write_ref=True,
    )
    assert read_snapshot_ref(env_copy) == digest


def test_suite_repinning_is_a_line_rewrite(docker_client: object, tmp_path: Path) -> None:
    """Repinning must not reorder keys or drop comments."""
    suite_copy = tmp_path / "checkout-agent"
    shutil.copytree(CHECKOUT_SUITE, suite_copy)
    original = (suite_copy / "tasks" / "expired-coupon.yaml").read_text()

    fake = "sha256:" + "ab" * 32
    changed = rewrite_suite_snapshots(suite_copy, fake)

    assert len(changed) == 5
    rewritten = (suite_copy / "tasks" / "expired-coupon.yaml").read_text()
    assert fake in rewritten
    assert rewritten.splitlines()[0] == original.splitlines()[0]
    assert len(rewritten.splitlines()) == len(original.splitlines())
    assert load_suite(suite_copy).task("expired-coupon").environment.snapshot == fake
