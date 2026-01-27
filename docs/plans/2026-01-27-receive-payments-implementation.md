# Receive Payments Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement backend API for receiving customer payments with invoice allocation and deposit integration.

**Architecture:** Hybrid approach - separate `receive_payments` module that integrates with existing `customer_deposits`. Payments from cash/bank create journal entries directly; payments from deposits reduce deposit balance. Overpayments auto-create customer deposits.

**Tech Stack:** FastAPI, asyncpg (raw SQL), PostgreSQL with RLS, Pydantic schemas

---

## Task 1: Database Migration

**Files:**
- Create: `backend/migrations/V085__receive_payments.sql`

**Step 1: Write the migration file**

```sql
-- ============================================================================
-- V085: Receive Payments Module (Penerimaan Pembayaran)
-- ============================================================================
-- Purpose: Track customer payments for invoices with deposit integration
-- Creates tables: receive_payments, receive_payment_allocations, receive_payment_sequences
-- Integrates with: customer_deposits (overpayment creates deposit, can pay from deposit)
-- ============================================================================

-- ============================================================================
-- 1. RECEIVE PAYMENTS TABLE - Main records
-- ============================================================================

CREATE TABLE IF NOT EXISTS receive_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Payment identification
    payment_number VARCHAR(50) NOT NULL,

    -- Customer reference
    customer_id UUID REFERENCES customers(id),
    customer_name VARCHAR(255) NOT NULL,

    -- Payment details
    payment_date DATE NOT NULL,
    payment_method VARCHAR(30) NOT NULL,  -- cash, bank_transfer
    bank_account_id UUID NOT NULL,        -- CoA account for cash/bank
    bank_account_name VARCHAR(255) NOT NULL,

    -- Source tracking (cash payment vs from deposit)
    source_type VARCHAR(20) NOT NULL DEFAULT 'cash',  -- 'cash', 'deposit'
    source_deposit_id UUID REFERENCES customer_deposits(id),

    -- Amounts (BIGINT for IDR)
    total_amount BIGINT NOT NULL,
    allocated_amount BIGINT NOT NULL DEFAULT 0,
    unapplied_amount BIGINT NOT NULL DEFAULT 0,
    discount_amount BIGINT NOT NULL DEFAULT 0,
    discount_account_id UUID,  -- CoA account for discount

    -- Status: draft -> posted -> voided
    status VARCHAR(20) DEFAULT 'draft',

    -- Reference & notes
    reference_number VARCHAR(100),
    notes TEXT,

    -- Accounting integration
    journal_id UUID,
    journal_number VARCHAR(50),
    void_journal_id UUID,

    -- Overpayment creates deposit - track link
    created_deposit_id UUID REFERENCES customer_deposits(id),

    -- Status tracking
    posted_at TIMESTAMPTZ,
    posted_by UUID,
    voided_at TIMESTAMPTZ,
    voided_by UUID,
    void_reason TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_rcv_payments_tenant_number UNIQUE(tenant_id, payment_number),
    CONSTRAINT chk_rcv_payment_status CHECK (status IN ('draft', 'posted', 'voided')),
    CONSTRAINT chk_rcv_payment_method CHECK (payment_method IN ('cash', 'bank_transfer')),
    CONSTRAINT chk_rcv_payment_source CHECK (source_type IN ('cash', 'deposit')),
    CONSTRAINT chk_rcv_payment_source_valid CHECK (
        (source_type = 'cash' AND source_deposit_id IS NULL) OR
        (source_type = 'deposit' AND source_deposit_id IS NOT NULL)
    )
);

COMMENT ON TABLE receive_payments IS 'Penerimaan Pembayaran - Customer payments for invoices';
COMMENT ON COLUMN receive_payments.source_type IS 'cash=normal payment, deposit=payment from customer deposit';
COMMENT ON COLUMN receive_payments.created_deposit_id IS 'If overpayment, links to auto-created customer deposit';

-- ============================================================================
-- 2. RECEIVE PAYMENT ALLOCATIONS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS receive_payment_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    payment_id UUID NOT NULL REFERENCES receive_payments(id) ON DELETE CASCADE,

    -- Invoice reference
    invoice_id UUID NOT NULL REFERENCES sales_invoices(id),
    invoice_number VARCHAR(50) NOT NULL,

    -- Allocation details
    invoice_amount BIGINT NOT NULL,      -- Original invoice total
    remaining_before BIGINT NOT NULL,    -- Remaining before this payment
    amount_applied BIGINT NOT NULL,      -- Amount applied from this payment
    remaining_after BIGINT NOT NULL,     -- Remaining after this payment

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_rcv_payment_allocation UNIQUE(tenant_id, payment_id, invoice_id)
);

COMMENT ON TABLE receive_payment_allocations IS 'Tracks which invoices a payment is applied to';

-- ============================================================================
-- 3. RECEIVE PAYMENT SEQUENCE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS receive_payment_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INT NOT NULL DEFAULT 0,
    year INT NOT NULL
);

-- ============================================================================
-- 4. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_rcv_payments_tenant_status ON receive_payments(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_tenant_date ON receive_payments(tenant_id, payment_date);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_customer ON receive_payments(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_number ON receive_payments(tenant_id, payment_number);
CREATE INDEX IF NOT EXISTS idx_rcv_payments_source_deposit ON receive_payments(source_deposit_id) WHERE source_deposit_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rcv_alloc_payment ON receive_payment_allocations(payment_id);
CREATE INDEX IF NOT EXISTS idx_rcv_alloc_invoice ON receive_payment_allocations(invoice_id);
CREATE INDEX IF NOT EXISTS idx_rcv_alloc_tenant ON receive_payment_allocations(tenant_id);

-- ============================================================================
-- 5. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE receive_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE receive_payment_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE receive_payment_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_receive_payments ON receive_payments;
DROP POLICY IF EXISTS rls_receive_payment_allocations ON receive_payment_allocations;
DROP POLICY IF EXISTS rls_receive_payment_sequences ON receive_payment_sequences;

CREATE POLICY rls_receive_payments ON receive_payments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_receive_payment_allocations ON receive_payment_allocations
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_receive_payment_sequences ON receive_payment_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 6. FUNCTIONS
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_receive_payment_number(p_tenant_id TEXT)
RETURNS VARCHAR AS $$
DECLARE
    v_year INT;
    v_next_number INT;
    v_payment_number VARCHAR(50);
BEGIN
    v_year := EXTRACT(YEAR FROM CURRENT_DATE);

    INSERT INTO receive_payment_sequences (tenant_id, last_number, year)
    VALUES (p_tenant_id, 1, v_year)
    ON CONFLICT (tenant_id)
    DO UPDATE SET
        last_number = CASE
            WHEN receive_payment_sequences.year = v_year
            THEN receive_payment_sequences.last_number + 1
            ELSE 1
        END,
        year = v_year
    RETURNING last_number INTO v_next_number;

    -- Format: RCV-YYYY-NNNN
    v_payment_number := 'RCV-' || v_year || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_payment_number;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 7. UPDATED_AT TRIGGER
-- ============================================================================

CREATE OR REPLACE FUNCTION update_receive_payments_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_receive_payments_updated_at ON receive_payments;
CREATE TRIGGER trg_receive_payments_updated_at
    BEFORE UPDATE ON receive_payments
    FOR EACH ROW EXECUTE FUNCTION update_receive_payments_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V085: Receive Payments created successfully';
    RAISE NOTICE 'Tables: receive_payments, receive_payment_allocations, receive_payment_sequences';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
```

