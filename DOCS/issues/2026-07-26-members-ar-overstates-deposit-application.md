# BUG: get_ar_balances_by_customer overstates AR by any applied deposit (misses DEPOSIT_APPLICATION)

**Date:** 2026-07-26
**Severity:** MEDIUM-HIGH (customer-facing AR balance is wrong whenever a deposit has been applied)
**Status:** was [INFER] from FASE 1 → now RUNTIME-CONFIRMED FACT (step 6).

## What
Two AR computations diverge after a customer-deposit application:
- `compute_ar_outstanding` (canonical, Branch 3) = **3.500.000** (CORRECT — includes the
  DEPOSIT_APPLICATION credit).
- `get_ar_balances_by_customer` (members.py:31, used by /api/members list & detail) = **5.000.000**
  (WRONG — overstated by the applied 1.500.000).

## Root cause (confirmed via the exact query)
```sql
FROM accounts_receivable ar
JOIN journal_entries je ON je.source_id = ar.source_id
JOIN journal_lines jl ON jl.journal_id = je.id ...
WHERE coa.account_code LIKE '1-104%'
```
`accounts_receivable` holds ONE row (source_type=INVOICE, source_id=INVOICE_id). The apply journal
has **source_id = DEPOSIT_id** (customer_deposits.py:~1412), and the apply path only UPDATEs the
INVOICE's AR row — it never inserts an AR row with source_id=deposit_id. So the join
`je.source_id = ar.source_id` matches only the INVOICE journal (Dr 1-10400 5.000.000) and **never
the DEPOSIT_APPLICATION credit** (Cr 1-10400 1.500.000). Result: 5.000.000, off by the applied
deposit. Verified in DB: accounts_receivable has exactly `INVOICE | amount 5.000.000 | amount_paid
1.500.000 | PARTIAL`; the members query still returns 5.000.000.

## Impact
Any customer-facing AR display backed by `get_ar_balances_by_customer` (members list/detail, and
anything reusing it) **overstates receivables by the total applied deposits** for that customer.
The canonical `compute_ar_outstanding` is correct, so the ledger and aging are fine — but the
member/customer screens disagree with the ledger.

## Fix options
1. Make `get_ar_balances_by_customer` derive from `compute_ar_outstanding` (single source of truth),
   OR
2. Include DEPOSIT_APPLICATION credits: the source_id-join is the wrong spine for netting; count all
   1-104% journal lines for the customer's invoices/deposits (Branch-3-style), not via
   accounts_receivable.source_id.

## Related (minor, same flow)
After DP apply: `sales_invoices.status` stays `'posted'` (the apply path sets `'paid'` only on full
settlement, else keeps prior status), while `accounts_receivable.status` correctly shows `PARTIAL`.
Status-model inconsistency between the two tables (not a ledger error — amount_paid is correct on
both). Note for whoever fixes the display.

---
## SEVERITY NUANCE + cross-reader class (2026-07-26)
- **Real in SQL, HTTP reachability uncertain.** The divergence is proven by running the exact
  `get_ar_balances_by_customer` query (→5.000.000 vs compute_ar 3.500.000). But GET /api/members/list
  returned `{"members":[],"total":0}` for the tenant despite 1 customer existing (the test customer
  has tipe='BADAN'; list_members is `FROM customers WHERE tenant_id` with no default tipe filter, yet
  returned empty — a separate data/filter quirk). So I could NOT reproduce the 5M through the HTTP
  endpoint for this customer. Severity: real bug, but possibly **dormant/limited reachability** —
  parallel to apply_vendor_deposit (correct-in-isolation, unreachable-in-practice). Downgrade from
  "customer-facing wrong number, definitely live" to "confirmed at query level; endpoint exposure
  unverified (members/list returned empty for the test customer)".
- **Cross-reader divergence class — now confirmed on BOTH sides of the balance sheet.**
  AP: 4 readers of remaining (3 copies of get_bill_remaining diverged; compute_ap canonical).
  AR: 3 readers — compute_ar_outstanding (correct, Branch 3) and get_invoice_remaining_from_journal
  (correct, delegates to compute_ar via FIX_P35_ARCANON) vs get_ar_balances_by_customer (WRONG,
  source_id join misses DEPOSIT_APPLICATION). So 1-of-3 AR readers diverges.
- **Proposed (POST-FIX, not a mid-run gate):** a cross-reader invariant
  `compute_ar_outstanding(tenant) total == SUM(get_ar_balances_by_customer(tenant))` in the test
  suite. Do NOT add as a harness gate now — it would fail mid-run on this known bug; add it after the
  fix so it guards against regression.
