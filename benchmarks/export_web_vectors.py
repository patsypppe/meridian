"""Export reference values for the dashboard's TypeScript statistics.

The dashboard re-implements `stats.passk` and `stats.intervals`. This writes what
the Python implementation returns for a fixed set of inputs; `web/src/stats.test.ts`
asserts the TypeScript agrees, and `tests/unit/test_web_vectors.py` fails if the
committed file goes stale.

    uv run python -m benchmarks.export_web_vectors
"""

from __future__ import annotations

import json
from pathlib import Path

from meridian.stats.intervals import t_interval, t_inv_cdf, wilson_interval
from meridian.stats.passk import pass_at_k, pass_hat_k

OUT = Path(__file__).resolve().parents[1] / "web" / "src" / "__fixtures__" / "stats-vectors.json"

PASSK_CASES = [(5, 4, 3), (10, 0, 3), (10, 10, 3), (10, 7, 3), (10, 1, 1), (8, 3, 8), (20, 13, 5)]
T_CASES = [(0.975, 1), (0.975, 2), (0.975, 4), (0.975, 6), (0.975, 29), (0.995, 9), (0.9, 3)]
T_INTERVAL_CASES = [
    [1.0, 2.0, 3.0, 4.0, 5.0],
    [0.0, 0.1, 0.3, 0.2, 0.0, 0.4, 0.1],
    [0.7, 0.29, 0.29, 0.29, 0.47, 0.17, 0.29],
]
WILSON_CASES = [(0, 10), (5, 10), (10, 10), (1, 10), (7, 20)]


def vectors() -> dict[str, list[dict[str, object]]]:
    return {
        "passk": [
            {
                "n": n,
                "c": c,
                "k": k,
                "pass_at_k": pass_at_k(n, c, k),
                "pass_hat_k": pass_hat_k(n, c, k),
            }
            for n, c, k in PASSK_CASES
        ],
        "t_quantile": [{"p": p, "df": df, "value": t_inv_cdf(p, df)} for p, df in T_CASES],
        "t_interval": [
            {"values": values, "lower": lo, "upper": hi}
            for values in T_INTERVAL_CASES
            for lo, hi in [t_interval(values)]
        ],
        "wilson": [
            {"successes": s, "n": n, "lower": lo, "upper": hi}
            for s, n in WILSON_CASES
            for lo, hi in [wilson_interval(s, n)]
        ],
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
