# Accounting Kernel API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose the existing Accounting Kernel (journals, ledger, periods) through REST API endpoints.

**Architecture:** 4 new FastAPI routers that delegate to existing services (JournalService, LedgerService). Database already has core tables; we add fiscal_years and trial_balance_snapshots tables plus tenant config columns.

**Tech Stack:** FastAPI, asyncpg, Pydantic v2, PostgreSQL

**Design Doc:** [2026-01-27-accounting-kernel-api-design.md](./2026-01-27-accounting-kernel-api-design.md)

---

## Task 1: Database Migration V085

**Files:**
- Create: `backend/migrations/V085__accounting_kernel_api.sql`

**Step 1: Create migration file**

```sql
-- ===========================================
-- V085: Accounting Kernel API Support
-- Adds fiscal_years, trial_balance_snapshots, and tenant config
-- ===========================================

-- ===========================================
-- 1. FISCAL YEARS TABLE
-- ===========================================
CREATE TABLE IF NOT EXISTS fiscal_years (
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

CREATE INDEX IF NOT EXISTS idx_fiscal_years_tenant ON fiscal_years(tenant_id);
CREATE INDEX IF NOT EXISTS idx_fiscal_years_status ON fiscal_years(tenant_id, status);

-- Enable RLS
ALTER TABLE fiscal_years ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fiscal_years_tenant_isolation ON fiscal_years;
CREATE POLICY fiscal_years_tenant_isolation ON fiscal_years
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ===========================================
-- 2. UPDATE FISCAL_PERIODS (add FK to fiscal_years)
-- ===========================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'fiscal_periods' AND column_name = 'fiscal_year_id'
    ) THEN
        ALTER TABLE fiscal_periods ADD COLUMN fiscal_year_id UUID REFERENCES fiscal_years(id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'fiscal_periods' AND column_name = 'period_number'
    ) THEN
        ALTER TABLE fiscal_periods ADD COLUMN period_number INT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_fiscal_periods_year ON fiscal_periods(fiscal_year_id);

-- ===========================================
-- 3. TRIAL BALANCE SNAPSHOTS
-- ===========================================
CREATE TABLE IF NOT EXISTS trial_balance_snapshots (
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

    CONSTRAINT chk_tb_snapshot_type CHECK (snapshot_type IN ('working', 'closing', 'adjusted'))
);

-- Unique constraint (use DO block to handle if exists)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_tb_snapshot_period_type'
    ) THEN
        ALTER TABLE trial_balance_snapshots
            ADD CONSTRAINT uq_tb_snapshot_period_type UNIQUE (tenant_id, period_id, snapshot_type);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_tb_snapshots_tenant ON trial_balance_snapshots(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tb_snapshots_period ON trial_balance_snapshots(period_id);

-- Enable RLS
ALTER TABLE trial_balance_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tb_snapshots_tenant_isolation ON trial_balance_snapshots;
CREATE POLICY tb_snapshots_tenant_isolation ON trial_balance_snapshots
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ===========================================
-- 4. UPDATE ACCOUNTING SETTINGS (tenant config)
-- ===========================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'journal_approval_required'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN journal_approval_required BOOLEAN DEFAULT FALSE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'strict_period_locking'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN strict_period_locking BOOLEAN DEFAULT FALSE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'allow_period_reopen'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN allow_period_reopen BOOLEAN DEFAULT TRUE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounting_settings' AND column_name = 'require_closing_notes'
    ) THEN
        ALTER TABLE accounting_settings ADD COLUMN require_closing_notes BOOLEAN DEFAULT FALSE;
    END IF;
END $$;

-- ===========================================
-- 5. HELPER FUNCTIONS
-- ===========================================

-- Get current open period for a tenant
CREATE OR REPLACE FUNCTION get_current_open_period(p_tenant_id TEXT)
RETURNS TABLE (
    id UUID,
    period_name TEXT,
    start_date DATE,
    end_date DATE,
    status TEXT,
    fiscal_year_id UUID
) AS $$
BEGIN
    RETURN QUERY
    SELECT fp.id, fp.period_name, fp.start_date, fp.end_date, fp.status, fp.fiscal_year_id
    FROM fiscal_periods fp
    WHERE fp.tenant_id = p_tenant_id
      AND fp.status = 'OPEN'
      AND CURRENT_DATE BETWEEN fp.start_date AND fp.end_date
    ORDER BY fp.start_date DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

-- Check if can close period (returns validation result)
CREATE OR REPLACE FUNCTION validate_period_close(
    p_tenant_id TEXT,
    p_period_id UUID
) RETURNS TABLE (
    can_close BOOLEAN,
    error_code TEXT,
    error_message TEXT,
    draft_count INT
) AS $$
DECLARE
    v_period RECORD;
    v_prev_period RECORD;
    v_draft_count INT;
    v_strict_mode BOOLEAN;
BEGIN
    -- Get period info
    SELECT * INTO v_period
    FROM fiscal_periods
    WHERE id = p_period_id AND tenant_id = p_tenant_id;

    IF v_period IS NULL THEN
        RETURN QUERY SELECT FALSE, 'PERIOD_NOT_FOUND'::TEXT, 'Period not found'::TEXT, 0;
        RETURN;
    END IF;

    IF v_period.status = 'CLOSED' THEN
        RETURN QUERY SELECT FALSE, 'PERIOD_ALREADY_CLOSED'::TEXT, 'Period is already closed'::TEXT, 0;
        RETURN;
    END IF;

    IF v_period.status = 'LOCKED' THEN
        RETURN QUERY SELECT FALSE, 'PERIOD_LOCKED'::TEXT, 'Period is locked and cannot be modified'::TEXT, 0;
        RETURN;
    END IF;

    -- Check previous period (must be closed for sequential closing)
    SELECT * INTO v_prev_period
    FROM fiscal_periods
    WHERE tenant_id = p_tenant_id
      AND end_date < v_period.start_date
    ORDER BY end_date DESC
    LIMIT 1;

    IF v_prev_period IS NOT NULL AND v_prev_period.status NOT IN ('CLOSED', 'LOCKED') THEN
        RETURN QUERY SELECT FALSE, 'PREVIOUS_PERIOD_OPEN'::TEXT,
            'Previous period (' || v_prev_period.period_name || ') must be closed first'::TEXT, 0;
        RETURN;
    END IF;

    -- Count draft journals in this period
    SELECT COUNT(*) INTO v_draft_count
    FROM journal_entries
    WHERE tenant_id = p_tenant_id
      AND journal_date BETWEEN v_period.start_date AND v_period.end_date
      AND status = 'DRAFT';

    -- Get strict mode setting
    SELECT COALESCE(strict_period_locking, FALSE) INTO v_strict_mode
    FROM accounting_settings
    WHERE tenant_id = p_tenant_id;

    IF v_draft_count > 0 AND v_strict_mode THEN
        RETURN QUERY SELECT FALSE, 'DRAFT_JOURNALS_EXIST'::TEXT,
            v_draft_count || ' draft journal(s) must be posted or deleted before closing'::TEXT, v_draft_count;
        RETURN;
    END IF;

    -- Can close (might have warning if drafts exist but not strict mode)
    IF v_draft_count > 0 THEN
        RETURN QUERY SELECT TRUE, 'WARNING_DRAFT_EXISTS'::TEXT,
            v_draft_count || ' draft journal(s) exist - use force=true to close anyway'::TEXT, v_draft_count;
    ELSE
        RETURN QUERY SELECT TRUE, NULL::TEXT, NULL::TEXT, 0;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

-- Create fiscal year with 12 periods
CREATE OR REPLACE FUNCTION create_fiscal_year_with_periods(
    p_tenant_id TEXT,
    p_name TEXT,
    p_start_month INT,
    p_year INT,
    p_created_by UUID DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    v_fiscal_year_id UUID;
    v_start_date DATE;
    v_end_date DATE;
    v_period_start DATE;
    v_period_end DATE;
    v_period_name TEXT;
    i INT;
BEGIN
    -- Calculate fiscal year dates
    v_start_date := make_date(p_year, p_start_month, 1);
    v_end_date := (v_start_date + INTERVAL '1 year' - INTERVAL '1 day')::DATE;

    -- Check for overlapping fiscal year
    IF EXISTS (
        SELECT 1 FROM fiscal_years
        WHERE tenant_id = p_tenant_id
          AND (
              (v_start_date BETWEEN start_date AND end_date) OR
              (v_end_date BETWEEN start_date AND end_date) OR
              (start_date BETWEEN v_start_date AND v_end_date)
          )
    ) THEN
        RAISE EXCEPTION 'Fiscal year overlaps with existing year';
    END IF;

    -- Create fiscal year
    INSERT INTO fiscal_years (tenant_id, name, start_month, start_date, end_date)
    VALUES (p_tenant_id, p_name, p_start_month, v_start_date, v_end_date)
    RETURNING id INTO v_fiscal_year_id;

    -- Create 12 monthly periods
    FOR i IN 0..11 LOOP
        v_period_start := (v_start_date + (i || ' months')::INTERVAL)::DATE;
        v_period_end := ((v_start_date + ((i + 1) || ' months')::INTERVAL) - INTERVAL '1 day')::DATE;
        v_period_name := TO_CHAR(v_period_start, 'YYYY-MM');

        INSERT INTO fiscal_periods (
            tenant_id, fiscal_year_id, period_number,
            period_name, start_date, end_date, status
        )
        VALUES (
            p_tenant_id,
            v_fiscal_year_id,
            i + 1,
            v_period_name,
            v_period_start,
            v_period_end,
            'OPEN'
        );
    END LOOP;

    RETURN v_fiscal_year_id;
END;
$$ LANGUAGE plpgsql;
```

