# Accounting Kernel API Design

**Date:** 2026-01-27
**Status:** Approved
**Author:** Claude + Human collaboration

---

## Overview

Design untuk 4 API routers baru yang meng-expose Accounting Kernel ke frontend. Backend sudah memiliki database schema dan service layer yang lengkap, yang missing adalah API endpoint layer.

### Target Segment

UMKM + Professional/Enterprise dengan tenant-configurable behavior.

### Scope

| Router | Endpoints | Purpose |
|--------|-----------|---------|
| `/api/journals` | 8 endpoints | Manual journal CRUD, reversal, queries |
| `/api/ledger` | 4 endpoints | Read-only ledger views & balances |
| `/api/fiscal-years` | 4 endpoints | Fiscal year management |
| `/api/periods` | 6 endpoints | Period management & closing |

---

## Key Design Decisions

### 1. Tenant-Level Configuration

```typescript
interface TenantAccountingSettings {
  // Journal workflow
  journalApprovalRequired: boolean;     // false = Draft→Post, true = Draft→Approve→Post

  // Period control
  strictPeriodLocking: boolean;         // false = warning only, true = hard block
  allowPeriodReopen: boolean;           // Enterprise mungkin mau disable ini

  // Audit
  requireClosingNotes: boolean;         // Wajib isi catatan saat tutup periode
}
```

**Defaults (UMKM-friendly):**
- `journalApprovalRequired: false`
- `strictPeriodLocking: false`
- `allowPeriodReopen: true`
- `requireClosingNotes: false`

### 2. Journal Workflow

**Draft → Post** sebagai default:
- User create journal → status `draft`
- User post journal → status `posted` → masuk ledger
- Professional tenant bisa enable approval: Draft → Approve → Post

### 3. Fiscal Year Structure

**Year + Periods hierarchy** untuk support non-calendar fiscal years:

```typescript
interface FiscalYear {
  id: string;
  name: string;              // "Tahun Buku 2026" atau "FY 2026/2027"
  startMonth: number;        // 1 = Jan (default), 4 = April, 7 = July
  startDate: string;         // Computed: "2026-01-01" atau "2026-04-01"
  endDate: string;           // Computed: "2026-12-31" atau "2027-03-31"
  status: 'open' | 'closed';
  periods: AccountingPeriod[];  // 12 monthly periods
}
```

Indonesian fiscal year reality:
- UMKM: 99% calendar year (Jan-Dec)
- PT/CV: Mostly Jan-Dec, beberapa April-March
- Multinational subsidiary: Ikut parent (bisa April-March, July-June)

### 4. Period Closing

**Warning + force option** untuk flexibility:
- Tampilkan warning jika ada draft journals
- Admin bisa force close dengan audit trail
- Strict mode available untuk enterprise (hard block)

---

## API Endpoints

### Journals Router (`/api/journals`)

| Method | Endpoint | Purpose | Access |
|--------|----------|---------|--------|
| GET | `/` | List journals with filters | User |
| GET | `/:id` | Journal detail + lines | User |
| POST | `/` | Create manual journal (draft/posted) | User |
| POST | `/:id/post` | Post draft journal | User |
| POST | `/:id/reverse` | Create reversal entry | User |
| DELETE | `/:id` | Delete draft only | User |
| GET | `/by-account/:accountId` | Journals for account | User |
| GET | `/by-source/:sourceType/:sourceId` | Journal for transaction | System/User |

**Query Parameters (GET `/`):**
```
?periodId=xxx           # Filter by period
?startDate=2026-01-01   # Filter by date range
?endDate=2026-01-31
?status=posted          # draft | posted | reversed
?sourceType=manual      # Filter by source
?accountId=xxx          # Filter entries containing this account
?search=xxx             # Search description
?page=1&limit=20        # Pagination
```

### Ledger Router (`/api/ledger`)

| Method | Endpoint | Purpose | Access |
|--------|----------|---------|--------|
| GET | `/` | All accounts with balances | User |
| GET | `/:accountId` | Account ledger + postings | User |
| GET | `/:accountId/balance` | Point-in-time balance | User |
| GET | `/summary` | Summary by account type | User |

**Query Parameters (GET `/:accountId`):**
```
?periodId=xxx           # Filter by period
?startDate=2026-01-01   # Filter by date range
?endDate=2026-01-31
?page=1&limit=50        # Pagination
```

