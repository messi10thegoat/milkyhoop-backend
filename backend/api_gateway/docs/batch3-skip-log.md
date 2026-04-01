# Batch 3 Skip Log

Semua 29 intent berhasil di-register. Tidak ada yang di-skip.

Note: Quotes module has no void endpoint (only delete for drafts).
void_quote was not included — quotes use delete_quote instead.

## Remaining from Batch 2 Skips (still pending)
- calc_count_items_by_category — needs RANK_GROUP_BY template
- calc_rank_customers_by_sales — needs GROUP BY on invoices
- calc_rank_vendors_by_purchases — needs GROUP BY on bills
- query_sales_invoices_by_customer — backend 500 bug
- query_general_ledger — no endpoint
- query_recurring_bills_due — backend auth bug

Tanggal: 2026-04-01