**Step 2: Verify migration file syntax**

Run: `cd /root/milkyhoop-dev && cat backend/migrations/V085__receive_payments.sql | head -20`

Expected: First 20 lines of migration shown without error

**Step 3: Commit**

```bash
git add backend/migrations/V085__receive_payments.sql
git commit -m "feat(receive-payments): add database migration V085"
```

---

## Task 2: Pydantic Schemas

**Files:**
- Create: `backend/api_gateway/app/schemas/receive_payments.py`

**Step 1: Write the schema file**

```python
"""
Pydantic schemas for Receive Payments module (Penerimaan Pembayaran).

Receive Payments handle customer payments for invoices:
- Can allocate to one or more invoices
- Overpayment creates customer deposit automatically
- Can pay from existing customer deposit

Flow: draft -> posted -> voided (optional)

Journal Entry on POST (Cash/Bank):
    Dr. Kas/Bank                        total_amount
    Dr. Potongan Penjualan (if any)     discount_amount
        Cr. Piutang Usaha                   allocated_amount
        Cr. Uang Muka Pelanggan (if any)    unapplied_amount

Journal Entry on POST (From Deposit):
    Dr. Uang Muka Pelanggan             total_amount
        Cr. Piutang Usaha                   allocated_amount
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import date


# =============================================================================
# REQUEST MODELS - Allocation
# =============================================================================

class AllocationInput(BaseModel):
    """Single invoice allocation in create/update request."""
    invoice_id: str = Field(..., description="Invoice UUID to allocate payment to")
    amount_applied: int = Field(..., gt=0, description="Amount to apply in IDR")


# =============================================================================
# REQUEST MODELS - Receive Payment
# =============================================================================

class CreateReceivePaymentRequest(BaseModel):
    """Request body for creating a receive payment."""
    customer_id: str = Field(..., description="Customer UUID")
    customer_name: str = Field(..., min_length=1, max_length=255)
    payment_date: date
    payment_method: Literal["cash", "bank_transfer"]
    bank_account_id: str = Field(..., description="Kas/Bank account UUID (CoA)")
    bank_account_name: str = Field(..., min_length=1, max_length=255)
    total_amount: int = Field(..., gt=0, description="Total payment amount in IDR")
    discount_amount: int = Field(0, ge=0, description="Early payment discount in IDR")
    discount_account_id: Optional[str] = Field(None, description="Discount account UUID (CoA)")
    source_type: Literal["cash", "deposit"] = "cash"
    source_deposit_id: Optional[str] = Field(None, description="Deposit UUID if source_type='deposit'")
    reference_number: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    allocations: List[AllocationInput] = Field(default_factory=list)
    save_as_draft: bool = Field(False, description="If true, save as draft without posting")

    @field_validator('customer_name')
    @classmethod
    def validate_customer_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Customer name is required')
        return v.strip()

    @field_validator('source_deposit_id')
    @classmethod
    def validate_source_deposit(cls, v, info):
        source_type = info.data.get('source_type', 'cash')
        if source_type == 'deposit' and not v:
            raise ValueError('source_deposit_id is required when source_type is deposit')
        if source_type == 'cash' and v:
            raise ValueError('source_deposit_id must be null when source_type is cash')
        return v


class UpdateReceivePaymentRequest(BaseModel):
    """Request body for updating a draft receive payment."""
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    payment_date: Optional[date] = None
    payment_method: Optional[Literal["cash", "bank_transfer"]] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    total_amount: Optional[int] = Field(None, gt=0)
    discount_amount: Optional[int] = Field(None, ge=0)
    discount_account_id: Optional[str] = None
    source_type: Optional[Literal["cash", "deposit"]] = None
    source_deposit_id: Optional[str] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    allocations: Optional[List[AllocationInput]] = None


class VoidPaymentRequest(BaseModel):
    """Request body for voiding a posted payment."""
    void_reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - Allocation
# =============================================================================

class AllocationResponse(BaseModel):
    """Invoice allocation in response."""
    id: str
    invoice_id: str
    invoice_number: str
    invoice_amount: int
    remaining_before: int
    amount_applied: int
    remaining_after: int


# =============================================================================
# RESPONSE MODELS - Receive Payment
# =============================================================================

class ReceivePaymentListItem(BaseModel):
    """Receive payment item for list responses."""
    id: str
    payment_number: str
    customer_id: Optional[str] = None
    customer_name: str
    payment_date: str
    payment_method: str
    source_type: str
    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    status: str
    invoice_count: int = 0
    created_at: str


class ReceivePaymentDetail(BaseModel):
    """Full receive payment detail."""
    id: str
    payment_number: str
    customer_id: Optional[str] = None
    customer_name: str

    # Payment details
    payment_date: str
    payment_method: str
    bank_account_id: str
    bank_account_name: str

    # Source
    source_type: str
    source_deposit_id: Optional[str] = None
    source_deposit_number: Optional[str] = None

    # Amounts
    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    discount_amount: int
    discount_account_id: Optional[str] = None

    # Status
    status: str

    # Reference
    reference_number: Optional[str] = None
    notes: Optional[str] = None

    # Accounting links
    journal_id: Optional[str] = None
    journal_number: Optional[str] = None
    void_journal_id: Optional[str] = None

    # Overpayment deposit link
    created_deposit_id: Optional[str] = None
    created_deposit_number: Optional[str] = None

    # Allocations
    allocations: List[AllocationResponse] = []

    # Audit
    posted_at: Optional[str] = None
    posted_by: Optional[str] = None
    voided_at: Optional[str] = None
    voided_by: Optional[str] = None
    void_reason: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class ReceivePaymentResponse(BaseModel):
    """Generic receive payment operation response."""
    success: bool
    message: str
    data: Optional[dict] = None


class ReceivePaymentDetailResponse(BaseModel):
    """Response for get receive payment detail."""
    success: bool = True
    data: ReceivePaymentDetail


class ReceivePaymentListResponse(BaseModel):
    """Response for list receive payments."""
    items: List[ReceivePaymentListItem]
    total: int
    has_more: bool = False


class ReceivePaymentSummaryResponse(BaseModel):
    """Response for receive payments summary."""
    success: bool = True
    data: dict


# =============================================================================
# RESPONSE MODELS - Supporting endpoints
# =============================================================================

class OpenInvoiceItem(BaseModel):
    """Open invoice item for customer."""
    id: str
    invoice_number: str
    invoice_date: str
    due_date: str
    total_amount: int
    paid_amount: int
    remaining_amount: int
    is_overdue: bool = False
    overdue_days: int = 0


class OpenInvoicesResponse(BaseModel):
    """Response for customer open invoices."""
    invoices: List[OpenInvoiceItem]
    summary: dict


class AvailableDepositItem(BaseModel):
    """Available deposit item for customer."""
    id: str
    deposit_number: str
    deposit_date: str
    amount: int
    amount_applied: int
    amount_refunded: int
    remaining_amount: int


class AvailableDepositsResponse(BaseModel):
    """Response for customer available deposits."""
    deposits: List[AvailableDepositItem]
    total_available: int
```

