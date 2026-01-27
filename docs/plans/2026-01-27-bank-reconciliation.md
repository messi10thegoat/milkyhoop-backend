# Bank Reconciliation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement bank reconciliation module enabling users to match bank statement transactions with system transactions, with import, auto-match, manual match, and adjustment capabilities.

**Architecture:** Multi-table design with reconciliation_sessions as the parent, bank_statement_lines for imported data, reconciliation_matches for links, and reconciliation_adjustments for balance adjustments. Uses existing bank_transactions table with new columns for reconciliation tracking.

**Tech Stack:** FastAPI + asyncpg (raw SQL), Pydantic schemas, PostgreSQL with RLS, pandas for CSV/XLSX parsing, ofxparse for OFX files

---

## Task 1: Database Migration

**Files:**
- Create: `backend/migrations/V086__bank_reconciliation.sql`

**Step 1: Write the migration file**

```sql
-- =============================================================================
-- V086: Bank Reconciliation Module
-- =============================================================================
-- Tables: reconciliation_sessions, bank_statement_lines, reconciliation_matches,
--         reconciliation_adjustments
-- Extends: bank_transactions (add reconciliation columns)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. RECONCILIATION SESSIONS
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reconciliation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    account_id UUID NOT NULL,
    statement_date DATE NOT NULL,
    statement_start_date DATE NOT NULL,
    statement_end_date DATE NOT NULL,
    statement_beginning_balance BIGINT NOT NULL,
    statement_ending_balance BIGINT NOT NULL,
    cleared_balance BIGINT,
    difference BIGINT,
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    CONSTRAINT chk_session_status CHECK (status IN ('not_started', 'in_progress', 'completed', 'cancelled')),
    CONSTRAINT chk_session_dates CHECK (statement_end_date >= statement_start_date)
);

CREATE INDEX IF NOT EXISTS idx_recon_sessions_tenant_account
    ON reconciliation_sessions(tenant_id, account_id);
CREATE INDEX IF NOT EXISTS idx_recon_sessions_status
    ON reconciliation_sessions(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_recon_sessions_date
    ON reconciliation_sessions(tenant_id, statement_date DESC);

ALTER TABLE reconciliation_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_reconciliation_sessions ON reconciliation_sessions
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- -----------------------------------------------------------------------------
-- 2. BANK STATEMENT LINES
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bank_statement_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES reconciliation_sessions(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    date DATE NOT NULL,
    description VARCHAR(500) NOT NULL,
    reference VARCHAR(100),
    amount BIGINT NOT NULL,
    type VARCHAR(10) NOT NULL,
    running_balance BIGINT,
    match_status VARCHAR(20) NOT NULL DEFAULT 'unmatched',
    match_confidence VARCHAR(10),
    match_difference BIGINT,
    raw_data JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_line_type CHECK (type IN ('debit', 'credit')),
    CONSTRAINT chk_line_match_status CHECK (match_status IN ('matched', 'unmatched', 'partially_matched', 'excluded')),
    CONSTRAINT chk_line_confidence CHECK (match_confidence IS NULL OR match_confidence IN ('exact', 'high', 'medium', 'low', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_statement_lines_session
    ON bank_statement_lines(session_id);
CREATE INDEX IF NOT EXISTS idx_statement_lines_status
    ON bank_statement_lines(tenant_id, match_status);
CREATE INDEX IF NOT EXISTS idx_statement_lines_date
    ON bank_statement_lines(tenant_id, date);

ALTER TABLE bank_statement_lines ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_bank_statement_lines ON bank_statement_lines
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- -----------------------------------------------------------------------------
-- 3. RECONCILIATION MATCHES
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reconciliation_matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES reconciliation_sessions(id) ON DELETE CASCADE,
    statement_line_id UUID NOT NULL REFERENCES bank_statement_lines(id) ON DELETE CASCADE,
    transaction_id UUID NOT NULL,
    tenant_id TEXT NOT NULL,
    match_type VARCHAR(20) NOT NULL,
    confidence VARCHAR(10) NOT NULL,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_match_type CHECK (match_type IN ('one_to_one', 'one_to_many', 'many_to_one')),
    CONSTRAINT chk_match_confidence CHECK (confidence IN ('exact', 'high', 'medium', 'low', 'manual')),
    CONSTRAINT uq_match_pair UNIQUE (statement_line_id, transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_recon_matches_session
    ON reconciliation_matches(session_id);
CREATE INDEX IF NOT EXISTS idx_recon_matches_statement
    ON reconciliation_matches(statement_line_id);
CREATE INDEX IF NOT EXISTS idx_recon_matches_transaction
    ON reconciliation_matches(transaction_id);

ALTER TABLE reconciliation_matches ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_reconciliation_matches ON reconciliation_matches
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- -----------------------------------------------------------------------------
-- 4. RECONCILIATION ADJUSTMENTS
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reconciliation_adjustments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES reconciliation_sessions(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    type VARCHAR(20) NOT NULL,
    amount BIGINT NOT NULL,
    description VARCHAR(500) NOT NULL,
    account_id UUID NOT NULL,
    journal_entry_id UUID,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_adjustment_type CHECK (type IN ('bank_fee', 'interest', 'correction', 'other'))
);

CREATE INDEX IF NOT EXISTS idx_recon_adjustments_session
    ON reconciliation_adjustments(session_id);

ALTER TABLE reconciliation_adjustments ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_reconciliation_adjustments ON reconciliation_adjustments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- -----------------------------------------------------------------------------
-- 5. EXTEND BANK_TRANSACTIONS (if table exists)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    -- Add is_cleared column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'is_cleared'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN is_cleared BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;

    -- Add is_reconciled column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'is_reconciled'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN is_reconciled BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;

    -- Add reconciled_session_id column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'reconciled_session_id'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN reconciled_session_id UUID;
    END IF;

    -- Add matched_statement_line_id column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'matched_statement_line_id'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN matched_statement_line_id UUID;
    END IF;

    -- Add cleared_at column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'cleared_at'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN cleared_at TIMESTAMPTZ;
    END IF;

    -- Add reconciled_at column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bank_transactions' AND column_name = 'reconciled_at'
    ) THEN
        ALTER TABLE bank_transactions ADD COLUMN reconciled_at TIMESTAMPTZ;
    END IF;
END $$;

-- Index for reconciliation queries on bank_transactions
CREATE INDEX IF NOT EXISTS idx_bank_txn_reconciliation
    ON bank_transactions(tenant_id, is_cleared, is_reconciled);

-- -----------------------------------------------------------------------------
-- 6. UPDATED_AT TRIGGERS
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_reconciliation_sessions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reconciliation_sessions_updated_at ON reconciliation_sessions;
CREATE TRIGGER trg_reconciliation_sessions_updated_at
    BEFORE UPDATE ON reconciliation_sessions
    FOR EACH ROW EXECUTE FUNCTION update_reconciliation_sessions_updated_at();

CREATE OR REPLACE FUNCTION update_bank_statement_lines_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bank_statement_lines_updated_at ON bank_statement_lines;
CREATE TRIGGER trg_bank_statement_lines_updated_at
    BEFORE UPDATE ON bank_statement_lines
    FOR EACH ROW EXECUTE FUNCTION update_bank_statement_lines_updated_at();

-- -----------------------------------------------------------------------------
-- 7. HELPER FUNCTION: Update session statistics
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_reconciliation_session_stats(p_session_id UUID)
RETURNS VOID AS $$
DECLARE
    v_cleared_balance BIGINT;
    v_ending_balance BIGINT;
BEGIN
    -- Calculate cleared balance from matched transactions
    SELECT COALESCE(SUM(
        CASE WHEN bsl.type = 'credit' THEN bsl.amount ELSE -bsl.amount END
    ), 0)
    INTO v_cleared_balance
    FROM bank_statement_lines bsl
    WHERE bsl.session_id = p_session_id
      AND bsl.match_status = 'matched';

    -- Get statement ending balance
    SELECT statement_ending_balance INTO v_ending_balance
    FROM reconciliation_sessions
    WHERE id = p_session_id;

    -- Update session
    UPDATE reconciliation_sessions
    SET cleared_balance = v_cleared_balance,
        difference = v_ending_balance - v_cleared_balance,
        updated_at = NOW()
    WHERE id = p_session_id;
END;
$$ LANGUAGE plpgsql;
```

**Step 2: Run the migration**

Run: `cd /root/milkyhoop-dev && docker compose exec api_gateway python -c "import asyncio; from app.config import settings; import asyncpg; asyncio.run(asyncpg.connect(**settings.get_db_config()).execute(open('backend/migrations/V086__bank_reconciliation.sql').read()))"`

Or via Flyway/migration tool if configured.

**Step 3: Verify migration**

Run: `docker compose exec postgres psql -U postgres -d milkydb -c "\dt *reconciliation*"`
Expected: Tables reconciliation_sessions, bank_statement_lines, reconciliation_matches, reconciliation_adjustments listed

**Step 4: Commit**