**Step 2: Verify migration syntax**

Run: `cd backend && cat migrations/V085__accounting_kernel_api.sql | head -20`
Expected: First 20 lines of migration displayed

**Step 3: Commit migration**

```bash
git add backend/migrations/V085__accounting_kernel_api.sql
git commit -m "feat(accounting): add migration V085 for accounting kernel API

- Add fiscal_years table with RLS
- Add trial_balance_snapshots table for period closing
- Add tenant config columns to accounting_settings
- Add helper functions for period validation and fiscal year creation

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Schema Files

**Files:**
- Create: `backend/api_gateway/app/schemas/journals.py`
- Create: `backend/api_gateway/app/schemas/ledger.py`
- Create: `backend/api_gateway/app/schemas/fiscal_years.py`
- Create: `backend/api_gateway/app/schemas/periods.py`

### Step 1: Create journals schema

```python
"""
Pydantic schemas for Journal Entry module.

Request and response models for /api/journals endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import date, datetime
from decimal import Decimal


# =============================================================================
# CONSTANTS
# =============================================================================

JOURNAL_STATUSES = ["draft", "posted", "reversed"]
SOURCE_TYPES = [
    "manual", "sales_invoice", "purchase_invoice",
    "payment_received", "payment_made", "expense",
    "adjustment", "opening", "closing"
]


# =============================================================================
# REQUEST MODELS
# =============================================================================

class JournalLineInput(BaseModel):
    """Single line item for journal entry."""
    account_id: str = Field(..., description="Account UUID")
    description: Optional[str] = Field(None, max_length=500)
    debit: Decimal = Field(default=Decimal("0"), ge=0)
    credit: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator('debit', 'credit', mode='before')
    @classmethod
    def coerce_decimal(cls, v):
        if v is None:
            return Decimal("0")
        return Decimal(str(v))


class CreateJournalRequest(BaseModel):
    """Request body for creating a manual journal entry."""
    entry_date: date = Field(..., description="Journal date")
    description: str = Field(..., min_length=1, max_length=500)
    lines: List[JournalLineInput] = Field(..., min_length=2)
    save_as_draft: bool = Field(default=False, description="If true, save as draft instead of posting")

    @field_validator('lines')
    @classmethod
    def validate_lines(cls, v):
        if len(v) < 2:
            raise ValueError('Journal must have at least 2 lines')

        total_debit = sum(line.debit for line in v)
        total_credit = sum(line.credit for line in v)

        if total_debit != total_credit:
            raise ValueError(f'Journal not balanced: debit={total_debit}, credit={total_credit}')

        if total_debit == 0:
            raise ValueError('Journal cannot have zero total')

        # Each line must have either debit or credit (not both, not neither)
        for i, line in enumerate(v):
            if line.debit > 0 and line.credit > 0:
                raise ValueError(f'Line {i+1}: cannot have both debit and credit')
            if line.debit == 0 and line.credit == 0:
                raise ValueError(f'Line {i+1}: must have either debit or credit')

        return v


class ReverseJournalRequest(BaseModel):
    """Request body for reversing a journal entry."""
    reversal_date: date = Field(..., description="Date for the reversal entry")
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for reversal")


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class JournalLineResponse(BaseModel):
    """Single line in journal response."""
    id: str
    line_number: int
    account_id: str
    account_code: str
    account_name: str
    description: Optional[str] = None
    debit: Decimal
    credit: Decimal


class JournalResponse(BaseModel):
    """Response for single journal entry."""
    id: str
    journal_number: str
    entry_date: date
    period_id: Optional[str] = None
    period_name: Optional[str] = None

    source_type: str
    source_id: Optional[str] = None
    source_number: Optional[str] = None

    description: str
    lines: List[JournalLineResponse]

    total_debit: Decimal
    total_credit: Decimal
    is_balanced: bool

    status: str
    reversal_of_id: Optional[str] = None
    reversed_by_id: Optional[str] = None

    created_by: Optional[str] = None
    created_at: datetime
    posted_at: Optional[datetime] = None
    posted_by: Optional[str] = None


class JournalListItem(BaseModel):
    """Simplified journal for list view."""
    id: str
    journal_number: str
    entry_date: date
    description: str
    source_type: str
    source_number: Optional[str] = None
    total_debit: Decimal
    total_credit: Decimal
    status: str
    created_at: datetime


class JournalSummary(BaseModel):
    """Summary statistics for journal list."""
    total_count: int
    draft_count: int
    posted_count: int
    reversed_count: int


class JournalListResponse(BaseModel):
    """Response for list journals endpoint."""
    success: bool = True
    data: List[JournalListItem]
    summary: JournalSummary
    pagination: dict
```

### Step 2: Create ledger schema

```python
"""
Pydantic schemas for General Ledger module.

Response models for /api/ledger endpoints (read-only).
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import date, datetime
from decimal import Decimal


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class LedgerEntryResponse(BaseModel):
    """Single entry in account ledger."""
    date: date
    journal_number: str
    journal_id: str
    description: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    source_type: str
    source_number: Optional[str] = None


class AccountInfoResponse(BaseModel):
    """Account information for ledger header."""
    id: str
    code: str
    name: str
    account_type: str
    normal_balance: str


class AccountLedgerResponse(BaseModel):
    """Response for single account ledger view."""
    success: bool = True
    data: dict  # Contains account, entries, totals


class AccountLedgerData(BaseModel):
    """Data structure for account ledger."""
    account: AccountInfoResponse
    opening_balance: Decimal
    entries: List[LedgerEntryResponse]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    net_movement: Decimal


class AccountBalanceResponse(BaseModel):
    """Response for account balance query."""
    success: bool = True
    data: dict


class AccountBalanceData(BaseModel):
    """Account balance data."""
    account_id: str
    account_code: str
    account_name: str
    as_of_date: date
    debit_balance: Decimal
    credit_balance: Decimal
    net_balance: Decimal


class LedgerAccountSummary(BaseModel):
    """Summary for single account in ledger list."""
    id: str
    code: str
    name: str
    account_type: str
    normal_balance: str
    debit_balance: Decimal
    credit_balance: Decimal
    net_balance: Decimal


class LedgerListResponse(BaseModel):
    """Response for ledger list (all accounts with balances)."""
    success: bool = True
    data: List[LedgerAccountSummary]
    as_of_date: date


class TypeSummary(BaseModel):
    """Summary for account type."""
    total_debit: Decimal
    total_credit: Decimal
    balance: Decimal
    account_count: int


class LedgerSummaryResponse(BaseModel):
    """Response for ledger summary by account type."""
    success: bool = True
    data: dict


class LedgerSummaryData(BaseModel):
    """Ledger summary data structure."""
    by_type: Dict[str, TypeSummary]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    total_revenue: Decimal
    total_expenses: Decimal
    is_balanced: bool
```

### Step 3: Create fiscal_years schema

```python
"""
Pydantic schemas for Fiscal Year module.

Request and response models for /api/fiscal-years endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date, datetime


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateFiscalYearRequest(BaseModel):
    """Request body for creating a fiscal year."""
    name: str = Field(..., min_length=1, max_length=100, description="e.g., 'Tahun Buku 2026'")
    year: int = Field(..., ge=2000, le=2100, description="The calendar year")
    start_month: int = Field(default=1, ge=1, le=12, description="1=Jan, 4=Apr, 7=Jul")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Fiscal year name is required')
        return v.strip()


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class PeriodSummary(BaseModel):
    """Brief period info for fiscal year response."""
    id: str
    period_number: int
    period_name: str
    start_date: date
    end_date: date
    status: str


class FiscalYearResponse(BaseModel):
    """Response for single fiscal year."""
    id: str
    name: str
    start_month: int
    start_date: date
    end_date: date
    status: str
    periods: List[PeriodSummary]
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    created_at: datetime


class FiscalYearListItem(BaseModel):
    """Fiscal year item for list view."""
    id: str
    name: str
    start_date: date
    end_date: date
    status: str
    period_count: int = 12
    open_period_count: int
    closed_period_count: int
    created_at: datetime


class FiscalYearListResponse(BaseModel):
    """Response for list fiscal years."""
    success: bool = True
    data: List[FiscalYearListItem]
    total: int
```

### Step 4: Create periods schema

```python
"""
Pydantic schemas for Accounting Period module.

