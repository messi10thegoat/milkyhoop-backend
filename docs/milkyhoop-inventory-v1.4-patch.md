# milkyhoop-inventory v1.4 Patch Notes

**Version:** 1.4
**Date:** 2026-03-02
**Status:** Ratified
**Companion:** milkyhoop-ironlaws v3.5, milkyhoop-arap v1.0

**Changelog v1.4:** Rule 11 reversal audit results. `record_inventory_reversal()` helper documented. Health check Check 9 + 10 added. Known limitation documented (manual journal reversal).

---

## Rule 11 — Reversal Cascade Audit Results (v1.4)

P4 audit (2026-03-02) menemukan:

| File | Function | Status | Notes |
|------|----------|--------|-------|
| `sales_invoices.py` | `void_invoice()` | ✅ OK | Correct dual-layer reversal |
| `sales_receipts.py` | `void_sales_receipt()` | ✅ OK | Correct dual-layer reversal |
| `stock_adjustments.py` | `void_stock_adjustment()` | ✅ OK | Correct dual-layer reversal |
| `bills_service.py` | `void_bill()` | ✅ FIXED | P4-fix, 7 CRITICAL defects corrected |
| `journals.py` | `reverse_journal()` | ⚠️ KNOWN GAP | Blind to inventory_ledger |

### P4-fix void_bill() Defects Corrected

| # | Defect | Fix |
|---|--------|-----|
| D1 | Advisory lock AFTER reads (race window) | Lock moved to first operation |
| D2 | Separate facade transaction (non-atomic) | Single conn.transaction() |
| D3 | Facade delegation (separate DB connection) | Direct journal line mirroring |
| D4 | Silent exception swallow in inventory | Explicit error propagation |
| D5 | Wrong WAC (purchase_price vs average_cost) | Uses last inventory_ledger snapshot |
| D6 | Wrong journal_id linkage | Links to REVERSAL journal, not original |
| D7 | Wrong source_type for reversal | Uses BILL_VOID, not STOCK_ADJUSTMENT |

### Shared Reversal Helper

`inventory_helpers.py:record_inventory_reversal()` — new shared function (P4-fix).
All void paths that involve inventory SHOULD use this helper.

```python
async def record_inventory_reversal(
    conn,
    tenant_id: str,
    source_type: str,       # e.g. "BILL"
    source_id: UUID,        # original bill/invoice ID
    reversal_journal_id: UUID,
    created_by: Optional[UUID] = None,
    reversal_date=None,     # defaults to today
    notes_prefix: str = "VOID",
) -> list:  # returns list of reversed product_ids
```

**Behavior:**
1. Finds original inventory_ledger entries by `source_type` + `source_id`
2. Creates mirror entries swapping `qty_in` ↔ `qty_out`
3. Uses `source_type = "{original}_VOID"` (e.g. `BILL_VOID`)
4. Links `journal_id` to the reversal journal
5. Snapshots current WAC (no recalc on outbound per Rule 3)
6. Returns list of affected product_ids for downstream processing

### Known Limitation

`journals.py:reverse_journal()` does NOT reverse `inventory_ledger`.
If manual journal touches Persediaan/HPP CoA, reversal creates GL entry but no inventory movement.

**Mitigated by:**
- Check 9 (inventory value drift) catches this in daily health check
- Health check expanded to 12 checks (Check 9: inventory value, Check 10: COGS orphans)
- Manual journal touching inventory CoA is rare and flagged for review

---

## Health Check Integration (v1.4)

| Check | Severity | What It Checks |
|-------|----------|---------------|
| Check 9 | HIGH | GL inventory balance vs inventory_ledger total value |
| Check 10 | HIGH | COGS journals without matching inventory_ledger outbound |

Both run per-tenant in daily health check (`accounting_health_check.sh`).

---

## Invariant Queries (Updated v1.4)

### Inv-1: warehouse_stock = GREATEST(0, SUM(inventory_ledger))

```sql
WITH lb AS (
  SELECT product_id, warehouse_id,
    COALESCE(SUM(quantity_in)-SUM(quantity_out),0) AS computed_qty
  FROM inventory_ledger WHERE warehouse_id IS NOT NULL
  GROUP BY product_id, warehouse_id
)
SELECT p.nama_produk, ws.quantity AS cached, lb.computed_qty AS ledger,
  ws.quantity - GREATEST(0, lb.computed_qty) AS gap
FROM warehouse_stock ws
JOIN products p ON p.id = ws.item_id
LEFT JOIN lb ON lb.product_id = ws.item_id AND lb.warehouse_id = ws.warehouse_id
WHERE ws.quantity != GREATEST(0, COALESCE(lb.computed_qty, 0));
-- Expected: 0 rows
```

### Inv-2: COGS journal = inventory_ledger outbound cost (Check 10)

```sql
SELECT je.journal_number, je.source_type
FROM journal_entries je
WHERE je.source_type IN ('SALES_INVOICE_COGS','SALES_RECEIPT_COGS')
  AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM inventory_ledger il
    WHERE il.source_id = je.source_id AND il.quantity_out > 0
  );
-- Expected: 0 rows
```

### Inv-3: GL inventory balance = inventory_ledger total value (Check 9)

```sql
WITH gl AS (
  SELECT COALESCE(SUM(jl.debit)-SUM(jl.credit),0) AS balance
  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
  JOIN chart_of_accounts coa ON coa.id=jl.account_id
  WHERE coa.account_code='1-10600' AND je.status='POSTED'
    AND je.reversed_by_id IS NULL
), lv AS (
  SELECT COALESCE(
    SUM(CASE WHEN quantity_in > 0 THEN quantity_in * unit_cost ELSE 0 END)
    - SUM(CASE WHEN quantity_out > 0 THEN quantity_out * unit_cost ELSE 0 END),
    0) AS balance
  FROM inventory_ledger
)
SELECT gl.balance AS gl, lv.balance AS ledger, gl.balance - lv.balance AS gap
FROM gl, lv;
-- gap SHOULD be 0
```

---

*Version: 1.4 | Updated: 2026-03-02 | Companion: milkyhoop-ironlaws v3.5, milkyhoop-arap v1.0*
