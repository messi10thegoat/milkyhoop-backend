# Follow-up: Sales Order "Terima DP" does not auto-prefill dp_amount

**Filed:** 2026-06-16 (alongside P3 bridge: quote/SO<->deposit linkage)
**Severity:** Minor, non-blocking
**Area:** Sales Orders, Customer Deposits (Uang Muka / DP)

## Symptom
When a user clicks "Terima DP" from a **Sales Order**, the deposit amount
defaults to `0` and the user must type it in manually. From a **Quote** the
amount prefills, because the Quote object carries `dp_amount`.

## Root cause
The Sales Order GET response does **not** return a `dp_amount` field (the Quote
GET does). The quote->SO convert path also does not copy `dp_amount` onto the
created SO. So at the SO stage there is no down-payment figure to prefill the
"Terima DP" form with.

## Proposed fix (either or both)
1. **Convert copies it:** in `quotes.convert_to_sales_order`, persist the
   quotes `dp_amount` onto the new `sales_orders` row (add column if absent).
2. **SO GET surfaces it:** include `dp_amount` in the Sales Order GET response
   so the FE "Terima DP" form can prefill `min(dp_amount, ...)`.

Then the SO "Terima DP" form prefills the agreed down-payment automatically,
matching the Quote-stage behaviour.

## Notes
- P3 already links a deposit to its `sales_order_id`; this is purely about the
  *suggested amount* prefill, not the linkage.
- No accounting impact: prefill is a UI convenience; the apply journal is
  unchanged (P1).