### Fiscal Years Router (`/api/fiscal-years`)

| Method | Endpoint | Purpose | Access |
|--------|----------|---------|--------|
| GET | `/` | List all fiscal years | User |
| GET | `/:id` | Year detail + periods | User |
| POST | `/` | Create fiscal year (auto-creates 12 periods) | User |
| POST | `/:id/close` | Close entire year | User |

### Periods Router (`/api/periods`)

| Method | Endpoint | Purpose | Access |
|--------|----------|---------|--------|
| GET | `/` | List all periods | User |
| GET | `/current` | Get current open period | User/System |
| GET | `/:id` | Period detail | User |
| PUT | `/:id` | Update period info | User |
| POST | `/:id/close` | Close period | User |
| POST | `/:id/reopen` | Reopen closed period | User (admin) |

---

## Request/Response Schemas

### Journal Schemas

```typescript
// POST /api/journals - Create
interface CreateJournalRequest {
  entryDate: string;              // "2026-01-15"
  description: string;
  lines: Array<{
    accountId: string;
    description?: string;
    debit: number;                // Either debit OR credit
    credit: number;
  }>;
  saveAsDraft?: boolean;          // Default: false (langsung post)
}

// Response for single journal
interface JournalResponse {
  id: string;
  journalNumber: string;          // "JV-2601-001"
  entryDate: string;
  periodId: string;
  periodName: string;             // "Januari 2026"

  sourceType: 'manual' | 'sales_invoice' | 'purchase_invoice' | 'payment' | 'receipt' | 'expense' | 'adjustment';
  sourceId?: string;
  sourceNumber?: string;          // "INV-001"

  description: string;
  lines: JournalLineResponse[];

  totalDebit: number;
  totalCredit: number;
  isBalanced: boolean;

  status: 'draft' | 'posted' | 'reversed';
  reversalOfId?: string;
  reversedById?: string;

  createdBy: string;
  createdAt: string;
  postedAt?: string;
  postedBy?: string;
}

interface JournalLineResponse {
  id: string;
  lineNumber: number;
  accountId: string;
  accountCode: string;
  accountName: string;
  description?: string;
  debit: number;
  credit: number;
}

// POST /api/journals/:id/reverse
interface ReverseJournalRequest {
  reversalDate: string;
  reason: string;                 // Required for audit
}

// GET /api/journals - List
interface JournalListResponse {
  success: boolean;
  data: {
    journals: JournalResponse[];
    summary: {
      totalCount: number;
      draftCount: number;
      postedCount: number;
      reversedCount: number;
    };
  };
  pagination: {
    page: number;
    limit: number;
    total: number;
    hasMore: boolean;
  };
}
```

### Ledger Schemas

```typescript
// GET /api/ledger/:accountId
interface AccountLedgerResponse {
  success: boolean;
  data: {
    account: {
      id: string;
      code: string;
      name: string;
      accountType: AccountType;
      normalBalance: 'debit' | 'credit';
    };

    openingBalance: number;

    entries: Array<{
      date: string;
      journalNumber: string;
      journalId: string;
      description: string;
      debit: number;
      credit: number;
      runningBalance: number;
      sourceType: string;
      sourceNumber?: string;
    }>;

    totalDebit: number;
    totalCredit: number;
    closingBalance: number;
    netMovement: number;
  };
  pagination: PaginationMeta;
}

// GET /api/ledger/summary
interface LedgerSummaryResponse {
  success: boolean;
  data: {
    byType: Record<AccountType, {
      totalDebit: number;
      totalCredit: number;
      balance: number;
      accountCount: number;
    }>;

    totalAssets: number;
    totalLiabilities: number;
    totalEquity: number;
    totalRevenue: number;
    totalExpenses: number;

    isBalanced: boolean;  // Assets = Liabilities + Equity
  };
}
```

### Period Schemas

