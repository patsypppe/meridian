# Coupon policy

A coupon may be applied to an order only when **both** of these hold:

1. The coupon code exists in the `coupons` table of the store database.
2. The order's `as_of` date is **on or before** the coupon's `expires_on` date.

If either fails, the coupon is **declined**: charge the full subtotal and record
`coupon.applied = false` with a `coupon.decline_reason` explaining which
condition failed. An expired coupon is declined, never honoured as a courtesy.

## Totals

- `subtotal_cents` is the sum of `qty * unit_cents` across line items.
- When a coupon is applied, `total_cents = subtotal_cents - round(subtotal_cents * percent_off / 100)`,
  rounded to the nearest cent, ties away from zero.
- When a coupon is declined, `total_cents = subtotal_cents`.

## Orders that cannot be invoiced

An order with no line items cannot be invoiced. Write **no** invoice, leave the
order row `pending`, and record the refusal at `/work/out/refusal-<id>.json`
with a `reason` field naming what was missing.

## On success

Write the invoice to `/work/out/invoice-<id>.json` and set the order row's
`status` to `invoiced` and `total_cents` to the invoiced total.
