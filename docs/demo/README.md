# Demo artifacts

Captured output, not screenshots. Each file is the literal stdout of the command
named at the top of it, so anything here can be regenerated and checked.

| File | What it shows |
|---|---|
| [`run-table.txt`](run-table.txt) | `meridian run --n 5 --k 3` — `pass@3 = 1.00` beside `pass^3 = 0.40` |
| [`gate-comment-fail.md`](gate-comment-fail.md) | The PR comment when the planner's expiry instruction is removed |
| [`gate-comment-pass.md`](gate-comment-pass.md) | The same gate after the change is reverted |
| [`contamination-probe.txt`](contamination-probe.txt) | The isolation probe passing **and** failing |

## The live demonstration

[**PR #12 — "refactor: tighten the planner prompt"**](https://github.com/patsypppe/meridian/pull/12)

| Run | Verdict |
|---|---|
| [`32539893977`](https://github.com/patsypppe/meridian/actions/runs/32539893977) | ❌ **failure** — named `expired-coupon`, `expired-on-bulk-order`, `stale-coupon` as now failing every trial |
| [`32540053647`](https://github.com/patsypppe/meridian/actions/runs/32540053647) | ✅ **success** after the revert, same PR, comment updated in place |

Closed unmerged, which is the correct outcome: the gate said no.

What a human reviewer had to go on was a three-line diff removing a sentence that
restates the policy document the agent already reads. The suite *mean* would have
moved from 86% to 80%. `pass^3` moved 0.486 → 0.314, and three tasks went from
working sometimes to never working at all.

## Regenerating them

```bash
make images
uv run meridian run --suite ./suites/checkout-agent --n 5 --k 3 --proxy-mode replay

# the failing direction
cp fixtures/checkout_agent/prompts/planner.degraded.md \
   fixtures/checkout_agent/prompts/planner.md
uv run meridian gate --suite ./suites/checkout-agent --baseline-ref HEAD \
   --n 5 --k 3 --tolerance 0.03 --comment-file docs/demo/gate-comment-fail.md
git checkout fixtures/checkout_agent/prompts/planner.md
```