```typescript
// POST /api/periods/:id/close
interface ClosePeriodRequest {
  closingNotes?: string;
  force?: boolean;                // Skip draft journal check
}

interface ClosePeriodResponse {
  success: boolean;
  data?: {
    period: AccountingPeriodResponse;
    trialBalanceSnapshot: TrialBalanceSnapshotResponse;
  };
  warnings?: Array<{
    code: 'DRAFT_JOURNALS_EXIST';
    message: string;
    draftJournals: Array<{ id: string; journalNumber: string; }>;
  }>;
  errors?: Array<{
    code: 'PREVIOUS_PERIOD_OPEN' | 'TRIAL_BALANCE_UNBALANCED' | 'PERIOD_ALREADY_CLOSED';
    message: string;
  }>;
}

// POST /api/periods/:id/reopen
interface ReopenPeriodRequest {
  reason: string;                 // Required for audit trail
}

interface AccountingPeriodResponse {
  id: string;
  name: string;                   // "Januari 2026"
  code: string;                   // "2026-01"
  fiscalYearId: string;
  periodNumber: number;           // 1-12
  startDate: string;
  endDate: string;
  status: 'future' | 'open' | 'closing' | 'closed';
  closedAt?: string;
  closedBy?: string;
  closingNotes?: string;
}
```

### Fiscal Year Schemas

```typescript
// POST /api/fiscal-years - Create
interface CreateFiscalYearRequest {
  name: string;                   // "Tahun Buku 2026"
  startMonth?: number;            // Default: 1 (January)
  year: number;                   // 2026
}

interface FiscalYearResponse {
  id: string;
  name: string;
  startMonth: number;
  startDate: string;
  endDate: string;
  status: 'open' | 'closed';
  periods: AccountingPeriodResponse[];
  closedAt?: string;
  closedBy?: string;
}
```

---

## Database Changes

### Migration: V085__accounting_kernel_api.sql

