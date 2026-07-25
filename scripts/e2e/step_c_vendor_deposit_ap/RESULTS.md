# Step C — compute_ap_outstanding vendor-deposit fix (V218): standalone proof

**DB:** `milkydb_c_test` (clone of LIVE `milkydb` — exact parity, `schema_migrations` 209 pre-existing).
**Migration applied via new pipeline:** `migrate.sh apply` → `newly-applied=1 already-present=209`, tracked `applied_by='runner'` (first real pending migration end-to-end, not backfill).
**Fixtures:** `scripts/e2e/step_c_vendor_deposit_ap/seed.sql` + `verify.sql`.

## The bug
`apply_vendor_deposit` posts `Dr 2-10100 (PAYABLE) / Cr 1-10800`, settling the bill. But `compute_ap_outstanding` built `paid_amount` only from `bill_payment_allocations` + `vendor_credit_applications`. A `DEPOSIT_APPLICATION` journal is in neither → its PAYABLE debit was counted nowhere → **AP overstated by the applied amount** (AR mirror already fixed live: Branch 3 / FIX_P35_ARCANON).

## 5-case regression matrix (before → after V218)

| Case | Bill | Setup | compute_ap BEFORE | compute_ap AFTER | Expected | ✓ |
|------|------|-------|------:|------:|------:|---|
| 1 NULL-handling | BILL-1 | obligation 1.000.000, no deposit | 1.000.000 | 1.000.000 | 1.000.000 (must NOT vanish) | ✓ |
| 2 partial | BILL-2 | oblig 1.000.000, deposit 400.000 | 1.000.000 (bug) | 600.000 | 600.000 | ✓ |
| 3 full | BILL-3 | oblig 1.000.000, deposit 1.000.000 | 1.000.000 (bug) | dropped (0) | 0 → dropped | ✓ |
| 4 all-sources | BILL-4 | oblig 1.000.000, payment 200.000, VC 100.000, deposit 300.000 | 700.000 (bug) | 400.000 | 400.000 | ✓ |
| 5 reversed | BILL-5 | oblig 1.000.000, deposit 500.000 REVERSED (Law-2) | 1.000.000 | 1.000.000 | 1.000.000 | ✓ |

- **Case 1** proves the `LEFT JOIN` + `COALESCE` on every term: the bill with no deposit is neither NULL-ed nor dropped from AP aging (silent-fallback class averted).
- **Case 4** proves **empirically** zero overlap across all four debit sources (obligation/payment/VC/deposit each counted once).
- **Case 5** proves the Law-2 reversal contract: setting `reversed_by_id` on the apply journal removes it from AP.

## Invariant 1 — compute_ap TOTAL vs raw ledger PAYABLE (all POSTED; reversal pairs net)
| | compute_ap total | raw ledger PAYABLE | drift |
|--|------:|------:|------:|
| BEFORE | 4.700.000 | 3.000.000 | **1.700.000** (= 400k+1.000k+300k bug) |
| AFTER  | 3.000.000 | 3.000.000 | **0** |

## Invariant 2 — compute_ap (per bill) vs inline `get_bill_remaining_from_journal`
| Bill | compute_ap | inline | diff |
|------|------:|------:|------:|
| BILL-1 | 1.000.000 | 1.000.000 | 0 |
| BILL-2 | 600.000 | 600.000 | 0 |
| BILL-3 | 0 | 0 | 0 |
| BILL-4 | 400.000 | 400.000 | 0 |
| BILL-5 | 1.000.000 | 500.000 | **500.000** |

**Finding (invariant did its job):** on the reversed case the inline calc `get_bill_remaining_from_journal` (vendor_deposits.py:54-100) disagrees by 500.000 because it filters `je.status='POSTED'` but **omits `je.reversed_by_id IS NULL`** — it keeps counting a reversed settlement. `compute_ap` (with the filter) is correct; the inline calc is wrong on reversal. Dormant today (no vendor-deposit un-apply endpoint) but a landmine once reversal ships. Reinforces the "retire the inline calc, use the canonical function" follow-up.

## UNIQUE precondition (Change 1) — armed
- Pre-check: 0 rows sharing a `journal_id` on either table → safe to ADD.
- `uq_cda_journal_id`, `uq_vda_journal_id` created.
- Negative test: a second `vendor_deposit_applications` row reusing an existing `journal_id` → `ERROR: duplicate key value violates unique constraint "uq_vda_journal_id"`. Fan-out is now structurally impossible, not convention.

## Schema-vs-code drift found while seeding (filed, out of Step-C scope)
- **`apply_vendor_deposit` writes non-existent bill columns**: `UPDATE bills SET paid_amount=..., total_amount=...` — `bills` has `amount_paid` (not `paid_amount`) and **no `total_amount`**. This UPDATE 500s → the endpoint has never successfully run against this schema. Same dead-end family as the customer-side P1 fixes. Vendor-deposit apply needs a column-drift pass before it can be exercised live.

## Provisioning dry-run (FASE-4 preview) — what broke
Seeding a minimal tenant surfaced, in order: (a) accounting tables FK `tenant_id → "Tenant"(id)` for `chart_of_accounts`/`fiscal_periods` (must create Tenant first); (b) `vendor_credits.reason` CHECK enum (`return|pricing_error|discount|damaged|other`); (c) the `bills` column drift above. None block Step C; all are provisioning-path notes for FASE 4.
