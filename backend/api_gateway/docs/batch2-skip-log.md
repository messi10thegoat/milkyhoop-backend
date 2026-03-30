# Batch 2 Skip Log

### calc_count_items_by_category
- **Alasan skip:** Butuh GROUP BY template (group items by category, count per group). SUMMARY_LIST hanya untuk endpoint yang sudah return grouped data. /api/items tidak return per-category counts.
- **Endpoint yang dicek:** /api/items
- **Apa yang dibutuhkan Batch 3:** Backend endpoint /api/items/summary with category breakdown, atau generic RANK_GROUP_BY template type
- **Tanggal:** 2026-03-30

### calc_rank_customers_by_sales
- **Alasan skip:** Butuh GROUP BY template (group invoices by customer, sum amount). /api/sales-invoices tidak return per-customer aggregation.
- **Endpoint yang dicek:** /api/sales-invoices
- **Apa yang dibutuhkan Batch 3:** Backend endpoint /api/sales-invoices/summary with per-customer breakdown, atau RANK_GROUP_BY template
- **Tanggal:** 2026-03-30

### query_sales_invoices_by_customer
- **Alasan skip:** /api/sales-invoices?customer_id={uuid} returns 500 error. Backend bug.
- **Endpoint yang dicek:** /api/sales-invoices?customer_id={uuid}
- **Response evidence:** {"detail":"Failed to list invoices"}
- **Apa yang dibutuhkan Batch 3:** Fix backend bug in sales_invoices.py customer_id filter
- **Tanggal:** 2026-03-30

### calc_rank_vendors_by_purchases
- **Alasan skip:** Same as calc_rank_customers_by_sales. Butuh GROUP BY on bills by vendor.
- **Endpoint yang dicek:** /api/bills
- **Apa yang dibutuhkan Batch 3:** Backend endpoint /api/bills/summary with per-vendor breakdown, atau RANK_GROUP_BY template
- **Tanggal:** 2026-03-30

### query_general_ledger
- **Alasan skip:** No endpoint exists. /api/reports/general-ledger returns 404. /api/general-ledger returns 404.
- **Endpoint yang dicek:** /api/reports/general-ledger, /api/general-ledger
- **Apa yang dibutuhkan Batch 3:** New backend endpoint for general ledger report
- **Tanggal:** 2026-03-30

### query_recurring_bills_due
- **Alasan skip:** /api/recurring-bills/due returns 500 auth error. Backend bug.
- **Endpoint yang dicek:** /api/recurring-bills/due
- **Response evidence:** {"error":"Authentication error"}
- **Apa yang dibutuhkan Batch 3:** Fix auth handling in recurring bills due endpoint
- **Tanggal:** 2026-03-30