```sql
-- ===========================================
-- 1. FISCAL YEARS TABLE
-- ===========================================
CREATE TABLE fiscal_years (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_month INT NOT NULL DEFAULT 1,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_at TIMESTAMPTZ,
    closed_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_fiscal_year_tenant_dates UNIQUE (tenant_id, start_date),
    CONSTRAINT chk_fiscal_year_status CHECK (status IN ('open', 'closed')),
    CONSTRAINT chk_fiscal_year_start_month CHECK (start_month BETWEEN 1 AND 12)
);

CREATE INDEX idx_fiscal_years_tenant ON fiscal_years(tenant_id);
CREATE INDEX idx_fiscal_years_status ON fiscal_years(tenant_id, status);

-- Enable RLS
ALTER TABLE fiscal_years ENABLE ROW LEVEL SECURITY;
CREATE POLICY fiscal_years_tenant_isolation ON fiscal_years
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ===========================================
-- 2. UPDATE FISCAL_PERIODS (add FK)
-- ===========================================
ALTER TABLE fiscal_periods
    ADD COLUMN IF NOT EXISTS fiscal_year_id UUID REFERENCES fiscal_years(id),
    ADD COLUMN IF NOT EXISTS period_number INT;

CREATE INDEX idx_fiscal_periods_year ON fiscal_periods(fiscal_year_id);

-- ===========================================
-- 3. TRIAL BALANCE SNAPSHOTS
-- ===========================================
CREATE TABLE trial_balance_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    period_id UUID NOT NULL REFERENCES fiscal_periods(id),
    as_of_date DATE NOT NULL,
    snapshot_type TEXT NOT NULL DEFAULT 'closing',

    lines JSONB NOT NULL,
    total_debit DECIMAL(18,2) NOT NULL,
    total_credit DECIMAL(18,2) NOT NULL,
    is_balanced BOOLEAN NOT NULL,

    generated_at TIMESTAMPTZ DEFAULT NOW(),
    generated_by UUID,

    CONSTRAINT chk_tb_snapshot_type CHECK (snapshot_type IN ('working', 'closing', 'adjusted')),
    CONSTRAINT uq_tb_snapshot UNIQUE (tenant_id, period_id, snapshot_type)
);

CREATE INDEX idx_tb_snapshots_tenant ON trial_balance_snapshots(tenant_id);
CREATE INDEX idx_tb_snapshots_period ON trial_balance_snapshots(period_id);

-- Enable RLS
ALTER TABLE trial_balance_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY tb_snapshots_tenant_isolation ON trial_balance_snapshots
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ===========================================
-- 4. UPDATE ACCOUNTING SETTINGS
-- ===========================================
ALTER TABLE accounting_settings
    ADD COLUMN IF NOT EXISTS journal_approval_required BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS strict_period_locking BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS allow_period_reopen BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS require_closing_notes BOOLEAN DEFAULT FALSE;

-- ===========================================
-- 5. HELPER FUNCTIONS
-- ===========================================

-- Get or create fiscal year for a date
CREATE OR REPLACE FUNCTION get_or_create_fiscal_year(
    p_tenant_id TEXT,
    p_date DATE
) RETURNS UUID AS $$
DECLARE
    v_fiscal_year_id UUID;
    v_start_month INT;
    v_year INT;
    v_start_date DATE;
    v_end_date DATE;
BEGIN
    -- Get tenant's fiscal year start month
    SELECT COALESCE(fiscal_year_start_month, 1) INTO v_start_month
    FROM accounting_settings WHERE tenant_id = p_tenant_id;

    IF v_start_month IS NULL THEN
        v_start_month := 1;
    END IF;

    -- Calculate fiscal year dates
    IF EXTRACT(MONTH FROM p_date) >= v_start_month THEN
        v_year := EXTRACT(YEAR FROM p_date);
    ELSE
        v_year := EXTRACT(YEAR FROM p_date) - 1;
    END IF;

    v_start_date := make_date(v_year, v_start_month, 1);
    v_end_date := (v_start_date + INTERVAL '1 year' - INTERVAL '1 day')::DATE;

    -- Find existing
    SELECT id INTO v_fiscal_year_id
    FROM fiscal_years
    WHERE tenant_id = p_tenant_id
      AND start_date = v_start_date;

    IF v_fiscal_year_id IS NULL THEN
        -- Create new fiscal year
        INSERT INTO fiscal_years (tenant_id, name, start_month, start_date, end_date)
        VALUES (
            p_tenant_id,
            'Tahun Buku ' || v_year,
            v_start_month,
            v_start_date,
            v_end_date
        )
        RETURNING id INTO v_fiscal_year_id;

        -- Create 12 periods
        FOR i IN 0..11 LOOP
            INSERT INTO fiscal_periods (
                tenant_id, fiscal_year_id, period_number,
                period_name, start_date, end_date, status
            )
            VALUES (
                p_tenant_id,
                v_fiscal_year_id,
                i + 1,
                TO_CHAR((v_start_date + (i || ' months')::INTERVAL), 'YYYY-MM'),
                (v_start_date + (i || ' months')::INTERVAL)::DATE,
                ((v_start_date + ((i + 1) || ' months')::INTERVAL) - INTERVAL '1 day')::DATE,
                CASE WHEN i = 0 THEN 'OPEN' ELSE 'OPEN' END
            );
        END LOOP;
    END IF;

    RETURN v_fiscal_year_id;
END;
$$ LANGUAGE plpgsql;

-- Check if can close period
CREATE OR REPLACE FUNCTION can_close_period(
    p_tenant_id TEXT,
    p_period_id UUID
) RETURNS TABLE (
    can_close BOOLEAN,
    reason TEXT,
    draft_journal_count INT
) AS $$
DECLARE
    v_period RECORD;
    v_prev_period RECORD;
    v_draft_count INT;
BEGIN
    -- Get period info
    SELECT * INTO v_period
    FROM fiscal_periods
    WHERE id = p_period_id AND tenant_id = p_tenant_id;

    IF v_period IS NULL THEN
        RETURN QUERY SELECT FALSE, 'Period not found'::TEXT, 0;
        RETURN;
    END IF;

    IF v_period.status = 'CLOSED' THEN
        RETURN QUERY SELECT FALSE, 'Period already closed'::TEXT, 0;
        RETURN;
    END IF;

    -- Check previous period
    SELECT * INTO v_prev_period
    FROM fiscal_periods
    WHERE tenant_id = p_tenant_id
      AND end_date < v_period.start_date
    ORDER BY end_date DESC
    LIMIT 1;

    IF v_prev_period IS NOT NULL AND v_prev_period.status != 'CLOSED' THEN
        RETURN QUERY SELECT FALSE, 'Previous period must be closed first'::TEXT, 0;
        RETURN;
    END IF;

    -- Count draft journals
    SELECT COUNT(*) INTO v_draft_count
    FROM journal_entries
    WHERE tenant_id = p_tenant_id
      AND journal_date BETWEEN v_period.start_date AND v_period.end_date
      AND status = 'DRAFT';

    IF v_draft_count > 0 THEN
        RETURN QUERY SELECT TRUE, 'Warning: ' || v_draft_count || ' draft journals exist'::TEXT, v_draft_count;
        RETURN;
    END IF;

    RETURN QUERY SELECT TRUE, 'OK'::TEXT, 0;
END;
$$ LANGUAGE plpgsql;
```