**Step 2: Verify schema file syntax**

Run: `cd /root/milkyhoop-dev && python -c "from backend.api_gateway.app.schemas.receive_payments import *; print('OK')"`

Expected: `OK` (no import errors)

**Step 3: Commit**

```bash
git add backend/api_gateway/app/schemas/receive_payments.py
git commit -m "feat(receive-payments): add Pydantic schemas"
```

---

## Task 3: Router - List & Get Endpoints

**Files:**
- Create: `backend/api_gateway/app/routers/receive_payments.py`

**Step 1: Write the router file with list and get endpoints**

```python
"""
Receive Payments Router - Penerimaan Pembayaran

Endpoints for managing customer payments for invoices.
Integrates with customer_deposits for deposit payments and overpayments.

Flow:
1. Create receive payment (draft or posted)
2. Allocate to invoice(s)
3. Post to accounting (creates journal entry)
4. Void if needed (creates reversing journal)

Journal Entry on POST (Cash/Bank):
    Dr. Kas/Bank                        total_amount
    Dr. Potongan Penjualan (if any)     discount_amount
        Cr. Piutang Usaha                   allocated_amount
        Cr. Uang Muka Pelanggan (if any)    unapplied_amount

Journal Entry on POST (From Deposit):
    Dr. Uang Muka Pelanggan             total_amount
        Cr. Piutang Usaha                   allocated_amount

Endpoints:
- GET    /receive-payments              - List receive payments
- GET    /receive-payments/summary      - Summary statistics
- GET    /receive-payments/{id}         - Get payment detail
- POST   /receive-payments              - Create payment
- PUT    /receive-payments/{id}         - Update draft payment
- DELETE /receive-payments/{id}         - Delete draft payment
- POST   /receive-payments/{id}/post    - Post to accounting
- POST   /receive-payments/{id}/void    - Void payment
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import uuid as uuid_module

from ..schemas.receive_payments import (
    CreateReceivePaymentRequest,
    UpdateReceivePaymentRequest,
    VoidPaymentRequest,
    ReceivePaymentResponse,
    ReceivePaymentDetailResponse,
    ReceivePaymentListResponse,
    ReceivePaymentSummaryResponse,
    OpenInvoicesResponse,
    AvailableDepositsResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes
CUSTOMER_DEPOSIT_ACCOUNT = "2-10400"  # Uang Muka Pelanggan (Liability)
AR_ACCOUNT = "1-10300"               # Piutang Usaha


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
# LIST RECEIVE PAYMENTS
# =============================================================================

@router.get("", response_model=ReceivePaymentListResponse)
async def list_receive_payments(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "voided"]] = Query("all"),
    customer_id: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    source_type: Optional[Literal["all", "cash", "deposit"]] = Query("all"),
    search: Optional[str] = Query(None, description="Search by payment number or customer name"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: Literal["payment_date", "payment_number", "customer_name", "total_amount", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List receive payments with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Build query conditions
            conditions = ["rp.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"rp.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"rp.customer_id = ${param_idx}")
                params.append(UUID(customer_id))
                param_idx += 1

            if payment_method:
                conditions.append(f"rp.payment_method = ${param_idx}")
                params.append(payment_method)
                param_idx += 1

            if source_type and source_type != "all":
                conditions.append(f"rp.source_type = ${param_idx}")
                params.append(source_type)
                param_idx += 1

            if search:
                conditions.append(
                    f"(rp.payment_number ILIKE ${param_idx} OR rp.customer_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"rp.payment_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"rp.payment_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort mapping
            sort_mapping = {
                "payment_date": "rp.payment_date",
                "payment_number": "rp.payment_number",
                "customer_name": "rp.customer_name",
                "total_amount": "rp.total_amount",
                "created_at": "rp.created_at"
            }
            sort_field = sort_mapping.get(sort_by, "rp.created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"""
                SELECT COUNT(*) FROM receive_payments rp
                WHERE {where_clause}
            """
            total = await conn.fetchval(count_query, *params)

            # Get items with invoice count
            query = f"""
                SELECT
                    rp.id, rp.payment_number, rp.customer_id, rp.customer_name,
                    rp.payment_date, rp.payment_method, rp.source_type,
                    rp.total_amount, rp.allocated_amount, rp.unapplied_amount,
                    rp.status, rp.created_at,
                    (SELECT COUNT(*) FROM receive_payment_allocations WHERE payment_id = rp.id) as invoice_count
                FROM receive_payments rp
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "payment_number": row["payment_number"],
                    "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
                    "customer_name": row["customer_name"],
                    "payment_date": row["payment_date"].isoformat(),
                    "payment_method": row["payment_method"],
                    "source_type": row["source_type"],
                    "total_amount": row["total_amount"],
                    "allocated_amount": row["allocated_amount"] or 0,
                    "unapplied_amount": row["unapplied_amount"] or 0,
                    "status": row["status"],
                    "invoice_count": row["invoice_count"] or 0,
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing receive payments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list receive payments")


# =============================================================================
# SUMMARY
# =============================================================================

@router.get("/summary", response_model=ReceivePaymentSummaryResponse)
async def get_receive_payments_summary(request: Request):
    """Get summary statistics for receive payments."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'voided') as voided_count,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'posted'), 0) as total_received,
                    COALESCE(SUM(allocated_amount) FILTER (WHERE status = 'posted'), 0) as total_allocated,
                    COALESCE(SUM(unapplied_amount) FILTER (WHERE status = 'posted'), 0) as total_unapplied
                FROM receive_payments
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "voided_count": row["voided_count"] or 0,
                    "total_received": int(row["total_received"] or 0),
                    "total_allocated": int(row["total_allocated"] or 0),
                    "total_unapplied": int(row["total_unapplied"] or 0),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting receive payments summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET RECEIVE PAYMENT DETAIL
# =============================================================================

@router.get("/{payment_id}", response_model=ReceivePaymentDetailResponse)
async def get_receive_payment(request: Request, payment_id: UUID):
    """Get detailed information for a receive payment."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get payment with related data
            payment = await conn.fetchrow("""
                SELECT rp.*,
                       sd.deposit_number as source_deposit_number,
                       cd.deposit_number as created_deposit_number
                FROM receive_payments rp
                LEFT JOIN customer_deposits sd ON rp.source_deposit_id = sd.id
                LEFT JOIN customer_deposits cd ON rp.created_deposit_id = cd.id
                WHERE rp.id = $1 AND rp.tenant_id = $2
            """, payment_id, ctx["tenant_id"])

            if not payment:
                raise HTTPException(status_code=404, detail="Receive payment not found")

            # Get allocations
            allocations = await conn.fetch("""
                SELECT * FROM receive_payment_allocations
                WHERE payment_id = $1
                ORDER BY created_at
            """, payment_id)

            return {
                "success": True,
                "data": {
                    "id": str(payment["id"]),
                    "payment_number": payment["payment_number"],
                    "customer_id": str(payment["customer_id"]) if payment["customer_id"] else None,
                    "customer_name": payment["customer_name"],
                    "payment_date": payment["payment_date"].isoformat(),
                    "payment_method": payment["payment_method"],
                    "bank_account_id": str(payment["bank_account_id"]),
                    "bank_account_name": payment["bank_account_name"],
                    "source_type": payment["source_type"],
                    "source_deposit_id": str(payment["source_deposit_id"]) if payment["source_deposit_id"] else None,
                    "source_deposit_number": payment["source_deposit_number"],
                    "total_amount": payment["total_amount"],
                    "allocated_amount": payment["allocated_amount"] or 0,
                    "unapplied_amount": payment["unapplied_amount"] or 0,
                    "discount_amount": payment["discount_amount"] or 0,
                    "discount_account_id": str(payment["discount_account_id"]) if payment["discount_account_id"] else None,
                    "status": payment["status"],
                    "reference_number": payment["reference_number"],
                    "notes": payment["notes"],
                    "journal_id": str(payment["journal_id"]) if payment["journal_id"] else None,
                    "journal_number": payment["journal_number"],
                    "void_journal_id": str(payment["void_journal_id"]) if payment["void_journal_id"] else None,
                    "created_deposit_id": str(payment["created_deposit_id"]) if payment["created_deposit_id"] else None,
                    "created_deposit_number": payment["created_deposit_number"],
                    "allocations": [
                        {
                            "id": str(alloc["id"]),
                            "invoice_id": str(alloc["invoice_id"]),
                            "invoice_number": alloc["invoice_number"],
                            "invoice_amount": alloc["invoice_amount"],
                            "remaining_before": alloc["remaining_before"],
                            "amount_applied": alloc["amount_applied"],
                            "remaining_after": alloc["remaining_after"],
                        }
                        for alloc in allocations
                    ],
                    "posted_at": payment["posted_at"].isoformat() if payment["posted_at"] else None,
                    "posted_by": str(payment["posted_by"]) if payment["posted_by"] else None,
                    "voided_at": payment["voided_at"].isoformat() if payment["voided_at"] else None,
                    "voided_by": str(payment["voided_by"]) if payment["voided_by"] else None,
                    "void_reason": payment["void_reason"],
                    "created_at": payment["created_at"].isoformat(),
                    "updated_at": payment["updated_at"].isoformat(),
                    "created_by": str(payment["created_by"]) if payment["created_by"] else None,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get receive payment")
```

