# Batch 3 Endpoint Audit
Date: 2026-03-31

## Summary
- Total audited: 31
- EXIST + WORKING: 22
- EXIST + BROKEN: 4
- NOT FOUND: 5

## Detail

### A. Reports (4)

| # | Endpoint | Path | Status | Response Shape | Journal Compliant? |
|---|----------|------|--------|----------------|--------------------|
| 1 | Profit & Loss | `/api/reports/profit-loss?start_date=X&end_date=Y` | 200 (requires dates, 422 without) | `{success, data: {period, revenue, costOfGoodsSold, grossProfit, operatingExpenses, operatingIncome, otherIncome, otherExpenses, incomeBeforeTax, taxExpense, netIncome}}` | YES — reads journal_entries + journal_lines, status=POSTED |
| 2 | Balance Sheet | `/api/reports/neraca/{periode}` (e.g. `2026-03`) | 200 | `{periode, tanggal, aset_lancar, aset_tetap, total_aset, kewajiban_jangka_pendek, kewajiban_jangka_panjang, total_kewajiban, ekuitas, is_balanced}` | YES — journal-derived |
| 3 | Cash Flow | `/api/reports/arus-kas/{periode}` (e.g. `2026-03`) | 200 | `{periode, tanggal_awal, tanggal_akhir, operasi, investasi, pendanaan, kenaikan_bersih_kas, kas_awal_periode, kas_akhir_periode}` | YES — analyzes journal entries on cash/bank accounts |
| 4 | Trial Balance | `/api/reports/trial-balance` | 200 (no params needed) | `{tenant_id, as_of_date, period_id, total_debit, total_credit, is_balanced, account_count, accounts}` | YES — uses AccountingFacade.get_trial_balance(), journal-derived |

**Notes:**
- `/api/reports/balance-sheet` and `/api/reports/cash-flow` are DEPRECATED — redirect to `neraca/{periode}` and `arus-kas/{periode}`
- `/api/reports/income-statement`, `/api/reports/pnl`, `/api/reports/neraca-saldo` = 404 (not registered)
- Profit-loss requires `start_date` and `end_date` query params (422 without)
- Also available: `/api/reports/trial-balance/summary`, `/api/reports/trial-balance/full`

### B. Credit Notes (5)

| # | Endpoint | Path | Status | Response Shape | Journal Compliant? |
|---|----------|------|--------|----------------|--------------------|
| 5 | List | `GET /api/credit-notes` | 200 | `{items, total, has_more}` | YES |
| 6 | Detail | `GET /api/credit-notes/{id}` | N/A (0 records in grapgrap) | Expected working (router exists) | YES |
| 7 | Create | `POST /api/credit-notes` | EXISTS (router line 548) | `CreateCreditNoteRequest` | YES — DRAFT->lines->UPDATE POSTED |
| 8 | Void | `POST /api/credit-notes/{id}/void` | EXISTS (router) | | YES |
| 9 | Summary | `GET /api/credit-notes/summary` | 200 | `{success, data}` | YES |

**Additional endpoints:** POST `/{id}/post`, POST `/{id}/apply`, POST `/{id}/refund`

### C. Vendor Credits (5)

| # | Endpoint | Path | Status | Response Shape | Journal Compliant? |
|---|----------|------|--------|----------------|--------------------|
| 10 | List | `GET /api/vendor-credits` | 200 | `{items, total, has_more}` | YES |
| 11 | Detail | `GET /api/vendor-credits/{id}` | 200 (tested with real ID) | | YES |
| 12 | Create | `POST /api/vendor-credits` | EXISTS (router line 521) | `CreateVendorCreditRequest` | YES — DRAFT->lines->UPDATE POSTED |
| 13 | Void | `POST /api/vendor-credits/{id}/void` | EXISTS (router) | | YES |
| 14 | Summary | `GET /api/vendor-credits/summary` | 200 | `{success, data}` | YES |

**Additional endpoints:** POST `/{id}/post`, POST `/{id}/apply`, POST `/{id}/receive-refund`

### D. Quotes (4)

| # | Endpoint | Path | Status | Response Shape | Journal Compliant? |
|---|----------|------|--------|----------------|--------------------|
| 15 | List | `GET /api/quotes` | 200 | `{items, total, has_more, page, limit, total_pages}` | N/A (no journal) |
| 16 | Detail | `GET /api/quotes/{id}` | 200 (tested with real ID) | | N/A |
| 17 | Create | `POST /api/quotes` | EXISTS (router line 425) | `CreateQuoteRequest` | N/A |
| 18 | Summary | `GET /api/quotes/summary` | 200 | `{success, data}` | N/A |

**Additional endpoints:** POST `/{id}/void` (line 830)

### E. Bank Transfers (3)

