# BUG (FILED, out of BATCH1 scope): credit_notes list filter 500 — UUID bound to VARCHAR customer_id

**Date:** 2026-07-27
**Severity:** MEDIUM (credit-notes-by-customer filter 500s; create path is fine)
**Status:** RUNTIME-PLAUSIBLE (same asyncpg mechanism as the confirmed A1 500), code-confirmed. NOT
fixed — outside BATCH1 scope (not on the DP -1..9 path, not the Uang Muka module).

## What
`credit_notes.customer_id` is `character varying` (verified via information_schema), while
`customers.id` is `uuid` — the same lone-drift class as `customer_deposits.customer_id`.
`routers/credit_notes.py:255-256`:
```python
conditions.append(f"customer_id = ${param_idx}")
params.append(UUID(customer_id))          # UUID object -> VARCHAR column
```
Binding a `uuid.UUID` to the VARCHAR column raises asyncpg "expected str, got UUID" → 500 whenever
the credit-notes list is filtered by customer_id. (The create INSERT at :653 already uses
`str(body.customer_id)`, so only the filter is affected.)

## Fix (when scoped)
Bind `str(customer_id)` at :256 (mirror the A1 fix), OR — preferred — resolve the whole class by
converting `credit_notes.customer_id` + `customer_deposits.customer_id` to `uuid` (see
`2026-07-27-DECISION-customer-id-varchar-to-uuid.md`).

## Why filed not fixed now
Owner scope for BATCH1: fix only the sites on the DP -1..9 path + the Uang Muka (deposit) module;
FILE the rest. Credit notes is a separate module. Recommend folding the one-line str() fix into the
same change that converts the columns, so it's a class fix, not another instance patch.
