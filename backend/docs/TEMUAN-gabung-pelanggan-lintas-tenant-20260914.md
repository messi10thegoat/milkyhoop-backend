# FINDING — Merging customers: the ghost wall was the ONLY thing preventing a cross-tenant leak (14 Sep 2026)

**Status:** CLOSED by unit (3) (same commit). Recorded separately so it isn't lost inside "merge fixed".

## Evidence (gate `scripts/gerbang_merge.py`, ROLLBACK)
- **Live code before unit (3):** every merge → 500 at `deleted_by = $3` (UUID into varchar). This is what made merge "never successful" (0 successes ever).
- **Same code with ONLY that wall removed (`lama1`):** merge from a kaos-biru customer with no CN/DP to target id `15c07294…` (a **grapgrap-manado** customer) → **200 "Merged 1 customers"**, and **3 kaos-biru invoices now point to the grapgrap customer**.
  - `sales_invoices` / `receive_payments` only have a simple FK `customers(id)` → no tenant check.
  - `credit_notes` / `customer_deposits` have the composite FK V247 → a source WITH CN/DP gets rejected by the DB (that's why the first trace returned 500).
- In the same mode:
  - target == source → 200, and the customer deactivates itself along with all its invoices;
  - target already deleted → 200;
  - duplicate sources → 200.

## Meaning
Anyone fixing the "merge always fails" bug by removing the wall alone would have opened a cross-tenant document leak and self-deactivation. The first error from a path that never ran was protecting the path from its own defects (the "overpayment" lesson class).

## Closure
Unit (3) validates the target and sources against the tenant with a single query. Another tenant's id == a made-up id == "Pelanggan tidak ditemukan" (identical). Sabotage removing the tenant filter → the cross-tenant scenario turns RED again (3 invoices move).

## Open suggestion (not done)
`sales_invoices`, `receive_payments`, `recurring_invoices`, `sales_receipts` still have a **simple** FK to customers. Upgrading them to a composite `(customer_id, tenant_id)` like V247 would enforce same-tenant in the DB for every writer. That is the ticket noted in the V247 unit.
