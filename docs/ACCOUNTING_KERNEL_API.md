# Accounting Kernel API Documentation

> **Version:** 1.0.0
> **Release Date:** 2026-01-27
> **Status:** Production Ready

## Overview

Accounting Kernel (Layer 0) adalah fondasi sistem akuntansi MilkyHoop yang menyediakan API untuk pengelolaan jurnal, buku besar, tahun fiskal, dan periode akuntansi. Layer ini bersifat **system-agnostic** dan dapat digunakan oleh modul lain (Bills, Sales Invoices, dll) maupun untuk entri manual.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    API Gateway (FastAPI)                     │
├─────────────────────────────────────────────────────────────┤
│  /api/journals     │  /api/ledger    │  /api/fiscal-years   │
│  /api/periods      │                 │                       │
├─────────────────────────────────────────────────────────────┤
│                   Accounting Kernel (Layer 0)                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ JournalSvc  │  │ LedgerSvc   │  │ FiscalPeriodSvc     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                      PostgreSQL + RLS                        │
│  journal_entries │ journal_lines │ fiscal_years │ fiscal_   │
│                  │               │              │ periods   │
└─────────────────────────────────────────────────────────────┘
```

## API Endpoints

### 1. Journals (Jurnal Umum)

Base URL: `/api/journals`

#### List Journals
```http
GET /api/journals
Authorization: Bearer {token}
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `period_id` | UUID | Filter by period |
| `start_date` | date | Filter from date (YYYY-MM-DD) |
| `end_date` | date | Filter to date (YYYY-MM-DD) |
| `status` | string | `draft`, `posted`, `reversed` |
| `source_type` | string | `MANUAL`, `bill`, `sales_invoice`, etc. |
| `page` | int | Page number (default: 1) |
| `per_page` | int | Items per page (default: 20, max: 100) |

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "journal_number": "JV-2601-0001",
      "entry_date": "2026-01-15",
      "description": "Manual journal entry",
      "source_type": "MANUAL",
      "total_debit": "1000000.00",
      "total_credit": "1000000.00",
      "status": "posted",
      "created_at": "2026-01-15T10:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 100,
    "total_pages": 5
  }
}
```

#### Get Journal Detail
```http
GET /api/journals/{journal_id}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "journal_number": "JV-2601-0001",
    "entry_date": "2026-01-15",
    "description": "Manual journal entry",
    "source_type": "MANUAL",
    "source_id": null,
    "total_debit": "1000000.00",
    "total_credit": "1000000.00",
    "status": "posted",
    "period_id": "uuid",
    "lines": [
      {
        "id": "uuid",
        "line_number": 1,
        "account_id": "uuid",
        "account_code": "1-10100",
        "account_name": "Kas",
        "memo": "Kas masuk",
        "debit": "1000000.00",
        "credit": "0.00"
      },
      {
        "id": "uuid",
        "line_number": 2,
        "account_id": "uuid",
        "account_code": "4-10100",
        "account_name": "Pendapatan Jasa",
        "memo": "Pendapatan",
        "debit": "0.00",
        "credit": "1000000.00"
      }
    ],
    "created_by": "uuid",
    "created_at": "2026-01-15T10:00:00Z",
    "posted_at": "2026-01-15T10:05:00Z"
  }
}
```

#### Create Journal (Manual Entry)
```http
POST /api/journals
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "entry_date": "2026-01-15",
  "description": "Penjualan tunai",
  "lines": [
    {
      "account_id": "uuid-kas",
      "debit": 1000000,
      "credit": 0,
      "description": "Kas masuk"
    },
    {
      "account_id": "uuid-pendapatan",
      "debit": 0,
      "credit": 1000000,
      "description": "Pendapatan jasa"
    }
  ]
}
```

**Validation Rules:**
- `lines` minimum 2 entries
- Total debit must equal total credit (double-entry)
- Each line must have either debit OR credit (not both)
- `account_id` must be valid UUID from chart of accounts

**Response (201 Created):**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "journal_number": "JV-2601-0001",
    "status": "draft"
  }
}
```

#### Post Journal
```http
POST /api/journals/{journal_id}/post
Authorization: Bearer {token}
```

