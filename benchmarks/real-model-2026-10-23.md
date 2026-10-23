# Real-model runs, 2026-10-23

First runs of the `checkout-agent` suite against real models (Groq, via the
proxy's OpenAI-compatible upstream), n=10 trials per task, k=3, 7 scored tasks.
These replace nothing in `self-eval.json`, which still measures the gate itself
against the deterministic stub. Cost figures are notional (default price table).

| Model | Suite pass^3 | 95% CI (over tasks) | Trials passed | Harness errors | Notes |
|---|---|---|---|---|---|
| `openai/gpt-oss-20b` | **0.05** | [0.00, 0.13] | 17 / 70 | 0 | 1 trial lost to a provider 429 after retries |
| `openai/gpt-oss-120b` | 0.36 (lower bound) | [0.26, 0.49] | not counted | 0 | **Contaminated**: several failures were provider 429s that outlasted a 30s retry cap; a clean rerun was cancelled |

## gpt-oss-20b, per task

| Task | Passed | pass@3 | pass^3 |
|---|---|---|---|
| expired-coupon | 4/10 | 0.83 | 0.03 |
| expired-on-bulk-order | 3/10 | 0.71 | 0.01 |
| expiry-boundary | 1/10 | 0.30 | 0.00 |
| happy-path | 1/10 | 0.30 | 0.00 |
| missing-field | 7/10 | 0.99 | 0.29 |
| stale-coupon | 0/10 | 0.00 | 0.00 |
| unknown-coupon | 1/10 | 0.30 | 0.00 |

## What this does and does not show

- The gate's machinery works on real model output: the two models separate
  clearly (0.05 vs a contaminated 0.36 lower bound) and the CIs reflect only 7 tasks.
- `happy-path` at 1/10 is lower than expected for a trivial task. It has **not**
  been audited: the cause could be the model, the Anthropic<->OpenAI tool-call
  translation (e.g. reasoning content is dropped), or the task's assertions.
  Read the transcripts before drawing conclusions about the model.
- Provider rate limits (429) used to be scored as agent failures. The proxy now
  retries them with Retry-After-aware backoff; exhausted retries are still
  recorded as `fail` rather than `harness_error`, which understates the model.
- One suite, one synthetic domain, 7 tasks: nothing here is comparable to a
  published benchmark score.
