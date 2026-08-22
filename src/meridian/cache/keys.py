"""Cache keys, specified now and wired later (`HANDOFF §8.6`).

Nothing here caches anything. With deterministic graders there is no expensive
call to memoize, so building the storage now would optimize a path that has no
cost on it (`HANDOFF §3.2`).

The *keys* ship anyway, because their shape is the part that is expensive to
change later. Once judge scores are stored under a key, altering the derivation
means either a migration across every stored row or a silent cache miss on all of
them. Fixing the shape while the cache is still empty costs nothing.

`v1` is a cache **epoch**, not a version number anybody maintains. Bumping it
invalidates every key at once, which is how a cache gets invalidated without a
migration -- the old entries are simply never addressed again and age out.

`output_hash` hashes the **graded artifact**, not the transcript. Across two
candidate versions of an agent, well over half of outputs are byte-identical even
when the trajectories that produced them differ completely; keying on the artifact
is precisely what makes the cache hit, and keying on the transcript is what would
make it useless.

This module is pure by the layout rule in `CLAUDE.md`: no I/O, no Docker, and no
imports from the runtime.

## One deliberate deviation from §8.6

The handoff writes the derivation as plain concatenation:

    reference_key = sha256("ref:v1:" + sample_id + ":" + generation_config_hash)

Taken literally, that collides. Joining on a delimiter is not injective once a
field may itself contain the delimiter, so `("a:b", "c")` and `("a", "b:c")`
produce an identical key -- two different samples silently sharing one cache
entry. That is not hypothetical here: `generation_config_hash` is a `sha256:`
-prefixed digest and already contains a colon, and a `sample_id` naming a task
and a trial (`task-1042:trial-3`) contains another. Under §8.6 as written, the
common case is the colliding case.

Each field is therefore length-prefixed before joining, which makes the encoding
injective -- the field boundaries are recoverable from the bytes, so no two
distinct tuples can encode the same way. The `ref:`/`judge:` namespaces, the
epoch, and the field order are exactly as §8.6 specifies; only the joining is
fixed.

Fixing it now is free, because the cache is empty. After judges land it is a
migration across every stored row -- which is the entire reason §8.6 says to
derive the keys before building the cache.
"""

from __future__ import annotations

import hashlib

__all__ = ["CACHE_EPOCH", "judge_score_key", "reference_key"]

CACHE_EPOCH = "v1"


def _sha256(text: str) -> str:
    """`sha256:`-prefixed, matching every other digest Meridian prints."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _joined(namespace: str, *fields: str) -> str:
    """Length-prefix every field so the encoding is injective.

    `("a:b", "c")` and `("a", "b:c")` must not encode identically. Prefixing each
    field with its length makes the boundaries recoverable from the encoding,
    which is exactly the property plain delimiter-joining lacks.
    """
    body = "".join(f"{len(field)}:{field}" for field in fields)
    return f"{namespace}:{CACHE_EPOCH}:{body}"


def reference_key(sample_id: str, generation_config_hash: str) -> str:
    """Address a generated reference output.

    Keyed on the sample and on everything that shaped generation, so changing a
    decoding parameter is a miss rather than a stale hit.
    """
    return _sha256(_joined("ref", sample_id, generation_config_hash))


def judge_score_key(
    sample_id: str,
    output_hash: str,
    judge_config_hash: str,
    metric: str,
) -> str:
    """Address one judge's score for one artifact under one metric.

    `metric` is part of the key: the same judge scoring the same artifact for
    faithfulness and for helpfulness must not collide, or the second metric
    silently reads the first one's score.
    """
    return _sha256(_joined("judge", sample_id, output_hash, judge_config_hash, metric))