**Behavior:**
- Changes status from `draft` to `posted`
- Cannot post if period is CLOSED or LOCKED
- Updates `updated_at` timestamp

**Response:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "status": "posted"
  }
}
```

#### Reverse Journal
```http
POST /api/journals/{journal_id}/reverse
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "reversal_date": "2026-01-20",
  "reason": "Koreksi kesalahan input"
}
```

**Behavior:**
- Creates a new journal with debits/credits swapped
- Links reversal to original via `reversal_of_id`
- Updates original with `reversed_by_id`
- Both journals get `reversed` status marker

#### Delete Journal (Draft Only)
```http
DELETE /api/journals/{journal_id}
Authorization: Bearer {token}
```

**Rules:**
- Only `draft` journals can be deleted
- Posted journals must be reversed instead

#### Get Journals by Account
```http
GET /api/journals/by-account/{account_id}
Authorization: Bearer {token}
```

#### Get Journals by Source
```http
GET /api/journals/by-source/{source_type}/{source_id}
Authorization: Bearer {token}
```

---

### 2. Ledger (Buku Besar)

Base URL: `/api/ledger`

#### List All Account Balances
```http
GET /api/ledger
Authorization: Bearer {token}
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `as_of_date` | date | Balance as of date (default: today) |
| `account_type` | string | `ASSET`, `LIABILITY`, `EQUITY`, `REVENUE`, `EXPENSE` |

**Response:**
```json
{
  "success": true,
  "as_of_date": "2026-01-27",
  "data": [
    {
      "id": "uuid",
      "code": "1-10100",
      "name": "Kas",
      "account_type": "ASSET",
      "normal_balance": "DEBIT",
      "debit_balance": "5000000.00",
      "credit_balance": "2000000.00",
      "net_balance": "3000000.00"
    }
  ]
}
```

#### Get Ledger Summary
```http
GET /api/ledger/summary
Authorization: Bearer {token}
```

**Response:**
```json
{
  "success": true,
  "summary": {
    "total_assets": "10000000.00",
    "total_liabilities": "3000000.00",
    "total_equity": "7000000.00",
    "total_revenue": "5000000.00",
    "total_expenses": "2000000.00",
    "net_income": "3000000.00"
  }
}
```

#### Get Account Ledger Detail
```http
GET /api/ledger/{account_id}
Authorization: Bearer {token}
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `start_date` | date | From date |
| `end_date` | date | To date |
| `page` | int | Page number |
| `per_page` | int | Items per page |

**Response:**
```json
{
  "success": true,
  "account": {
    "id": "uuid",
    "code": "1-10100",
    "name": "Kas"
  },
  "opening_balance": "1000000.00",
  "entries": [
    {
      "date": "2026-01-15",
      "journal_number": "JV-2601-0001",
      "description": "Penjualan tunai",
      "debit": "500000.00",
      "credit": "0.00",
      "running_balance": "1500000.00"
    }
  ],
  "closing_balance": "1500000.00"
}
```

#### Get Account Balance
```http
GET /api/ledger/{account_id}/balance
Authorization: Bearer {token}
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `as_of_date` | date | Balance as of date |

---

### 3. Fiscal Years (Tahun Fiskal)

Base URL: `/api/fiscal-years`

#### List Fiscal Years
```http
GET /api/fiscal-years
Authorization: Bearer {token}
```

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "name": "Tahun Fiskal 2026",
      "start_month": 1,
      "start_date": "2026-01-01",
      "end_date": "2026-12-31",
      "status": "open",
      "period_count": 12,
      "closed_periods": 0
    }
  ],
  "total": 1
}
```

#### Get Fiscal Year Detail
```http
GET /api/fiscal-years/{fiscal_year_id}
Authorization: Bearer {token}
```

**Response includes all 12 periods:**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "name": "Tahun Fiskal 2026",
    "start_month": 1,
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
    "status": "open",
    "periods": [
      {
        "id": "uuid",
        "period_number": 1,
        "period_name": "2026-01",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "status": "OPEN"
      }
    ]
  }
}
```

#### Create Fiscal Year
```http
POST /api/fiscal-years
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "name": "Tahun Fiskal 2026",
  "start_month": 1,
  "year": 2026
}
```

