> **RESOLVED 2026-07-27 (BATCH1 A1).** Fixed: `sales_invoices.py` binds `str(invoice['customer_id'])` (customer_deposits.customer_id is the lone VARCHAR *_id; every sibling is uuid). Endpoint now returns 200. Full class audit: `2026-07-27-uuid-varchar-bind-class-audit.md`. Regression-gated by harness step 6 (C1).

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

---
## ESCALATION (2026-07-26) — bigger than a type bug: DP apply is unreachable end-to-end via UI

Combine with the step-3 gap (tagih-DP absent by design): the canonical DP lifecycle is
Terima DP (receive) → Apply DP (use it against a faktur). Receive works. **Apply does NOT via the
UI**: the ApplyDepositPanel must first LIST applicable deposits to let the user pick one, and that
list endpoint (`GET /api/sales-invoices/{id}/applicable-deposits`) 500s. **A user can TAKE a down
payment but cannot USE it.** This is a half-wired feature, not an isolated endpoint bug.

### Classification correction: BACKEND bug, not FE
The FE panel is only the consumer. The defect is an asyncpg parameter-binding type mismatch in the
backend query (customer_id UUID bound to a VARCHAR column, sales_invoices.py:3686). Fix belongs in
the backend router, not the FE. Important for routing the fix.

### PATTERN — third instance of "engine built, UI wiring unfinished"
1. `apply_vendor_deposit` — write path 500s (bills.paid_amount/total_amount don't exist); feature
   dead end-to-end (and mis-posts if naively fixed — 1-10800 wrong class).
2. Quote `payment_*` (V219) — stored on write, never rendered (detail API + PDF both drop them);
   the customer never sees where to pay.
3. `applicable-deposits` — deposit stored + posted + spine-linked, but the discovery query 500s,
   so it can never be applied via the UI.
Three separate capabilities where the ledger/engine side works and the last-mile read/UI wiring was
never finished. **Owner should see this as a pattern**, not three incidents: the accounting engine
is ahead of the product surface. Recommend a "read/render/UI reachability" pass per money feature
(write 201 is not "done").
