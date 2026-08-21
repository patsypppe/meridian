# Demo artifacts

Captured output, not screenshots. Each file is the literal stdout of the command
named at the top of it, so anything here can be regenerated and checked.

| File | What it shows |
|---|---|
| [`run-table.txt`](run-table.txt) | `meridian run --n 5 --k 3` — `pass@3 = 1.00` beside `pass^3 = 0.40` |
| [`gate-comment-fail.md`](gate-comment-fail.md) | The PR comment when the planner's expiry instruction is removed |
| [`gate-comment-pass.md`](gate-comment-pass.md) | The same gate after the change is reverted |
| [`contamination-probe.txt`](contamination-probe.txt) | The isolation probe passing **and** failing |

The live version of the failing check is on the demo pull request; see the link
in the repository README.

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