**Behavior:**
- Automatically creates 12 monthly periods
- Validates no overlap with existing fiscal years
- `start_month` can be 1-12 (supports non-calendar fiscal years)

**Response (201 Created):**
```json
{
  "success": true,
  "data": {
    "id": "uuid",
    "name": "Tahun Fiskal 2026",
    "periods": [/* 12 periods */]
  }
}
```

#### Close Fiscal Year
```http
POST /api/fiscal-years/{fiscal_year_id}/close
Authorization: Bearer {token}
```

**Requirements:**
- All periods must be closed first
- Cannot be undone

---

### 4. Periods (Periode Akuntansi)

Base URL: `/api/periods`

#### List Periods
```http
GET /api/periods
Authorization: Bearer {token}
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `fiscal_year_id` | UUID | Filter by fiscal year |
| `status` | string | `OPEN`, `CLOSED`, `LOCKED` |

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "period_name": "2026-01",
      "period_number": 1,
      "fiscal_year_name": "Tahun Fiskal 2026",
      "start_date": "2026-01-01",
      "end_date": "2026-01-31",
      "status": "OPEN",
      "journal_count": 15,
      "draft_journal_count": 2
    }
  ],
  "total": 12
}
```

#### Get Current Period
```http
GET /api/periods/current
Authorization: Bearer {token}
```

Returns the currently open period based on today's date.

#### Get Period Detail
```http
GET /api/periods/{period_id}
Authorization: Bearer {token}
```

#### Update Period
```http
PUT /api/periods/{period_id}
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "status": "CLOSED"
}
```

#### Close Period
```http
POST /api/periods/{period_id}/close
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "force": false
}
```

**Behavior:**
- Validates previous period is closed (sequential closing)
- Checks for draft journals (warning or block based on tenant config)
- Generates Trial Balance snapshot on close
- `force: true` bypasses draft journal warning (if tenant config allows)

**Response:**
```json
{
  "success": true,
  "data": {
    "period_id": "uuid",
    "status": "CLOSED",
    "trial_balance_snapshot_id": "uuid",
    "closed_at": "2026-02-01T00:00:00Z"
  }
}
```

#### Reopen Period
```http
POST /api/periods/{period_id}/reopen
Authorization: Bearer {token}
Content-Type: application/json
```

**Request Body:**
```json
{
  "reason": "Koreksi jurnal yang terlewat"
}
```

**Requirements:**
- Only CLOSED periods can be reopened
- LOCKED periods cannot be reopened
- Subsequent periods must be reopened first (reverse sequential)

---

## Database Schema

### Tables Created (V085 Migration)

```sql
-- Fiscal Years
CREATE TABLE fiscal_years (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_month INT NOT NULL DEFAULT 1,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status TEXT DEFAULT 'open',
    closed_at TIMESTAMPTZ,
    closed_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Trial Balance Snapshots
CREATE TABLE trial_balance_snapshots (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    period_id UUID REFERENCES fiscal_periods(id),
    as_of_date DATE NOT NULL,
    snapshot_type TEXT DEFAULT 'closing',
    lines JSONB NOT NULL,
    total_debit DECIMAL(18,2) NOT NULL,
    total_credit DECIMAL(18,2) NOT NULL,
    is_balanced BOOLEAN NOT NULL,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    generated_by UUID
);
```

### Helper Functions

```sql
-- Get current open period for tenant
SELECT * FROM get_current_open_period('tenant_id');

-- Validate period can be closed
SELECT * FROM validate_period_close('tenant_id', 'period_id');

-- Create fiscal year with 12 periods
SELECT create_fiscal_year_with_periods('tenant_id', 'FY 2026', 1, 2026);
```

---

## Error Codes

| HTTP Code | Error | Description |
|-----------|-------|-------------|
| 400 | `INVALID_LINES` | Journal lines invalid (min 2, must balance) |
| 400 | `UNBALANCED_ENTRY` | Debit != Credit |
| 403 | `PERIOD_CLOSED` | Cannot post to closed period |
| 403 | `PERIOD_LOCKED` | Cannot modify locked period |
| 403 | `CANNOT_DELETE_POSTED` | Can only delete draft journals |
| 404 | `JOURNAL_NOT_FOUND` | Journal ID not found |
| 404 | `PERIOD_NOT_FOUND` | Period ID not found |
| 409 | `FISCAL_YEAR_OVERLAP` | Fiscal year overlaps with existing |
| 409 | `PREVIOUS_PERIOD_OPEN` | Must close previous period first |