**Step 2: Verify router file syntax**

Run: `cd /root/milkyhoop-dev && python -c "from backend.api_gateway.app.routers.receive_payments import router; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/receive_payments.py
git commit -m "feat(receive-payments): add list and get endpoints"
```

---

## Task 4: Router - Create & Update Endpoints

**Files:**
- Modify: `backend/api_gateway/app/routers/receive_payments.py`

**Step 1: Add create endpoint after get_receive_payment function**

Append the following code to the router file:

```python
# =============================================================================
# CREATE RECEIVE PAYMENT
# =============================================================================

@router.post("", response_model=ReceivePaymentResponse, status_code=201)
async def create_receive_payment(request: Request, body: CreateReceivePaymentRequest):
    """
    Create a new receive payment.

    If save_as_draft=True, saves as draft without posting.
    If save_as_draft=False (default), posts immediately with journal entry.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Validate bank account exists and is asset type
                bank_account = await conn.fetchrow("""
                    SELECT id, account_code, name, account_type
                    FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.bank_account_id), ctx["tenant_id"])

                if not bank_account:
                    raise HTTPException(status_code=400, detail="Bank account not found")

                if bank_account["account_type"] != "ASSET":
                    raise HTTPException(
                        status_code=400,
                        detail="Bank account must be an asset account (Kas/Bank)"
                    )

                # Validate customer exists
                customer = await conn.fetchrow("""
                    SELECT id, name FROM customers
                    WHERE id = $1 AND tenant_id = $2
                """, UUID(body.customer_id), ctx["tenant_id"])

                if not customer:
                    raise HTTPException(status_code=400, detail="Customer not found")

                # Validate source deposit if source_type='deposit'
                if body.source_type == "deposit":
                    deposit = await conn.fetchrow("""
                        SELECT id, deposit_number, amount, amount_applied, amount_refunded, status
                        FROM customer_deposits
                        WHERE id = $1 AND tenant_id = $2 AND customer_id = $3
                    """, UUID(body.source_deposit_id), ctx["tenant_id"], UUID(body.customer_id))

                    if not deposit:
                        raise HTTPException(status_code=400, detail="Source deposit not found")

                    if deposit["status"] not in ("posted", "partial"):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Cannot use deposit with status '{deposit['status']}'"
                        )

                    deposit_remaining = deposit["amount"] - (deposit["amount_applied"] or 0) - (deposit["amount_refunded"] or 0)
                    if body.total_amount > deposit_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Payment amount ({body.total_amount}) exceeds deposit remaining ({deposit_remaining})"
                        )

                # Validate allocations
                total_allocated = 0
                validated_allocations = []

                for alloc in body.allocations:
                    invoice = await conn.fetchrow("""
                        SELECT id, invoice_number, total_amount, amount_paid, status, customer_id
                        FROM sales_invoices
                        WHERE id = $1 AND tenant_id = $2
                    """, UUID(alloc.invoice_id), ctx["tenant_id"])

                    if not invoice:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {alloc.invoice_id} not found"
                        )

                    if str(invoice["customer_id"]) != body.customer_id:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {invoice['invoice_number']} belongs to different customer"
                        )

                    invoice_remaining = invoice["total_amount"] - (invoice["amount_paid"] or 0)
                    if alloc.amount_applied > invoice_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Allocation ({alloc.amount_applied}) exceeds invoice remaining ({invoice_remaining})"
                        )

                    validated_allocations.append({
                        "invoice_id": invoice["id"],
                        "invoice_number": invoice["invoice_number"],
                        "invoice_amount": invoice["total_amount"],
                        "remaining_before": invoice_remaining,
                        "amount_applied": alloc.amount_applied,
                        "remaining_after": invoice_remaining - alloc.amount_applied,
                    })
                    total_allocated += alloc.amount_applied

                # Calculate amounts
                allocated_amount = total_allocated
                # Effective amount after discount
                effective_amount = body.total_amount + body.discount_amount
                unapplied_amount = effective_amount - allocated_amount

                if unapplied_amount < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Total allocation ({allocated_amount}) exceeds payment amount ({effective_amount})"
                    )

                # Generate payment number
                payment_number = await conn.fetchval(
                    "SELECT generate_receive_payment_number($1)",
                    ctx["tenant_id"]
                )

                # Insert payment
                payment_id = await conn.fetchval("""
                    INSERT INTO receive_payments (
                        tenant_id, payment_number, customer_id, customer_name,
                        payment_date, payment_method, bank_account_id, bank_account_name,
                        source_type, source_deposit_id,
                        total_amount, allocated_amount, unapplied_amount,
                        discount_amount, discount_account_id,
                        reference_number, notes, status, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, 'draft', $18)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    payment_number,
                    UUID(body.customer_id),
                    body.customer_name,
                    body.payment_date,
                    body.payment_method,
                    UUID(body.bank_account_id),
                    body.bank_account_name,
                    body.source_type,
                    UUID(body.source_deposit_id) if body.source_deposit_id else None,
                    body.total_amount,
                    allocated_amount,
                    unapplied_amount,
                    body.discount_amount,
                    UUID(body.discount_account_id) if body.discount_account_id else None,
                    body.reference_number,
                    body.notes,
                    ctx["user_id"]
                )

                # Insert allocations
                for alloc in validated_allocations:
                    await conn.execute("""
                        INSERT INTO receive_payment_allocations (
                            tenant_id, payment_id, invoice_id, invoice_number,
                            invoice_amount, remaining_before, amount_applied, remaining_after
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                        ctx["tenant_id"],
                        payment_id,
                        alloc["invoice_id"],
                        alloc["invoice_number"],
                        alloc["invoice_amount"],
                        alloc["remaining_before"],
                        alloc["amount_applied"],
                        alloc["remaining_after"]
                    )

                logger.info(f"Receive payment created: {payment_id}, number={payment_number}")

                result = {
                    "success": True,
                    "message": "Receive payment created successfully",
                    "data": {
                        "id": str(payment_id),
                        "payment_number": payment_number,
                        "total_amount": body.total_amount,
                        "allocated_amount": allocated_amount,
                        "unapplied_amount": unapplied_amount,
                        "status": "draft"
                    }
                }

                # Auto post if not draft
                if not body.save_as_draft:
                    post_result = await _post_payment(conn, ctx, payment_id)
                    result["data"]["status"] = "posted"
                    result["data"]["journal_id"] = post_result.get("journal_id")
                    result["message"] = "Receive payment created and posted"

                return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating receive payment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create receive payment")


# =============================================================================
# UPDATE RECEIVE PAYMENT (DRAFT ONLY)
# =============================================================================

@router.put("/{payment_id}", response_model=ReceivePaymentResponse)
async def update_receive_payment(request: Request, payment_id: UUID, body: UpdateReceivePaymentRequest):
    """
    Update a draft receive payment.

    Only draft payments can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Check payment exists and is draft
                payment = await conn.fetchrow("""
                    SELECT id, status FROM receive_payments
                    WHERE id = $1 AND tenant_id = $2
                """, payment_id, ctx["tenant_id"])

                if not payment:
                    raise HTTPException(status_code=404, detail="Receive payment not found")

                if payment["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Only draft payments can be updated"
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True, exclude={'allocations'})

                if not update_data and body.allocations is None:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(payment_id)}
                    }

                # Handle allocations update
                if body.allocations is not None:
                    # Delete existing allocations
                    await conn.execute(
                        "DELETE FROM receive_payment_allocations WHERE payment_id = $1",
                        payment_id
                    )

                    # Get customer_id for validation
                    customer_id = update_data.get("customer_id")
                    if not customer_id:
                        existing = await conn.fetchrow(
                            "SELECT customer_id FROM receive_payments WHERE id = $1",
                            payment_id
                        )
                        customer_id = str(existing["customer_id"])

                    # Validate and insert new allocations
                    total_allocated = 0
                    for alloc in body.allocations:
                        invoice = await conn.fetchrow("""
                            SELECT id, invoice_number, total_amount, amount_paid, customer_id
                            FROM sales_invoices
                            WHERE id = $1 AND tenant_id = $2
                        """, UUID(alloc.invoice_id), ctx["tenant_id"])

                        if not invoice:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invoice {alloc.invoice_id} not found"
                            )

                        if str(invoice["customer_id"]) != customer_id:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invoice belongs to different customer"
                            )

                        invoice_remaining = invoice["total_amount"] - (invoice["amount_paid"] or 0)
                        if alloc.amount_applied > invoice_remaining:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Allocation exceeds invoice remaining"
                            )

                        await conn.execute("""
                            INSERT INTO receive_payment_allocations (
                                tenant_id, payment_id, invoice_id, invoice_number,
                                invoice_amount, remaining_before, amount_applied, remaining_after
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                            ctx["tenant_id"],
                            payment_id,
                            invoice["id"],
                            invoice["invoice_number"],
                            invoice["total_amount"],
                            invoice_remaining,
                            alloc.amount_applied,
                            invoice_remaining - alloc.amount_applied
                        )
                        total_allocated += alloc.amount_applied

                    update_data["allocated_amount"] = total_allocated

                # Recalculate unapplied if total or allocated changed
                if "total_amount" in update_data or "allocated_amount" in update_data or "discount_amount" in update_data:
                    current = await conn.fetchrow(
                        "SELECT total_amount, allocated_amount, discount_amount FROM receive_payments WHERE id = $1",
                        payment_id
                    )
                    total = update_data.get("total_amount", current["total_amount"])
                    discount = update_data.get("discount_amount", current["discount_amount"] or 0)
                    allocated = update_data.get("allocated_amount", current["allocated_amount"] or 0)
                    update_data["unapplied_amount"] = (total + discount) - allocated

                # Build update query
                if update_data:
                    updates = []
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field in ("customer_id", "bank_account_id", "discount_account_id", "source_deposit_id") and value:
                            updates.append(f"{field} = ${param_idx}")
                            params.append(UUID(value))
                        else:
                            updates.append(f"{field} = ${param_idx}")
                            params.append(value)
                        param_idx += 1

                    params.extend([payment_id, ctx["tenant_id"]])
                    query = f"""
                        UPDATE receive_payments
                        SET {', '.join(updates)}, updated_at = NOW()
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """
                    await conn.execute(query, *params)

                logger.info(f"Receive payment updated: {payment_id}")

                return {
                    "success": True,
                    "message": "Receive payment updated successfully",
                    "data": {"id": str(payment_id)}
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update receive payment")


# =============================================================================
# DELETE RECEIVE PAYMENT (DRAFT ONLY)
# =============================================================================

@router.delete("/{payment_id}", response_model=ReceivePaymentResponse)
async def delete_receive_payment(request: Request, payment_id: UUID):
    """
    Delete a draft receive payment.

    Only draft payments can be deleted. Use void for posted payments.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Check payment exists and is draft
            payment = await conn.fetchrow("""
                SELECT id, status, payment_number FROM receive_payments
                WHERE id = $1 AND tenant_id = $2
            """, payment_id, ctx["tenant_id"])

            if not payment:
                raise HTTPException(status_code=404, detail="Receive payment not found")

            if payment["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft payments can be deleted. Use void for posted."
                )

            # Delete (cascade will delete allocations)
            await conn.execute(
                "DELETE FROM receive_payments WHERE id = $1",
                payment_id
            )

            logger.info(f"Receive payment deleted: {payment_id}")

            return {
                "success": True,
                "message": "Receive payment deleted successfully",
                "data": {
                    "id": str(payment_id),
                    "payment_number": payment["payment_number"]
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete receive payment")
```

