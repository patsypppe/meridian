## Meridian gate

✅ **PASS** — suite pass^k moved -0.000 (0.486 → 0.486), within the 0.030 tolerance

<details><summary>Failing trials in this run</summary>

- `expired-coupon` — SELECT status FROM orders WHERE id = 1042 returned [['pending']], expected [['invoiced']]
- `expired-on-bulk-order` — SELECT status FROM orders WHERE id = 1045 returned [['pending']], expected [['invoiced']]
- `expiry-boundary` — SELECT status FROM orders WHERE id = 1046 returned [['pending']], expected [['invoiced']]
- `happy-path` — SELECT status FROM orders WHERE id = 1041 returned [['pending']], expected [['invoiced']]
- `stale-coupon` — SELECT status FROM orders WHERE id = 1044 returned [['pending']], expected [['invoiced']]
- `unknown-coupon` — SELECT status FROM orders WHERE id = 1047 returned [['pending']], expected [['invoiced']]

</details>

| task | baseline pass^3 | head pass^3 | |
|---|---|---|---|
| `expired-coupon` | 0.400 | 0.400 |  |
| `expired-on-bulk-order` | 0.400 | 0.400 |  |
| `expiry-boundary` | 0.400 | 0.400 |  |
| `happy-path` | 0.400 | 0.400 |  |
| `missing-field` | 1.000 | 1.000 |  |
| `stale-coupon` | 0.400 | 0.400 |  |
| `unknown-coupon` | 0.400 | 0.400 |  |

| | baseline | head |
|---|---|---|
| suite pass^3 | 0.486 | 0.486 |

**Sensitivity.** Not estimated: all 7 tasks moved by the same amount, so there is no spread to estimate sensitivity from. Treat the verdict as directional.

`n=5` · `k=3` · status `complete` · harness errors 0.0% · cost 51c

<sub>head run `run-1787380628-5c7a41` · baseline run `run-1787380473-108eb8` · manifest `sha256:f532fd6b9d662bc6…` · reproduce with `meridian replay run-1787380628-5c7a41`</sub>
