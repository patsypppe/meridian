"""Boundaries where the input is not ours.

Meridian's own threat model puts a task file in the attacker's hands. The product
is a CI gate: a pull request edits `suites/`, CI runs the gate on the edited
suite, and the grader's failure detail is posted back as a PR comment and printed
to a public job log. So a task definition is untrusted input, and so is anything
the agent puts in the workdir or sends to the proxy.

Each test here corresponds to a way that went wrong before it was closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.grading.graders.base import GraderError, resolve
from meridian.models.task import Limits
from meridian.runtime.proxy.budget import BudgetExceeded, BudgetLedger
from meridian.runtime.proxy.cassette import CassetteStore, request_key

pytestmark = pytest.mark.unit


# -- graders may only read what the trial produced ----------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "/work/../../../../../../etc/hosts",
        "/work/../.git/config",
        "/work/subdir/../../../../etc/passwd",
        "/work/./../../etc/hosts",
    ],
)
def test_a_traversal_out_of_the_workdir_is_refused(tmp_path: Path, hostile: str) -> None:
    """Prefix-matching alone let these through: they all start with `/work/`.

    The reachable consequence was a host file's contents rendered into a grader
    failure detail, which the gate posts to the pull request.
    """
    with pytest.raises(GraderError):
        resolve(tmp_path, hostile, "/work")


def test_a_symlink_planted_by_the_agent_cannot_escape(tmp_path: Path) -> None:
    """The workdir is the one thing the agent can write, so it can put a link there."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("token", encoding="utf-8")
    (state_dir / "out").symlink_to(tmp_path)

    with pytest.raises(GraderError):
        resolve(state_dir, "/work/out/secret.txt", "/work")


def test_ordinary_paths_still_resolve(tmp_path: Path) -> None:
    """The containment check must not break the case it exists to protect."""
    assert resolve(tmp_path, "/work/out/invoice.json", "/work") == tmp_path / "out/invoice.json"
    assert resolve(tmp_path, "/work", "/work") == tmp_path


# -- the proxy is reachable from the container --------------------------------


def test_a_request_with_a_temperature_can_be_keyed() -> None:
    """Floats reach this function from the provider's own request shape.

    The no-floats rule protects *manifest* hashes from drifting across platforms.
    Applied to a request body it protected nothing and broke the proxy: raising
    here became a 500, which the agent reported as a model error, which the
    grader scored as the agent's failure.
    """
    key = request_key({"model": "claude", "temperature": 0.7, "top_p": 0.95, "messages": []})
    assert key.startswith("sha256:")


def test_keying_a_request_is_still_deterministic_with_floats() -> None:
    body = {"model": "claude", "temperature": 0.7, "messages": []}
    assert request_key(dict(body)) == request_key(dict(body))


def test_a_different_temperature_is_a_different_request() -> None:
    """Or replay serves a response recorded under different sampling."""
    base = {"model": "claude", "messages": []}
    assert request_key({**base, "temperature": 0.7}) != request_key({**base, "temperature": 0.2})


@pytest.mark.parametrize(
    "hostile",
    ["../escape", "../../etc/passwd", "/absolute", "with/slash", "Upper", "a"],
)
def test_a_cassette_cannot_be_written_outside_its_directory(tmp_path: Path, hostile: str) -> None:
    """The slug arrives in a request header, so the container chooses it.

    In record mode it names the file the *proxy* writes, and the proxy is the
    process holding the credential.
    """
    with pytest.raises(ValueError):
        CassetteStore(tmp_path).path_for(hostile)


def test_a_real_slug_still_names_a_tape(tmp_path: Path) -> None:
    assert CassetteStore(tmp_path).path_for("expired-coupon") == tmp_path / "expired-coupon.json"


# -- a ceiling the agent can step around is not a ceiling ---------------------


def test_an_unknown_task_slug_cannot_buy_an_unlimited_budget() -> None:
    """The task name is a header the container sets.

    An unrecognised slug used to fall past the per-trial check entirely, so an
    agent escaped its own limit by calling itself something else.
    """
    ledger = BudgetLedger(
        limits={"real-task": Limits(timeout_seconds=60, max_tokens=1000, budget_cents=5)},
        run_budget_cents=None,
    )
    with pytest.raises(BudgetExceeded):
        ledger.check_before("some-other-name", 0)


def test_a_known_task_is_still_allowed_to_spend() -> None:
    ledger = BudgetLedger(
        limits={"real-task": Limits(timeout_seconds=60, max_tokens=1000, budget_cents=5)},
        run_budget_cents=None,
    )
    ledger.check_before("real-task", 0)


def test_a_run_with_no_configured_limits_is_unaffected() -> None:
    """Some runs genuinely configure no per-task ceilings; that is not an escape."""
    BudgetLedger(limits={}, run_budget_cents=None).check_before("anything", 0)
