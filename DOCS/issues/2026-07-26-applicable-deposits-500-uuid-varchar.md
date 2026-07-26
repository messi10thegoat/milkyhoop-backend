# BUG: GET /api/sales-invoices/{id}/applicable-deposits 500s (UUID bound to VARCHAR customer_id)

**Date:** 2026-07-26
**Severity:** HIGH (the FE "apply deposit" panel is broken — DP apply is unreachable via the UI)
**Surfaced by:** FASE-4 step 6 test 3(a) (call the FE panel's discovery endpoint before applying).

## What
`GET /api/sales-invoices/{invoice_id}/applicable-deposits` returns 500
(`{"detail":"Failed to list applicable deposits"}`). Traceback:
```
Error listing applicable deposits: invalid input for query argument $2:
  UUID('758ef550-...') (expected str, got UUID)
  File ".../routers/sales_invoices.py", line 3686, in get_applicable_deposits
    rows = await conn.fetch(...)
TypeError: expected str, got UUID
```
`$2` is the customer_id (758ef550… = our test customer). It is bound as a Python `UUID`, but
`customer_deposits.customer_id` (and `customers.id`) are **VARCHAR** — asyncpg refuses to encode a
UUID into a text parameter. So the query never runs; the endpoint always 500s for any invoice whose
customer_id is passed as UUID.

## Impact
This is the exact endpoint the FE `ApplyDepositPanel` (on the invoice detail) calls to list
deposits available to apply. With it 500ing, **the panel shows an error / no deposits → a user
cannot discover or select a DP to apply through the UI.** The DP is correctly created, posted, and
spine-linked (sales_order_id + quote_id set) — the linkage is fine; the discovery query is broken by
a type mismatch. (Apply-by-ID via POST /apply may still work; the DISCOVERY path is what's broken.)

## Root cause
The customer_id-is-VARCHAR gotcha (documented project-wide: receive_payments.customer_id VARCHAR,
customers.id VARCHAR). `get_applicable_deposits` converts/binds customer_id as a UUID for `$2`
instead of `str(customer_id)`.

## Fix
Bind `str(customer_id)` (or cast in SQL `cd.customer_id = $2::text`) at sales_invoices.py:3686.
Audit the other params in that query for the same UUID-vs-text mismatch.

## Note for the harness
Step 6's core assertion (does apply reduce AR via Branch 3?) is testable via POST
/customer-deposits/{id}/apply directly (apply-by-ID), which does not depend on this broken discovery
endpoint. The discovery bug is filed here; whether to proceed with apply-by-ID to complete the B0 /
Branch-3 runtime proof is an owner decision (the FE panel path is broken regardless).