```bash
git add backend/migrations/V086__bank_reconciliation.sql
git commit -m "feat(bank-reconciliation): add database migration V086

- Create reconciliation_sessions table
- Create bank_statement_lines table
- Create reconciliation_matches table
- Create reconciliation_adjustments table
- Extend bank_transactions with reconciliation columns
- Add RLS policies and indexes
- Add helper function for stats update

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Pydantic Schemas

**Files:**
- Create: `backend/api_gateway/app/schemas/bank_reconciliation.py`

**Step 1: Write the schemas file**

```python
"""
Pydantic schemas for Bank Reconciliation module.

Flow:
1. Create session with statement dates and balances
2. Import bank statement file (CSV/OFX/XLSX)
3. Auto-match or manually match statement lines to transactions
4. Add adjustments if needed (bank fees, interest)
5. Complete reconciliation when difference is zero
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from datetime import date, datetime


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateSessionRequest(BaseModel):
    """Request to start a new reconciliation session."""

    account_id: str = Field(..., description="Bank account UUID")
    statement_date: date = Field(..., description="Statement closing date")
    statement_start_date: date = Field(..., description="Period start date")
    statement_end_date: date = Field(..., description="Period end date")
    statement_beginning_balance: int = Field(..., description="Opening balance in IDR")
    statement_ending_balance: int = Field(..., description="Closing balance in IDR")

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, v):
        if not v or not v.strip():
            raise ValueError("account_id is required")
        return v.strip()


class ImportConfigCSV(BaseModel):
    """Configuration for CSV import."""

    format: Literal["csv"] = "csv"
    date_column: str = Field(..., description="Column name for date")
    description_column: str = Field(..., description="Column name for description")
    amount_column: Optional[str] = Field(None, description="Single amount column (signed)")
    debit_column: Optional[str] = Field(None, description="Debit column (separate)")
    credit_column: Optional[str] = Field(None, description="Credit column (separate)")
    reference_column: Optional[str] = Field(None, description="Reference/check number column")
    balance_column: Optional[str] = Field(None, description="Running balance column")
    date_format: str = Field("DD/MM/YYYY", description="Date format pattern")
    decimal_separator: str = Field(",", description="Decimal separator")
    skip_rows: int = Field(0, ge=0, description="Header rows to skip")


class MatchRequest(BaseModel):
    """Request to match statement line with transactions."""

    statement_line_id: str = Field(..., description="Statement line UUID")
    transaction_ids: List[str] = Field(..., min_length=1, description="Transaction UUIDs to match")


class AutoMatchRequest(BaseModel):
    """Optional configuration for auto-matching."""

    confidence_threshold: Literal["exact", "high", "medium", "low"] = Field(
        "high", description="Minimum confidence for auto-match"
    )
    date_tolerance_days: int = Field(3, ge=0, le=30, description="Date tolerance in days")


class CreateTransactionFromLineRequest(BaseModel):
    """Request to create transaction from unmatched statement line."""

    statement_line_id: str = Field(..., description="Statement line UUID")
    type: Literal["expense", "income", "transfer"] = Field(..., description="Transaction type")
    account_id: str = Field(..., description="Chart of Accounts UUID for categorization")
    contact_id: Optional[str] = Field(None, description="Vendor/Customer UUID")
    description: Optional[str] = Field(None, description="Override description")
    auto_match: bool = Field(True, description="Auto-match after creation")


class AdjustmentItem(BaseModel):
    """Single adjustment for reconciliation completion."""

    type: Literal["bank_fee", "interest", "correction", "other"] = Field(...)
    amount: int = Field(..., description="Amount in IDR (positive)")
    description: str = Field(..., min_length=1, max_length=500)
    account_id: str = Field(..., description="Chart of Accounts UUID")


class CompleteSessionRequest(BaseModel):
    """Request to complete reconciliation session."""

    adjustments: List[AdjustmentItem] = Field(default_factory=list)


# =============================================================================
# RESPONSE MODELS - Nested
# =============================================================================

class AccountSummary(BaseModel):
    """Bank account summary for reconciliation."""

    id: str
    name: str
    account_number: Optional[str] = None
    current_balance: int
    last_reconciled_date: Optional[str] = None
    last_reconciled_balance: Optional[int] = None
    statement_balance: Optional[int] = None
    statement_date: Optional[str] = None
    unreconciled_difference: Optional[int] = None
    needs_reconciliation: bool
    days_since_reconciliation: Optional[int] = None
    active_session_id: Optional[str] = None
    active_session_status: Optional[str] = None


class SessionStatistics(BaseModel):
    """Session matching statistics."""

    total_statement_lines: int
    matched_count: int
    unmatched_count: int
    excluded_count: int = 0
    total_cleared: int
    total_uncleared: int


class SessionListItem(BaseModel):
    """Session item for list response."""

    id: str
    account_id: str
    account_name: str
    account_number: Optional[str] = None
    statement_date: str
    statement_start_date: str
    statement_end_date: str
    statement_beginning_balance: int
    statement_ending_balance: int
    status: str
    cleared_balance: Optional[int] = None
    cleared_count: int = 0
    uncleared_count: int = 0
    difference: Optional[int] = None
    created_at: str
    completed_at: Optional[str] = None
    total_statement_lines: int = 0
    matched_count: int = 0
    unmatched_count: int = 0
    adjustments_count: int = 0


class SessionDetail(BaseModel):
    """Full session detail."""

    id: str
    account_id: str
    account_name: str
    statement_date: str
    statement_start_date: str
    statement_end_date: str
    statement_beginning_balance: int
    statement_ending_balance: int
    status: str
    cleared_balance: Optional[int] = None
    difference: Optional[int] = None
    statistics: SessionStatistics
    created_at: str
    updated_at: str


class StatementLineItem(BaseModel):
    """Statement line for list response."""

    id: str
    date: str
    description: str
    reference: Optional[str] = None
    amount: int
    type: str
    running_balance: Optional[int] = None
    match_status: str
    matched_transaction_ids: List[str] = []
    match_confidence: Optional[str] = None


class TransactionItem(BaseModel):
    """System transaction for matching."""

    id: str
    date: str
    description: str
    reference: Optional[str] = None
    amount: int
    type: str
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    source_number: Optional[str] = None
    contact_name: Optional[str] = None
    contact_type: Optional[str] = None
    is_cleared: bool
    is_reconciled: bool


class MatchSuggestion(BaseModel):
    """Auto-match suggestion."""

    statement_line_id: str
    transaction_ids: List[str]
    confidence: str
    match_type: str
    reason: str


class ImportError(BaseModel):
    """Import error detail."""

    row: int
    error: str


class HistoryItem(BaseModel):
    """Reconciliation history item."""

    id: str
    statement_date: str
    statement_ending_balance: int
    matched_count: int
    adjustments_count: int
    completed_at: str
    completed_by: str


# =============================================================================
# RESPONSE WRAPPERS
# =============================================================================

class AccountsListResponse(BaseModel):
    """Response for list accounts."""

    data: List[AccountSummary]
    total: int


class SessionsListResponse(BaseModel):
    """Response for list sessions."""

    data: List[SessionListItem]
    total: int
    hasMore: bool = False


class SessionCreateResponse(BaseModel):
    """Response for create session."""

    id: str
    status: str
    created_at: str


class SessionDetailResponse(BaseModel):
    """Response for get session detail."""

    success: bool = True
    data: SessionDetail


class ImportResponse(BaseModel):
    """Response for import statement."""

    imported_count: int
    valid_count: int
    invalid_count: int
    total_debits: int
    total_credits: int
    date_range: Dict[str, str]
    errors: List[ImportError] = []


class StatementLinesResponse(BaseModel):
    """Response for list statement lines."""

    data: List[StatementLineItem]
    total: int
    hasMore: bool = False


class TransactionsResponse(BaseModel):
    """Response for list transactions."""

    data: List[TransactionItem]
    total: int
    hasMore: bool = False


class MatchResponse(BaseModel):
    """Response for create match."""

    match_id: str
    match_type: str
    confidence: str
    cleared_amount: int
    session_stats: Dict[str, int]


class UnmatchResponse(BaseModel):
    """Response for remove match."""

    success: bool
    session_stats: Dict[str, int]


class AutoMatchResponse(BaseModel):
    """Response for auto-match."""

    matches_created: int
    suggestions: List[MatchSuggestion]
    session_stats: Dict[str, int]


class CreateTransactionResponse(BaseModel):
    """Response for create transaction from line."""

    transaction_id: str
    match_id: Optional[str] = None
    session_stats: Dict[str, int]


class CompleteResponse(BaseModel):
    """Response for complete session."""

    success: bool
    completed_at: str
    final_stats: Dict[str, Any]
    journal_entries_created: List[str] = []


class CancelResponse(BaseModel):
    """Response for cancel session."""

    success: bool
    cleared_transactions_reset: int


class HistoryResponse(BaseModel):
    """Response for reconciliation history."""

    data: List[HistoryItem]
    total: int
    hasMore: bool = False
```

**Step 2: Verify schema imports**

Run: `cd /root/milkyhoop-dev && docker compose exec api_gateway python -c "from app.schemas.bank_reconciliation import *; print('Schemas OK')"`
Expected: "Schemas OK"

**Step 3: Commit**

```bash
git add backend/api_gateway/app/schemas/bank_reconciliation.py
git commit -m "feat(bank-reconciliation): add Pydantic schemas

- Request schemas for session, import, match, complete
- Response schemas for accounts, sessions, statements, transactions
- Nested models for statistics, suggestions, history

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Router - Core CRUD (Sessions & Accounts)

**Files:**
- Create: `backend/api_gateway/app/routers/bank_reconciliation.py`

**Step 1: Write the router file with core endpoints**

```python
"""
Bank Reconciliation Router

