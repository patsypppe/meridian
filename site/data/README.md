# Showcase data

Everything the showcase page draws comes from `showcase.json`, which
`export.py` builds from the raw output in `raw/` using Meridian's own
functions (`pass_hat_k`, `decide`, `paired_regression_p_value`,
`minimum_detectable_effect`, `required_tasks`). The export recomputes every
gate verdict from the archived runs and aborts if it disagrees with the PR
comment the gate wrote.

| File | Produced by |
|---|---|
| `raw/run.json`, `raw/run.txt` | `meridian run --suite ./suites/checkout-agent --n 5 --k 3 --proxy-mode replay` |
| `raw/probe.json` | `probe.py`: writer then probe, with per-trial volumes and with `unsafe_shared_env` |
| `raw/audit.json`, `raw/audit.txt` | `meridian suite audit --suite ./suites/checkout-agent` |
| `raw/gate-baseline.json` | `meridian run ... --proxy-mode record --gate-mode`, the one baseline all three gates share |
| `raw/gate-*.md`, `raw/gate-*.log` | `meridian gate --baseline-run <baseline> --n 5 --k 3 --tolerance 0.03 --require-significance` for the degraded prompt, the invoice-rounding bug, and the unchanged agent |
| self-measurement | read from `benchmarks/self-eval.json` (`make self-eval`), not regenerated here |

## Regenerating

```bash
make images                   # once
bash site/data/regenerate.sh  # ~5 minutes, no API key
node --test site/tests/*.test.mjs
```

Every run talks to the deterministic stand-in provider behind the proxy, so
nothing is billed. `make images` repins the task files in `suites/` to your
local image digests; do not commit that change. Seeded edits to `fixtures/`
are restored when the script exits.
