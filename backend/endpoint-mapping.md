# P1.5 Endpoint Mapping — Financial Field Sources

## Legend
- **J** = Journal-derived (Law 16 compliant)
- **W** = Write-side cache (Law 21 acceptable)
- **S** = Seed/initial value (acceptable)

## Bills / AP

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/bills` | GET | bills_service.py:get_bills() | amount_paid, amount_due | **J** — journal CTE | OK |
| `/api/bills/{id}` | GET | bills_service.py:get_bill() | amount_paid, amount_due | **J** — journal CTE | OK |
| `/api/bills/v2/{id}` | GET | bills_service.py:get_bill_v2() | amount_paid, amount_due | **J** — journal CTE | OK |
| `/api/bills/summary` | GET | bills_service.py:get_summary() | remaining | **J** — bill_ledger CTE | OK |
| `/api/bills/outstanding-summary` | GET | bills_service.py:get_outstanding_summary() | remaining | **J** — bill_ledger CTE | OK |
| `/api/bills/record-payment` | POST | bills_service.py:record_payment() | amount_paid (validation) | **W** — FOR UPDATE in txn | OK |
| `/api/bills/record-payment` | POST | bills_service.py:record_payment() | amount_due (response) | **J** — computed from known values | FIXED P1.5 |
| `/api/bills/{id}` | PUT | bills_service.py:update_bill() | amount_paid (guard) | **W** — check before edit | OK |
| `/api/bills/{id}/void` | POST | bills_service.py:void_bill() | amount_paid (guard) | **W** — check before void | OK |
| `/api/bills` | POST | bills_service.py:create_bill_v2() | amount_paid: 0 | **S** — initial seed | OK |

## Bill Payments

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/bill-payments/summary` | GET | bill_payments.py | total_paid | **J** — SUM(total_amount) from payments table | OK |
| `/api/bill-payments` | POST | bill_payments.py | remaining (validation) | **J** — get_bill_remaining_from_journal() | OK |
| `/api/bill-payments` | POST | bill_payments.py | amount_paid (cache write) | **W** — in txn | OK |
| `/api/bill-payments/{id}/post` | POST | bill_payments.py | amount_paid (cache write) | **W** — in txn | OK |
| `/api/bill-payments/{id}/void` | POST | bill_payments.py | amount_paid (cache reversal) | **W** — in txn | OK |
| `/api/bill-payments/open-bills` | GET | bill_payments.py | paid_amount | **J** — journal_remaining | OK |

## Sales Invoices / AR

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/sales-invoices` | GET | sales_invoices.py:list | amount_paid, amount_due | **J** — journal CTE | OK |
| `/api/sales-invoices/{id}` | GET | sales_invoices.py:detail | amount_paid, amount_due | **J** — journal CTE | OK |
| `/api/sales-invoices/summary` | GET | sales_invoices.py:summary | outstanding | **J** — ar_with_outstanding CTE | OK |
| `/api/sales-invoices/{id}/pdf` | GET | sales_invoices.py:pdf | amount_paid, amount_due | **J** — journal CTE | OK |
| `/api/sales-invoices/{id}/record-payment` | POST | sales_invoices.py | amount_paid (cache write) | **W** — in txn | OK |

## Receive Payments

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/receive-payments` | POST | receive_payments.py | remaining (validation) | **J** — get_invoice_remaining_from_journal() | OK |
| `/api/receive-payments` | POST | receive_payments.py | amount_paid (cache write) | **W** — derived from journal | OK |
| `/api/receive-payments/{id}/void` | POST | receive_payments.py | amount_paid (cache reversal) | **W** — derived from journal | OK |

## Vendors

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/vendors/{id}/balance` | GET | vendors.py | total_paid, outstanding | **J** — full journal CTE | OK |
| `/api/vendors/{id}/open-bills` | GET | vendors.py | paid_amount | **J** — journal-verified BPA | OK |
| `/api/vendors/{id}/opening-balance` | POST | vendors.py | amount_paid: 0 | **S** — initial seed | OK |

## Customers

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/customers` | GET | customers.py:list | ar_balance | **J** — journal CTE | OK |
| `/api/customers/{id}` | GET | customers.py:detail | ar_balance | **J** — journal CTE | OK |
| `/api/customers/search` | GET | customers.py:fuzzy | ar_balance | **J** — LATERAL journal | FIXED P1.5 |
| `/api/customers/{id}/outstanding` | GET | customers.py | remaining, paid_amount | **J** — 4-component journal | OK |

## Credit Notes / Deposits

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/credit-notes/{id}/apply` | POST | credit_notes.py | amount_paid (cache write) | **W** — derived from journal | OK |
| `/api/customer-deposits/{id}/apply` | POST | customer_deposits.py | amount_paid (cache write) | **W** — derived from journal | OK |
| `/api/vendor-credits/{id}/apply` | POST | vendor_credits.py | amount_paid (cache write) | **W** — derived from journal | OK |
| `/api/vendor-deposits/{id}/apply` | POST | vendor_deposits.py | remaining (validation) | **J** — journal helper | FIXED P1.5 |
| `/api/vendor-deposits/{id}/apply` | POST | vendor_deposits.py | amount_paid (cache write) | **W** — in txn | OK |

## Reports

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/reports/timing-differences` | GET | reports.py | paid_amount, balance_due | **J** — AR/AP journal CTEs | OK |
| `/api/reports/ar-aging` | GET | reports.py | amount_paid, balance | **J** — AR journal CTE | OK |
| `/api/reports/ap-aging` | GET | reports.py | amount_paid, balance | **J** — AP journal CTE | OK |

## Recurring Bills

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/recurring-bills/{id}/history` | GET | recurring_bills.py | paid_amount | **J** — inline journal query | FIXED P1.5 |

## Reconciliation Service

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| Reconciliation check | Internal | reconciliation_service.py | bills_outstanding | **J** — journal CTE | FIXED P1.5 |
| Reconciliation check | Internal | reconciliation_service.py | ap_subledger | **J** — journal CTE | FIXED P1.5 |
| Reconciliation check | Internal | reconciliation_service.py | gl_ap_balance | **J** — journal_lines | OK (was already) |

## Bot / Insight

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| Bot bill matching | Internal | tool_executor.py | amount_paid, amount_due | **J** — via /api/bills (journal-backed) | OK |
| Insight narrator | Internal | narrator.py | amount_paid | **J** — via API (dead variable) | OK |
| Insight query | Internal | query_templates.py | amount_due | **J** — via /api/bills (journal-backed) | OK |

## Opening Balance

| Endpoint | Method | File | Field | Source | Status |
|----------|--------|------|-------|--------|--------|
| `/api/opening-balance` | POST | opening_balance.py | amount_paid: 0 | **S** — initial seed | OK |

---

## Summary: P1.5 Fixes Applied

| # | File | Fix | Severity |
|---|------|-----|----------|
| V1a | reconciliation_service.py:72 | bills_outstanding → journal CTE | HIGH |
| V1b | reconciliation_service.py:80 | ap_subledger → journal CTE | HIGH |
| V2 | vendor_deposits.py:491 | validation → get_bill_remaining_from_journal() | MEDIUM |
| V3 | vendor_deposits.py:594 | response → computed from journal-based remaining | MEDIUM |
| V4 | customers.py:~358 | fuzzy search saldo_hutang → LATERAL journal CTE | MEDIUM |
| V5 | recurring_bills.py:~716 | DB function → inline journal query | LOW |
| V6 | bills_service.py:1234 | response → computed from known values | LOW |

**Result: Zero remaining READ path violations. All financial reads derive from journal_lines.**