---

## Tenant Configuration

Configurable via `accounting_settings` table:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `journal_approval_required` | boolean | false | Require approval before posting |
| `strict_period_locking` | boolean | false | Block posting if drafts exist |
| `auto_post_system_journals` | boolean | true | Auto-post system-generated journals |
| `allow_period_reopen` | boolean | true | Allow reopening closed periods |

---

## Implementation Notes

### Files Created

| File | Description |
|------|-------------|
| `backend/migrations/V085__accounting_kernel_api.sql` | Database migration |
| `backend/api_gateway/app/schemas/journals.py` | Pydantic schemas |
| `backend/api_gateway/app/schemas/ledger.py` | Pydantic schemas |
| `backend/api_gateway/app/schemas/fiscal_years.py` | Pydantic schemas |
| `backend/api_gateway/app/schemas/periods.py` | Pydantic schemas |
| `backend/api_gateway/app/routers/journals.py` | API endpoints (8 routes) |
| `backend/api_gateway/app/routers/ledger.py` | API endpoints (4 routes) |
| `backend/api_gateway/app/routers/fiscal_years.py` | API endpoints (4 routes) |
| `backend/api_gateway/app/routers/periods.py` | API endpoints (6 routes) |

### Commits

| Commit | Description |
|--------|-------------|
| `8ea202e5` | Migration V085 |
| `a55345da` | Pydantic schemas (4 files) |
| `7d70fd39` | Fiscal Years router |
| `b0a62d6e` | Periods router |
| `0a69d2b2` | Journals router |
| `713c1023` | Ledger router |
| `5f388585` | Register routers in main.py |
| `bc12e1fd` | Fix column mapping issues |

---

## Testing

### E2E Test Results (2026-01-27)

| Test | Status | Notes |
|------|--------|-------|
| Login | ✅ Pass | Token obtained |
| GET /api/fiscal-years | ✅ Pass | Empty list initially |
| POST /api/fiscal-years | ✅ Pass | Created FY 2027 with 12 periods |
| GET /api/periods | ✅ Pass | Returns period list |
| GET /api/periods/current | ✅ Pass | Returns current open period |
| GET /api/journals | ✅ Pass | Returns journal list |
| POST /api/journals | ✅ Pass | Created JV-2601-0003 |
| GET /api/ledger | ✅ Pass | Returns 29 accounts |

### Sample Test Journal Created

```json
{
  "id": "bb4053f8-b880-47de-8aff-c7f501de69af",
  "journal_number": "JV-2601-0003",
  "description": "Test E2E Jurnal Umum - Accounting Kernel API",
  "total_debit": "750000.00",
  "total_credit": "750000.00",
  "status": "posted"
}
```

---

## Usage Examples

### Create and Post Manual Journal

```bash
# 1. Create journal (draft)
curl -X POST "https://milkyhoop.com/api/journals" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "entry_date": "2026-01-27",
    "description": "Pembayaran sewa kantor",
    "lines": [
      {"account_id": "uuid-biaya-sewa", "debit": 5000000, "credit": 0},
      {"account_id": "uuid-kas", "debit": 0, "credit": 5000000}
    ]
  }'

# 2. Post journal
curl -X POST "https://milkyhoop.com/api/journals/{id}/post" \
  -H "Authorization: Bearer $TOKEN"
```

### Create Fiscal Year

```bash
curl -X POST "https://milkyhoop.com/api/fiscal-years" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tahun Fiskal 2026",
    "start_month": 1,
    "year": 2026
  }'
```

### Close Period with Trial Balance Snapshot

```bash
curl -X POST "https://milkyhoop.com/api/periods/{id}/close" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"force": false}'
```

---

## Related Documentation

- [Design Spec](./plans/2026-01-27-accounting-kernel-api-design.md)
- [Implementation Plan](./plans/2026-01-27-accounting-kernel-api-implementation.md)
- [Chart of Accounts API](./ACCOUNTS_API.md)

---

*Last Updated: 2026-01-27*
*Author: Claude Opus 4.5*
