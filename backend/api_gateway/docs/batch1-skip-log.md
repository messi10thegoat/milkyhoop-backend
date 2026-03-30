# Batch 1 Skip Log

### calc_rank_expense_accounts
- **Alasan skip:** RANK template tidak support GROUP BY. Expense list returns individual expenses, bukan per-account aggregation. Perlu RANK_GROUP_BY template type baru.
- **Endpoint yang dicek:** /api/expenses/summary (has top_accounts but not sortable RANK format)
- **Response evidence:** top_accounts[] array exists in summary but RANK template expects flat list with numeric field per item
- **Apa yang dibutuhkan Batch 2:** RANK_GROUP_BY template type atau custom aggregation template
- **Tanggal:** 2026-03-30
