## Meridian gate

❌ **FAIL** — suite pass^k dropped 0.171 (0.486 → 0.314), p=0.019

**Now fails every trial:** `expired-coupon`, `expired-on-bulk-order`, `stale-coupon`

<details><summary>Failing trials in this run</summary>

- `expired-coupon` — /work/out/invoice-1042.json $.total_cents is 3999, expected 4999
- `expired-on-bulk-order` — /work/out/invoice-1045.json $.total_cents is 24000, expected 30000
- `expiry-boundary` — SELECT status FROM orders WHERE id = 1046 returned [['pending']], expected [['invoiced']]
- `happy-path` — SELECT status FROM orders WHERE id = 1041 returned [['pending']], expected [['invoiced']]
- `stale-coupon` — /work/out/invoice-1044.json $.total_cents is 10200, expected 12000
- `unknown-coupon` — SELECT status FROM orders WHERE id = 1047 returned [['pending']], expected [['invoiced']]

</details>

| task | baseline pass^3 | head pass^3 | |
|---|---|---|---|
| `expired-coupon` | 0.400 | 0.000 | 🔻 regressed |
| `expired-on-bulk-order` | 0.400 | 0.000 | 🔻 regressed |
| `expiry-boundary` | 0.400 | 0.400 |  |
| `happy-path` | 0.400 | 0.400 |  |
| `missing-field` | 1.000 | 1.000 |  |
| `stale-coupon` | 0.400 | 0.000 | 🔻 regressed |
| `unknown-coupon` | 0.400 | 0.400 |  |

| | baseline | head |
|---|---|---|
| suite pass^3 | 0.486 | 0.314 |

**Sensitivity.** Regressions smaller than about **0.201** would more often than not have gone unnoticed by this comparison (80% power, one-sided alpha=0.05). Resolving the configured tolerance of 0.030 would take about **315 comparable tasks**; this comparison has 7.

`n=5` · `k=3` · status `complete` · harness errors 0.0% · cost 51c

<sub>head run `run-1787380583-b5ef2e` · baseline run `run-1787380473-108eb8` · manifest `sha256:a33f58bd364886fc…` · reproduce with `meridian replay run-1787380583-b5ef2e`</sub>
