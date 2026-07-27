# AUDIT: UUID→VARCHAR parameter-bind class (BATCH1 A2)

**Date:** 2026-07-27
**Trigger:** applicable-deposits 500 (A1). Owner asked to grep the whole class, not just the one site.

## The class, precisely
asyncpg accepts a `str` for a `uuid` column parameter (it parses), but REJECTS a `uuid.UUID`
object bound to a `character varying` column ("expected str, got UUID"). So the entire failure
class reduces to: **a UUID object bound to a VARCHAR `*_id` column.**

Schema reality (verified via information_schema): EVERY id / FK column is `uuid` — `customers.id`,
`sales_invoices.customer_id`, `receive_payments.customer_id`, `sales_orders.customer_id`,
`quotes.customer_id`, `vendors.id`, `bills.vendor_id`, `bill_payments_v2.vendor_id`,
`vendor_deposits.vendor_id` — **except `customer_deposits.customer_id`, which is `character
varying`.** That lone drift is the only VARCHAR `*_id` column that ever receives an id value, so it
is the only place this class can bite. (The skill docs claiming customers.id / receive_payments.
customer_id are VARCHAR are STALE for the recovered schema.)

## Full site enumeration (every reference to customer_deposits.customer_id)
| site | binds | verdict |
|------|-------|---------|
| sales_invoices.py get_applicable_deposits (rows query) | `invoice["customer_id"]` (uuid from sales_invoices) → cd.customer_id | **WAS THE BUG → FIXED (A1): now `str(...)`** |
| receive_payments.py:1037 (create, source_type=deposit) | `body.customer_id` (str) | already fixed earlier (`FIX_RCV_DEPOSIT_CUSTOMERID` comment) |
| customers.py:1142 (deposits-with-balance) | `str(customer_id)` + `customer_id::text = $2` | already safe |
| customers.py:1720 (bulk reassign) | `customer_id = $1::text ... = ANY($3::text[])` | already safe |
| customer_deposits.py:331 (list filter) | Query `customer_id: Optional[str]` | safe (str) |
| customer_deposits.py:745 (create INSERT) | `body.customer_id: Optional[str]` | safe (str) |
| customer_deposits.py:2366 (available-for-customer) | path `customer_id: str` | safe (str) |
| customer_deposits.py:211 `compute_customer_deposit_balance(conn, tenant_id, customer_id)` | untyped param | **DEAD CODE — no callers anywhere; latent only.** Left as-is (no live path); flagged here. |

Reverse direction (str → uuid column): none found on the -1..9 path or the Uang Muka module — no
`cd.customer_id` JOINs a uuid column, and asyncpg tolerates str→uuid anyway. Vendor side is
all-uuid (vendor_deposits.vendor_id uuid), no drift.

## Scope decision (per owner)
Fixed the one live on-path/Uang-Muka site (A1). Everything else was already defensive or safe.
The single remaining item is DEAD (`compute_customer_deposit_balance`, no callers) — filed here,
not fixed (no behavior to change). Recommend either deleting the dead helper or `::text`-guarding
it if it is ever revived. Root cleaner fix (out of scope, own decision): migrate
`customer_deposits.customer_id` to `uuid` to remove the drift entirely — but that is DDL and needs
its own migration + backfill, so NOT done here.
