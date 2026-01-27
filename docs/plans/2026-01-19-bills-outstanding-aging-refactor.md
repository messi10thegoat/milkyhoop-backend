# Bills Outstanding Summary - Proper Aging Separation

**Date:** 2026-01-19
**Status:** Implemented
**Breaking Change:** Yes (deploy backend + frontend together)

---

## Problem

Struktur response `/api/bills/outstanding-summary` sebelumnya mencampur aging (jatuh tempo) dengan payment status (partial). Ini tidak proper secara akuntansi dan membingungkan user.

**Before (salah):**
```json
{
  "total_outstanding": 50000000,
  "amounts": {
    "overdue": 20000000,
    "unpaid": 15000000,
    "partial": 15000000  // ← mencampur konsep
  }
}
```

Masalah: `overdue + unpaid + partial != total_outstanding` karena ada overlap.

---

## Solution

Memisahkan aging (waktu) dari payment status dengan struktur yang proper:

**After (proper):**
```json
{
  "total_outstanding": 50000000,
  "by_aging": {
    "overdue": 35000000,   // due_date < TODAY
    "current": 15000000    // due_date >= TODAY (includes NULL)
  },
  "counts": {
    "total": 13,
    "overdue": 8,
    "current": 5,
    "partial": 3,
    "partial_overdue": 2,
    "partial_current": 1,
    "vendors": 6,
    "no_due_date": 0
  },
  "aging_breakdown": {
    "overdue_1_30": 15000000,
    "overdue_31_60": 10000000,
    "overdue_61_90": 5000000,
    "overdue_90_plus": 5000000
  },
  "urgency": {
    "oldest_days": 45,
    "largest_amount": 8000000,
    "due_within_7_days": 3
  }
}
```

---

## Invariants

1. `by_aging.overdue + by_aging.current === total_outstanding`
2. `sum(aging_breakdown.*) === by_aging.overdue`
3. `counts.overdue + counts.current === counts.total`
4. `counts.partial === counts.partial_overdue + counts.partial_current`

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| NULL due_date handling | Treat as "current" | Conservative - doesn't inflate overdue |
| Partial as bucket | Count only, not amount | Partial is payment status, not aging |
| Breaking change | Yes | Cleaner, no deprecated fields |
| Dashboard sync | Same base filter | `status_v2 NOT IN ('void', 'draft')` |

---

## Files Changed

1. `backend/api_gateway/app/schemas/bills.py`
   - Added: `OutstandingByAging`, `OutstandingCounts`, `AgingBreakdown`, `UrgencyMetrics`
   - Added: `OutstandingSummaryData`, `OutstandingSummaryResponse`

2. `backend/api_gateway/app/services/bills_service.py`
   - Refactored: `get_outstanding_summary()` with proper aging query

3. `backend/api_gateway/app/routers/bills.py`
   - Updated: response_model to `OutstandingSummaryResponse`
   - Updated: docstring with new structure

---

## Dashboard Sync

Dashboard `/api/dashboard/summary` hutang uses the same base filter and should return consistent totals:

| Dashboard Field | Bills Outstanding Field |
|-----------------|-------------------------|
| `hutang.total` | `data.total_outstanding` |
| `hutang.current` | `data.by_aging.current` |
| `hutang.overdue_1_30 + 31_60 + 61_90 + 90_plus` | `data.by_aging.overdue` |

---

## Testing Checklist

- [ ] `by_aging.overdue + by_aging.current == total_outstanding`
- [ ] `sum(aging_breakdown.*) == by_aging.overdue`
- [ ] NULL due_date bills counted in `current` and `no_due_date`
- [ ] Partial counts split correctly by aging
- [ ] Dashboard hutang.total == bills outstanding total
- [ ] Void and draft bills excluded
