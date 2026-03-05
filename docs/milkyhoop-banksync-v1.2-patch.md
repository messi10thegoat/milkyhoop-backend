# milkyhoop-banksync v1.2 Patch Notes

**Version:** 1.2
**Date:** 2026-03-02
**Status:** Ratified
**Companion:** milkyhoop-ironlaws v3.5

**Changelog v1.2:** Rule 6 bank cache deprecated. `compute_bank_balance()` shared helper. Performance index added.

---

## Rule 6 Update (v1.2) — Bank Balance Cache Deprecated

### What Changed

`bank_accounts.current_balance` is **DEPRECATED** as of v1.2.

| Aspect | Before (v1.1) | After (v1.2) |
|--------|---------------|--------------|
| Read path | `SELECT current_balance FROM bank_accounts` | `compute_bank_balance()` from journal |
| Write path | `UPDATE bank_accounts SET current_balance = ...` | No longer written |
| Column status | Active cache | Retained but ignored |
| Health check | Compare journal vs cache | Compare journal vs bank_transactions |

### Why

BCA account showed 37M gap between cache (63.5M) and journal truth (26M). The cache was proven stale and unreliable. Per Law 1, 16, 21: read paths MUST derive from journal, caches are not sources of truth.

### Performance

Covering index ensures journal-derived balance query runs in < 1ms:

```sql
-- Created in v1.2
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_journal_lines_account_balance
ON journal_lines (account_id)
INCLUDE (debit, credit);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_journal_entries_posted_tenant
ON journal_entries (tenant_id, status)
WHERE status = 'POSTED';
```

Benchmark: 0.287ms for balance query (target was < 50ms).

### Shared Helper

```python
# app/services/bank_helpers.py
async def compute_bank_balance(
    conn, bank_account_id: str, tenant_id: str
) -> Decimal:
    """
    Compute bank balance from journal_lines. Pure ledger, no cache.
    Per Law 1, 16 — this is the ONLY correct way to get bank balance.
    """
    return await conn.fetchval("""
        SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN bank_accounts ba ON ba.coa_id = jl.account_id
        WHERE ba.id = $1
          AND je.status = 'POSTED'
          AND je.tenant_id = $2
    """, bank_account_id, tenant_id)
```

### Migration Path

1. ✅ Performance indexes created
2. ✅ `compute_bank_balance()` helper created
3. All READ paths migrated to journal compute
4. All WRITE paths stopped updating cache
5. Health check updated (journal vs bank_transactions)
6. Column retained for backward compatibility (no DROP)

### Updated Rule 6 — Balance Source of Truth

```sql
-- ✅ Bank balance from ledger (CORRECT)
SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_id
JOIN bank_accounts ba ON ba.coa_id = jl.account_id
WHERE ba.id = $1 AND je.status = 'POSTED' AND je.tenant_id = $2;

-- ❌ FORBIDDEN (DEPRECATED)
SELECT current_balance FROM bank_accounts WHERE id = $1;

-- ❌ FORBIDDEN (never was correct)
SELECT SUM(amount) FROM bank_transactions WHERE bank_account_id = $1;
```

---

## Rule 9 Update (v1.2) — Reconciliation Invariant

Health check Check 3 updated to compare journal_lines vs bank_transactions (not cache):

```sql
-- THE INVARIANT — gap HARUS 0
WITH bank_coa AS (
  SELECT ba.id AS bank_account_id, ba.account_name, ba.coa_id, coa.account_code
  FROM bank_accounts ba
  JOIN chart_of_accounts coa ON coa.id = ba.coa_id
  WHERE ba.tenant_id = $1
), jb AS (
  SELECT bc.bank_account_id,
    COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
  FROM bank_coa bc
  LEFT JOIN journal_lines jl ON jl.account_id = bc.coa_id
  LEFT JOIN journal_entries je ON je.id = jl.journal_id
    AND je.status = 'POSTED'
  GROUP BY bc.bank_account_id
), btb AS (
  SELECT bank_account_id,
    COALESCE(SUM(amount), 0) AS bank_txn_sum
  FROM bank_transactions
  WHERE tenant_id = $1
  GROUP BY bank_account_id
)
SELECT bc.account_name, bc.account_code,
  COALESCE(jb.ledger_balance, 0) AS ledger,
  COALESCE(btb.bank_txn_sum, 0) AS bank_txn,
  COALESCE(jb.ledger_balance, 0) - COALESCE(btb.bank_txn_sum, 0) AS gap
FROM bank_coa bc
LEFT JOIN jb ON jb.bank_account_id = bc.bank_account_id
LEFT JOIN btb ON btb.bank_account_id = bc.bank_account_id
WHERE ABS(COALESCE(jb.ledger_balance, 0) - COALESCE(btb.bank_txn_sum, 0)) > 0.01;
-- ALL rows MUST have gap = 0
```

---

*Version: 1.2 | Updated: 2026-03-02 | Companion: milkyhoop-ironlaws v3.5*
