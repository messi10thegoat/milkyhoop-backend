# Orphan PAYMENT_RECEIVED — AR drift class (anthonius-iwan +1,000,000)

- Date: 2026-06-17
- Surfaced by: P3.5 canonical-AR reconciliation invariant (`verify_ar_reconciliation` / Check 14)
- Exemption: `ar_reconciliation_exemptions` tenant `anthonius-iwan` baseline_drift 1,000,000, ticket `P35-ORPHAN-AI`, is_permanent=false (shrinking debt)
- Status: REPORT ONLY — fix is OUT OF P3.5 scope (deliberately not patched here)

## CLASS VERDICT: ONE-OFF LEGACY (closed path) — NOT an open wrapper-bypass

The drift is a single legacy artifact from the deprecated `sales_invoice_payments`
settlement path. No current code path can create new orphans of this class.

## The orphan (exact)

| field | value |
|---|---|
| tenant | anthonius-iwan |
| customer | Inggrid (7097458e-9511-466c-94e3-20dcd61cb244) |
| journal | 3307676f-56fe-4abf-85fe-699db6e4b4c9 (RCV-2603-0001), source_type PAYMENT_RECEIVED |
| journal source_id | f21e5a56-3e76-4905-91fc-3b4e5ad94107 |
| amount | Cr RECEIVABLE 1,000,000 (Dr Bank) |
| invoice | INV-2603-0001 (7a86dfa5...), total 28,560,000 |
| created_at | 2026-03-05 10:25:52 (system's earliest tx date) |

The matching settlement row lives in the LEGACY `sales_invoice_payments` table
(`sales_invoice_payments.id = f21e5a56`, journal_id 3307676f, invoice_id 7a86dfa5,
amount 1,000,000, payment_method 'transfer'). There is NO `receive_payments` row and
NO `receive_payment_allocations` row.

## Why it drifts

- Canonical `compute_ar_outstanding()` Branch-1 keys settlements off
  `receive_payment_allocations`. The legacy payment has no allocation row, so the
  1M settlement is NOT counted -> canonical over-states Inggrid's outstanding by 1M
  (canonical 27,460,000).
- The reconciliation's GL-attribution CTE resolves PAYMENT_RECEIVED -> customer via
  `receive_payments rp WHERE rp.id = je.source_id`. No rp row -> customer_id resolves
  NULL -> the orphan's GL credit line is dropped from the per-customer GL sum, so
  per-customer gl_ar = 28,460,000. drift = gl_ar - canonical = +1,000,000.
- Raw GL (ungrouped) and canonical actually agree at 27,460,000; the drift is an
  attribution artifact of the orphan having no wrapper row to hang a customer on.

## Class scope (whole DB)

- `sales_invoice_payments`: exactly **1 row**, 1 tenant (anthonius-iwan), 1 date (2026-03-05).
- `journal_entries` source_type=PAYMENT_RECEIVED: exactly **1** journal, same tenant/date.
- Modern `RECEIVE_PAYMENT` path: 30 journals, 2026-03-05 -> 2026-06-16, 3 tenants (the active path).

## Is the path OPEN? — NO

- `grep -rn 'INSERT INTO sales_invoice_payments'` over backend = **zero**. All
  remaining references are SELECT/JOIN reads, explicitly commented "deprecated"
  (e.g. sales_invoices.py:561 "Get payments from receive_payments, NOT deprecated
  sales_invoice_payments").
- The only code that POSTS PAYMENT_RECEIVED journals is the `accounting_kernel`
  dormant subsystem: `auto_posting.post_payment_received` /
  `ar_service.apply_payment`, reached only via
  `accounting_kernel/integration/transaction_handler.py`. `TransactionHandler` has
  **zero callers in api_gateway** — the live gateway never invokes it for AR.
- Live receive-payment flow = `api_gateway/app/routers/receive_payments.py`, which
  writes `receive_payments` + `receive_payment_allocations` (modern canonical wrapper,
  INSERT sites lines ~1159/1315). New payments always create allocation rows ->
  Branch-1 counts them -> no new orphans.

Conclusion: closed legacy path; the 1M is a single 2026-03-05 build-era artifact.

## Near-term cleanup recommendation (separate ticket, not P3.5)

Backfill the orphan into the modern wrapper so the exemption can be retired:
1. Create a `receive_payments` row from `sales_invoice_payments` f21e5a56
   (customer Inggrid, total_amount 1,000,000, payment_method bank_transfer,
   link journal_id 3307676f, status posted).
2. Create the matching `receive_payment_allocations` row (invoice 7a86dfa5,
   allocated 1,000,000) so Branch-1 counts the settlement.
3. Re-run `verify_ar_reconciliation('anthonius-iwan')` -> expect drift 0.
4. Delete the `ar_reconciliation_exemptions` row for anthonius-iwan (P35-ORPHAN-AI).

Do NOT post a new journal — journal 3307676f already exists and is balanced; the fix
is wrapper/allocation backfill ONLY (Iron Law: no double-posting). One-off data fix,
guarded by the reconciliation invariant which will go green when complete.

## Optional hardening (low priority)

Consider dropping/locking the legacy `sales_invoice_payments` table once the single
row is migrated, to make the closed-path guarantee structural rather than convention.
