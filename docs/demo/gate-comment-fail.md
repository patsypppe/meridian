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

`n=5` · `k=3` · status `complete` · harness errors 0.0% · cost 51c

<sub>head run `run-1787355993-64d3a9` · baseline run `run-1787355957-cbdbc8` · manifest `sha256:3dad961d32847743…` · reproduce with `meridian replay run-1787355993-64d3a9`</sub>
