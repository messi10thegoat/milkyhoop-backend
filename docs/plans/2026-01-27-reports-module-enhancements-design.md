# Reports Module Enhancements Design

**Date:** 2026-01-27
**Status:** Approved

## Overview

Extend the existing reports module (`reports.py`) with 4 new features:
1. Drill-down endpoint for transaction details
2. Export endpoint (PDF, Excel, CSV)
3. Period comparison for P&L and Balance Sheet
4. Configurable aging buckets for AR/AP

---

## 1. Drill-Down Endpoint

### Purpose
When a user clicks on a line item in P&L or Balance Sheet, they can drill into the underlying journal transactions.

### Endpoint
`GET /api/reports/drill-down`

### Query Parameters
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `account_id` | UUID | Yes | Account to drill into |
| `start_date` | date | Yes | Start of period |
| `end_date` | date | Yes | End of period |
| `page` | int | No | Page number (default: 1) |
| `limit` | int | No | Items per page (default: 50) |

### SQL Query
```sql
SELECT
    je.id as journal_id,
    je.journal_number,
    je.entry_date,
    je.source_type,      -- 'invoice', 'bill', 'payment', 'manual'
    je.source_id,        -- Link to original document
    je.description,
    jl.debit,
    jl.credit,
    jl.memo
FROM journal_lines jl
JOIN journal_entries je ON je.id = jl.journal_entry_id
WHERE jl.account_id = $1
  AND je.entry_date BETWEEN $2 AND $3
  AND je.tenant_id = $4
  AND je.status = 'POSTED'
ORDER BY je.entry_date, je.created_at
OFFSET $5 LIMIT $6
```

### Response Schema
```python
class DrillDownTransaction(BaseModel):
    journal_id: UUID
    journal_number: str
    entry_date: date
    source_type: Optional[str]  # invoice, bill, payment, manual
    source_id: Optional[UUID]
    description: Optional[str]
    memo: Optional[str]
    debit: Decimal
    credit: Decimal
    running_balance: Decimal

class DrillDownResponse(BaseModel):
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    normal_balance: str  # DEBIT or CREDIT
    period_start: date
    period_end: date
    opening_balance: Decimal
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    transactions: List[DrillDownTransaction]
    pagination: PaginationMeta
```

### Running Balance Calculation
```python
# Respect account's normal_balance
if normal_balance == 'DEBIT':
    running_balance = opening_balance + debit - credit
else:
    running_balance = opening_balance + credit - debit
```

---

## 2. Export Endpoint

### Purpose
Generate downloadable files (PDF, Excel, CSV) for any report.

### Endpoint
`POST /api/reports/export`

### Request Body
```python
class ExportReportRequest(BaseModel):
    report_type: Literal['profit-loss', 'balance-sheet', 'cash-flow', 'ar-aging', 'ap-aging', 'trial-balance']
    format: Literal['pdf', 'excel', 'csv']
    parameters: dict  # Report-specific params (start_date, end_date, comparison, etc.)
```

### Implementation
- **PDF:** Extend `PDFService` with Jinja2 templates for each report type
- **Excel:** Use `openpyxl` library
- **CSV:** Python's built-in `csv` module

### Response
Direct `StreamingResponse` with appropriate content-type:
- PDF: `application/pdf`
- Excel: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- CSV: `text/csv`

### File Naming
```python
filename = f"{report_type}-{start_date}-{end_date}.{format}"
# Example: profit-loss-2026-01-01-2026-01-31.pdf
```

---

## 3. Period Comparison

### Purpose
Add comparison parameter to P&L and Balance Sheet for period-over-period analysis.

### Affected Endpoints
- `GET /profit-loss/{periode}` - add `comparison` query param
- `GET /balance-sheet/{periode}` - new journal-based endpoint with comparison

### Query Parameter
```
?comparison=previous-year     # Jan 2026 vs Jan 2025 (PSAK compliant, default)
?comparison=previous-period   # Jan 2026 vs Dec 2025 (trend analysis)
?comparison=custom&compare_start=2025-06-01&compare_end=2025-06-30  # Flexible
```