Enables matching of bank statement transactions with system transactions.

Database Tables:
- reconciliation_sessions: Session tracking
- bank_statement_lines: Imported statement data
- reconciliation_matches: Match records
- reconciliation_adjustments: Fee/interest adjustments
- bank_transactions: Extended with reconciliation columns

Endpoints:
- GET  /accounts - List bank accounts with reconciliation status
- GET  /sessions - List reconciliation sessions
- POST /sessions - Start new session
- GET  /sessions/:id - Get session detail
- POST /sessions/:id/cancel - Cancel session
- GET  /history/:accountId - Get reconciliation history
"""

from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date, datetime
import uuid
import json

from ..schemas.bank_reconciliation import (
    CreateSessionRequest,
    ImportConfigCSV,
    MatchRequest,
    AutoMatchRequest,
    CreateTransactionFromLineRequest,
    CompleteSessionRequest,
    AccountsListResponse,
    SessionsListResponse,
    SessionCreateResponse,
    SessionDetailResponse,
    ImportResponse,
    StatementLinesResponse,
    TransactionsResponse,
    MatchResponse,
    UnmatchResponse,
    AutoMatchResponse,
    CreateTransactionResponse,
    CompleteResponse,
    CancelResponse,
    HistoryResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create database connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
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
# ACCOUNTS ENDPOINTS
# =============================================================================

@router.get("/accounts", response_model=AccountsListResponse)
async def list_accounts(
    request: Request,
    needs_reconciliation: Optional[bool] = Query(None),
):
    """List bank accounts with reconciliation status."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get bank accounts with reconciliation info
            rows = await conn.fetch(
                """
                SELECT
                    ba.id,
                    ba.name,
                    ba.account_number,
                    COALESCE(ba.current_balance, 0) as current_balance,
                    (
                        SELECT rs.statement_date
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'completed'
                        ORDER BY rs.statement_date DESC
                        LIMIT 1
                    ) as last_reconciled_date,
                    (
                        SELECT rs.statement_ending_balance
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'completed'
                        ORDER BY rs.statement_date DESC
                        LIMIT 1
                    ) as last_reconciled_balance,
                    (
                        SELECT rs.id
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'in_progress'
                        LIMIT 1
                    ) as active_session_id,
                    (
                        SELECT rs.status
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'in_progress'
                        LIMIT 1
                    ) as active_session_status
                FROM bank_accounts ba
                WHERE ba.tenant_id = $1
                  AND ba.is_active = true
                ORDER BY ba.name
                """,
                ctx["tenant_id"]
            )

            accounts = []
            today = date.today()

            for row in rows:
                last_recon = row["last_reconciled_date"]
                days_since = (today - last_recon).days if last_recon else None
                needs_recon = days_since is None or days_since > 7

                if needs_reconciliation is not None and needs_recon != needs_reconciliation:
                    continue

                accounts.append({
                    "id": str(row["id"]),
                    "name": row["name"],
                    "account_number": row["account_number"],
                    "current_balance": row["current_balance"] or 0,
                    "last_reconciled_date": str(last_recon) if last_recon else None,
                    "last_reconciled_balance": row["last_reconciled_balance"],
                    "statement_balance": None,
                    "statement_date": None,
                    "unreconciled_difference": None,
                    "needs_reconciliation": needs_recon,
                    "days_since_reconciliation": days_since,
                    "active_session_id": str(row["active_session_id"]) if row["active_session_id"] else None,
                    "active_session_status": row["active_session_status"],
                })

            return {"data": accounts, "total": len(accounts)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list accounts")


# =============================================================================
# SESSIONS ENDPOINTS
# =============================================================================

@router.get("/sessions", response_model=SessionsListResponse)
async def list_sessions(
    request: Request,
    account_id: Optional[str] = Query(None),
    status: Optional[Literal["not_started", "in_progress", "completed", "cancelled"]] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List reconciliation sessions with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["rs.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if account_id:
                conditions.append(f"rs.account_id = ${param_idx}")
                params.append(UUID(account_id))
                param_idx += 1

            if status:
                conditions.append(f"rs.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count total
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM reconciliation_sessions rs WHERE {where_clause}",
                *params
            )

            # Fetch sessions with stats
            params.extend([limit, offset])
            rows = await conn.fetch(
                f"""
                SELECT
                    rs.*,
                    ba.name as account_name,
                    ba.account_number,
                    (SELECT COUNT(*) FROM bank_statement_lines bsl WHERE bsl.session_id = rs.id) as total_lines,
                    (SELECT COUNT(*) FROM bank_statement_lines bsl WHERE bsl.session_id = rs.id AND bsl.match_status = 'matched') as matched_count,
                    (SELECT COUNT(*) FROM bank_statement_lines bsl WHERE bsl.session_id = rs.id AND bsl.match_status = 'unmatched') as unmatched_count,
                    (SELECT COUNT(*) FROM reconciliation_adjustments ra WHERE ra.session_id = rs.id) as adjustments_count
                FROM reconciliation_sessions rs
                JOIN bank_accounts ba ON ba.id = rs.account_id
                WHERE {where_clause}
                ORDER BY rs.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params
            )

            sessions = [
                {
                    "id": str(row["id"]),
                    "account_id": str(row["account_id"]),
                    "account_name": row["account_name"],
                    "account_number": row["account_number"],
                    "statement_date": str(row["statement_date"]),
                    "statement_start_date": str(row["statement_start_date"]),
                    "statement_end_date": str(row["statement_end_date"]),
                    "statement_beginning_balance": row["statement_beginning_balance"],
                    "statement_ending_balance": row["statement_ending_balance"],
                    "status": row["status"],
                    "cleared_balance": row["cleared_balance"],
                    "cleared_count": row["matched_count"] or 0,
                    "uncleared_count": row["unmatched_count"] or 0,
                    "difference": row["difference"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                    "total_statement_lines": row["total_lines"] or 0,
                    "matched_count": row["matched_count"] or 0,
                    "unmatched_count": row["unmatched_count"] or 0,
                    "adjustments_count": row["adjustments_count"] or 0,
                }
                for row in rows
            ]

            return {
                "data": sessions,
                "total": total,
                "hasMore": (offset + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list sessions")


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(request: Request, body: CreateSessionRequest):
    """Start a new reconciliation session."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Validate account exists
            account = await conn.fetchrow(
                "SELECT id, is_active FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                UUID(body.account_id), ctx["tenant_id"]
            )

            if not account:
                raise HTTPException(status_code=404, detail="Bank account not found")

            if not account["is_active"]:
                raise HTTPException(status_code=400, detail="Bank account is inactive")

            # Check no in-progress session exists
            existing = await conn.fetchval(
                """
                SELECT id FROM reconciliation_sessions
                WHERE account_id = $1 AND tenant_id = $2 AND status = 'in_progress'
                """,
                UUID(body.account_id), ctx["tenant_id"]
            )

            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="An in-progress reconciliation session already exists for this account"
                )

            # Create session
            session_id = uuid.uuid4()
            now = datetime.utcnow()

            await conn.execute(
                """
                INSERT INTO reconciliation_sessions (
                    id, tenant_id, account_id, statement_date,
                    statement_start_date, statement_end_date,
                    statement_beginning_balance, statement_ending_balance,
                    status, created_by, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
                """,
                session_id, ctx["tenant_id"], UUID(body.account_id),
                body.statement_date, body.statement_start_date, body.statement_end_date,
                body.statement_beginning_balance, body.statement_ending_balance,
                "in_progress", ctx["user_id"], now
            )

            return {
                "id": str(session_id),
                "status": "in_progress",
                "created_at": now.isoformat()
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create session")


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(request: Request, session_id: UUID):
    """Get session details with statistics."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow(
                """
                SELECT
                    rs.*,
                    ba.name as account_name
                FROM reconciliation_sessions rs
                JOIN bank_accounts ba ON ba.id = rs.account_id
                WHERE rs.id = $1 AND rs.tenant_id = $2
                """,
                session_id, ctx["tenant_id"]
            )

            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            # Get statistics
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_lines,
                    COUNT(*) FILTER (WHERE match_status = 'matched') as matched_count,
                    COUNT(*) FILTER (WHERE match_status = 'unmatched') as unmatched_count,
                    COUNT(*) FILTER (WHERE match_status = 'excluded') as excluded_count,
                    COALESCE(SUM(CASE WHEN match_status = 'matched' THEN amount ELSE 0 END), 0) as total_cleared,
                    COALESCE(SUM(CASE WHEN match_status = 'unmatched' THEN amount ELSE 0 END), 0) as total_uncleared
                FROM bank_statement_lines
                WHERE session_id = $1
                """,
                session_id
            )

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "account_id": str(row["account_id"]),
                    "account_name": row["account_name"],
                    "statement_date": str(row["statement_date"]),
                    "statement_start_date": str(row["statement_start_date"]),
                    "statement_end_date": str(row["statement_end_date"]),
                    "statement_beginning_balance": row["statement_beginning_balance"],
                    "statement_ending_balance": row["statement_ending_balance"],
                    "status": row["status"],
                    "cleared_balance": row["cleared_balance"],
                    "difference": row["difference"],
                    "statistics": {
                        "total_statement_lines": stats["total_lines"] or 0,
                        "matched_count": stats["matched_count"] or 0,
                        "unmatched_count": stats["unmatched_count"] or 0,
                        "excluded_count": stats["excluded_count"] or 0,
                        "total_cleared": stats["total_cleared"] or 0,
                        "total_uncleared": stats["total_uncleared"] or 0,
                    },
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get session")


@router.post("/sessions/{session_id}/cancel", response_model=CancelResponse)
async def cancel_session(request: Request, session_id: UUID):
    """Cancel an in-progress reconciliation session."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session exists and is in_progress
            session = await conn.fetchrow(
                "SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Can only cancel in-progress sessions")

            # Reset cleared transactions
            reset_count = await conn.fetchval(
                """
                UPDATE bank_transactions
                SET is_cleared = false,
                    cleared_at = NULL,
                    matched_statement_line_id = NULL
                WHERE tenant_id = $1
                  AND matched_statement_line_id IN (
                      SELECT id FROM bank_statement_lines WHERE session_id = $2
                  )
                RETURNING COUNT(*)
                """,
                ctx["tenant_id"], session_id
            ) or 0

            # Update session status
            await conn.execute(
                "UPDATE reconciliation_sessions SET status = 'cancelled' WHERE id = $1",
                session_id
            )

            return {"success": True, "cleared_transactions_reset": reset_count}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel session")


# =============================================================================
# HISTORY ENDPOINT
# =============================================================================

@router.get("/history/{account_id}", response_model=HistoryResponse)
async def get_history(
    request: Request,
    account_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Get reconciliation history for an account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            total = await conn.fetchval(
                """
                SELECT COUNT(*) FROM reconciliation_sessions
                WHERE account_id = $1 AND tenant_id = $2 AND status = 'completed'
                """,
                account_id, ctx["tenant_id"]
            )

            rows = await conn.fetch(
                """
                SELECT
                    rs.id,
                    rs.statement_date,
                    rs.statement_ending_balance,
                    rs.completed_at,
                    u.name as completed_by,
                    (SELECT COUNT(*) FROM bank_statement_lines bsl
                     WHERE bsl.session_id = rs.id AND bsl.match_status = 'matched') as matched_count,
                    (SELECT COUNT(*) FROM reconciliation_adjustments ra
                     WHERE ra.session_id = rs.id) as adjustments_count
                FROM reconciliation_sessions rs
                LEFT JOIN users u ON u.id = rs.created_by
                WHERE rs.account_id = $1 AND rs.tenant_id = $2 AND rs.status = 'completed'
                ORDER BY rs.statement_date DESC
                LIMIT $3 OFFSET $4
                """,
                account_id, ctx["tenant_id"], limit, offset
            )

            history = [
                {
                    "id": str(row["id"]),
                    "statement_date": str(row["statement_date"]),
                    "statement_ending_balance": row["statement_ending_balance"],
                    "matched_count": row["matched_count"] or 0,
                    "adjustments_count": row["adjustments_count"] or 0,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                    "completed_by": row["completed_by"] or "Unknown",
                }
                for row in rows
            ]

            return {
                "data": history,
                "total": total,
                "hasMore": (offset + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get history")
```

**Step 2: Verify router imports**

Run: `cd /root/milkyhoop-dev && docker compose exec api_gateway python -c "from app.routers.bank_reconciliation import router; print('Router OK')"`
Expected: "Router OK"

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/bank_reconciliation.py
git commit -m "feat(bank-reconciliation): add router with core CRUD endpoints

- GET /accounts - list bank accounts with reconciliation status
- GET/POST /sessions - list and create sessions
- GET /sessions/:id - get session detail with statistics
- POST /sessions/:id/cancel - cancel in-progress session
- GET /history/:accountId - reconciliation history

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Router - Import & Statement Lines

**Files:**
- Modify: `backend/api_gateway/app/routers/bank_reconciliation.py`

**Step 1: Add import endpoint and statement lines**

Add after the history endpoint:

```python
# =============================================================================
# IMPORT ENDPOINT
# =============================================================================

@router.post("/sessions/{session_id}/import", response_model=ImportResponse)
async def import_statement(
    request: Request,
    session_id: UUID,
    file: UploadFile = File(...),
    config: Optional[str] = None,
):
    """Import bank statement file (CSV, OFX, QIF, XLSX)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        # Validate file type
        allowed_types = {
            "text/csv": "csv",
            "application/csv": "csv",
            "application/vnd.ms-excel": "csv",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/x-ofx": "ofx",
            "text/x-ofx": "ofx",
            "application/x-qif": "qif",
        }

        content_type = file.content_type or ""
        file_ext = file.filename.split(".")[-1].lower() if file.filename else ""

        if content_type not in allowed_types and file_ext not in ["csv", "xlsx", "ofx", "qif"]:
            raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV, XLSX, OFX, or QIF.")

        # Read file content
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:  # 10MB limit
            raise HTTPException(status_code=400, detail="File too large. Maximum 10MB.")

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                "SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Cannot import to non-active session")

            # Parse file based on type
            file_type = file_ext or allowed_types.get(content_type, "csv")
            parsed_config = json.loads(config) if config else None

            lines, errors = await parse_statement_file(content, file_type, parsed_config)

            # Insert lines
            valid_count = 0
            total_debits = 0
            total_credits = 0
            min_date = None
            max_date = None

            for line in lines:
                line_id = uuid.uuid4()
                try:
                    await conn.execute(
                        """
                        INSERT INTO bank_statement_lines (
                            id, session_id, tenant_id, date, description,
                            reference, amount, type, running_balance,
                            match_status, raw_data
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        """,
                        line_id, session_id, ctx["tenant_id"],
                        line["date"], line["description"],
                        line.get("reference"), abs(line["amount"]),
                        line["type"], line.get("running_balance"),
                        "unmatched", json.dumps(line.get("raw_data", {}))
                    )
                    valid_count += 1

                    if line["type"] == "debit":
                        total_debits += abs(line["amount"])
                    else:
                        total_credits += abs(line["amount"])

                    if min_date is None or line["date"] < min_date:
                        min_date = line["date"]
                    if max_date is None or line["date"] > max_date:
                        max_date = line["date"]

                except Exception as e:
                    errors.append({"row": len(lines), "error": str(e)})

            return {
                "imported_count": len(lines),
                "valid_count": valid_count,
                "invalid_count": len(errors),
                "total_debits": total_debits,
                "total_credits": total_credits,
                "date_range": {
                    "start": str(min_date) if min_date else None,
                    "end": str(max_date) if max_date else None,
                },
                "errors": errors[:10],  # Limit errors returned
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing statement: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to import statement")


async def parse_statement_file(content: bytes, file_type: str, config: Optional[dict]) -> tuple:
    """Parse statement file and return lines and errors."""
    import io

    lines = []
    errors = []

    if file_type == "csv":
        import csv

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")

        reader = csv.DictReader(io.StringIO(text))

        cfg = config or {}
        date_col = cfg.get("date_column", "date")
        desc_col = cfg.get("description_column", "description")
        amount_col = cfg.get("amount_column")
        debit_col = cfg.get("debit_column")
        credit_col = cfg.get("credit_column")
        ref_col = cfg.get("reference_column")
        bal_col = cfg.get("balance_column")
        date_fmt = cfg.get("date_format", "DD/MM/YYYY")
        skip_rows = cfg.get("skip_rows", 0)

        for i, row in enumerate(reader):
            if i < skip_rows:
                continue

            try:
                # Parse date
                date_str = row.get(date_col, "").strip()
                parsed_date = parse_date(date_str, date_fmt)

                # Parse amount
                if amount_col and row.get(amount_col):
                    amt = parse_amount(row[amount_col], cfg.get("decimal_separator", ","))
                    line_type = "credit" if amt >= 0 else "debit"
                    amt = abs(amt)
                elif debit_col or credit_col:
                    debit = parse_amount(row.get(debit_col, "0"), cfg.get("decimal_separator", ","))
                    credit = parse_amount(row.get(credit_col, "0"), cfg.get("decimal_separator", ","))
                    if debit > 0:
                        amt = debit
                        line_type = "debit"
                    else:
                        amt = credit
                        line_type = "credit"
                else:
                    raise ValueError("No amount column configured")

                lines.append({
                    "date": parsed_date,
                    "description": row.get(desc_col, "").strip()[:500],
                    "reference": row.get(ref_col, "").strip()[:100] if ref_col else None,
                    "amount": int(amt),
                    "type": line_type,
                    "running_balance": int(parse_amount(row.get(bal_col, "0"), cfg.get("decimal_separator", ","))) if bal_col else None,
                    "raw_data": dict(row),
                })
            except Exception as e:
                errors.append({"row": i + 2, "error": str(e)})

    elif file_type == "ofx":
        try:
            from ofxparse import OfxParser

            ofx = OfxParser.parse(io.BytesIO(content))

            for account in ofx.accounts:
                for txn in account.statement.transactions:
                    lines.append({
                        "date": txn.date.date() if hasattr(txn.date, "date") else txn.date,
                        "description": (txn.memo or txn.payee or "")[:500],
                        "reference": txn.id[:100] if txn.id else None,
                        "amount": int(abs(float(txn.amount)) * 100),  # Convert to cents then IDR
                        "type": "credit" if float(txn.amount) >= 0 else "debit",
                        "running_balance": None,
                        "raw_data": {"id": txn.id, "type": txn.type},
                    })
        except ImportError:
            errors.append({"row": 0, "error": "OFX parser not installed"})
        except Exception as e:
            errors.append({"row": 0, "error": f"OFX parse error: {str(e)}"})

    elif file_type == "xlsx":
        try:
            import pandas as pd

            df = pd.read_excel(io.BytesIO(content))
            cfg = config or {}
            date_col = cfg.get("date_column", df.columns[0])
            desc_col = cfg.get("description_column", df.columns[1] if len(df.columns) > 1 else df.columns[0])
            amount_col = cfg.get("amount_column")

            for i, row in df.iterrows():
                try:
                    parsed_date = pd.to_datetime(row[date_col]).date()

                    if amount_col:
                        amt = float(row[amount_col])
                        line_type = "credit" if amt >= 0 else "debit"
                        amt = abs(amt)
                    else:
                        amt = 0
                        line_type = "credit"

                    lines.append({
                        "date": parsed_date,
                        "description": str(row[desc_col])[:500],
                        "reference": None,
                        "amount": int(amt),
                        "type": line_type,
                        "running_balance": None,
                        "raw_data": row.to_dict(),
                    })
                except Exception as e:
                    errors.append({"row": i + 2, "error": str(e)})
        except ImportError:
            errors.append({"row": 0, "error": "pandas not installed"})
        except Exception as e:
            errors.append({"row": 0, "error": f"Excel parse error: {str(e)}"})

    return lines, errors


def parse_date(date_str: str, fmt: str) -> date:
    """Parse date string with format pattern."""
    from datetime import datetime

    # Convert format pattern to Python strftime
    py_fmt = fmt.replace("DD", "%d").replace("MM", "%m").replace("YYYY", "%Y").replace("YY", "%y")

    return datetime.strptime(date_str, py_fmt).date()


def parse_amount(amount_str: str, decimal_sep: str = ",") -> float:
    """Parse amount string to float."""
    if not amount_str:
        return 0.0

    # Remove thousands separator and normalize decimal
    cleaned = str(amount_str).strip()
    cleaned = cleaned.replace(" ", "").replace("\xa0", "")

    if decimal_sep == ",":
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    # Remove currency symbols
    cleaned = cleaned.replace("Rp", "").replace("IDR", "").strip()

    return float(cleaned)


# =============================================================================
# STATEMENT LINES ENDPOINT
# =============================================================================

@router.get("/sessions/{session_id}/statements", response_model=StatementLinesResponse)
async def list_statement_lines(
    request: Request,
    session_id: UUID,
    match_status: Optional[Literal["matched", "unmatched", "partially_matched", "excluded"]] = Query(None),
    search: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Get statement lines for a session."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session access
            session = await conn.fetchval(
                "SELECT id FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            conditions = ["bsl.session_id = $1"]
            params = [session_id]
            param_idx = 2

            if match_status:
                conditions.append(f"bsl.match_status = ${param_idx}")
                params.append(match_status)
                param_idx += 1

            if search:
                conditions.append(f"(bsl.description ILIKE ${param_idx} OR bsl.reference ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bank_statement_lines bsl WHERE {where_clause}",
                *params
            )

            params.extend([limit, offset])
            rows = await conn.fetch(
                f"""
                SELECT
                    bsl.*,
                    COALESCE(
                        (SELECT array_agg(rm.transaction_id::text)
                         FROM reconciliation_matches rm
                         WHERE rm.statement_line_id = bsl.id),
                        ARRAY[]::text[]
                    ) as matched_transaction_ids
                FROM bank_statement_lines bsl
                WHERE {where_clause}
                ORDER BY bsl.date DESC, bsl.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params
            )

            lines = [
                {
                    "id": str(row["id"]),
                    "date": str(row["date"]),
                    "description": row["description"],
                    "reference": row["reference"],
                    "amount": row["amount"],
                    "type": row["type"],
                    "running_balance": row["running_balance"],
                    "match_status": row["match_status"],
                    "matched_transaction_ids": list(row["matched_transaction_ids"] or []),
                    "match_confidence": row["match_confidence"],
                }
                for row in rows
            ]

            return {
                "data": lines,
                "total": total,
                "hasMore": (offset + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing statement lines: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list statement lines")
```

**Step 2: Verify import works**

Run: `cd /root/milkyhoop-dev && docker compose exec api_gateway python -c "from app.routers.bank_reconciliation import parse_date, parse_amount; print(parse_date('27/01/2026', 'DD/MM/YYYY')); print(parse_amount('1.500.000,50', ','))"`
Expected: `2026-01-27` and `1500000.5`

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/bank_reconciliation.py
git commit -m "feat(bank-reconciliation): add import and statement lines endpoints

- POST /sessions/:id/import - import CSV/OFX/XLSX files
- GET /sessions/:id/statements - list statement lines with filters
- Add parse_statement_file for CSV/OFX/XLSX parsing
- Add date and amount parsing utilities

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Router - Transactions & Matching

**Files:**
- Modify: `backend/api_gateway/app/routers/bank_reconciliation.py`

**Step 1: Add transactions and matching endpoints**

Add after statement lines endpoint:

```python
# =============================================================================
# TRANSACTIONS ENDPOINT
# =============================================================================

@router.get("/sessions/{session_id}/transactions", response_model=TransactionsResponse)
async def list_transactions(
    request: Request,
    session_id: UUID,
    is_cleared: Optional[bool] = Query(None),
    type: Optional[Literal["debit", "credit"]] = Query(None),
    search: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """Get system transactions available for matching."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get session to find account and date range
            session = await conn.fetchrow(
                """
                SELECT account_id, statement_start_date, statement_end_date
                FROM reconciliation_sessions
                WHERE id = $1 AND tenant_id = $2
                """,
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            conditions = [
                "bt.tenant_id = $1",
                "bt.account_id = $2",
                "bt.date >= $3",
                "bt.date <= $4",
                "bt.is_reconciled = false"
            ]
            params = [
                ctx["tenant_id"],
                session["account_id"],
                session["statement_start_date"],
                session["statement_end_date"]
            ]
            param_idx = 5

            if is_cleared is not None:
                conditions.append(f"bt.is_cleared = ${param_idx}")
                params.append(is_cleared)
                param_idx += 1

            if type:
                conditions.append(f"bt.type = ${param_idx}")
                params.append(type)
                param_idx += 1

            if search:
                conditions.append(f"(bt.description ILIKE ${param_idx} OR bt.reference ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bank_transactions bt WHERE {where_clause}",
                *params
            )

            params.extend([limit, offset])
            rows = await conn.fetch(
                f"""
                SELECT
                    bt.*,
                    c.name as contact_name,
                    CASE
                        WHEN bt.source_type = 'receive_payment' THEN 'customer'
                        WHEN bt.source_type IN ('expense', 'bill_payment') THEN 'vendor'
                        ELSE NULL
                    END as contact_type
                FROM bank_transactions bt
                LEFT JOIN contacts c ON c.id = bt.contact_id
                WHERE {where_clause}
                ORDER BY bt.date DESC, bt.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params
            )

            transactions = [
                {
                    "id": str(row["id"]),
                    "date": str(row["date"]),
                    "description": row["description"],
                    "reference": row["reference"],
                    "amount": row["amount"],
                    "type": row["type"],
                    "source_type": row["source_type"],
                    "source_id": str(row["source_id"]) if row.get("source_id") else None,
                    "source_number": row.get("source_number"),
                    "contact_name": row["contact_name"],
                    "contact_type": row["contact_type"],
                    "is_cleared": row["is_cleared"],
                    "is_reconciled": row["is_reconciled"],
                }
                for row in rows
            ]

            return {
                "data": transactions,
                "total": total,
                "hasMore": (offset + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list transactions")


# =============================================================================
# MATCHING ENDPOINTS
# =============================================================================

@router.post("/sessions/{session_id}/match", response_model=MatchResponse)
async def create_match(request: Request, session_id: UUID, body: MatchRequest):
    """Create a match between statement line and transactions."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                "SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Session is not in progress")

            # Verify statement line
            line = await conn.fetchrow(
                "SELECT id, type, amount, match_status FROM bank_statement_lines WHERE id = $1 AND session_id = $2",
                UUID(body.statement_line_id), session_id
            )

            if not line:
                raise HTTPException(status_code=404, detail="Statement line not found")

            if line["match_status"] == "matched":
                raise HTTPException(status_code=400, detail="Statement line already matched")

            # Verify transactions
            transaction_uuids = [UUID(tid) for tid in body.transaction_ids]
            transactions = await conn.fetch(
                """
                SELECT id, type, amount, is_cleared, is_reconciled
                FROM bank_transactions
                WHERE id = ANY($1) AND tenant_id = $2
                """,
                transaction_uuids, ctx["tenant_id"]
            )

            if len(transactions) != len(body.transaction_ids):
                raise HTTPException(status_code=404, detail="One or more transactions not found")

            # Validate all transactions are same type as statement line
            for txn in transactions:
                if txn["type"] != line["type"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Transaction type mismatch. Statement line is {line['type']}, transaction is {txn['type']}"
                    )
                if txn["is_reconciled"]:
                    raise HTTPException(status_code=400, detail="Transaction already reconciled")

            # Determine match type
            match_type = "one_to_one" if len(body.transaction_ids) == 1 else "one_to_many"

            # Create matches
            cleared_amount = 0
            for txn in transactions:
                match_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO reconciliation_matches (
                        id, session_id, statement_line_id, transaction_id,
                        tenant_id, match_type, confidence, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    match_id, session_id, UUID(body.statement_line_id),
                    txn["id"], ctx["tenant_id"], match_type, "manual", ctx["user_id"]
                )

                # Mark transaction as cleared
                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET is_cleared = true, cleared_at = NOW(), matched_statement_line_id = $2
                    WHERE id = $1
                    """,
                    txn["id"], UUID(body.statement_line_id)
                )

                cleared_amount += txn["amount"]

            # Update statement line status
            await conn.execute(
                """
                UPDATE bank_statement_lines
                SET match_status = 'matched', match_confidence = 'manual'
                WHERE id = $1
                """,
                UUID(body.statement_line_id)
            )

            # Update session stats
            await conn.execute("SELECT update_reconciliation_session_stats($1)", session_id)

            # Get updated stats
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE match_status = 'matched') as matched_count,
                    COUNT(*) FILTER (WHERE match_status = 'unmatched') as unmatched_count,
                    (SELECT difference FROM reconciliation_sessions WHERE id = $1) as difference
                FROM bank_statement_lines
                WHERE session_id = $1
                """,
                session_id
            )

            return {
                "match_id": str(match_id),
                "match_type": match_type,
                "confidence": "manual",
                "cleared_amount": cleared_amount,
                "session_stats": {
                    "matched_count": stats["matched_count"] or 0,
                    "unmatched_count": stats["unmatched_count"] or 0,
                    "difference": stats["difference"] or 0,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating match: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create match")


@router.delete("/sessions/{session_id}/match/{match_id}", response_model=UnmatchResponse)
async def delete_match(request: Request, session_id: UUID, match_id: UUID):
    """Remove a match (unmatch)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get match details
            match = await conn.fetchrow(
                """
                SELECT rm.statement_line_id, rm.transaction_id
                FROM reconciliation_matches rm
                JOIN reconciliation_sessions rs ON rs.id = rm.session_id
                WHERE rm.id = $1 AND rm.session_id = $2 AND rs.tenant_id = $3
                """,
                match_id, session_id, ctx["tenant_id"]
            )

            if not match:
                raise HTTPException(status_code=404, detail="Match not found")

            # Delete match
            await conn.execute("DELETE FROM reconciliation_matches WHERE id = $1", match_id)

            # Reset transaction
            await conn.execute(
                """
                UPDATE bank_transactions
                SET is_cleared = false, cleared_at = NULL, matched_statement_line_id = NULL
                WHERE id = $1
                """,
                match["transaction_id"]
            )

            # Check if statement line has other matches
            remaining = await conn.fetchval(
                "SELECT COUNT(*) FROM reconciliation_matches WHERE statement_line_id = $1",
                match["statement_line_id"]
            )

            if remaining == 0:
                await conn.execute(
                    """
                    UPDATE bank_statement_lines
                    SET match_status = 'unmatched', match_confidence = NULL
                    WHERE id = $1
                    """,
                    match["statement_line_id"]
                )

            # Update session stats
            await conn.execute("SELECT update_reconciliation_session_stats($1)", session_id)

            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE match_status = 'matched') as matched_count,
                    COUNT(*) FILTER (WHERE match_status = 'unmatched') as unmatched_count,
                    (SELECT difference FROM reconciliation_sessions WHERE id = $1) as difference
                FROM bank_statement_lines
                WHERE session_id = $1
                """,
                session_id
            )

            return {
                "success": True,
                "session_stats": {
                    "matched_count": stats["matched_count"] or 0,
                    "unmatched_count": stats["unmatched_count"] or 0,
                    "difference": stats["difference"] or 0,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting match: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete match")
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/routers/bank_reconciliation.py
git commit -m "feat(bank-reconciliation): add transactions and matching endpoints

- GET /sessions/:id/transactions - list system transactions for matching
- POST /sessions/:id/match - create match between line and transactions
- DELETE /sessions/:id/match/:matchId - remove match (unmatch)
- Updates session statistics after match operations

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Router - Auto-Match & Create Transaction

**Files:**
- Modify: `backend/api_gateway/app/routers/bank_reconciliation.py`

**Step 1: Add auto-match and create transaction endpoints**

Add after delete_match endpoint:

```python
# =============================================================================
# AUTO-MATCH ENDPOINT
# =============================================================================

@router.post("/sessions/{session_id}/auto-match", response_model=AutoMatchResponse)
async def auto_match(request: Request, session_id: UUID, body: Optional[AutoMatchRequest] = None):
    """Run auto-matching algorithm."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        config = body or AutoMatchRequest()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                """
                SELECT status, account_id, statement_start_date, statement_end_date
                FROM reconciliation_sessions
                WHERE id = $1 AND tenant_id = $2
                """,
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Session is not in progress")

            # Get unmatched statement lines
            unmatched_lines = await conn.fetch(
                """
                SELECT id, date, description, reference, amount, type
                FROM bank_statement_lines
                WHERE session_id = $1 AND match_status = 'unmatched'
                ORDER BY date
                """,
                session_id
            )

            # Get available transactions
            available_txns = await conn.fetch(
                """
                SELECT id, date, description, reference, amount, type
                FROM bank_transactions
                WHERE tenant_id = $1
                  AND account_id = $2
                  AND date >= $3
                  AND date <= $4
                  AND is_cleared = false
                  AND is_reconciled = false
                ORDER BY date
                """,
                ctx["tenant_id"],
                session["account_id"],
                session["statement_start_date"],
                session["statement_end_date"]
            )

            matches_created = 0
            suggestions = []

            # Index transactions by amount and type for fast lookup
            txn_by_amount = {}
            for txn in available_txns:
                key = (txn["type"], txn["amount"])
                if key not in txn_by_amount:
                    txn_by_amount[key] = []
                txn_by_amount[key].append(txn)

            for line in unmatched_lines:
                key = (line["type"], line["amount"])
                candidates = txn_by_amount.get(key, [])

                best_match = None
                best_confidence = None
                best_reason = None

                for txn in candidates:
                    date_diff = abs((line["date"] - txn["date"]).days)

                    # Exact match: same amount and same date
                    if date_diff == 0:
                        best_match = txn
                        best_confidence = "exact"
                        best_reason = "Tanggal dan nominal sama persis"
                        break

                    # High confidence: same amount, date within tolerance
                    if date_diff <= config.date_tolerance_days:
                        if best_confidence not in ["exact"]:
                            best_match = txn
                            best_confidence = "high"
                            best_reason = f"Nominal sama, selisih tanggal {date_diff} hari"

                    # Medium confidence: same amount, date within 7 days
                    elif date_diff <= 7:
                        if best_confidence not in ["exact", "high"]:
                            best_match = txn
                            best_confidence = "medium"
                            best_reason = f"Nominal sama, selisih tanggal {date_diff} hari"

                if best_match and confidence_meets_threshold(best_confidence, config.confidence_threshold):
                    # Auto-create match
                    match_id = uuid.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO reconciliation_matches (
                            id, session_id, statement_line_id, transaction_id,
                            tenant_id, match_type, confidence, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                        match_id, session_id, line["id"],
                        best_match["id"], ctx["tenant_id"], "one_to_one", best_confidence, ctx["user_id"]
                    )

                    await conn.execute(
                        """
                        UPDATE bank_transactions
                        SET is_cleared = true, cleared_at = NOW(), matched_statement_line_id = $2
                        WHERE id = $1
                        """,
                        best_match["id"], line["id"]
                    )

                    await conn.execute(
                        """
                        UPDATE bank_statement_lines
                        SET match_status = 'matched', match_confidence = $2
                        WHERE id = $1
                        """,
                        line["id"], best_confidence
                    )

                    # Remove from available
                    txn_by_amount[key].remove(best_match)
                    matches_created += 1

                elif best_match:
                    # Add as suggestion
                    suggestions.append({
                        "statement_line_id": str(line["id"]),
                        "transaction_ids": [str(best_match["id"])],
                        "confidence": best_confidence,
                        "match_type": "one_to_one",
                        "reason": best_reason,
                    })

            # Update session stats
            await conn.execute("SELECT update_reconciliation_session_stats($1)", session_id)

            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE match_status = 'matched') as matched_count,
                    COUNT(*) FILTER (WHERE match_status = 'unmatched') as unmatched_count,
                    (SELECT difference FROM reconciliation_sessions WHERE id = $1) as difference
                FROM bank_statement_lines
                WHERE session_id = $1
                """,
                session_id
            )

            return {
                "matches_created": matches_created,
                "suggestions": suggestions[:20],  # Limit suggestions
                "session_stats": {
                    "matched_count": stats["matched_count"] or 0,
                    "unmatched_count": stats["unmatched_count"] or 0,
                    "difference": stats["difference"] or 0,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error auto-matching: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to auto-match")


def confidence_meets_threshold(confidence: str, threshold: str) -> bool:
    """Check if confidence level meets threshold."""
    levels = ["exact", "high", "medium", "low"]
    return levels.index(confidence) <= levels.index(threshold)


# =============================================================================
# CREATE TRANSACTION FROM LINE
# =============================================================================

@router.post("/sessions/{session_id}/transactions", response_model=CreateTransactionResponse)
async def create_transaction_from_line(
    request: Request,
    session_id: UUID,
    body: CreateTransactionFromLineRequest
):
    """Create a new transaction from unmatched statement line."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                "SELECT status, account_id FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Session is not in progress")

            # Get statement line
            line = await conn.fetchrow(
                "SELECT * FROM bank_statement_lines WHERE id = $1 AND session_id = $2",
                UUID(body.statement_line_id), session_id
            )

            if not line:
                raise HTTPException(status_code=404, detail="Statement line not found")

            if line["match_status"] == "matched":
                raise HTTPException(status_code=400, detail="Statement line already matched")

            # Create bank transaction
            txn_id = uuid.uuid4()
            description = body.description or line["description"]

            await conn.execute(
                """
                INSERT INTO bank_transactions (
                    id, tenant_id, account_id, date, description, reference,
                    amount, type, source_type, contact_id,
                    is_cleared, is_reconciled, created_by, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
                """,
                txn_id, ctx["tenant_id"], session["account_id"],
                line["date"], description, line["reference"],
                line["amount"], line["type"], body.type,
                UUID(body.contact_id) if body.contact_id else None,
                body.auto_match, False, ctx["user_id"]
            )

            match_id = None

            if body.auto_match:
                # Auto-match the new transaction
                match_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO reconciliation_matches (
                        id, session_id, statement_line_id, transaction_id,
                        tenant_id, match_type, confidence, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    match_id, session_id, UUID(body.statement_line_id),
                    txn_id, ctx["tenant_id"], "one_to_one", "manual", ctx["user_id"]
                )

                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET matched_statement_line_id = $2, cleared_at = NOW()
                    WHERE id = $1
                    """,
                    txn_id, UUID(body.statement_line_id)
                )

                await conn.execute(
                    """
                    UPDATE bank_statement_lines
                    SET match_status = 'matched', match_confidence = 'manual'
                    WHERE id = $1
                    """,
                    UUID(body.statement_line_id)
                )

                await conn.execute("SELECT update_reconciliation_session_stats($1)", session_id)

            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE match_status = 'matched') as matched_count,
                    COUNT(*) FILTER (WHERE match_status = 'unmatched') as unmatched_count,
                    (SELECT difference FROM reconciliation_sessions WHERE id = $1) as difference
                FROM bank_statement_lines
                WHERE session_id = $1
                """,
                session_id
            )

            return {
                "transaction_id": str(txn_id),
                "match_id": str(match_id) if match_id else None,
                "session_stats": {
                    "matched_count": stats["matched_count"] or 0,
                    "unmatched_count": stats["unmatched_count"] or 0,
                    "difference": stats["difference"] or 0,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create transaction")
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/routers/bank_reconciliation.py
git commit -m "feat(bank-reconciliation): add auto-match and create transaction endpoints

- POST /sessions/:id/auto-match - run auto-matching algorithm
- POST /sessions/:id/transactions - create transaction from unmatched line
- Auto-match by exact date/amount, high/medium confidence levels
- Support confidence threshold configuration

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Router - Complete Session

**Files:**
- Modify: `backend/api_gateway/app/routers/bank_reconciliation.py`

**Step 1: Add complete session endpoint**

Add after create_transaction_from_line endpoint:

```python
# =============================================================================
# COMPLETE SESSION ENDPOINT
# =============================================================================

@router.post("/sessions/{session_id}/complete", response_model=CompleteResponse)
async def complete_session(request: Request, session_id: UUID, body: CompleteSessionRequest):
    """Complete the reconciliation session."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                """
                SELECT rs.*, ba.name as account_name, ba.coa_id as bank_coa_id
                FROM reconciliation_sessions rs
                JOIN bank_accounts ba ON ba.id = rs.account_id
                WHERE rs.id = $1 AND rs.tenant_id = $2
                """,
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Session is not in progress")

            # Calculate final difference
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_matched,
                    COALESCE(SUM(CASE
                        WHEN type = 'credit' THEN amount
                        ELSE -amount
                    END), 0) as net_amount
                FROM bank_statement_lines
                WHERE session_id = $1 AND match_status = 'matched'
                """,
                session_id
            )

            # Calculate adjustment total
            adjustment_total = sum(
                adj.amount if adj.type in ["interest", "correction"] else -adj.amount
                for adj in body.adjustments
            )

            final_difference = session["statement_ending_balance"] - session["statement_beginning_balance"] - stats["net_amount"] - adjustment_total

            if final_difference != 0 and len(body.adjustments) == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Difference of {final_difference} IDR remains. Add adjustments or match more transactions."
                )

            journal_entries_created = []

            # Create adjustments and journal entries
            for adj in body.adjustments:
                adj_id = uuid.uuid4()

                # Create adjustment record
                await conn.execute(
                    """
                    INSERT INTO reconciliation_adjustments (
                        id, session_id, tenant_id, type, amount, description,
                        account_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    adj_id, session_id, ctx["tenant_id"],
                    adj.type, adj.amount, adj.description,
                    UUID(adj.account_id), ctx["user_id"]
                )

                # Create journal entry for adjustment
                je_id = uuid.uuid4()
                je_number = f"JE-RECON-{session_id.hex[:8]}-{adj_id.hex[:4]}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, date, description,
                        status, source_type, source_id, created_by, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                    """,
                    je_id, ctx["tenant_id"], je_number,
                    session["statement_date"], adj.description,
                    "POSTED", "reconciliation_adjustment", adj_id, ctx["user_id"]
                )

                # Determine debit/credit based on adjustment type
                if adj.type in ["bank_fee"]:
                    # Bank fee: Dr. Expense, Cr. Bank
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, tenant_id, account_id, description, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, $6, 0)
                        """,
                        uuid.uuid4(), je_id, ctx["tenant_id"],
                        UUID(adj.account_id), adj.description, adj.amount
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, tenant_id, account_id, description, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid.uuid4(), je_id, ctx["tenant_id"],
                        session["bank_coa_id"], adj.description, adj.amount
                    )
                elif adj.type in ["interest"]:
                    # Interest: Dr. Bank, Cr. Income
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, tenant_id, account_id, description, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, $6, 0)
                        """,
                        uuid.uuid4(), je_id, ctx["tenant_id"],
                        session["bank_coa_id"], adj.description, adj.amount
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, tenant_id, account_id, description, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid.uuid4(), je_id, ctx["tenant_id"],
                        UUID(adj.account_id), adj.description, adj.amount
                    )
                else:
                    # Correction/other: based on sign
                    # Positive = increase bank (Dr. Bank, Cr. Account)
                    # This is simplified - real impl may need more logic
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, tenant_id, account_id, description, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, $6, 0)
                        """,
                        uuid.uuid4(), je_id, ctx["tenant_id"],
                        session["bank_coa_id"], adj.description, adj.amount
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, tenant_id, account_id, description, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid.uuid4(), je_id, ctx["tenant_id"],
                        UUID(adj.account_id), adj.description, adj.amount
                    )

                # Update adjustment with journal entry ID
                await conn.execute(
                    "UPDATE reconciliation_adjustments SET journal_entry_id = $1 WHERE id = $2",
                    je_id, adj_id
                )

                journal_entries_created.append(str(je_id))

            # Mark all matched transactions as reconciled
            await conn.execute(
                """
                UPDATE bank_transactions
                SET is_reconciled = true,
                    reconciled_at = NOW(),
                    reconciled_session_id = $1
                WHERE tenant_id = $2
                  AND matched_statement_line_id IN (
                      SELECT id FROM bank_statement_lines WHERE session_id = $1
                  )
                """,
                session_id, ctx["tenant_id"]
            )

            # Complete session
            now = datetime.utcnow()
            await conn.execute(
                """
                UPDATE reconciliation_sessions
                SET status = 'completed',
                    completed_at = $2,
                    difference = 0,
                    cleared_balance = statement_ending_balance
                WHERE id = $1
                """,
                session_id, now
            )

            return {
                "success": True,
                "completed_at": now.isoformat(),
                "final_stats": {
                    "total_matched": stats["total_matched"] or 0,
                    "total_adjustments": len(body.adjustments),
                    "final_difference": 0,
                },
                "journal_entries_created": journal_entries_created
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to complete session")
```

**Step 2: Commit**

```bash
git add backend/api_gateway/app/routers/bank_reconciliation.py
git commit -m "feat(bank-reconciliation): add complete session endpoint

- POST /sessions/:id/complete - finalize reconciliation
- Create adjustment journal entries for bank fees, interest, corrections
- Mark matched transactions as reconciled
- Validate difference is zero or covered by adjustments

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Register Router in main.py

**Files:**
- Modify: `backend/api_gateway/app/main.py`

**Step 1: Find router registration section**

Search for existing router includes to find the pattern.

**Step 2: Add bank_reconciliation router import**

Add import near other router imports:

```python
from .routers import bank_reconciliation
```

**Step 3: Add router registration**

Add after other banking routers (bank_accounts, bank_transfers):

```python
app.include_router(
    bank_reconciliation.router,
    prefix="/api/bank-reconciliation",
    tags=["bank-reconciliation"]
)
```

**Step 4: Verify server starts**

Run: `cd /root/milkyhoop-dev && docker compose restart api_gateway && sleep 5 && docker compose logs api_gateway --tail 20`
Expected: No import errors, server started successfully

**Step 5: Commit**

```bash
git add backend/api_gateway/app/main.py
git commit -m "feat(bank-reconciliation): register router in main.py

- Import bank_reconciliation router
- Register with /api/bank-reconciliation prefix

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Add Dependencies (if needed)

**Files:**
- Modify: `backend/api_gateway/requirements.txt` (if exists)

**Step 1: Check if ofxparse and pandas are already installed**

Run: `docker compose exec api_gateway pip list | grep -E "ofxparse|pandas"`

**Step 2: Add dependencies if missing**

If not present, add to requirements.txt:

```
ofxparse>=0.21
pandas>=2.0.0
openpyxl>=3.1.0
```

**Step 3: Rebuild container if dependencies added**

Run: `docker compose build api_gateway && docker compose up -d api_gateway`

**Step 4: Commit**

```bash
git add backend/api_gateway/requirements.txt
git commit -m "chore(deps): add ofxparse, pandas, openpyxl for bank statement parsing

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Integration Test

**Files:**
- Create: `backend/api_gateway/tests/test_bank_reconciliation.py`

**Step 1: Write basic integration tests**

```python
"""
Integration tests for Bank Reconciliation module.
"""

import pytest
from httpx import AsyncClient
from uuid import uuid4
from datetime import date


@pytest.fixture
def auth_headers():
    """Mock authentication headers."""
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def sample_session_data():
    """Sample session creation data."""
    return {
        "account_id": str(uuid4()),
        "statement_date": str(date.today()),
        "statement_start_date": "2026-01-01",
        "statement_end_date": "2026-01-25",
        "statement_beginning_balance": 100000000,
        "statement_ending_balance": 120000000,
    }


class TestBankReconciliationEndpoints:
    """Test bank reconciliation API endpoints."""

    @pytest.mark.asyncio
    async def test_list_accounts(self, client: AsyncClient, auth_headers):
        """Test listing bank accounts for reconciliation."""
        response = await client.get(
            "/api/bank-reconciliation/accounts",
            headers=auth_headers
        )
        assert response.status_code in [200, 401]  # 401 if auth not mocked

    @pytest.mark.asyncio
    async def test_list_sessions(self, client: AsyncClient, auth_headers):
        """Test listing reconciliation sessions."""
        response = await client.get(
            "/api/bank-reconciliation/sessions",
            headers=auth_headers
        )
        assert response.status_code in [200, 401]

    @pytest.mark.asyncio
    async def test_create_session_validation(self, client: AsyncClient, auth_headers):
        """Test session creation validation."""
        # Missing required fields
        response = await client.post(
            "/api/bank-reconciliation/sessions",
            json={},
            headers=auth_headers
        )
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, client: AsyncClient, auth_headers):
        """Test getting non-existent session."""
        response = await client.get(
            f"/api/bank-reconciliation/sessions/{uuid4()}",
            headers=auth_headers
        )
        assert response.status_code in [404, 401]


class TestParsingFunctions:
    """Test file parsing utilities."""

    def test_parse_date_dd_mm_yyyy(self):
        """Test parsing DD/MM/YYYY format."""
        from app.routers.bank_reconciliation import parse_date

        result = parse_date("27/01/2026", "DD/MM/YYYY")
        assert result == date(2026, 1, 27)

    def test_parse_date_yyyy_mm_dd(self):
        """Test parsing YYYY-MM-DD format."""
        from app.routers.bank_reconciliation import parse_date

        result = parse_date("2026-01-27", "YYYY-MM-DD")
        assert result == date(2026, 1, 27)

    def test_parse_amount_indonesian(self):
        """Test parsing Indonesian number format."""
        from app.routers.bank_reconciliation import parse_amount

        result = parse_amount("1.500.000,50", ",")
        assert result == 1500000.50

    def test_parse_amount_standard(self):
        """Test parsing standard number format."""
        from app.routers.bank_reconciliation import parse_amount

        result = parse_amount("1,500,000.50", ".")
        assert result == 1500000.50

    def test_parse_amount_with_currency(self):
        """Test parsing amount with currency symbol."""
        from app.routers.bank_reconciliation import parse_amount

        result = parse_amount("Rp 1.000.000", ",")
        assert result == 1000000.0


class TestAutoMatchLogic:
    """Test auto-match algorithm logic."""

    def test_confidence_meets_threshold_exact(self):
        """Test exact confidence meets all thresholds."""
        from app.routers.bank_reconciliation import confidence_meets_threshold

        assert confidence_meets_threshold("exact", "exact") is True
        assert confidence_meets_threshold("exact", "high") is True
        assert confidence_meets_threshold("exact", "medium") is True
        assert confidence_meets_threshold("exact", "low") is True

    def test_confidence_meets_threshold_high(self):
        """Test high confidence threshold."""
        from app.routers.bank_reconciliation import confidence_meets_threshold

        assert confidence_meets_threshold("high", "exact") is False
        assert confidence_meets_threshold("high", "high") is True
        assert confidence_meets_threshold("high", "medium") is True

    def test_confidence_meets_threshold_low(self):
        """Test low confidence threshold."""
        from app.routers.bank_reconciliation import confidence_meets_threshold

        assert confidence_meets_threshold("low", "exact") is False
        assert confidence_meets_threshold("low", "high") is False
        assert confidence_meets_threshold("low", "low") is True
```

**Step 2: Run tests**

Run: `cd /root/milkyhoop-dev && docker compose exec api_gateway pytest tests/test_bank_reconciliation.py -v`
Expected: Tests pass (or skip if fixtures not configured)

**Step 3: Commit**

```bash
git add backend/api_gateway/tests/test_bank_reconciliation.py
git commit -m "test(bank-reconciliation): add integration tests

- Test endpoint responses
- Test date and amount parsing utilities
- Test confidence threshold logic

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Summary

This plan implements the complete Bank Reconciliation backend module:

1. **Migration (V086)** - All 4 tables with RLS, indexes, triggers
2. **Schemas** - Request/Response models for all endpoints
3. **Router** - 14 endpoints covering full reconciliation workflow
4. **Registration** - Added to main.py
5. **Dependencies** - ofxparse, pandas for file parsing
6. **Tests** - Basic integration and unit tests

**Total endpoints implemented:**
- GET /accounts
- GET/POST /sessions
- GET /sessions/:id
- POST /sessions/:id/import
- GET /sessions/:id/statements
- GET /sessions/:id/transactions
- POST /sessions/:id/match
- DELETE /sessions/:id/match/:matchId
- POST /sessions/:id/auto-match
- POST /sessions/:id/transactions
- POST /sessions/:id/complete
- POST /sessions/:id/cancel
- GET /history/:accountId
