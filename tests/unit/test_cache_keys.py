"""Cache key derivation (`HANDOFF §8.6`).

No cache exists yet, so nothing here protects a running system. What it protects
is the *shape* of the key, which is the part that becomes expensive later: once
scores are stored under these keys, changing the derivation is either a migration
across every stored row or a silent miss on all of them.

Every argument gets a test proving it is actually part of the key. An argument
that is accepted and then ignored is the failure mode worth catching here — it
produces a cache that confidently returns the wrong entry.
"""

from __future__ import annotations

import pytest

from meridian.cache import CACHE_EPOCH, judge_score_key, reference_key

pytestmark = pytest.mark.unit

SAMPLE = "task-1042:trial-3"
GEN_CONFIG = "sha256:aaaa"
OUTPUT = "sha256:bbbb"
JUDGE_CONFIG = "sha256:cccc"
METRIC = "faithfulness"


def a_judge_key(**overrides: str) -> str:
    kwargs = {
        "sample_id": SAMPLE,
        "output_hash": OUTPUT,
        "judge_config_hash": JUDGE_CONFIG,
        "metric": METRIC,
    }
    kwargs.update(overrides)
    return judge_score_key(**kwargs)


# -- shape -------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [reference_key(SAMPLE, GEN_CONFIG), a_judge_key()],
    ids=["reference", "judge"],
)
def test_a_key_is_a_prefixed_sha256(key: str) -> None:
    """`sha256:` + 64 hex, matching every other digest Meridian prints."""
    assert key.startswith("sha256:")
    assert len(key) == len("sha256:") + 64
    int(key.removeprefix("sha256:"), 16)  # raises if it is not hex


# -- determinism -------------------------------------------------------------


def test_the_same_reference_inputs_give_the_same_key() -> None:
    assert reference_key(SAMPLE, GEN_CONFIG) == reference_key(SAMPLE, GEN_CONFIG)


def test_the_same_judge_inputs_give_the_same_key() -> None:
    assert a_judge_key() == a_judge_key()


# -- every argument is load-bearing ------------------------------------------


def test_a_different_sample_is_a_different_reference_key() -> None:
    assert reference_key("other", GEN_CONFIG) != reference_key(SAMPLE, GEN_CONFIG)


def test_a_different_generation_config_is_a_different_reference_key() -> None:
    """Change a decoding parameter and the cache must miss, not serve the old output."""
    assert reference_key(SAMPLE, "sha256:zzzz") != reference_key(SAMPLE, GEN_CONFIG)


@pytest.mark.parametrize(
    "field",
    ["sample_id", "output_hash", "judge_config_hash", "metric"],
)
def test_every_judge_argument_changes_the_key(field: str) -> None:
    """An argument accepted and then ignored yields confidently wrong cache hits."""
    assert a_judge_key(**{field: "changed"}) != a_judge_key()


def test_the_same_judge_and_artifact_under_two_metrics_do_not_collide() -> None:
    """Or the second metric silently reads the first one's score."""
    assert a_judge_key(metric="faithfulness") != a_judge_key(metric="helpfulness")


# -- namespaces and the epoch ------------------------------------------------


def test_the_two_namespaces_never_collide_for_one_sample() -> None:
    """`ref:` and `judge:` share a keyspace, so their prefixes have to separate them."""
    assert reference_key(SAMPLE, GEN_CONFIG) != a_judge_key()


def test_the_epoch_is_part_of_every_key() -> None:
    """Bumping the epoch is how the cache is invalidated without a migration.

    Recomputed here rather than monkeypatched: the point is that the epoch string
    genuinely reaches the digest, which a patched module constant would not prove
    if the derivation had baked the old value in at import time.
    """
    import hashlib

    def expected(namespace: str, epoch: str, *fields: str) -> str:
        body = "".join(f"{len(f)}:{f}" for f in fields)
        return "sha256:" + hashlib.sha256(f"{namespace}:{epoch}:{body}".encode()).hexdigest()

    assert reference_key(SAMPLE, GEN_CONFIG) == expected("ref", CACHE_EPOCH, SAMPLE, GEN_CONFIG)
    assert reference_key(SAMPLE, GEN_CONFIG) != expected("ref", "v2", SAMPLE, GEN_CONFIG)


def test_the_epoch_is_a_bare_version_token() -> None:
    """`v1`, not `sha256:...` or a date — it is compared and printed by humans."""
    assert CACHE_EPOCH.startswith("v")
    assert CACHE_EPOCH[1:].isdigit()


# -- the delimiter actually delimits -----------------------------------------


def test_field_boundaries_cannot_be_forged_by_shifting_a_colon() -> None:
    """Naive concatenation lets `a:b` + `c` collide with `a` + `b:c`.

    This is the classic length-extension-by-delimiter bug. It is asserted rather
    than assumed, because the day it regresses two unrelated samples start
    sharing a cache entry and nothing looks wrong.
    """
    assert reference_key("a:b", "c") != reference_key("a", "b:c")
    assert a_judge_key(sample_id="a:b", output_hash="c") != a_judge_key(
        sample_id="a", output_hash="b:c"
    )