Request and response models for /api/periods endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import date, datetime


# =============================================================================
# REQUEST MODELS
# =============================================================================

class UpdatePeriodRequest(BaseModel):
    """Request body for updating period info."""
    name: Optional[str] = Field(None, max_length=50)


class ClosePeriodRequest(BaseModel):
    """Request body for closing a period."""
    closing_notes: Optional[str] = Field(None, max_length=1000)
    force: bool = Field(default=False, description="Force close even with draft journals")


class ReopenPeriodRequest(BaseModel):
    """Request body for reopening a period."""
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for reopening")

    @field_validator('reason')
    @classmethod
    def validate_reason(cls, v):
        if not v or not v.strip():
            raise ValueError('Reason is required for reopening a period')
        return v.strip()


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class PeriodResponse(BaseModel):
    """Response for single accounting period."""
    id: str
    period_name: str
    period_number: Optional[int] = None
    fiscal_year_id: Optional[str] = None
    fiscal_year_name: Optional[str] = None
    start_date: date
    end_date: date
    status: str
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    closing_notes: Optional[str] = None


class PeriodListItem(BaseModel):
    """Period item for list view."""
    id: str
    period_name: str
    period_number: Optional[int] = None
    fiscal_year_name: Optional[str] = None
    start_date: date
    end_date: date
    status: str
    journal_count: int = 0
    draft_journal_count: int = 0


class PeriodListResponse(BaseModel):
    """Response for list periods."""
    success: bool = True
    data: List[PeriodListItem]
    total: int


class DraftJournalInfo(BaseModel):
    """Brief info about draft journal."""
    id: str
    journal_number: str
    description: str
    entry_date: date


class ClosePeriodWarning(BaseModel):
    """Warning during period close."""
    code: str
    message: str
    draft_journals: List[DraftJournalInfo] = []


class ClosePeriodError(BaseModel):
    """Error during period close."""
    code: str
    message: str


class TrialBalanceSnapshotResponse(BaseModel):
    """Trial balance snapshot created during period close."""
    id: str
    as_of_date: date
    total_debit: float
    total_credit: float
    is_balanced: bool
    generated_at: datetime


class ClosePeriodResponse(BaseModel):
    """Response for close period endpoint."""
    success: bool
    data: Optional[dict] = None
    warnings: List[ClosePeriodWarning] = []
    errors: List[ClosePeriodError] = []
```

### Step 5: Commit schema files

```bash
git add backend/api_gateway/app/schemas/journals.py \
        backend/api_gateway/app/schemas/ledger.py \
        backend/api_gateway/app/schemas/fiscal_years.py \
        backend/api_gateway/app/schemas/periods.py
git commit -m "feat(accounting): add Pydantic schemas for accounting kernel API

- journals.py: Create/reverse request, list/detail responses
- ledger.py: Account ledger and summary responses
- fiscal_years.py: Create request, year with periods response
- periods.py: Close/reopen requests, validation responses

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Fiscal Years Router

**Files:**
- Create: `backend/api_gateway/app/routers/fiscal_years.py`

### Step 1: Create fiscal years router

```python
"""
Fiscal Years Router - Fiscal Year Management

CRUD endpoints for managing fiscal years and auto-creating periods.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg

from ..schemas.fiscal_years import (
    CreateFiscalYearRequest,
    FiscalYearResponse,
    FiscalYearListItem,
    FiscalYearListResponse,
    PeriodSummary,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None
    }


# =============================================================================
# LIST FISCAL YEARS
# =============================================================================
@router.get("", response_model=FiscalYearListResponse)
async def list_fiscal_years(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status: open, closed"),
):
    """List all fiscal years for the tenant."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["fy.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status:
                conditions.append(f"fy.status = ${param_idx}")
                params.append(status)

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT
                    fy.id, fy.name, fy.start_date, fy.end_date, fy.status, fy.created_at,
                    COUNT(fp.id) as period_count,
                    COUNT(fp.id) FILTER (WHERE fp.status = 'OPEN') as open_period_count,
                    COUNT(fp.id) FILTER (WHERE fp.status IN ('CLOSED', 'LOCKED')) as closed_period_count
                FROM fiscal_years fy
                LEFT JOIN fiscal_periods fp ON fp.fiscal_year_id = fy.id
                WHERE {where_clause}
                GROUP BY fy.id
                ORDER BY fy.start_date DESC
            """

            rows = await conn.fetch(query, *params)

            items = [
                FiscalYearListItem(
                    id=str(row["id"]),
                    name=row["name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                    period_count=row["period_count"] or 0,
                    open_period_count=row["open_period_count"] or 0,
                    closed_period_count=row["closed_period_count"] or 0,
                    created_at=row["created_at"],
                )
                for row in rows
            ]

            return FiscalYearListResponse(data=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List fiscal years error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list fiscal years")


# =============================================================================
# GET FISCAL YEAR DETAIL
# =============================================================================
@router.get("/{fiscal_year_id}", response_model=dict)
async def get_fiscal_year(request: Request, fiscal_year_id: UUID):
    """Get fiscal year detail with all periods."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get fiscal year
            fy_row = await conn.fetchrow("""
                SELECT id, name, start_month, start_date, end_date, status,
                       closed_at, closed_by, created_at
                FROM fiscal_years
                WHERE id = $1 AND tenant_id = $2
            """, fiscal_year_id, ctx["tenant_id"])

            if not fy_row:
                raise HTTPException(status_code=404, detail="Fiscal year not found")

            # Get periods
            period_rows = await conn.fetch("""
                SELECT id, period_number, period_name, start_date, end_date, status
                FROM fiscal_periods
                WHERE fiscal_year_id = $1 AND tenant_id = $2
                ORDER BY period_number
            """, fiscal_year_id, ctx["tenant_id"])

            periods = [
                PeriodSummary(
                    id=str(row["id"]),
                    period_number=row["period_number"] or 0,
                    period_name=row["period_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                )
                for row in period_rows
            ]

            return {
                "success": True,
                "data": FiscalYearResponse(
                    id=str(fy_row["id"]),
                    name=fy_row["name"],
                    start_month=fy_row["start_month"],
                    start_date=fy_row["start_date"],
                    end_date=fy_row["end_date"],
                    status=fy_row["status"],
                    periods=periods,
                    closed_at=fy_row["closed_at"],
                    closed_by=str(fy_row["closed_by"]) if fy_row["closed_by"] else None,
                    created_at=fy_row["created_at"],
                )
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get fiscal year error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get fiscal year")


# =============================================================================
# CREATE FISCAL YEAR
# =============================================================================
@router.post("", response_model=dict, status_code=201)
async def create_fiscal_year(request: Request, body: CreateFiscalYearRequest):
    """Create a new fiscal year with 12 monthly periods."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Use the helper function to create fiscal year with periods
            try:
                fiscal_year_id = await conn.fetchval("""
                    SELECT create_fiscal_year_with_periods($1, $2, $3, $4, $5)
                """, ctx["tenant_id"], body.name, body.start_month, body.year, ctx["user_id"])
            except asyncpg.RaiseError as e:
                if "overlaps" in str(e).lower():
                    raise HTTPException(
                        status_code=400,
                        detail="Fiscal year overlaps with existing year"
                    )
                raise

            # Fetch the created fiscal year
            return await get_fiscal_year(request, fiscal_year_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create fiscal year error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create fiscal year")


# =============================================================================
# CLOSE FISCAL YEAR
# =============================================================================
@router.post("/{fiscal_year_id}/close", response_model=dict)
async def close_fiscal_year(request: Request, fiscal_year_id: UUID):
    """Close entire fiscal year (all periods must be closed first)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check fiscal year exists
            fy_row = await conn.fetchrow("""
                SELECT id, status FROM fiscal_years
                WHERE id = $1 AND tenant_id = $2
            """, fiscal_year_id, ctx["tenant_id"])

            if not fy_row:
                raise HTTPException(status_code=404, detail="Fiscal year not found")

            if fy_row["status"] == "closed":
                raise HTTPException(status_code=409, detail="Fiscal year is already closed")

            # Check all periods are closed
            open_count = await conn.fetchval("""
                SELECT COUNT(*) FROM fiscal_periods
                WHERE fiscal_year_id = $1 AND tenant_id = $2 AND status NOT IN ('CLOSED', 'LOCKED')
            """, fiscal_year_id, ctx["tenant_id"])

            if open_count > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot close fiscal year: {open_count} period(s) are still open"
                )

            # Close fiscal year
            await conn.execute("""
                UPDATE fiscal_years
                SET status = 'closed', closed_at = NOW(), closed_by = $3
                WHERE id = $1 AND tenant_id = $2
            """, fiscal_year_id, ctx["tenant_id"], ctx["user_id"])

            return await get_fiscal_year(request, fiscal_year_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Close fiscal year error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close fiscal year")
```

