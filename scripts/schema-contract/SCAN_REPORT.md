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

---

# RECLASSIFICATION by path membership (round 2) + key reframe

## Item 1 — bill_payments.py:1983 audit_logs.user_id is OFF the write path
It is the "Activities tab" READ endpoint (SELECT from audit_logs; also ghost `action`,
`description`, `created_at` → real `createdAt`/`userId`). Step 0b (create bill payment)
does NOT depend on it. Not a blocker → the Activities tab itself 500s. Class C.

## Item 2 — hit membership (from committed scan; ZEROs stated explicitly)
- **A. ON E2E PATH (steps 0-9):** bills.py=0, sales_orders.py=0, customer_deposits.py=0,
  sales_invoices.py=0, receive_payments.py=0  → CORE HAPPY PATH IS CLEAN.
  Non-zero on path: quotes.py (step 1) and items.py (step 0 usage). bill_payments.py's
  only hit is the off-write-path Activities read above.
- **B. PROVISIONING:** items.py (step 0), opening_balance.py, transactions.py (best-effort,
  try/except → silent). vendors/customers/warehouses/bank_accounts = 0.
- **C. OFF PATH:** everything else, incl. kernel_document_executor:967 (item create uses
  items.py POST /items, NOT the kernel — confirmed), payment_request_service, F&B vertical.

## KEY REFRAME — the on-path hits are RECOVERY-DRIFT, not code-ghosts
Two distinct classes hide in the 207:
1. **Code-ghost** — code names a column that never existed in any migration (real name differs).
   e.g. journal_entries.entry_date→journal_date, audit_logs.user_id→userId, F&B vertical,
   payment_request_service. Fix = edit code.
2. **Recovery-drift** — code + migrations INTEND the column, but the rebuilt DB lacks it
   (skipped migration or out-of-band ALTER never captured as a VNNN). Fix = migration to ADD.
The two ON-PATH items are class 2:
   - quotes: POST /quotes (quotes.py:502) writes opening_text, closing_text, payment_bank_name,
     payment_account_number, payment_account_holder — 5 columns absent from live `quotes`.
     V186 added the *default* text to accounting_settings but nothing added these to `quotes`.
     → step 1 (Penawaran) 500s on live TODAY.
   - products.sales_tax_id / purchase_tax_id: referenced by items.py search + JOIN tax_codes;
     backing migration V125 is in the 15-SKIP list → columns never created. → item search 500s.
Fix approach for A+B = a VNNN (V219) adding the missing columns IF NOT EXISTS — this IS the
untracked-schema regression root the mission targets, not a code patch.

## Item 5 — 207 is a FLOOR, not a ceiling
54 files under routers/+services/ build SQL dynamically (f-string interpolation, .format(),
string concat). The regex scanner cannot resolve columns inside those → they are NOT covered.
Ghost columns hidden in dynamic SQL are invisible to this pass.

## STRATEGIC — this scan is a DEAD-SURFACE detector
A guaranteed-500 ghost = code that has never executed against this schema. Confirmed never-run:
apply_vendor_deposit, payment_request→journal, the F&B vertical (~70 refs), likely opening_balance.
The apply_vendor_deposit pattern proves the chain: write path dead → zero data → read-path bug
undetected. The F&B vertical likely hides the same.
**F&B vertical decision (like vendor deposit): CONSCIOUS DEFER with written status — either dead
code to delete, or a vertical that will 500 when attempted. Not merely "not urgent."**

---

# COLUMN-LEVEL DIFF: live milkydb vs milkydb_saved_20260725 (authoritative pre-swap oracle)

**Result — the recovery-drift hypothesis is DISPROVEN by the oracle:**
- Columns in SAVED missing from LIVE (would = silently-dropped): **0.**
- Columns in LIVE not in SAVED: **5 — and all 5 are `schema_migrations.*`** (the tracking table
  this mission created). Nothing else differs.
- Therefore **recovery dropped ZERO columns**; live is a column-level superset of saved. Table-level
  parity (271==271) did NOT hide column drift — there is none. The earlier anticipated note
  ("recovery rebuild silently dropped in-use columns") is NOT supported by evidence and is retracted.

**Consequence for the on-path hits — they are CODE-GHOSTS, not recovery-drift:**
- `quotes` opening_text/closing_text/payment_bank_name/payment_account_number/payment_account_holder:
  absent in SAVED too. Never lived. → strip from quotes.py:502 (only quote-CREATE endpoint), do NOT
  add columns. (The passing quote-DP E2E on `saved` did not exercise these columns.)
- `products.sales_tax_id/purchase_tax_id`: absent in SAVED; SAVED has `sales_tax`,`purchase_tax`
  (varchar). GET /items/autocomplete tested on live → ERROR "column p.sales_tax_id does not exist"
  (confirmed 500). Code-ghost: the column model is `sales_tax`(varchar), code still names `sales_tax_id`.
- **V125 contradiction resolved:** V125 (products_tax_indexes) indexes the OLD `sales_tax_id` name;
  the model changed to `sales_tax` varchar, so V125 is correctly DEAD (skip stands). The GHOST is the
  code (items.py), not the migration. No contradiction.

**Implication for build_fresh.sh urgency:** the "rebuild silently drops in-use columns" argument is NOT
evidence-backed (0 dropped). build_fresh-into-repo still matters for the untracked Step-0 stub +
gap_patch schema (schema created outside any VNNN), but NOT because recovery drops columns — it doesn't.

---
## SCANNER EXTENSION PROPOSAL — sub-class: parameter-binding type compatibility (2026-07-26)

Existing scanner classes: (1) missing-column / schema drift (column referenced but absent);
(2) proposed value-domain drift (literal value not in the code's accepted set).

**NEW sub-class — type-compatibility between a query parameter binding and the column's type.**
Motivating live bug: `get_applicable_deposits` binds `customer_id` as a Python `UUID` to a
**VARCHAR** column → asyncpg `TypeError: expected str, got UUID` → endpoint 500s
(sales_invoices.py:3686). The column EXISTS, so an existence-only scanner is blind to it.

Why documentation alone is insufficient: "customer_id is VARCHAR not UUID" is ALREADY a documented
gotcha in the skill docs, and it STILL shipped a 500. A doc is a memory aid; a scanner is a gate.

Proposed check: for each `conn.fetch/execute(sql, *params)`, map each `$n` to its column
(from the SQL) and flag when a param is constructed via `UUID(...)`/`uuid`-typed but the target
column is `text/varchar` (or vice-versa: a str bound to a `uuid` column). Start with the known
hot columns: `customer_id`, `vendor_id`, `receive_payments.customer_id`, any `*_id` that is VARCHAR
in the schema. Even a lint limited to those would have caught this.

Bundle with the value-domain-drift proposal as "scanner v2: existence + value-domain + bind-type".
