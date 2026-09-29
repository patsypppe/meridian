## Meridian gate

⚠️ **PASS (with warning)** — suite pass^k dropped 0.114 but p=0.091 > 0.050; the drop is not distinguishable from noise across this task set

**Now fails every trial:** `expired-coupon`, `expiry-boundary`

<details><summary>Failing trials in this run</summary>

- `expired-coupon` — /work/out/invoice-1042.json $.total_cents is 4900, expected 4999
- `expired-on-bulk-order` — SELECT status FROM orders WHERE id = 1045 returned [['pending']], expected [['invoiced']]
- `expiry-boundary` — /work/out/invoice-1046.json $.total_cents is 7400, expected 7440
- `happy-path` — SELECT status FROM orders WHERE id = 1041 returned [['pending']], expected [['invoiced']]
- `stale-coupon` — SELECT status FROM orders WHERE id = 1044 returned [['pending']], expected [['invoiced']]
- `unknown-coupon` — SELECT status FROM orders WHERE id = 1047 returned [['pending']], expected [['invoiced']]

</details>

| task | baseline pass^3 | head pass^3 | |
|---|---|---|---|
| `expired-coupon` | 0.400 | 0.000 | 🔻 regressed |
| `expired-on-bulk-order` | 0.400 | 0.400 |  |
| `expiry-boundary` | 0.400 | 0.000 | 🔻 regressed |
| `happy-path` | 0.400 | 0.400 |  |
| `missing-field` | 1.000 | 1.000 |  |
| `stale-coupon` | 0.400 | 0.400 |  |
| `unknown-coupon` | 0.400 | 0.400 |  |

| | baseline | head |
|---|---|---|
| suite pass^3 | 0.486 | 0.371 |

**Sensitivity.** Regressions smaller than about **0.183** would more often than not have gone unnoticed by this comparison (80% power, one-sided alpha=0.05). Resolving the configured tolerance of 0.030 would take about **262 comparable tasks**; this comparison has 7.

`n=5` · `k=3` · status `complete` · harness errors 0.0% · cost 51c

<sub>head run `run-1790713822-53c681` · baseline run `run-1790713766-3cc6f2` · manifest `sha256:51c0aca8d267ca4f…` · reproduce with `meridian replay run-1790713822-53c681`</sub>
