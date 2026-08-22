"""Isolation options, asserted without a daemon.

`isolation.py` is the one file a reviewer reads to audit Rule 1, so its output is
pinned here rather than only exercised indirectly. If someone loosens an option
during a debugging session, this fails before the loosened option ships.
"""

from __future__ import annotations

import pytest

from meridian.runtime.isolation import (
    RUN_LABEL,
    TRIAL_LABEL,
    IsolationPolicy,
    container_kwargs,
)
from meridian.suites.loader import load_suite
from tests.conftest import CHECKOUT_SUITE

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def kwargs() -> dict[str, object]:
    task = load_suite(CHECKOUT_SUITE).task("expired-coupon")
    return container_kwargs(task, "run-1", 3, workdir_volume="vol-1")


def test_never_runs_as_root(kwargs: dict[str, object]) -> None:
    assert kwargs["user"] == "10001:10001"


def test_rootfs_is_read_only(kwargs: dict[str, object]) -> None:
    assert kwargs["read_only"] is True


def test_all_capabilities_are_dropped(kwargs: dict[str, object]) -> None:
    assert kwargs["cap_drop"] == ["ALL"]


def test_privilege_escalation_is_blocked(kwargs: dict[str, object]) -> None:
    assert kwargs["security_opt"] == ["no-new-privileges:true"]


def test_resource_ceilings_come_from_the_task(kwargs: dict[str, object]) -> None:
    assert kwargs["pids_limit"] == 256
    assert kwargs["mem_limit"] == "2048m"
    assert kwargs["nano_cpus"] == 2_000_000_000


def test_swap_is_disabled_so_the_memory_ceiling_means_what_it_says(
    kwargs: dict[str, object],
) -> None:
    """Docker defaults memory-swap to twice memory when only memory is set.

    Left at the default, a task asking for 2048MB quietly gets 4096MB of address
    space, and a trial that swaps runs slowly enough that one task's memory
    ceiling starts deciding another task's timeout.
    """
    assert kwargs["memswap_limit"] == kwargs["mem_limit"]


def test_no_secrets_are_passed_into_the_container(kwargs: dict[str, object]) -> None:
    """The container talks to the proxy; the proxy holds the credential."""
    environment = kwargs["environment"]
    assert isinstance(environment, dict)
    assert set(environment) == {"MERIDIAN_WORKDIR"}


def test_auto_remove_is_off_so_state_can_be_extracted(kwargs: dict[str, object]) -> None:
    assert kwargs["auto_remove"] is False


def test_labels_let_the_sweeper_find_orphans(kwargs: dict[str, object]) -> None:
    assert kwargs["labels"] == {RUN_LABEL: "run-1", TRIAL_LABEL: "3"}


def test_a_task_asking_for_no_network_gets_none_even_with_a_network_available() -> None:
    task = load_suite(CHECKOUT_SUITE).task("contamination-probe")
    kwargs = container_kwargs(
        task, "run-1", 0, workdir_volume="v", network_name="meridian-trial-run-1"
    )
    assert kwargs["network_mode"] == "none"


def test_a_proxy_only_task_joins_the_trial_network() -> None:
    task = load_suite(CHECKOUT_SUITE).task("expired-coupon")
    kwargs = container_kwargs(
        task, "run-1", 0, workdir_volume="v", network_name="meridian-trial-run-1"
    )
    assert kwargs["network_mode"] == "meridian-trial-run-1"


def test_proxy_only_without_a_network_still_gets_nothing() -> None:
    """Fail closed: a missing proxy network must not become open egress."""
    task = load_suite(CHECKOUT_SUITE).task("expired-coupon")
    assert container_kwargs(task, "r", 0, workdir_volume="v")["network_mode"] == "none"


def test_isolated_policy_gives_every_trial_its_own_volume() -> None:
    policy = IsolationPolicy()
    assert policy.volume_is_per_trial
    names = {policy.workdir_volume("run", "task", i) for i in range(8)}
    assert len(names) == 8


def test_unsafe_policy_shares_one_volume_across_the_run() -> None:
    policy = IsolationPolicy(unsafe_shared_env=True)
    assert not policy.volume_is_per_trial
    names = {policy.workdir_volume("run", f"task-{i}", i) for i in range(8)}
    assert names == {"meridian-shared-run"}