---

## File Structure

### New Files to Create

```
backend/api_gateway/app/
├── routers/
│   ├── journals.py          # ~250 lines
│   ├── ledger.py            # ~200 lines
│   ├── fiscal_years.py      # ~150 lines
│   └── periods.py           # ~200 lines
├── schemas/
│   ├── journals.py          # ~100 lines
│   ├── ledger.py            # ~80 lines
│   ├── fiscal_years.py      # ~50 lines
│   └── periods.py           # ~70 lines

backend/migrations/
└── V085__accounting_kernel_api.sql  # ~150 lines
```

### Files to Modify

```
backend/api_gateway/app/main.py
  - Add: from .routers import journals, ledger, fiscal_years, periods
  - Add: 4 router includes

backend/api_gateway/app/schemas/accounting_settings.py
  - Add: tenant config fields to existing schema
```

---

## Error Handling

### Error Codes

| Error Code | HTTP | Condition |
|------------|------|-----------|
| `JOURNAL_NOT_BALANCED` | 400 | totalDebit ≠ totalCredit |
| `JOURNAL_NOT_FOUND` | 404 | Journal ID doesn't exist |
| `JOURNAL_ALREADY_POSTED` | 409 | Trying to edit/delete posted journal |
| `JOURNAL_ALREADY_REVERSED` | 409 | Trying to reverse already-reversed journal |
| `JOURNAL_IS_DRAFT` | 400 | Trying to reverse draft (must post first) |
| `PERIOD_NOT_FOUND` | 404 | Period ID doesn't exist |
| `PERIOD_ALREADY_CLOSED` | 409 | Trying to close already-closed period |
| `PERIOD_LOCKED` | 403 | Trying to post to locked period |
| `PREVIOUS_PERIOD_OPEN` | 400 | Sequential closing violation |
| `DRAFT_JOURNALS_EXIST` | 400 | Drafts block closing (strict mode) |
| `TRIAL_BALANCE_UNBALANCED` | 500 | Bug indicator - should never happen |
| `REOPEN_NOT_ALLOWED` | 403 | Tenant setting disables reopen |
| `ACCOUNT_NOT_FOUND` | 404 | Account ID doesn't exist |
| `FISCAL_YEAR_OVERLAP` | 400 | New year overlaps existing |
| `APPROVAL_REQUIRED` | 403 | Journal needs approval (if enabled) |

### Edge Cases

| Edge Case | Handling |
|-----------|----------|
| Journal date in closed period | Reject with PERIOD_LOCKED |
| Journal date in future period | Allow (period auto-created if needed) |
| Delete account with journal entries | Reject (handled by existing constraint) |
| Reverse journal in closed period | Create reversal in CURRENT open period |
| Close period with 0 transactions | Allow (empty period is valid) |
| Fiscal year with non-12 periods | Reject (always 12 monthly periods) |
| Concurrent period close requests | Use DB transaction + row lock |
| Journal line with 0 debit & credit | Reject (meaningless line) |

---

## Implementation Order

| Phase | Task | Est. Lines |
|-------|------|------------|
| 1 | Migration V085 | ~150 |
| 2 | Schemas (4 files) | ~300 |
| 3 | Fiscal Years Router | ~150 |
| 4 | Periods Router | ~200 |
| 5 | Journals Router | ~250 |
| 6 | Ledger Router | ~200 |
| 7 | Update main.py | ~20 |
| 8 | E2E Testing | - |

**Total:** ~1,270 lines new code

---

## Testing Strategy

### Unit Tests
- Journal balance validation
- Period closing logic
- Fiscal year date calculations

### Integration Tests
- Create manual journal → verify in ledger
- Close period → verify TB snapshot created
- Reverse journal → verify both entries exist

### E2E Tests (via browser)
- Login → Create Journal → Post → View in Ledger
- Close period flow with warnings
- Tenant settings change behavior

---

## Future Enhancements (Out of Scope)

- Multi-level approval workflow
- Journal templates
- Recurring journals
- Consolidated ledger (multi-entity)
- Journal import from CSV/Excel
