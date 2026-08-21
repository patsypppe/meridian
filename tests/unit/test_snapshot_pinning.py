"""Pinning rules — pure logic, no daemon required."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from meridian.snapshots.build import rewrite_suite_snapshots
from meridian.snapshots.registry import (
    UnpinnedReferenceError,
    is_digest,
    require_digest,
    write_snapshot_ref,
)
from meridian.suites.loader import load_suite
from meridian.suites.validate import SuiteValidationError
from tests.conftest import CHECKOUT_SUITE

DIGEST = "sha256:" + "0" * 64


@pytest.mark.unit
@pytest.mark.parametrize(
    "reference",
    ["checkout:latest", "checkout", "sha256:short", "sha512:" + "0" * 64, ""],
)
def test_tags_are_not_digests(reference: str) -> None:
    assert not is_digest(reference)
    with pytest.raises(UnpinnedReferenceError):
        require_digest(reference)


@pytest.mark.unit
def test_a_digest_passes_the_pin_check() -> None:
    assert require_digest(DIGEST) == DIGEST


@pytest.mark.unit
def test_the_loader_rejects_a_tagged_snapshot(tmp_path: Path) -> None:
    """The rule is enforced at load time, not only where the container starts."""
    suite = tmp_path / "checkout-agent"
    shutil.copytree(CHECKOUT_SUITE, suite)
    task = suite / "tasks" / "happy-path.yaml"
    pinned = load_suite(suite).task("happy-path").environment.snapshot
    task.write_text(task.read_text().replace(pinned, "checkout:latest"))

    with pytest.raises(SuiteValidationError) as excinfo:
        load_suite(suite)
    assert "unpinned_snapshot" in excinfo.value.codes()


@pytest.mark.unit
def test_snapshot_ref_refuses_to_record_a_tag(tmp_path: Path) -> None:
    with pytest.raises(UnpinnedReferenceError):
        write_snapshot_ref(tmp_path, digest="checkout:latest", tag="t", platform="linux/arm64")


@pytest.mark.unit
def test_repinning_refuses_a_tag(tmp_path: Path) -> None:
    suite = tmp_path / "checkout-agent"
    shutil.copytree(CHECKOUT_SUITE, suite)
    with pytest.raises(UnpinnedReferenceError):
        rewrite_suite_snapshots(suite, "checkout:latest")