**Step 2: Verify the additions**

Run: `cd /root/milkyhoop-dev && python -c "from backend.api_gateway.app.routers.receive_payments import router; print('Endpoints:', len(router.routes))"`

Expected: `Endpoints: 6` (list, summary, get, create, update, delete)

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/receive_payments.py
git commit -m "feat(receive-payments): add create, update, delete endpoints"
```

---

## Task 5: Router - Post & Void Endpoints

**Files:**
- Modify: `backend/api_gateway/app/routers/receive_payments.py`

**Step 1: Add internal _post_payment function and post/void endpoints**

Append the following code to the router file:

```python
# =============================================================================
# INTERNAL: POST PAYMENT
# =============================================================================

async def _post_payment(conn, ctx: dict, payment_id: UUID) -> dict:
    """Internal function to post a payment to accounting."""

    # Get payment
    payment = await conn.fetchrow("""
        SELECT * FROM receive_payments
        WHERE id = $1 AND tenant_id = $2
    """, payment_id, ctx["tenant_id"])

    if not payment:
        raise HTTPException(status_code=404, detail="Receive payment not found")

    if payment["status"] != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot post payment with status '{payment['status']}'"
        )

    # Get account IDs
    deposit_account_id = await conn.fetchval("""
        SELECT id FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = $2
    """, ctx["tenant_id"], CUSTOMER_DEPOSIT_ACCOUNT)

    ar_account_id = await conn.fetchval("""
        SELECT id FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = $2
    """, ctx["tenant_id"], AR_ACCOUNT)

    if not deposit_account_id or not ar_account_id:
        raise HTTPException(
            status_code=500,
            detail="Required accounts not found (2-10400, 1-10300)"
        )

    # Create journal entry
    journal_id = uuid_module.uuid4()
    trace_id = uuid_module.uuid4()

    journal_number = await conn.fetchval("""
        SELECT get_next_journal_number($1, 'RCV')
    """, ctx["tenant_id"])

    if not journal_number:
        journal_number = f"JRN-{payment['payment_number']}"

    total_debit = payment["total_amount"] + (payment["discount_amount"] or 0)

    await conn.execute("""
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            status, total_debit, total_credit, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'RECEIVE_PAYMENT', $6, $7, 'POSTED', $8, $8, $9)
    """,
        journal_id,
        ctx["tenant_id"],
        journal_number,
        payment["payment_date"],
        f"Penerimaan Pembayaran {payment['payment_number']} - {payment['customer_name']}",
        payment_id,
        str(trace_id),
        float(total_debit),
        ctx["user_id"]
    )

    line_number = 1

    # DEBIT side
    if payment["source_type"] == "cash":
        # Dr. Bank/Cash
        await conn.execute("""
            INSERT INTO journal_lines (
                id, journal_id, line_number, account_id, debit, credit, memo
            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
        """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            payment["bank_account_id"],
            float(payment["total_amount"]),
            f"Terima Pembayaran - {payment['payment_number']}"
        )
        line_number += 1
    else:
        # Dr. Customer Deposit (source_type = 'deposit')
        await conn.execute("""
            INSERT INTO journal_lines (
                id, journal_id, line_number, account_id, debit, credit, memo
            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
        """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            deposit_account_id,
            float(payment["total_amount"]),
            f"Aplikasi Deposit - {payment['payment_number']}"
        )
        line_number += 1

    # Dr. Discount (if any)
    if payment["discount_amount"] and payment["discount_amount"] > 0:
        discount_account = payment["discount_account_id"] or await conn.fetchval("""
            SELECT id FROM chart_of_accounts
            WHERE tenant_id = $1 AND account_code LIKE '6-%' AND name ILIKE '%potongan%'
            LIMIT 1
        """, ctx["tenant_id"])

        if discount_account:
            await conn.execute("""
                INSERT INTO journal_lines (
                    id, journal_id, line_number, account_id, debit, credit, memo
                ) VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
                uuid_module.uuid4(),
                journal_id,
                line_number,
                discount_account,
                float(payment["discount_amount"]),
                f"Potongan Penjualan - {payment['payment_number']}"
            )
            line_number += 1

    # CREDIT side
    # Cr. Accounts Receivable (allocated amount)
    if payment["allocated_amount"] and payment["allocated_amount"] > 0:
        await conn.execute("""
            INSERT INTO journal_lines (
                id, journal_id, line_number, account_id, debit, credit, memo
            ) VALUES ($1, $2, $3, $4, 0, $5, $6)
        """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            ar_account_id,
            float(payment["allocated_amount"]),
            f"Pelunasan Piutang - {payment['customer_name']}"
        )
        line_number += 1

    # Cr. Customer Deposit (unapplied amount - overpayment)
    created_deposit_id = None
    if payment["unapplied_amount"] and payment["unapplied_amount"] > 0:
        await conn.execute("""
            INSERT INTO journal_lines (
                id, journal_id, line_number, account_id, debit, credit, memo
            ) VALUES ($1, $2, $3, $4, 0, $5, $6)
        """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            deposit_account_id,
            float(payment["unapplied_amount"]),
            f"Uang Muka dari Overpayment - {payment['customer_name']}"
        )

        # Auto-create customer deposit for overpayment
        deposit_number = await conn.fetchval(
            "SELECT generate_customer_deposit_number($1, 'OVP')",
            ctx["tenant_id"]
        )

        created_deposit_id = await conn.fetchval("""
            INSERT INTO customer_deposits (
                tenant_id, deposit_number, customer_id, customer_name,
                amount, deposit_date, payment_method,
                account_id, reference, notes,
                status, posted_at, posted_by, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'posted', NOW(), $11, $11)
            RETURNING id
        """,
            ctx["tenant_id"],
            deposit_number,
            payment["customer_id"],
            payment["customer_name"],
            payment["unapplied_amount"],
            payment["payment_date"],
            payment["payment_method"],
            payment["bank_account_id"],
            f"Overpayment from {payment['payment_number']}",
            f"Auto-created from overpayment on {payment['payment_number']}",
            ctx["user_id"]
        )

        logger.info(f"Auto-created deposit {deposit_number} from overpayment")

    # Update invoices - reduce remaining, update status
    allocations = await conn.fetch(
        "SELECT * FROM receive_payment_allocations WHERE payment_id = $1",
        payment_id
    )

    for alloc in allocations:
        new_amount_paid = await conn.fetchval("""
            SELECT amount_paid + $2 FROM sales_invoices WHERE id = $1
        """, alloc["invoice_id"], alloc["amount_applied"])

        invoice_total = await conn.fetchval(
            "SELECT total_amount FROM sales_invoices WHERE id = $1",
            alloc["invoice_id"]
        )

        new_status = "paid" if new_amount_paid >= invoice_total else "partial"

        await conn.execute("""
            UPDATE sales_invoices
            SET amount_paid = $2, status = $3, updated_at = NOW()
            WHERE id = $1
        """, alloc["invoice_id"], new_amount_paid, new_status)

        # Update AR if exists
        await conn.execute("""
            UPDATE accounts_receivable
            SET amount_paid = amount_paid + $2,
                status = CASE
                    WHEN amount_paid + $2 >= amount THEN 'PAID'
                    ELSE 'PARTIAL'
                END,
                updated_at = NOW()
            WHERE source_id = $1 AND source_type = 'INVOICE'
        """, alloc["invoice_id"], alloc["amount_applied"])

    # If payment from deposit, reduce deposit balance
    if payment["source_type"] == "deposit" and payment["source_deposit_id"]:
        # Create deposit application record
        await conn.execute("""
            INSERT INTO customer_deposit_applications (
                id, tenant_id, deposit_id, invoice_id, invoice_number,
                amount_applied, application_date, journal_id, created_by
            )
            SELECT
                gen_random_uuid(),
                $1,
                $2,
                rpa.invoice_id,
                rpa.invoice_number,
                rpa.amount_applied,
                $3,
                $4,
                $5
            FROM receive_payment_allocations rpa
            WHERE rpa.payment_id = $6
        """,
            ctx["tenant_id"],
            payment["source_deposit_id"],
            payment["payment_date"],
            journal_id,
            ctx["user_id"],
            payment_id
        )

        # Deposit status will be updated by trigger

    # Update payment status
    await conn.execute("""
        UPDATE receive_payments
        SET status = 'posted',
            journal_id = $2,
            journal_number = $3,
            created_deposit_id = $4,
            posted_at = NOW(),
            posted_by = $5,
            updated_at = NOW()
        WHERE id = $1
    """, payment_id, journal_id, journal_number, created_deposit_id, ctx["user_id"])

    return {
        "journal_id": str(journal_id),
        "journal_number": journal_number,
        "created_deposit_id": str(created_deposit_id) if created_deposit_id else None
    }


# =============================================================================
# POST RECEIVE PAYMENT TO ACCOUNTING
# =============================================================================

@router.post("/{payment_id}/post", response_model=ReceivePaymentResponse)
async def post_receive_payment(request: Request, payment_id: UUID):
    """
    Post receive payment to accounting.

    Creates journal entry and updates invoice balances.
    If overpayment, auto-creates customer deposit.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                result = await _post_payment(conn, ctx, payment_id)

                logger.info(f"Receive payment posted: {payment_id}, journal={result['journal_id']}")

                return {
                    "success": True,
                    "message": "Receive payment posted to accounting",
                    "data": {
                        "id": str(payment_id),
                        "journal_id": result["journal_id"],
                        "journal_number": result["journal_number"],
                        "created_deposit_id": result.get("created_deposit_id"),
                        "status": "posted"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post receive payment")


# =============================================================================
# VOID RECEIVE PAYMENT
# =============================================================================

@router.post("/{payment_id}/void", response_model=ReceivePaymentResponse)
async def void_receive_payment(request: Request, payment_id: UUID, body: VoidPaymentRequest):
    """
    Void a posted receive payment.

    Creates reversing journal entry.
    Restores invoice balances.
    Voids any auto-created deposit from overpayment.
    Restores source deposit balance if paid from deposit.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get payment
                payment = await conn.fetchrow("""
                    SELECT * FROM receive_payments
                    WHERE id = $1 AND tenant_id = $2
                """, payment_id, ctx["tenant_id"])

                if not payment:
                    raise HTTPException(status_code=404, detail="Receive payment not found")

                if payment["status"] == "voided":
                    raise HTTPException(status_code=400, detail="Payment already voided")

                if payment["status"] == "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void draft payment. Delete it instead."
                    )

                # Create reversal journal
                void_journal_id = uuid_module.uuid4()

                if payment["journal_id"]:
                    # Get original journal lines
                    original_lines = await conn.fetch("""
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """, payment["journal_id"])

                    journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'VD')",
                        ctx["tenant_id"]
                    ) or f"VD-{payment['payment_number']}"

                    # Get original total
                    original_journal = await conn.fetchrow(
                        "SELECT total_debit FROM journal_entries WHERE id = $1",
                        payment["journal_id"]
                    )

                    # Create reversal header
                    await conn.execute("""
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'RECEIVE_PAYMENT', $5, $6, 'POSTED', $7, $7, $8)
                    """,
                        void_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {payment['payment_number']} - {payment['customer_name']} - {body.void_reason}",
                        payment_id,
                        payment["journal_id"],
                        float(original_journal["total_debit"]),
                        ctx["user_id"]
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute("""
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            void_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # Swap
                            line["debit"],   # Swap
                            f"Reversal - {line['memo'] or ''}"
                        )

                    # Mark original journal as reversed
                    await conn.execute("""
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                    """, payment["journal_id"], void_journal_id)

                # Restore invoice balances
                allocations = await conn.fetch(
                    "SELECT * FROM receive_payment_allocations WHERE payment_id = $1",
                    payment_id
                )

                for alloc in allocations:
                    # Get current invoice state
                    invoice = await conn.fetchrow(
                        "SELECT amount_paid, total_amount FROM sales_invoices WHERE id = $1",
                        alloc["invoice_id"]
                    )

                    new_amount_paid = (invoice["amount_paid"] or 0) - alloc["amount_applied"]
                    if new_amount_paid < 0:
                        new_amount_paid = 0

                    new_status = "posted" if new_amount_paid == 0 else "partial"

                    await conn.execute("""
                        UPDATE sales_invoices
                        SET amount_paid = $2, status = $3, updated_at = NOW()
                        WHERE id = $1
                    """, alloc["invoice_id"], new_amount_paid, new_status)

                    # Update AR
                    await conn.execute("""
                        UPDATE accounts_receivable
                        SET amount_paid = GREATEST(0, amount_paid - $2),
                            status = CASE
                                WHEN GREATEST(0, amount_paid - $2) = 0 THEN 'OPEN'
                                ELSE 'PARTIAL'
                            END,
                            updated_at = NOW()
                        WHERE source_id = $1 AND source_type = 'INVOICE'
                    """, alloc["invoice_id"], alloc["amount_applied"])

                # Void auto-created deposit if exists
                if payment["created_deposit_id"]:
                    await conn.execute("""
                        UPDATE customer_deposits
                        SET status = 'void',
                            voided_at = NOW(),
                            voided_by = $2,
                            voided_reason = $3,
                            updated_at = NOW()
                        WHERE id = $1
                    """, payment["created_deposit_id"], ctx["user_id"], f"Payment {payment['payment_number']} voided")

                # Restore source deposit if paid from deposit
                if payment["source_type"] == "deposit" and payment["source_deposit_id"]:
                    # Remove deposit applications created by this payment
                    await conn.execute("""
                        DELETE FROM customer_deposit_applications
                        WHERE deposit_id = $1 AND journal_id = $2
                    """, payment["source_deposit_id"], payment["journal_id"])
                    # Deposit status will be updated by trigger

                # Update payment status
                await conn.execute("""
                    UPDATE receive_payments
                    SET status = 'voided',
                        void_journal_id = $2,
                        voided_at = NOW(),
                        voided_by = $3,
                        void_reason = $4,
                        updated_at = NOW()
                    WHERE id = $1
                """, payment_id, void_journal_id, ctx["user_id"], body.void_reason)

                logger.info(f"Receive payment voided: {payment_id}")

                return {
                    "success": True,
                    "message": "Receive payment voided successfully",
                    "data": {
                        "id": str(payment_id),
                        "void_journal_id": str(void_journal_id),
                        "status": "voided"
                    }
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding receive payment {payment_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void receive payment")
```

**Step 2: Verify the additions**

Run: `cd /root/milkyhoop-dev && python -c "from backend.api_gateway.app.routers.receive_payments import router; print('Endpoints:', len(router.routes))"`

Expected: `Endpoints: 8` (list, summary, get, create, update, delete, post, void)

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/receive_payments.py
git commit -m "feat(receive-payments): add post and void endpoints with journal integration"
```

---

## Task 6: Customer Supporting Endpoints

**Files:**
- Modify: `backend/api_gateway/app/routers/customers.py`

**Step 1: Find the customers router file**

Run: `ls -la /root/milkyhoop-dev/backend/api_gateway/app/routers/customers.py`

**Step 2: Add open-invoices and available-deposits endpoints**

Add these endpoints to the customers router:

```python
# =============================================================================
# CUSTOMER OPEN INVOICES (for receive payments)
# =============================================================================

@router.get("/{customer_id}/open-invoices")
async def get_customer_open_invoices(
    request: Request,
    customer_id: UUID,
):
    """
    Get open (unpaid/partially paid) invoices for a customer.
    Used by receive payments to select invoices for allocation.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get invoices with remaining balance
            rows = await conn.fetch("""
                SELECT
                    id, invoice_number, invoice_date, due_date,
                    total_amount, amount_paid,
                    total_amount - COALESCE(amount_paid, 0) as remaining_amount,
                    CASE WHEN due_date < CURRENT_DATE THEN true ELSE false END as is_overdue,
                    GREATEST(0, CURRENT_DATE - due_date) as overdue_days
                FROM sales_invoices
                WHERE tenant_id = $1
                  AND customer_id = $2
                  AND status IN ('posted', 'partial', 'overdue')
                  AND total_amount > COALESCE(amount_paid, 0)
                ORDER BY due_date ASC, invoice_date ASC
            """, ctx["tenant_id"], customer_id)

            invoices = [
                {
                    "id": str(row["id"]),
                    "invoice_number": row["invoice_number"],
                    "invoice_date": row["invoice_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "paid_amount": row["amount_paid"] or 0,
                    "remaining_amount": row["remaining_amount"],
                    "is_overdue": row["is_overdue"],
                    "overdue_days": row["overdue_days"],
                }
                for row in rows
            ]

            total_outstanding = sum(inv["remaining_amount"] for inv in invoices)
            total_overdue = sum(inv["remaining_amount"] for inv in invoices if inv["is_overdue"])

            return {
                "invoices": invoices,
                "summary": {
                    "total_outstanding": total_outstanding,
                    "total_overdue": total_overdue,
                    "invoice_count": len(invoices),
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting open invoices for customer {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get open invoices")


# =============================================================================
# CUSTOMER AVAILABLE DEPOSITS (for receive payments)
# =============================================================================

@router.get("/{customer_id}/available-deposits")
async def get_customer_available_deposits(
    request: Request,
    customer_id: UUID,
):
    """
    Get customer deposits with remaining balance.
    Used by receive payments when paying from deposit.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get deposits with remaining balance
            rows = await conn.fetch("""
                SELECT
                    id, deposit_number, deposit_date,
                    amount, amount_applied, amount_refunded,
                    amount - COALESCE(amount_applied, 0) - COALESCE(amount_refunded, 0) as remaining_amount
                FROM customer_deposits
                WHERE tenant_id = $1
                  AND customer_id = $2::text
                  AND status IN ('posted', 'partial')
                  AND amount > COALESCE(amount_applied, 0) + COALESCE(amount_refunded, 0)
                ORDER BY deposit_date ASC
            """, ctx["tenant_id"], str(customer_id))

            deposits = [
                {
                    "id": str(row["id"]),
                    "deposit_number": row["deposit_number"],
                    "deposit_date": row["deposit_date"].isoformat(),
                    "amount": row["amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["remaining_amount"],
                }
                for row in rows
            ]

            total_available = sum(dep["remaining_amount"] for dep in deposits)

            return {
                "deposits": deposits,
                "total_available": total_available,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting available deposits for customer {customer_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get available deposits")
```

**Step 3: Commit**

```bash
git add backend/api_gateway/app/routers/customers.py
git commit -m "feat(customers): add open-invoices and available-deposits endpoints"
```

---

## Task 7: Register Router in main.py

**Files:**
- Modify: `backend/api_gateway/app/main.py`

**Step 1: Find the router registration section**

Run: `grep -n "include_router.*customer_deposits" /root/milkyhoop-dev/backend/api_gateway/app/main.py`

**Step 2: Add receive_payments router registration**

Add after the customer_deposits router registration:

```python
from .routers import receive_payments
# ... in router registration section:
app.include_router(receive_payments.router, prefix="/api/receive-payments", tags=["receive-payments"])
```

**Step 3: Verify import and registration**

Run: `cd /root/milkyhoop-dev && python -c "from backend.api_gateway.app.main import app; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add backend/api_gateway/app/main.py
git commit -m "feat(receive-payments): register router in main.py"
```

---

## Task 8: Run Migration & Test Endpoints

**Step 1: Apply migration**

Run: `cd /root/milkyhoop-dev && flyway -configFiles=flyway.conf migrate`

Expected: Migration V085 applied successfully

**Step 2: Start dev server**

Run: `cd /root/milkyhoop-dev && uvicorn backend.api_gateway.app.main:app --reload --port 8000`

**Step 3: Test list endpoint**

Run: `curl -s http://localhost:8000/api/receive-payments | jq`

Expected: Empty list response `{"items": [], "total": 0, "has_more": false}`

**Step 4: Commit final verification**

```bash
git add -A
git commit -m "feat(receive-payments): complete MVP implementation

- Database migration V085 with tables and RLS
- Pydantic schemas for request/response
- Full CRUD endpoints
- Post/void with journal integration
- Deposit integration (overpayment creates deposit, can pay from deposit)
- Customer supporting endpoints (open-invoices, available-deposits)"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Database Migration | `V085__receive_payments.sql` |
| 2 | Pydantic Schemas | `schemas/receive_payments.py` |
| 3 | List & Get Endpoints | `routers/receive_payments.py` |
| 4 | Create/Update/Delete | `routers/receive_payments.py` |
| 5 | Post/Void + Journal | `routers/receive_payments.py` |
| 6 | Customer Endpoints | `routers/customers.py` |
| 7 | Router Registration | `main.py` |
| 8 | Migration & Test | Integration test |

**Total: 8 tasks**
