# AUDIT: UUID→VARCHAR parameter-bind class (BATCH1 A2)

**Date:** 2026-07-27 (corrected same day after information_schema audit)
**Trigger:** applicable-deposits 500 (A1). Owner asked to grep the whole class, not just one site.

## The class, precisely
asyncpg accepts a `str` for a `uuid` column parameter (it parses), but REJECTS a `uuid.UUID`
object bound to a `character varying` column ("expected str, got UUID"). So the failure class is:
**a UUID object bound to a VARCHAR column that holds an id.**

## CORRECTION to an earlier overclaim
An earlier draft said "the only VARCHAR `*_id` column in the whole schema is
customer_deposits.customer_id." That is WRONG. Verified via `information_schema` (not grep):
- Many tables have `tenant_id` VARCHAR — but `"Tenant".id` is `text`, so tenant_id VARCHAR→text is
  CONSISTENT (no drift; always bound as a str from auth context).
- `customers.tax_id` / `vendors.tax_id` are VARCHAR but are tax NUMBERS, not FKs.
- `audit_logs.action_id`, `userguide_chunks.doc_id`, `userguide_query_log.user_id` are VARCHAR
  (referents unverified; out of scope).

The accurate statement: **VARCHAR columns that reference a `uuid` PK.** There are TWO, both pointing
at `customers.id` (uuid):
1. `customer_deposits.customer_id`
2. **`credit_notes.customer_id`** (missed by the first, code-only grep — found only via
   information_schema).

So this is a small RECURRING CLASS (2 columns), not a one-off. Every other `*_id` is `uuid`
(sales_invoices/receive_payments/sales_orders/quotes customer_id; all vendor_* ; customers.id).

## Full site enumeration (customer_deposits.customer_id)
| site | binds | verdict |
|------|-------|---------|
| sales_invoices.py get_applicable_deposits | `invoice["customer_id"]` (uuid) → cd.customer_id | **WAS THE BUG → FIXED (A1): `str(...)`** |
| receive_payments.py:1037 | `body.customer_id` (str) | already fixed (`FIX_RCV_DEPOSIT_CUSTOMERID`) |
| customers.py:1142 / :1720 | `str(...)` + `::text` | already safe |
| customer_deposits.py 331 / 745 / 2366 | Query/body/path typed `str` | safe |
| customer_deposits.py:211 compute_customer_deposit_balance | untyped param | DEAD (no callers) — flagged, not fixed |

## credit_notes.customer_id — LIVE second instance (OUT OF SCOPE → filed)
`credit_notes.py:255-256` builds `customer_id = $N` and appends **`UUID(customer_id)`** — a UUID
object bound to the VARCHAR column → the SAME 500 as A1, on the credit-notes-by-customer list
filter. NOT on the DP -1..9 path and NOT the Uang Muka module, so per scope it is FILED, not fixed:
see `2026-07-27-credit-notes-customer-id-uuid-varchar.md`. (Its INSERT at :653 already uses
`str(...)`, so only the list filter is affected.)

## Reverse direction (str → uuid) & vendor side
None found on the -1..9 path or Uang Muka module: no `cd.customer_id` JOINs a uuid column, and
asyncpg tolerates str→uuid anyway. Vendor side is all-uuid (vendor_deposits.vendor_id uuid) — no drift.

## Recommendation
Two live sites in this class have now been found (A1 + credit_notes). Patching per-site fixes
INSTANCES; converting the columns fixes the CLASS. See the conversion decision:
`2026-07-27-DECISION-customer-id-varchar-to-uuid.md`.