### Date Range Calculation
```python
def get_comparison_dates(start_date, end_date, comparison_type, custom_start=None, custom_end=None):
    if comparison_type == "previous-year":
        # Same period, previous year
        comp_start = start_date.replace(year=start_date.year - 1)
        comp_end = end_date.replace(year=end_date.year - 1)
    elif comparison_type == "previous-period":
        # Immediately prior period of same length
        delta = end_date - start_date
        comp_end = start_date - timedelta(days=1)
        comp_start = comp_end - delta
    elif comparison_type == "custom":
        comp_start = custom_start
        comp_end = custom_end
    return comp_start, comp_end
```

### Extended Response Schema
```python
class ReportLineItemWithComparison(BaseModel):
    account_id: UUID
    account_code: str
    account_name: str
    amount: Decimal
    comparison_amount: Optional[Decimal] = None
    variance: Optional[Decimal] = None           # amount - comparison_amount
    variance_percent: Optional[float] = None     # (variance / comparison_amount) * 100

class ProfitLossWithComparison(BaseModel):
    period: PeriodInfo
    comparison_period: Optional[PeriodInfo] = None
    revenue: SectionWithComparison
    cost_of_goods_sold: SectionWithComparison
    gross_profit: Decimal
    gross_profit_comparison: Optional[Decimal] = None
    gross_profit_variance: Optional[Decimal] = None
    gross_profit_variance_percent: Optional[float] = None
    # ... etc
```

---

## 4. Configurable Aging Buckets

### Purpose
Allow tenants to customize AR/AP aging brackets.

### Database Schema Change
Add to `accounting_settings` table:
```sql
ALTER TABLE accounting_settings
ADD COLUMN aging_brackets_ar JSONB DEFAULT '[0, 30, 60, 90, 120]',
ADD COLUMN aging_brackets_ap JSONB DEFAULT '[0, 30, 60, 90, 120]';
```

The array represents day boundaries: `[0, 30, 60, 90, 120]` creates:
- Current (≤ 0 days overdue)
- 1-30 days
- 31-60 days
- 61-90 days
- 91-120 days
- 120+ days

### Query Parameter Override
```
GET /ar-aging?bucket_days=0,15,30,45,60,90
```
If provided, overrides tenant settings for this request only.

### Dynamic Bucket Generation
```python
def generate_buckets(boundaries: List[int]) -> List[AgingBucket]:
    buckets = []

    # Current bucket (not overdue)
    buckets.append(AgingBucket(
        key="current",
        label="Belum Jatuh Tempo",
        min_days=None,
        max_days=0
    ))

    # Overdue buckets
    for i, boundary in enumerate(boundaries):
        if i == 0:
            continue  # Skip 0, handled by "current"

        prev_boundary = boundaries[i-1]
        buckets.append(AgingBucket(
            key=f"{prev_boundary+1}-{boundary}",
            label=f"{prev_boundary+1}-{boundary} Hari",
            min_days=prev_boundary + 1,
            max_days=boundary
        ))

    # Final bucket (over last boundary)
    last = boundaries[-1]
    buckets.append(AgingBucket(
        key=f"over-{last}",
        label=f">{last} Hari",
        min_days=last + 1,
        max_days=None
    ))

    return buckets
```

### Settings Endpoint Update
Extend `PATCH /accounting-settings` to accept:
```python
class UpdateAccountingSettingsRequest(BaseModel):
    # ... existing fields ...
    aging_brackets_ar: Optional[List[int]] = None  # e.g., [0, 30, 60, 90, 120]
    aging_brackets_ap: Optional[List[int]] = None
```

---

## Implementation Order

1. **Drill-down endpoint** - Independent, can be done first
2. **Period comparison** - Extends existing P&L, needs schema changes
3. **Custom aging buckets** - Needs migration + settings update
4. **Export endpoint** - Depends on all reports being finalized, do last

---

## Files to Modify/Create

| File | Changes |
|------|---------|
| `routers/reports.py` | Add drill-down, export, comparison endpoints |
| `schemas/reports.py` | New response models for comparison, drill-down |
| `schemas/accounting_settings.py` | Add aging_brackets fields |
| `services/pdf_service.py` | Add report templates |
| `services/export_service.py` | New - Excel/CSV generation |
| `templates/pdf/` | New templates for each report type |
| `migrations/V087__aging_brackets_settings.sql` | Add aging_brackets columns |

---

## Dependencies

- `openpyxl` - Excel export (add to requirements.txt)
- `weasyprint` - Already installed for PDF
- `jinja2` - Already installed for templates