### Step 2: Commit fiscal years router

```bash
git add backend/api_gateway/app/routers/fiscal_years.py
git commit -m "feat(accounting): add fiscal years router

Endpoints:
- GET /api/fiscal-years - list all fiscal years
- GET /api/fiscal-years/:id - get detail with periods
- POST /api/fiscal-years - create with auto 12 periods
- POST /api/fiscal-years/:id/close - close entire year

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Periods Router

**Files:**
- Create: `backend/api_gateway/app/routers/periods.py`

### Step 1: Create periods router

```python
"""
Accounting Periods Router - Period Management

Endpoints for managing accounting periods including close/reopen operations.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg
from datetime import date

from ..schemas.periods import (
    UpdatePeriodRequest,
    ClosePeriodRequest,
    ReopenPeriodRequest,
    PeriodResponse,
    PeriodListItem,
    PeriodListResponse,
    ClosePeriodResponse,
    ClosePeriodWarning,
    ClosePeriodError,
    DraftJournalInfo,
    TrialBalanceSnapshotResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None
    }


# =============================================================================
# LIST PERIODS
# =============================================================================
@router.get("", response_model=PeriodListResponse)
async def list_periods(
    request: Request,
    fiscal_year_id: Optional[UUID] = Query(None, description="Filter by fiscal year"),
    status: Optional[str] = Query(None, description="Filter by status: OPEN, CLOSED, LOCKED"),
    year: Optional[int] = Query(None, description="Filter by calendar year"),
):
    """List all accounting periods."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["fp.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if fiscal_year_id:
                conditions.append(f"fp.fiscal_year_id = ${param_idx}")
                params.append(fiscal_year_id)
                param_idx += 1

            if status:
                conditions.append(f"fp.status = ${param_idx}")
                params.append(status.upper())
                param_idx += 1

            if year:
                conditions.append(f"EXTRACT(YEAR FROM fp.start_date) = ${param_idx}")
                params.append(year)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT
                    fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date, fp.status,
                    fy.name as fiscal_year_name,
                    COUNT(je.id) as journal_count,
                    COUNT(je.id) FILTER (WHERE je.status = 'DRAFT') as draft_journal_count
                FROM fiscal_periods fp
                LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                LEFT JOIN journal_entries je ON je.period_id = fp.id
                WHERE {where_clause}
                GROUP BY fp.id, fy.name
                ORDER BY fp.start_date DESC
            """

            rows = await conn.fetch(query, *params)

            items = [
                PeriodListItem(
                    id=str(row["id"]),
                    period_name=row["period_name"],
                    period_number=row["period_number"],
                    fiscal_year_name=row["fiscal_year_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                    journal_count=row["journal_count"] or 0,
                    draft_journal_count=row["draft_journal_count"] or 0,
                )
                for row in rows
            ]

            return PeriodListResponse(data=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List periods error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list periods")


# =============================================================================
# GET CURRENT PERIOD
# =============================================================================
@router.get("/current", response_model=dict)
async def get_current_period(request: Request):
    """Get the current open accounting period."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow("""
                SELECT fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date,
                       fp.status, fp.fiscal_year_id, fy.name as fiscal_year_name
                FROM fiscal_periods fp
                LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                WHERE fp.tenant_id = $1
                  AND fp.status = 'OPEN'
                  AND CURRENT_DATE BETWEEN fp.start_date AND fp.end_date
                ORDER BY fp.start_date DESC
                LIMIT 1
            """, ctx["tenant_id"])

            if not row:
                # Try to find any open period
                row = await conn.fetchrow("""
                    SELECT fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date,
                           fp.status, fp.fiscal_year_id, fy.name as fiscal_year_name
                    FROM fiscal_periods fp
                    LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                    WHERE fp.tenant_id = $1 AND fp.status = 'OPEN'
                    ORDER BY fp.start_date DESC
                    LIMIT 1
                """, ctx["tenant_id"])

            if not row:
                return {"success": True, "data": None, "message": "No open period found"}

            return {
                "success": True,
                "data": PeriodResponse(
                    id=str(row["id"]),
                    period_name=row["period_name"],
                    period_number=row["period_number"],
                    fiscal_year_id=str(row["fiscal_year_id"]) if row["fiscal_year_id"] else None,
                    fiscal_year_name=row["fiscal_year_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                )
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get current period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get current period")


# =============================================================================
# GET PERIOD DETAIL
# =============================================================================
@router.get("/{period_id}", response_model=dict)
async def get_period(request: Request, period_id: UUID):
    """Get accounting period detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow("""
                SELECT fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date,
                       fp.status, fp.closed_at, fp.closed_by, fp.lock_reason,
                       fp.fiscal_year_id, fy.name as fiscal_year_name
                FROM fiscal_periods fp
                LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                WHERE fp.id = $1 AND fp.tenant_id = $2
            """, period_id, ctx["tenant_id"])

            if not row:
                raise HTTPException(status_code=404, detail="Period not found")

            return {
                "success": True,
                "data": PeriodResponse(
                    id=str(row["id"]),
                    period_name=row["period_name"],
                    period_number=row["period_number"],
                    fiscal_year_id=str(row["fiscal_year_id"]) if row["fiscal_year_id"] else None,
                    fiscal_year_name=row["fiscal_year_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                    closed_at=row["closed_at"],
                    closed_by=str(row["closed_by"]) if row["closed_by"] else None,
                    closing_notes=row["lock_reason"],
                )
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get period")


# =============================================================================
# UPDATE PERIOD
# =============================================================================
@router.put("/{period_id}", response_model=dict)
async def update_period(request: Request, period_id: UUID, body: UpdatePeriodRequest):
    """Update accounting period info."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check period exists
            existing = await conn.fetchrow("""
                SELECT id, status FROM fiscal_periods
                WHERE id = $1 AND tenant_id = $2
            """, period_id, ctx["tenant_id"])

            if not existing:
                raise HTTPException(status_code=404, detail="Period not found")

            if existing["status"] == "LOCKED":
                raise HTTPException(status_code=403, detail="Cannot modify locked period")

            # Update fields
            updates = []
            params = []
            param_idx = 1

            if body.name is not None:
                updates.append(f"period_name = ${param_idx}")
                params.append(body.name)
                param_idx += 1

            if not updates:
                return await get_period(request, period_id)

            params.extend([period_id, ctx["tenant_id"]])
            update_clause = ", ".join(updates)

            await conn.execute(f"""
                UPDATE fiscal_periods
                SET {update_clause}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """, *params)

            return await get_period(request, period_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update period")


# =============================================================================
# CLOSE PERIOD
# =============================================================================
@router.post("/{period_id}/close", response_model=ClosePeriodResponse)
async def close_period(request: Request, period_id: UUID, body: ClosePeriodRequest):
    """Close an accounting period."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Validate using helper function
            validation = await conn.fetchrow("""
                SELECT * FROM validate_period_close($1, $2)
            """, ctx["tenant_id"], period_id)

            if not validation["can_close"]:
                error_code = validation["error_code"]
                if error_code == "PERIOD_NOT_FOUND":
                    raise HTTPException(status_code=404, detail=validation["error_message"])
                elif error_code in ("PERIOD_ALREADY_CLOSED", "PERIOD_LOCKED"):
                    raise HTTPException(status_code=409, detail=validation["error_message"])
                elif error_code == "PREVIOUS_PERIOD_OPEN":
                    return ClosePeriodResponse(
                        success=False,
                        errors=[ClosePeriodError(code=error_code, message=validation["error_message"])]
                    )
                elif error_code == "DRAFT_JOURNALS_EXIST":
                    # Get draft journal details
                    draft_rows = await conn.fetch("""
                        SELECT id, journal_number, description, journal_date
                        FROM journal_entries
                        WHERE tenant_id = $1 AND period_id = $2 AND status = 'DRAFT'
                        LIMIT 10
                    """, ctx["tenant_id"], period_id)

                    draft_journals = [
                        DraftJournalInfo(
                            id=str(row["id"]),
                            journal_number=row["journal_number"],
                            description=row["description"],
                            entry_date=row["journal_date"],
                        )
                        for row in draft_rows
                    ]

                    return ClosePeriodResponse(
                        success=False,
                        errors=[ClosePeriodError(code=error_code, message=validation["error_message"])],
                        warnings=[ClosePeriodWarning(
                            code="DRAFT_JOURNALS_EXIST",
                            message=f"{validation['draft_count']} draft journal(s) exist",
                            draft_journals=draft_journals,
                        )]
                    )

            # Handle warning case (drafts exist but not strict mode)
            warnings = []
            if validation["error_code"] == "WARNING_DRAFT_EXISTS":
                if not body.force:
                    draft_rows = await conn.fetch("""
                        SELECT id, journal_number, description, journal_date
                        FROM journal_entries
                        WHERE tenant_id = $1 AND period_id = $2 AND status = 'DRAFT'
                        LIMIT 10
                    """, ctx["tenant_id"], period_id)

                    draft_journals = [
                        DraftJournalInfo(
                            id=str(row["id"]),
                            journal_number=row["journal_number"],
                            description=row["description"],
                            entry_date=row["journal_date"],
                        )
                        for row in draft_rows
                    ]

                    return ClosePeriodResponse(
                        success=False,
                        warnings=[ClosePeriodWarning(
                            code="DRAFT_JOURNALS_EXIST",
                            message=validation["error_message"],
                            draft_journals=draft_journals,
                        )]
                    )
                else:
                    warnings.append(ClosePeriodWarning(
                        code="FORCE_CLOSE",
                        message=f"Period closed with {validation['draft_count']} draft journal(s)",
                    ))

            # Get period info for TB generation
            period = await conn.fetchrow("""
                SELECT start_date, end_date FROM fiscal_periods WHERE id = $1
            """, period_id)

            # Generate trial balance snapshot
            tb_data = await conn.fetch("""
                SELECT
                    coa.id as account_id,
                    coa.account_code,
                    coa.name as account_name,
                    coa.account_type,
                    coa.normal_balance,
                    COALESCE(SUM(jl.debit), 0) as total_debit,
                    COALESCE(SUM(jl.credit), 0) as total_credit
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $2
                WHERE coa.tenant_id = $1 AND coa.is_active = TRUE
                GROUP BY coa.id
                HAVING COALESCE(SUM(jl.debit), 0) != 0 OR COALESCE(SUM(jl.credit), 0) != 0
                ORDER BY coa.account_code
            """, ctx["tenant_id"], period["end_date"])

            import json
            lines_json = json.dumps([dict(row) for row in tb_data], default=str)
            total_debit = sum(row["total_debit"] for row in tb_data)
            total_credit = sum(row["total_credit"] for row in tb_data)
            is_balanced = total_debit == total_credit

            # Save TB snapshot
            snapshot_id = await conn.fetchval("""
                INSERT INTO trial_balance_snapshots
                    (tenant_id, period_id, as_of_date, snapshot_type, lines,
                     total_debit, total_credit, is_balanced, generated_by)
                VALUES ($1, $2, $3, 'closing', $4::jsonb, $5, $6, $7, $8)
                ON CONFLICT (tenant_id, period_id, snapshot_type)
                DO UPDATE SET
                    lines = EXCLUDED.lines,
                    total_debit = EXCLUDED.total_debit,
                    total_credit = EXCLUDED.total_credit,
                    is_balanced = EXCLUDED.is_balanced,
                    generated_at = NOW(),
                    generated_by = EXCLUDED.generated_by
                RETURNING id
            """, ctx["tenant_id"], period_id, period["end_date"], lines_json,
                total_debit, total_credit, is_balanced, ctx["user_id"])

            # Close the period
            await conn.execute("""
                UPDATE fiscal_periods
                SET status = 'CLOSED', closed_at = NOW(), closed_by = $3, lock_reason = $4
                WHERE id = $1 AND tenant_id = $2
            """, period_id, ctx["tenant_id"], ctx["user_id"], body.closing_notes)

            # Get updated period
            period_response = await get_period(request, period_id)

            return ClosePeriodResponse(
                success=True,
                data={
                    "period": period_response["data"],
                    "trial_balance_snapshot": TrialBalanceSnapshotResponse(
                        id=str(snapshot_id),
                        as_of_date=period["end_date"],
                        total_debit=float(total_debit),
                        total_credit=float(total_credit),
                        is_balanced=is_balanced,
                        generated_at=datetime.now(),
                    )
                },
                warnings=warnings,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Close period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close period")


# =============================================================================
# REOPEN PERIOD
# =============================================================================
@router.post("/{period_id}/reopen", response_model=dict)
async def reopen_period(request: Request, period_id: UUID, body: ReopenPeriodRequest):
    """Reopen a closed accounting period."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check tenant settings
            settings_row = await conn.fetchrow("""
                SELECT allow_period_reopen FROM accounting_settings
                WHERE tenant_id = $1
            """, ctx["tenant_id"])

            if settings_row and not settings_row["allow_period_reopen"]:
                raise HTTPException(
                    status_code=403,
                    detail="Period reopening is disabled for this tenant"
                )

            # Check period
            period = await conn.fetchrow("""
                SELECT id, status FROM fiscal_periods
                WHERE id = $1 AND tenant_id = $2
            """, period_id, ctx["tenant_id"])

            if not period:
                raise HTTPException(status_code=404, detail="Period not found")

            if period["status"] == "LOCKED":
                raise HTTPException(status_code=403, detail="Cannot reopen locked period")

            if period["status"] == "OPEN":
                raise HTTPException(status_code=400, detail="Period is already open")

            # Reopen period
            await conn.execute("""
                UPDATE fiscal_periods
                SET status = 'OPEN', closed_at = NULL, closed_by = NULL
                WHERE id = $1 AND tenant_id = $2
            """, period_id, ctx["tenant_id"])

            # Log audit trail
            await conn.execute("""
                INSERT INTO audit_logs (tenant_id, entity_type, entity_id, action, actor_id, details)
                VALUES ($1, 'fiscal_period', $2, 'reopen', $3, $4)
            """, ctx["tenant_id"], str(period_id), ctx["user_id"],
                json.dumps({"reason": body.reason}))

            return await get_period(request, period_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reopen period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reopen period")


# Import for datetime
from datetime import datetime
import json
```

### Step 2: Commit periods router

```bash
git add backend/api_gateway/app/routers/periods.py
git commit -m "feat(accounting): add periods router

Endpoints:
- GET /api/periods - list periods with filters
- GET /api/periods/current - get current open period
- GET /api/periods/:id - get period detail
- PUT /api/periods/:id - update period
- POST /api/periods/:id/close - close with TB snapshot
- POST /api/periods/:id/reopen - reopen with audit

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Journals Router

**Files:**
- Create: `backend/api_gateway/app/routers/journals.py`

### Step 1: Create journals router

```python
"""
Journals Router - Manual Journal Entry Management

CRUD endpoints for manual journal entries with double-entry validation.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal

from ..schemas.journals import (
    CreateJournalRequest,
    ReverseJournalRequest,
    JournalResponse,
    JournalLineResponse,
    JournalListItem,
    JournalListResponse,
    JournalSummary,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None
    }


async def get_next_journal_number(conn, tenant_id: str, prefix: str = "JV") -> str:
    """Get next journal number using sequence."""
    year_month = date.today().strftime("%y%m")

    # Get or create sequence for this prefix/month
    seq = await conn.fetchval("""
        INSERT INTO journal_number_sequences (tenant_id, prefix, year_month, last_number)
        VALUES ($1, $2, $3, 1)
        ON CONFLICT (tenant_id, prefix, year_month)
        DO UPDATE SET last_number = journal_number_sequences.last_number + 1
        RETURNING last_number
    """, tenant_id, prefix, year_month)

    return f"{prefix}-{year_month}-{seq:04d}"


# =============================================================================
# LIST JOURNALS
# =============================================================================
@router.get("", response_model=JournalListResponse)
async def list_journals(
    request: Request,
    period_id: Optional[UUID] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    status: Optional[str] = Query(None, description="draft, posted, reversed"),
    source_type: Optional[str] = Query(None),
    account_id: Optional[UUID] = Query(None, description="Filter by account in lines"),
    search: Optional[str] = Query(None, description="Search description"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List journal entries with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["je.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if period_id:
                conditions.append(f"je.period_id = ${param_idx}")
                params.append(period_id)
                param_idx += 1

            if start_date:
                conditions.append(f"je.journal_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"je.journal_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            if status:
                conditions.append(f"je.status = ${param_idx}")
                params.append(status.upper())
                param_idx += 1

            if source_type:
                conditions.append(f"je.source_type = ${param_idx}")
                params.append(source_type.upper())
                param_idx += 1

            if account_id:
                conditions.append(f"""
                    EXISTS (SELECT 1 FROM journal_lines jl
                            WHERE jl.journal_id = je.id AND jl.account_id = ${param_idx})
                """)
                params.append(account_id)
                param_idx += 1

            if search:
                conditions.append(f"je.description ILIKE ${param_idx}")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count totals
            count_query = f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'DRAFT') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'POSTED') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'VOID' OR reversal_of_id IS NOT NULL) as reversed_count
                FROM journal_entries je
                WHERE {where_clause}
            """
            counts = await conn.fetchrow(count_query, *params)

            # Get data with pagination
            offset = (page - 1) * limit
            params.extend([limit, offset])

            query = f"""
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.source_type, je.total_debit, je.total_credit, je.status, je.created_at
                FROM journal_entries je
                WHERE {where_clause}
                ORDER BY je.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """

            rows = await conn.fetch(query, *params)

            items = [
                JournalListItem(
                    id=str(row["id"]),
                    journal_number=row["journal_number"],
                    entry_date=row["journal_date"],
                    description=row["description"],
                    source_type=row["source_type"].lower() if row["source_type"] else "manual",
                    total_debit=row["total_debit"] or Decimal("0"),
                    total_credit=row["total_credit"] or Decimal("0"),
                    status=row["status"].lower() if row["status"] else "draft",
                    created_at=row["created_at"],
                )
                for row in rows
            ]

            return JournalListResponse(
                data=items,
                summary=JournalSummary(
                    total_count=counts["total"],
                    draft_count=counts["draft_count"],
                    posted_count=counts["posted_count"],
                    reversed_count=counts["reversed_count"],
                ),
                pagination={
                    "page": page,
                    "limit": limit,
                    "total": counts["total"],
                    "has_more": offset + len(items) < counts["total"],
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List journals error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list journals")


# =============================================================================
# GET JOURNAL DETAIL
# =============================================================================
@router.get("/{journal_id}", response_model=dict)
async def get_journal(request: Request, journal_id: UUID):
    """Get journal entry detail with lines."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get journal header
            je_row = await conn.fetchrow("""
                SELECT je.*, fp.period_name
                FROM journal_entries je
                LEFT JOIN fiscal_periods fp ON fp.id = je.period_id
                WHERE je.id = $1 AND je.tenant_id = $2
            """, journal_id, ctx["tenant_id"])

            if not je_row:
                raise HTTPException(status_code=404, detail="Journal not found")

            # Get lines with account info
            lines = await conn.fetch("""
                SELECT jl.*, coa.account_code, coa.name as account_name
                FROM journal_lines jl
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE jl.journal_id = $1
                ORDER BY jl.line_number
            """, journal_id)

            line_responses = [
                JournalLineResponse(
                    id=str(line["id"]),
                    line_number=line["line_number"],
                    account_id=str(line["account_id"]),
                    account_code=line["account_code"],
                    account_name=line["account_name"],
                    description=line["memo"] or line["description"],
                    debit=line["debit"] or Decimal("0"),
                    credit=line["credit"] or Decimal("0"),
                )
                for line in lines
            ]

            return {
                "success": True,
                "data": JournalResponse(
                    id=str(je_row["id"]),
                    journal_number=je_row["journal_number"],
                    entry_date=je_row["journal_date"],
                    period_id=str(je_row["period_id"]) if je_row["period_id"] else None,
                    period_name=je_row["period_name"],
                    source_type=je_row["source_type"].lower() if je_row["source_type"] else "manual",
                    source_id=str(je_row["source_id"]) if je_row["source_id"] else None,
                    description=je_row["description"],
                    lines=line_responses,
                    total_debit=je_row["total_debit"] or Decimal("0"),
                    total_credit=je_row["total_credit"] or Decimal("0"),
                    is_balanced=(je_row["total_debit"] or 0) == (je_row["total_credit"] or 0),
                    status=je_row["status"].lower() if je_row["status"] else "draft",
                    reversal_of_id=str(je_row["reversal_of_id"]) if je_row["reversal_of_id"] else None,
                    reversed_by_id=str(je_row["reversed_by_id"]) if je_row["reversed_by_id"] else None,
                    created_by=str(je_row["created_by"]) if je_row["created_by"] else None,
                    created_at=je_row["created_at"],
                    posted_at=je_row["posted_at"],
                    posted_by=str(je_row["posted_by"]) if je_row["posted_by"] else None,
                )
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal")


# =============================================================================
# CREATE JOURNAL
# =============================================================================
@router.post("", response_model=dict, status_code=201)
async def create_journal(request: Request, body: CreateJournalRequest):
    """Create a manual journal entry."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check period is open
            period = await conn.fetchrow("""
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
                ORDER BY start_date DESC LIMIT 1
            """, ctx["tenant_id"], body.entry_date)

            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post to {period['status'].lower()} period"
                )

            # Check approval requirement
            settings_row = await conn.fetchrow("""
                SELECT journal_approval_required FROM accounting_settings
                WHERE tenant_id = $1
            """, ctx["tenant_id"])

            needs_approval = settings_row and settings_row["journal_approval_required"]
            initial_status = "DRAFT" if body.save_as_draft or needs_approval else "POSTED"

            # Calculate totals
            total_debit = sum(line.debit for line in body.lines)
            total_credit = sum(line.credit for line in body.lines)

            # Get journal number
            journal_number = await get_next_journal_number(conn, ctx["tenant_id"], "JV")

            async with conn.transaction():
                # Create journal header
                journal_id = await conn.fetchval("""
                    INSERT INTO journal_entries (
                        tenant_id, journal_number, journal_date, description,
                        source_type, total_debit, total_credit, status,
                        period_id, created_by, posted_at, posted_by
                    )
                    VALUES ($1, $2, $3, $4, 'MANUAL', $5, $6, $7, $8, $9,
                            CASE WHEN $7 = 'POSTED' THEN NOW() ELSE NULL END,
                            CASE WHEN $7 = 'POSTED' THEN $9 ELSE NULL END)
                    RETURNING id
                """, ctx["tenant_id"], journal_number, body.entry_date, body.description,
                    total_debit, total_credit, initial_status,
                    period["id"] if period else None, ctx["user_id"])

                # Create journal lines
                for i, line in enumerate(body.lines, 1):
                    await conn.execute("""
                        INSERT INTO journal_lines (
                            journal_id, line_number, account_id, memo, debit, credit
                        )
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, journal_id, i, UUID(line.account_id), line.description,
                        line.debit, line.credit)

            return await get_journal(request, journal_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create journal")


# =============================================================================
# POST DRAFT JOURNAL
# =============================================================================
@router.post("/{journal_id}/post", response_model=dict)
async def post_journal(request: Request, journal_id: UUID):
    """Post a draft journal entry."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check journal exists and is draft
            journal = await conn.fetchrow("""
                SELECT id, status, journal_date FROM journal_entries
                WHERE id = $1 AND tenant_id = $2
            """, journal_id, ctx["tenant_id"])

            if not journal:
                raise HTTPException(status_code=404, detail="Journal not found")

            if journal["status"] != "DRAFT":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot post journal with status: {journal['status']}"
                )

            # Check period is open
            period = await conn.fetchrow("""
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
            """, ctx["tenant_id"], journal["journal_date"])

            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post to {period['status'].lower()} period"
                )

            # Post journal
            await conn.execute("""
                UPDATE journal_entries
                SET status = 'POSTED', posted_at = NOW(), posted_by = $3
                WHERE id = $1 AND tenant_id = $2
            """, journal_id, ctx["tenant_id"], ctx["user_id"])

            return await get_journal(request, journal_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Post journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post journal")


# =============================================================================
# REVERSE JOURNAL
# =============================================================================
@router.post("/{journal_id}/reverse", response_model=dict)
async def reverse_journal(request: Request, journal_id: UUID, body: ReverseJournalRequest):
    """Create a reversal entry for a posted journal."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check journal exists and is posted
            journal = await conn.fetchrow("""
                SELECT id, status, reversed_by_id, journal_number, description
                FROM journal_entries
                WHERE id = $1 AND tenant_id = $2
            """, journal_id, ctx["tenant_id"])

            if not journal:
                raise HTTPException(status_code=404, detail="Journal not found")

            if journal["status"] == "DRAFT":
                raise HTTPException(status_code=400, detail="Cannot reverse draft journal")

            if journal["reversed_by_id"]:
                raise HTTPException(status_code=409, detail="Journal is already reversed")

            # Get original lines
            lines = await conn.fetch("""
                SELECT account_id, memo, debit, credit FROM journal_lines
                WHERE journal_id = $1 ORDER BY line_number
            """, journal_id)

            # Check reversal period is open
            period = await conn.fetchrow("""
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
            """, ctx["tenant_id"], body.reversal_date)

            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post reversal to {period['status'].lower()} period"
                )

            # Create reversal
            reversal_number = await get_next_journal_number(conn, ctx["tenant_id"], "JV")
            total_debit = sum(line["credit"] for line in lines)  # Swap debit/credit
            total_credit = sum(line["debit"] for line in lines)

            async with conn.transaction():
                # Create reversal journal
                reversal_id = await conn.fetchval("""
                    INSERT INTO journal_entries (
                        tenant_id, journal_number, journal_date, description,
                        source_type, total_debit, total_credit, status,
                        period_id, reversal_of_id, reversal_reason,
                        created_by, posted_at, posted_by
                    )
                    VALUES ($1, $2, $3, $4, 'MANUAL', $5, $6, 'POSTED', $7, $8, $9, $10, NOW(), $10)
                    RETURNING id
                """, ctx["tenant_id"], reversal_number, body.reversal_date,
                    f"Reversal of {journal['journal_number']}: {body.reason}",
                    total_debit, total_credit, period["id"] if period else None,
                    journal_id, body.reason, ctx["user_id"])

                # Create reversed lines (swap debit/credit)
                for i, line in enumerate(lines, 1):
                    await conn.execute("""
                        INSERT INTO journal_lines (journal_id, line_number, account_id, memo, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, reversal_id, i, line["account_id"], line["memo"],
                        line["credit"], line["debit"])  # Swapped

                # Link original to reversal
                await conn.execute("""
                    UPDATE journal_entries
                    SET reversed_by_id = $3, reversed_at = NOW()
                    WHERE id = $1 AND tenant_id = $2
                """, journal_id, ctx["tenant_id"], reversal_id)

            return await get_journal(request, reversal_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reverse journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reverse journal")


# =============================================================================
# DELETE DRAFT JOURNAL
# =============================================================================
@router.delete("/{journal_id}", response_model=dict)
async def delete_journal(request: Request, journal_id: UUID):
    """Delete a draft journal entry."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check journal exists and is draft
            journal = await conn.fetchrow("""
                SELECT id, status FROM journal_entries
                WHERE id = $1 AND tenant_id = $2
            """, journal_id, ctx["tenant_id"])

            if not journal:
                raise HTTPException(status_code=404, detail="Journal not found")

            if journal["status"] != "DRAFT":
                raise HTTPException(
                    status_code=409,
                    detail="Only draft journals can be deleted"
                )

            # Delete lines first, then header
            async with conn.transaction():
                await conn.execute("DELETE FROM journal_lines WHERE journal_id = $1", journal_id)
                await conn.execute("""
                    DELETE FROM journal_entries WHERE id = $1 AND tenant_id = $2
                """, journal_id, ctx["tenant_id"])

            return {"success": True, "message": "Journal deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete journal")


# =============================================================================
# GET JOURNALS BY ACCOUNT
# =============================================================================
@router.get("/by-account/{account_id}", response_model=JournalListResponse)
async def get_journals_by_account(
    request: Request,
    account_id: UUID,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get all journals containing a specific account."""
    # Reuse list_journals with account_id filter
    return await list_journals(
        request=request,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=limit,
    )


# =============================================================================
# GET JOURNAL BY SOURCE
# =============================================================================
@router.get("/by-source/{source_type}/{source_id}", response_model=dict)
async def get_journal_by_source(
    request: Request,
    source_type: str,
    source_id: UUID,
):
    """Get journal entry for a specific source document."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            journal = await conn.fetchrow("""
                SELECT id FROM journal_entries
                WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3
                ORDER BY created_at DESC LIMIT 1
            """, ctx["tenant_id"], source_type.upper(), source_id)

            if not journal:
                return {"success": True, "data": None}

            return await get_journal(request, journal["id"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get journal by source error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal")
```

### Step 2: Commit journals router

```bash
git add backend/api_gateway/app/routers/journals.py
git commit -m "feat(accounting): add journals router

Endpoints:
- GET /api/journals - list with filters
- GET /api/journals/:id - detail with lines
- POST /api/journals - create manual journal
- POST /api/journals/:id/post - post draft
- POST /api/journals/:id/reverse - create reversal
- DELETE /api/journals/:id - delete draft
- GET /api/journals/by-account/:id - filter by account
- GET /api/journals/by-source/:type/:id - find by source

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Ledger Router

**Files:**
- Create: `backend/api_gateway/app/routers/ledger.py`

### Step 1: Create ledger router

```python
"""
General Ledger Router - Read-Only Ledger Views

Endpoints for viewing account ledgers and balances.
All operations are read-only - ledger is populated via journals.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal

from ..schemas.ledger import (
    LedgerEntryResponse,
    AccountInfoResponse,
    AccountLedgerResponse,
    AccountBalanceResponse,
    LedgerAccountSummary,
    LedgerListResponse,
    LedgerSummaryResponse,
    TypeSummary,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id}


# =============================================================================
# LIST ALL ACCOUNTS WITH BALANCES
# =============================================================================
@router.get("", response_model=LedgerListResponse)
async def list_ledger_accounts(
    request: Request,
    as_of_date: Optional[date] = Query(None, description="Balance as of date (default: today)"),
    account_type: Optional[str] = Query(None, description="Filter by type: ASSET, LIABILITY, etc."),
    include_zero: bool = Query(False, description="Include accounts with zero balance"),
):
    """List all accounts with their current balances."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if not as_of_date:
            as_of_date = date.today()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["coa.tenant_id = $1", "coa.is_active = TRUE"]
            params = [ctx["tenant_id"], as_of_date]
            param_idx = 3

            if account_type:
                conditions.append(f"coa.account_type = ${param_idx}")
                params.append(account_type.upper())
                param_idx += 1

            where_clause = " AND ".join(conditions)

            having_clause = ""
            if not include_zero:
                having_clause = "HAVING COALESCE(SUM(jl.debit), 0) != 0 OR COALESCE(SUM(jl.credit), 0) != 0"

            query = f"""
                SELECT
                    coa.id,
                    coa.account_code as code,
                    coa.name,
                    coa.account_type,
                    coa.normal_balance,
                    COALESCE(SUM(jl.debit), 0) as debit_balance,
                    COALESCE(SUM(jl.credit), 0) as credit_balance,
                    CASE
                        WHEN coa.normal_balance = 'DEBIT'
                        THEN COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
                        ELSE COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0)
                    END as net_balance
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $2
                WHERE {where_clause}
                GROUP BY coa.id
                {having_clause}
                ORDER BY coa.account_code
            """

            rows = await conn.fetch(query, *params)

            accounts = [
                LedgerAccountSummary(
                    id=str(row["id"]),
                    code=row["code"],
                    name=row["name"],
                    account_type=row["account_type"],
                    normal_balance=row["normal_balance"],
                    debit_balance=row["debit_balance"],
                    credit_balance=row["credit_balance"],
                    net_balance=row["net_balance"],
                )
                for row in rows
            ]

            return LedgerListResponse(data=accounts, as_of_date=as_of_date)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List ledger accounts error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list ledger accounts")


# =============================================================================
# GET ACCOUNT LEDGER
# =============================================================================
@router.get("/{account_id}", response_model=AccountLedgerResponse)
async def get_account_ledger(
    request: Request,
    account_id: UUID,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Get detailed ledger for a single account with running balance."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get account info
            account = await conn.fetchrow("""
                SELECT id, account_code, name, account_type, normal_balance
                FROM chart_of_accounts
                WHERE id = $1 AND tenant_id = $2
            """, account_id, ctx["tenant_id"])

            if not account:
                raise HTTPException(status_code=404, detail="Account not found")

            # Calculate opening balance (before start_date)
            opening_balance = Decimal("0")
            if start_date:
                ob_row = await conn.fetchrow("""
                    SELECT
                        COALESCE(SUM(jl.debit), 0) as total_debit,
                        COALESCE(SUM(jl.credit), 0) as total_credit
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = $1
                      AND je.tenant_id = $2
                      AND je.status = 'POSTED'
                      AND je.journal_date < $3
                """, account_id, ctx["tenant_id"], start_date)

                if account["normal_balance"] == "DEBIT":
                    opening_balance = (ob_row["total_debit"] or 0) - (ob_row["total_credit"] or 0)
                else:
                    opening_balance = (ob_row["total_credit"] or 0) - (ob_row["total_debit"] or 0)

            # Build query for entries
            conditions = [
                "jl.account_id = $1",
                "je.tenant_id = $2",
                "je.status = 'POSTED'"
            ]
            params = [account_id, ctx["tenant_id"]]
            param_idx = 3

            if start_date:
                conditions.append(f"je.journal_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"je.journal_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            offset = (page - 1) * limit
            params.extend([limit, offset])

            entries_query = f"""
                SELECT
                    je.journal_date as date,
                    je.journal_number,
                    je.id as journal_id,
                    je.description,
                    jl.debit,
                    jl.credit,
                    je.source_type
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE {where_clause}
                ORDER BY je.journal_date, je.created_at
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """

            rows = await conn.fetch(entries_query, *params)

            # Calculate running balance
            running_balance = opening_balance
            entries = []
            total_debit = Decimal("0")
            total_credit = Decimal("0")

            for row in rows:
                debit = row["debit"] or Decimal("0")
                credit = row["credit"] or Decimal("0")

                if account["normal_balance"] == "DEBIT":
                    running_balance = running_balance + debit - credit
                else:
                    running_balance = running_balance + credit - debit

                total_debit += debit
                total_credit += credit

                entries.append(LedgerEntryResponse(
                    date=row["date"],
                    journal_number=row["journal_number"],
                    journal_id=str(row["journal_id"]),
                    description=row["description"],
                    debit=debit,
                    credit=credit,
                    running_balance=running_balance,
                    source_type=row["source_type"].lower() if row["source_type"] else "manual",
                ))

            closing_balance = opening_balance
            if account["normal_balance"] == "DEBIT":
                closing_balance = opening_balance + total_debit - total_credit
            else:
                closing_balance = opening_balance + total_credit - total_debit

            return AccountLedgerResponse(
                data={
                    "account": AccountInfoResponse(
                        id=str(account["id"]),
                        code=account["account_code"],
                        name=account["name"],
                        account_type=account["account_type"],
                        normal_balance=account["normal_balance"],
                    ),
                    "opening_balance": opening_balance,
                    "entries": entries,
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "closing_balance": closing_balance,
                    "net_movement": total_debit - total_credit,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get account ledger error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get account ledger")


# =============================================================================
# GET ACCOUNT BALANCE
# =============================================================================
@router.get("/{account_id}/balance", response_model=AccountBalanceResponse)
async def get_account_balance(
    request: Request,
    account_id: UUID,
    as_of_date: Optional[date] = Query(None),
):
    """Get point-in-time balance for an account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if not as_of_date:
            as_of_date = date.today()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow("""
                SELECT
                    coa.id,
                    coa.account_code,
                    coa.name,
                    coa.normal_balance,
                    COALESCE(SUM(jl.debit), 0) as debit_balance,
                    COALESCE(SUM(jl.credit), 0) as credit_balance
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $3
                WHERE coa.id = $1 AND coa.tenant_id = $2
                GROUP BY coa.id
            """, account_id, ctx["tenant_id"], as_of_date)

            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            if row["normal_balance"] == "DEBIT":
                net_balance = row["debit_balance"] - row["credit_balance"]
            else:
                net_balance = row["credit_balance"] - row["debit_balance"]

            return AccountBalanceResponse(
                data={
                    "account_id": str(row["id"]),
                    "account_code": row["account_code"],
                    "account_name": row["name"],
                    "as_of_date": as_of_date,
                    "debit_balance": row["debit_balance"],
                    "credit_balance": row["credit_balance"],
                    "net_balance": net_balance,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get account balance error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get account balance")


# =============================================================================
# GET LEDGER SUMMARY
# =============================================================================
@router.get("/summary", response_model=LedgerSummaryResponse)
async def get_ledger_summary(
    request: Request,
    as_of_date: Optional[date] = Query(None),
):
    """Get summary of ledger balances by account type."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if not as_of_date:
            as_of_date = date.today()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            rows = await conn.fetch("""
                SELECT
                    coa.account_type,
                    COUNT(DISTINCT coa.id) as account_count,
                    COALESCE(SUM(jl.debit), 0) as total_debit,
                    COALESCE(SUM(jl.credit), 0) as total_credit
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $2
                WHERE coa.tenant_id = $1 AND coa.is_active = TRUE
                GROUP BY coa.account_type
            """, ctx["tenant_id"], as_of_date)

            by_type = {}
            totals = {"ASSET": 0, "LIABILITY": 0, "EQUITY": 0, "INCOME": 0, "EXPENSE": 0}

            for row in rows:
                account_type = row["account_type"]
                total_debit = row["total_debit"] or Decimal("0")
                total_credit = row["total_credit"] or Decimal("0")

                # Calculate balance based on normal balance
                if account_type in ("ASSET", "EXPENSE"):
                    balance = total_debit - total_credit
                else:
                    balance = total_credit - total_debit

                by_type[account_type] = TypeSummary(
                    total_debit=total_debit,
                    total_credit=total_credit,
                    balance=balance,
                    account_count=row["account_count"],
                )
                totals[account_type] = balance

            # Accounting equation check: Assets = Liabilities + Equity
            is_balanced = totals["ASSET"] == (totals["LIABILITY"] + totals["EQUITY"] +
                                               totals["INCOME"] - totals["EXPENSE"])

            return LedgerSummaryResponse(
                data={
                    "by_type": by_type,
                    "total_assets": totals["ASSET"],
                    "total_liabilities": totals["LIABILITY"],
                    "total_equity": totals["EQUITY"],
                    "total_revenue": totals["INCOME"],
                    "total_expenses": totals["EXPENSE"],
                    "is_balanced": is_balanced,
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get ledger summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get ledger summary")
```

### Step 2: Commit ledger router

```bash
git add backend/api_gateway/app/routers/ledger.py
git commit -m "feat(accounting): add ledger router

Read-only endpoints:
- GET /api/ledger - all accounts with balances
- GET /api/ledger/:id - account ledger with running balance
- GET /api/ledger/:id/balance - point-in-time balance
- GET /api/ledger/summary - summary by account type

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Update main.py

**Files:**
- Modify: `backend/api_gateway/app/main.py`

### Step 1: Add imports and router includes

Add after existing router imports (around line 90):

```python
# Accounting Kernel API Routers
from .routers import journals
from .routers import ledger
from .routers import fiscal_years
from .routers import periods
```

Add after existing router includes (around line 460):

```python
# ===========================================
# ACCOUNTING KERNEL API
# ===========================================

# Journals router (Manual Journal Entries)
app.include_router(journals.router, prefix="/api/journals", tags=["journals"])

# Ledger router (General Ledger Views)
app.include_router(ledger.router, prefix="/api/ledger", tags=["ledger"])

# Fiscal Years router (Fiscal Year Management)
app.include_router(fiscal_years.router, prefix="/api/fiscal-years", tags=["fiscal-years"])

# Periods router (Accounting Period Management)
app.include_router(periods.router, prefix="/api/periods", tags=["periods"])
```

### Step 2: Remove old /api/journals alias endpoint

Remove lines 537-617 (the old list_journals function in main.py) since we now have a proper journals router.

### Step 3: Commit main.py update

```bash
git add backend/api_gateway/app/main.py
git commit -m "feat(accounting): register accounting kernel routers

- Add journals, ledger, fiscal_years, periods routers
- Remove old /api/journals alias endpoint

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 8: E2E Testing

### Step 1: Start services (manual)

```bash
# Ensure services are running
docker-compose up -d
```

### Step 2: Run migration

```bash
cd backend && flyway migrate
```

### Step 3: Test endpoints via curl

**Test Fiscal Years:**
```bash
# Create fiscal year
curl -X POST https://milkyhoop.com/api/fiscal-years \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Tahun Buku 2026", "year": 2026, "start_month": 1}'

# List fiscal years
curl https://milkyhoop.com/api/fiscal-years \
  -H "Authorization: Bearer $TOKEN"
```

**Test Periods:**
```bash
# Get current period
curl https://milkyhoop.com/api/periods/current \
  -H "Authorization: Bearer $TOKEN"

# List periods
curl https://milkyhoop.com/api/periods \
  -H "Authorization: Bearer $TOKEN"
```

**Test Journals:**
```bash
# Create manual journal
curl -X POST https://milkyhoop.com/api/journals \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "entry_date": "2026-01-15",
    "description": "Test manual journal",
    "lines": [
      {"account_id": "<cash_account_id>", "debit": 100000, "credit": 0},
      {"account_id": "<revenue_account_id>", "debit": 0, "credit": 100000}
    ]
  }'

# List journals
curl https://milkyhoop.com/api/journals \
  -H "Authorization: Bearer $TOKEN"
```

**Test Ledger:**
```bash
# Get ledger summary
curl https://milkyhoop.com/api/ledger/summary \
  -H "Authorization: Bearer $TOKEN"

# Get all accounts with balances
curl https://milkyhoop.com/api/ledger \
  -H "Authorization: Bearer $TOKEN"
```

### Step 4: Final commit

```bash
git add -A
git commit -m "feat(accounting): complete accounting kernel API implementation

Summary:
- Migration V085: fiscal_years, trial_balance_snapshots, settings
- 4 schema files: journals, ledger, fiscal_years, periods
- 4 router files with full CRUD operations
- ~1200 lines of new code

Endpoints added:
- /api/journals (8 endpoints)
- /api/ledger (4 endpoints)
- /api/fiscal-years (4 endpoints)
- /api/periods (6 endpoints)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Summary

| Task | Files | Est. Lines |
|------|-------|------------|
| 1. Migration V085 | 1 file | ~180 |
| 2. Schemas | 4 files | ~350 |
| 3. Fiscal Years Router | 1 file | ~200 |
| 4. Periods Router | 1 file | ~350 |
| 5. Journals Router | 1 file | ~400 |
| 6. Ledger Router | 1 file | ~300 |
| 7. Update main.py | 1 file | ~20 |
| **Total** | **10 files** | **~1800 lines** |
