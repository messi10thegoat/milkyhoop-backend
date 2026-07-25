# Schema-contract scan — ghost-column references in embedded SQL (one-off)

**Scope:** every `SELECT alias.col`, `WHERE/ORDER/GROUP/HAVING/ON alias.col`, `UPDATE t SET col`,
`INSERT INTO t (cols)`, `RETURNING col` across `routers/` (129) + `services/` (68) files, cross-checked
against `information_schema.columns` (4257 cols, live `milkydb`). Scanner: `schema_scan.py` (committed).
Full raw per-file output: `schema_scan_out.txt` (committed).

**Precision:** high by construction — only reports when the table is unambiguously resolvable
(explicit for UPDATE/INSERT/RETURNING; `FROM/JOIN <realtable> <alias>` for `alias.col`), CTE/subquery
aliases skipped. Calibrated against the DB: every column cluster spot-checked was a real absence.
**Under-reports** (never false-positives) on schema-qualified names, dynamic f-string columns, CTE
columns, and `ON CONFLICT DO UPDATE SET`.

**Result: 209 raw hits → 207 real (2 FPs = `.py` module names: `credit_notes.py:201`, `transactions.py:102`).**
95 on WRITE positions (INSERT/UPDATE), 52 read (rest are dups). 28 files. **No fixes applied — triage list only.**

## Root-cause clusters (same drift repeated)
| Wrong (in code) | Right (in schema) | Where |
|---|---|---|
| `journal_entries.entry_date` / `.posting_date` | `journal_date` | branches, cost_centers, consolidation, kasbank, payment_request_service |
| `journal_entries.reference` / `.memo` / `.source_number` | `description` (+ no such cols) | branches, cost_centers, kasbank, cheques |
| `journal_entries.journal_type` / `.created_by_name` / `.posted_at` / `.posted_by` / `.updated_by` | (do not exist; posting = `status='POSTED'`) | cheques, payment_request_service, branches |
| `journal_lines.journal_entry_id` | `journal_id` | payment_request_service |
| `journal_lines.description` | `memo` | branches, payment_request_service |
| `chart_of_accounts.code` | `account_code` | branches, consolidation |
| `products.name` / `.code` / `.is_active` / `.stock_quantity` / `.unit_cost` / `.content_unit` / `.sales_tax_id` / `.purchase_tax_id` | `nama_produk` / `kode_produk` / `status` / `stok` / (n/a) / (n/a) / `sales_tax` / `purchase_tax` | branches, items, kernel_document_executor, transactions, opening_balance |
| `bank_transactions.account_id` / `.is_credit` / `.reference` / `.source_type` / `.source_id` / `.contact_id` / `.updated_at` | `bank_account_id` / (n/a) / `reference_number` / `source_module`/`origin_type` / `reference_id` / (n/a) / (n/a) | bank_reconciliation, cheques |
| `accounts_receivable/payable.balance` / `.total_amount` / `.created_by` / `.vendor_id` | (do not exist — wrapper schema drift) | opening_balance |
| `audit_logs.user_id` | `userId` (camelCase) | bill_payments |
| `exchange_rates.from_currency_id`/`.to_currency_id`/`.rate_date`/`.source`/`.created_by` | (do not exist) | currencies |

## TIER 1 — shared kernel / core-accounting / DP-flow-reachable (verify first)
- **services/payment_request_service.py:404,430** [WRITE] — journal_entries + journal_lines creation uses 5 ghost cols → payment-request posting 500s. CONFIRMED by reading code. (Also bypasses Law-20 DRAFT→POSTED.)
- **services/kernel_document_executor.py:967** [WRITE] — `INSERT products(... is_active ...)` → product creation via kernel 500s. CONFIRMED. **Potential E2E step-0 blocker if item creation routes through the kernel.**
- **routers/opening_balance.py:805,838,876** [WRITE] — `INSERT accounts_receivable/accounts_payable(balance,total_amount,created_by[,vendor_id])` + `UPDATE products SET stock_quantity` → opening-balance provisioning 500s.
- **routers/bill_payments.py:1983** [read] — `audit_logs.user_id` (→ `userId`).
- **routers/transactions.py:716** [WRITE] — `UPDATE products SET content_unit`.
- **routers/tax_invoices.py:902,926** [read] — `credit_notes.tax_invoice_id`, `vendor_credits.tax_invoice_id`.
- **routers/vendor_deposits.py:718** [WRITE] — `bills.paid_amount` (the known dead vendor-deposit path).
- **routers/unified_chat.py:6280** [WRITE] — `UPDATE reconciliation_sessions SET matched_count` (bot reconcile path).
- **routers/items.py:1684-1688** [read] — `products.sales_tax_id`/`purchase_tax_id`.
- Others in tier: expense_extended (bp.memo, bp.payment_account_id), stock_adjustments, stock_transfers, warehouse_bins (`biN_type` typo), production_costing, recurring_invoices, quotes, reports, device (`bills.bill_date`), credit_notes (tax_invoice_id).

## TIER 2 — peripheral accounting (lower traffic; triage per endpoint)
branches.py (journal_entries/coa/journal_lines drift, heavy), consolidation.py, cost_centers.py,
cheques.py, currencies.py (exchange_rates — whole CRUD drifted), bank_reconciliation.py
(`bank_transactions` INSERT with 6 ghost cols at :2288), kasbank.py.

## TIER 3 — restaurant/F&B vertical (almost certainly DEAD for a konveksi tenant)
kds.py (kds_stations/orders/order_items/alerts), tables.py (table_areas/table_sessions/restaurant_tables),
recipes.py (recipes/recipe_ingredients/recipe_instructions/menu_items/menu_categories). ~70 of the 207.

## Meta
Three instances of this class were previously found by accident (`f.void_reason` via E2E, `bills.paid_amount`
via seeding, inline divergence via case 5). This scan found 207 by looking on purpose — most in features the
DP flow never touches, but TIER 1 includes at least two guaranteed-500 kernel/provisioning paths. **CI check
is the durable fix (proposed, not built): run this scan in CI and fail on TIER-1 regressions.**