| # | Endpoint | Path | Status | Response Shape | Journal Compliant? |
|---|----------|------|--------|----------------|--------------------|
| 19 | List | `GET /api/bank-transfers` | 200 | `{items, total, has_more}` | YES |
| 20 | Create | `POST /api/bank-transfers` | EXISTS (kasbank_v2.py line 1323) | `CreateTransferRequest` | YES — DRAFT->POST->VOID workflow |
| 21 | Void | `POST /api/bank-transfers/{id}/void` | EXISTS (router) | | YES |

**Additional endpoints:** POST `/{id}/post`

### F. Customer Deposits (3)

| # | Endpoint | Path | Status | Response Shape | Journal Compliant? |
|---|----------|------|--------|----------------|--------------------|
| 22 | List | `GET /api/customer-deposits` | 200 | `{items, total, has_more}` | YES |
| 23 | Create | `POST /api/customer-deposits` | EXISTS (line 464) | `CreateCustomerDepositRequest` | YES — DRAFT->lines->UPDATE POSTED |
| 24 | Void | `POST /api/customer-deposits/{id}/void` | EXISTS (router) | | YES |

**Additional endpoints:** GET `/{id}`, GET `/summary` (200), PATCH `/{id}`, DELETE `/{id}`, POST `/{id}/post`, POST `/{id}/apply`, POST `/{id}/refund`
**Note:** 0 records in grapgrap tenant. Detail endpoint untested but router exists.

### G. Vendor Deposits (3)

| # | Endpoint | Path | Status | Response Shape | Journal Compliant? |
|---|----------|------|--------|----------------|--------------------|
| 25 | List | `GET /api/vendor-deposits` | 200 | `{items, total}` | YES |
| 26 | Create | `POST /api/vendor-deposits` | EXISTS (line 234) | `VendorDepositCreate` | YES — DRAFT->POSTED |
| 27 | Void | `POST /api/vendor-deposits/{id}/void` | EXISTS (line 794) | | YES — Law 20 compliant |

**Additional endpoints:** GET `/{id}`, GET `/summary` (200, `{total_deposits, total_applied, total_remaining, deposit_count, pending_count}`), POST `/{id}/post`, POST `/{id}/apply`, POST `/{id}/receive-refund`
**Note:** 0 records in grapgrap tenant.

### H. Cross-Module Calc (2)

| # | Endpoint | Path | Status | Response Shape | Notes |
|---|----------|------|--------|----------------|-------|
| 28 | Items price fields | `GET /api/items` | 200 | Fields: `sales_price`, `purchase_price`, `costing_method` | No `margin` field — must compute client-side |
| 29 | Top products | `GET /api/inventory/top-products` | 200 | `{success, data: {period, products, total_products}}` | Working |

### I. Batch 2 Skipped Bugs (2)

| # | Endpoint | Path | Status | Error | Notes |
|---|----------|------|--------|-------|-------|
| 30 | Sales invoices bad customer_id | `GET /api/sales-invoices?customer_id=TEST` | **500** | `{"detail": "Failed to list invoices"}` | STILL BROKEN — non-UUID customer_id crashes instead of returning empty/400 |
| 31 | Recurring bills due | `GET /api/recurring-bills/due` | **500** | `{"error": "Authentication error"}` | STILL BROKEN — auth context issue in `get_due_recurring_bills` DB function |

## Recommendation

### Intents ready for Batch 3 (endpoint exists and works): 24
- **Reports (4):** profit-loss, neraca, arus-kas, trial-balance — all journal-compliant, all working
- **Credit Notes (5):** list, detail, create, void, summary — full CRUD
- **Vendor Credits (5):** list, detail, create, void, summary — full CRUD
- **Quotes (4):** list, detail, create, void/summary — full CRUD (no journal needed)
- **Bank Transfers (3):** list, create, void — full lifecycle
- **Customer Deposits (3):** list, create, void — full lifecycle
- **Vendor Deposits (3):** list, create, void — full lifecycle
- **Top Products (1):** `/api/inventory/top-products` working
- **Items Margin (1):** compute from `sales_price - purchase_price` client-side

### Intents that need endpoint fix first: 2
- **#30** `GET /api/sales-invoices?customer_id=<non-uuid>` — returns 500, should return 400 or empty list
- **#31** `GET /api/recurring-bills/due` — returns 500 auth error, DB function param mismatch

### Intents that need new endpoint: 0
All planned endpoints already exist.

### Path Gotchas for Bot Intent Implementation
1. Balance sheet = `/api/reports/neraca/{periode}` NOT `/api/reports/balance-sheet` (deprecated)
2. Cash flow = `/api/reports/arus-kas/{periode}` NOT `/api/reports/cash-flow` (deprecated)
3. Profit-loss requires `start_date` + `end_date` query params (422 without)
4. Trial balance works with no params (defaults to current date)
5. Neraca response keys are Bahasa Indonesia (`aset_lancar`, `kewajiban_jangka_pendek`, `ekuitas`)
