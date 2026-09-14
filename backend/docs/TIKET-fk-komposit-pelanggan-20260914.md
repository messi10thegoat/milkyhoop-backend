# TICKET — composite customer FK (V252) + note on the 24h owner check

## 24h owner-PERMISSION_UNMAPPED check — CANCELLED (not measurable)
MASTER's correction, recorded: the stage-3 flip is OWNER-AWARE — the owner ALWAYS passes an unmapped write (bypass), so a PERMISSION_UNMAPPED from the owner is impossible by construction. A check that cannot go red is not a measurement. So this is removed as an "owed" item, not left pending. (The real guard is the coverage ratchet + non-owner behavior, both gated.)

## V252 — composite FK (customer_id, tenant_id) → customers(id, tenant_id), 14 Sep 2026, commit 2c8760b3
Closes the cross-tenant gap found by the customer-merge unit (simple FK on sales_invoices/receive_payments let a document point at another tenant's customer).

**Measurement first (all uuid, no type change → no restart):** every table with a uuid `customer_id` has `tenant_id`, and orphan-or-cross-tenant rows = **0** on all of them.

**Applied to 8 tables:**
- upgraded simple → composite: sales_invoices, receive_payments, recurring_invoices, sales_receipts;
- added (had no FK): accounts_receivable, proformas, quotes, sales_orders.
The migration RAISEs if any of the 8 has an orphan/cross-tenant row (measured 0). ON DELETE NO ACTION (V247 pattern). Target UNIQUE `uq_customers_id_tenant` (V247).

**Excluded, recorded (not touched):**
- `customer_activities`, `customer_price_lists` — simple FK with **ON DELETE CASCADE** (child metadata); upgrading changes delete semantics → separate decision.
- `cheques`, `item_serials`, `production_orders`, `table_reservations` — `customer_id` present but its role as a customer-party isn't confirmed; 0 violations today, awaiting confirmation before adding an FK that could reject a future legitimate insert.

**Now 10 composite customer FKs** (8 + credit_notes/customer_deposits from V247).

**Gate `scripts/gerbang_v252.py`:** new/live — cross-tenant `UPDATE customer_id` → **23503** on every table with rows, same-tenant → ok, create-invoice handler → 200, in-tenant merge → 200; old — cross-tenant UPDATE **LOLOS** on all (gap proven). 27/27 (dry-run) / 26/26 (live).
Lossless ROLLBACK restores the 4 simple FKs and drops the 4 added.
