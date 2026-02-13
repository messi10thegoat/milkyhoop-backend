"""
Payroll Router - Penggajian Karyawan

Endpoints for managing payroll runs and employee allocations.
Supports approval workflow and journal posting.

Flow:
1. Create payroll run (draft)
2. Add employee allocations
3. Submit for approval
4. Approve/reject
5. Post to journal (creates accounting entries)
6. Void if needed

Journal Entry on POST:
    Dr. Beban Gaji (6100)               total_amount
        Cr. Hutang Gaji (2105)              total_amount (if accrual)
    OR
        Cr. Kas/Bank                        total_amount (if direct payment)

Endpoints:
- GET    /payroll              - List payroll runs
- GET    /payroll/summary      - Summary statistics
- GET    /payroll/{id}         - Get payroll run detail
- POST   /payroll              - Create payroll run
- PUT    /payroll/{id}         - Update draft payroll
- DELETE /payroll/{id}         - Delete draft payroll
- POST   /payroll/{id}/submit  - Submit for approval
- POST   /payroll/{id}/approve - Approve payroll
- POST   /payroll/{id}/reject  - Reject payroll
- POST   /payroll/{id}/post    - Post to journal
- POST   /payroll/{id}/void    - Void payroll

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - Router handles HTTP, logic in router
- Law 6: Source Traceability - Journal linked via source_type=PAYROLL
- Law 8: Balance changes only via journal posting
"""

from fastapi import APIRouter, HTTPException, Request, Query, Depends
from typing import Optional, Literal, List
from uuid import UUID
import uuid as uuid_module
import logging
import asyncpg
from datetime import date, datetime
from decimal import Decimal
import json
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payroll", tags=["payroll"])

# Connection pool
_pool: Optional[asyncpg.Pool] = None

# Account codes for journal entries
SALARY_EXPENSE_ACCOUNT = "6-10100"    # Beban Gaji (Expense)
SALARY_PAYABLE_ACCOUNT = "2-10500"    # Hutang Gaji (Liability)


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        from ..config import settings
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
        "user_id": UUID(user_id) if user_id else None,
        "username": user.get("username") or user.get("email", "Unknown"),
        "business_role_code": user.get("business_role_code"),
    }


async def check_period_is_open(conn, tenant_id: str, transaction_date) -> None:
    """Check if the accounting period for the transaction date is open."""
    period = await conn.fetchrow(
        """
        SELECT id, period_name, status FROM fiscal_periods
        WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
        ORDER BY start_date DESC LIMIT 1
        """,
        tenant_id,
        transaction_date,
    )

    if period and period["status"] in ("CLOSED", "LOCKED"):
        period_name = period["period_name"]
        period_status = period["status"].lower()
        raise HTTPException(
            status_code=403,
            detail=f"Cannot post to {period_status} period ({period_name})",
        )


async def ensure_tables_exist(conn, tenant_id: str) -> None:
    """Ensure payroll tables exist (create if not)."""
    # Check if payroll_runs table exists
    table_exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'payroll_runs')"
    )

    if not table_exists:
        # Create payroll_runs table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payroll_runs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id TEXT NOT NULL,
                payroll_number TEXT NOT NULL,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                payment_date DATE,
                description TEXT,
                total_basic_salary NUMERIC(15,2) DEFAULT 0,
                total_allowances NUMERIC(15,2) DEFAULT 0,
                total_deductions NUMERIC(15,2) DEFAULT 0,
                total_net_salary NUMERIC(15,2) DEFAULT 0,
                employee_count INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                payment_method TEXT DEFAULT 'bank_transfer',
                bank_account_id UUID,
                journal_id UUID,
                submitted_at TIMESTAMPTZ,
                submitted_by UUID,
                approved_at TIMESTAMPTZ,
                approved_by UUID,
                rejected_at TIMESTAMPTZ,
                rejected_by UUID,
                rejection_reason TEXT,
                posted_at TIMESTAMPTZ,
                posted_by UUID,
                voided_at TIMESTAMPTZ,
                voided_by UUID,
                void_reason TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                created_by UUID,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                updated_by UUID,
                CONSTRAINT payroll_runs_status_check CHECK (
                    status IN ('draft', 'pending_approval', 'approved', 'rejected', 'posted', 'voided')
                )
            )
        """)

        # Create indexes
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payroll_runs_tenant ON payroll_runs(tenant_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payroll_runs_status ON payroll_runs(tenant_id, status)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payroll_runs_period ON payroll_runs(tenant_id, period_start, period_end)"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payroll_runs_number ON payroll_runs(tenant_id, payroll_number)"
        )

        # Create payroll_allocations table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payroll_allocations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id TEXT NOT NULL,
                payroll_id UUID NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
                employee_id UUID,
                employee_name TEXT NOT NULL,
                employee_code TEXT,
                position TEXT,
                department TEXT,
                basic_salary NUMERIC(15,2) DEFAULT 0,
                allowances JSONB DEFAULT '[]'::jsonb,
                total_allowances NUMERIC(15,2) DEFAULT 0,
                deductions JSONB DEFAULT '[]'::jsonb,
                total_deductions NUMERIC(15,2) DEFAULT 0,
                net_salary NUMERIC(15,2) DEFAULT 0,
                bank_name TEXT,
                bank_account_number TEXT,
                bank_name TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Create indexes for allocations
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payroll_allocations_payroll ON payroll_allocations(payroll_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payroll_allocations_tenant ON payroll_allocations(tenant_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payroll_allocations_employee ON payroll_allocations(tenant_id, employee_id)"
        )

        logger.info("Created payroll tables successfully")


# =============================================================================
# SCHEMAS
# =============================================================================

class AllocationItem(BaseModel):
    employee_id: Optional[str] = None
    employee_name: str
    employee_code: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    basic_salary: float = 0
    allowances: List[dict] = Field(default_factory=list)
    deductions: List[dict] = Field(default_factory=list)
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_name: Optional[str] = None
    notes: Optional[str] = None


class CreatePayrollRequest(BaseModel):
    period_start: date
    period_end: date
    payment_date: Optional[date] = None
    description: Optional[str] = None
    payment_method: str = "bank_transfer"
    bank_account_id: Optional[str] = None
    allocations: List[AllocationItem] = Field(default_factory=list)


class UpdatePayrollRequest(BaseModel):
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    payment_date: Optional[date] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None
    bank_account_id: Optional[str] = None
    allocations: Optional[List[AllocationItem]] = None


class RejectPayrollRequest(BaseModel):
    reason: str


class VoidPayrollRequest(BaseModel):
    reason: str


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

async def get_next_payroll_number(conn, tenant_id: str) -> str:
    """Generate next payroll number."""
    # Try to use stored function first
    try:
        number = await conn.fetchval(
            "SELECT get_next_journal_number($1, 'PR')", tenant_id
        )
        if number:
            return number
    except Exception:
        pass

    # Fallback: generate manually
    today = datetime.now()
    prefix = f"PR-{today.strftime('%Y%m')}"

    last_number = await conn.fetchval(
        """
        SELECT payroll_number FROM payroll_runs
        WHERE tenant_id = $1 AND payroll_number LIKE $2
        ORDER BY payroll_number DESC LIMIT 1
        """,
        tenant_id,
        f"{prefix}%"
    )

    if last_number:
        try:
            seq = int(last_number.split("-")[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1

    return f"{prefix}-{seq:04d}"


def calculate_allocation_totals(allocation: dict) -> dict:
    """Calculate totals for an allocation."""
    basic_salary = Decimal(str(allocation.get("basic_salary", 0)))

    allowances = allocation.get("allowances", [])
    total_allowances = sum(Decimal(str(a.get("amount", 0))) for a in allowances)

    deductions = allocation.get("deductions", [])
    total_deductions = sum(Decimal(str(d.get("amount", 0))) for d in deductions)

    net_salary = basic_salary + total_allowances - total_deductions

    return {
        "basic_salary": basic_salary,
        "total_allowances": total_allowances,
        "total_deductions": total_deductions,
        "net_salary": net_salary,
    }


# =============================================================================
# LIST PAYROLL RUNS
# =============================================================================

@router.get("")
async def list_payroll_runs(
    request: Request,
    status: Optional[Literal["all", "draft", "pending_approval", "approved", "rejected", "posted", "voided"]] = Query("all"),
    period_start: Optional[date] = Query(None),
    period_end: Optional[date] = Query(None),
    search: Optional[str] = Query(None, description="Search by payroll number"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: Literal["period_start", "payroll_number", "total_net_salary", "created_at"] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List payroll runs with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
            await ensure_tables_exist(conn, ctx["tenant_id"])

            # Build query conditions
            conditions = ["pr.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"pr.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if period_start:
                conditions.append(f"pr.period_start >= ${param_idx}")
                params.append(period_start)
                param_idx += 1

            if period_end:
                conditions.append(f"pr.period_end <= ${param_idx}")
                params.append(period_end)
                param_idx += 1

            if search:
                conditions.append(f"pr.payroll_number ILIKE ${param_idx}")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort mapping
            sort_mapping = {
                "period_start": "pr.period_start",
                "payroll_number": "pr.payroll_number",
                "total_net_salary": "pr.total_net_salary",
                "created_at": "pr.created_at",
            }
            sort_field = sort_mapping.get(sort_by, "pr.created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM payroll_runs pr WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Fetch data
            query = f"""
                SELECT
                    pr.id,
                    pr.payroll_number,
                    pr.period_start,
                    pr.period_end,
                    pr.payment_date,
                    pr.description,
                    pr.total_basic_salary,
                    pr.total_allowances,
                    pr.total_deductions,
                    pr.total_net_salary,
                    pr.employee_count,
                    pr.status,
                    pr.payment_method,
                    pr.created_at,
                    pr.submitted_at,
                    pr.approved_at,
                    pr.posted_at
                FROM payroll_runs pr
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """
            params.extend([skip, limit])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "payroll_number": row["payroll_number"],
                    "period_start": row["period_start"].isoformat() if row["period_start"] else None,
                    "period_end": row["period_end"].isoformat() if row["period_end"] else None,
                    "payment_date": row["payment_date"].isoformat() if row["payment_date"] else None,
                    "description": row["description"],
                    "total_basic_salary": float(row["total_basic_salary"] or 0),
                    "total_allowances": float(row["total_allowances"] or 0),
                    "total_deductions": float(row["total_deductions"] or 0),
                    "total_net_salary": float(row["total_net_salary"] or 0),
                    "employee_count": row["employee_count"] or 0,
                    "status": row["status"],
                    "payment_method": row["payment_method"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
                    "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
                    "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
                }
                for row in rows
            ]

            return {
                "success": True,
                "data": items,
                "meta": {
                    "total": total,
                    "skip": skip,
                    "limit": limit,
                    "has_more": skip + len(items) < total,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing payroll runs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list payroll runs")


# =============================================================================
# GET SUMMARY
# =============================================================================

@router.get("/summary")
async def get_payroll_summary(request: Request):
    """Get payroll summary statistics."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
            await ensure_tables_exist(conn, ctx["tenant_id"])

            # Get status counts
            status_counts = await conn.fetch(
                """
                SELECT status, COUNT(*) as count, COALESCE(SUM(total_net_salary), 0) as total
                FROM payroll_runs
                WHERE tenant_id = $1
                GROUP BY status
                """,
                ctx["tenant_id"]
            )

            summary = {
                "draft": {"count": 0, "total": 0},
                "pending_approval": {"count": 0, "total": 0},
                "approved": {"count": 0, "total": 0},
                "rejected": {"count": 0, "total": 0},
                "posted": {"count": 0, "total": 0},
                "voided": {"count": 0, "total": 0},
            }

            for row in status_counts:
                if row["status"] in summary:
                    summary[row["status"]] = {
                        "count": row["count"],
                        "total": float(row["total"]),
                    }

            # Get current month totals
            current_month_total = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as count,
                    COALESCE(SUM(total_net_salary), 0) as total
                FROM payroll_runs
                WHERE tenant_id = $1
                    AND status = 'posted'
                    AND DATE_TRUNC('month', period_start) = DATE_TRUNC('month', CURRENT_DATE)
                """,
                ctx["tenant_id"]
            )

            return {
                "success": True,
                "data": {
                    "by_status": summary,
                    "current_month": {
                        "count": current_month_total["count"],
                        "total": float(current_month_total["total"]),
                    },
                    "total_all": sum(s["count"] for s in summary.values()),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payroll summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get payroll summary")


# =============================================================================
# GET PAYROLL DETAIL
# =============================================================================

@router.get("/{payroll_id}")
async def get_payroll_detail(request: Request, payroll_id: UUID):
    """Get payroll run detail with allocations."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
            await ensure_tables_exist(conn, ctx["tenant_id"])

            # Get payroll run
            payroll = await conn.fetchrow(
                """
                SELECT * FROM payroll_runs
                WHERE id = $1 AND tenant_id = $2
                """,
                payroll_id,
                ctx["tenant_id"]
            )

            if not payroll:
                raise HTTPException(status_code=404, detail="Payroll run not found")

            # Get allocations
            allocations = await conn.fetch(
                """
                SELECT * FROM payroll_allocations
                WHERE payroll_id = $1
                ORDER BY employee_name
                """,
                payroll_id
            )

            allocation_items = [
                {
                    "id": str(a["id"]),
                    "employee_id": str(a["employee_id"]) if a["employee_id"] else None,
                    "employee_name": a["employee_name"],
                    "employee_code": a["employee_code"],
                    "position": a["position"],
                    "department": a["department"],
                    "basic_salary": float(a["basic_salary"] or 0),
                    "allowances": a["allowances"] or [],
                    "total_allowances": float(a["total_allowances"] or 0),
                    "deductions": a["deductions"] or [],
                    "total_deductions": float(a["total_deductions"] or 0),
                    "net_salary": float(a["net_salary"] or 0),
                    "bank_name": a["bank_name"],
                    "bank_account_number": a["bank_account_number"],
                    "bank_name": a["bank_name"],
                    "notes": a["notes"],
                }
                for a in allocations
            ]

            return {
                "success": True,
                "data": {
                    "id": str(payroll["id"]),
                    "payroll_number": payroll["payroll_number"],
                    "period_start": payroll["period_start"].isoformat() if payroll["period_start"] else None,
                    "period_end": payroll["period_end"].isoformat() if payroll["period_end"] else None,
                    "payment_date": payroll["payment_date"].isoformat() if payroll["payment_date"] else None,
                    "description": payroll["description"],
                    "total_basic_salary": float(payroll["total_basic_salary"] or 0),
                    "total_allowances": float(payroll["total_allowances"] or 0),
                    "total_deductions": float(payroll["total_deductions"] or 0),
                    "total_net_salary": float(payroll["total_net_salary"] or 0),
                    "employee_count": payroll["employee_count"] or 0,
                    "status": payroll["status"],
                    "payment_method": payroll["payment_method"],
                    "bank_account_id": str(payroll["bank_account_id"]) if payroll["bank_account_id"] else None,
                    "journal_id": str(payroll["journal_id"]) if payroll["journal_id"] else None,
                    "submitted_at": payroll["submitted_at"].isoformat() if payroll["submitted_at"] else None,
                    "approved_at": payroll["approved_at"].isoformat() if payroll["approved_at"] else None,
                    "rejected_at": payroll["rejected_at"].isoformat() if payroll["rejected_at"] else None,
                    "rejection_reason": payroll["rejection_reason"],
                    "posted_at": payroll["posted_at"].isoformat() if payroll["posted_at"] else None,
                    "voided_at": payroll["voided_at"].isoformat() if payroll["voided_at"] else None,
                    "void_reason": payroll["void_reason"],
                    "created_at": payroll["created_at"].isoformat() if payroll["created_at"] else None,
                    "allocations": allocation_items,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payroll detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get payroll detail")


# =============================================================================
# CREATE PAYROLL RUN
# =============================================================================

@router.post("")
async def create_payroll_run(request: Request, data: CreatePayrollRequest):
    """Create a new payroll run."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
                await ensure_tables_exist(conn, ctx["tenant_id"])

                # Generate payroll number
                payroll_number = await get_next_payroll_number(conn, ctx["tenant_id"])

                # Calculate totals from allocations
                total_basic = Decimal("0")
                total_allowances = Decimal("0")
                total_deductions = Decimal("0")
                total_net = Decimal("0")

                for alloc in data.allocations:
                    totals = calculate_allocation_totals(alloc.dict())
                    total_basic += totals["basic_salary"]
                    total_allowances += totals["total_allowances"]
                    total_deductions += totals["total_deductions"]
                    total_net += totals["net_salary"]

                # Create payroll run
                payroll_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO payroll_runs (
                        id, tenant_id, payroll_number, period_start, period_end,
                        payment_date, description, total_basic_salary, total_allowances,
                        total_deductions, total_net_salary, employee_count,
                        status, payment_method, bank_account_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    """,
                    payroll_id,
                    ctx["tenant_id"],
                    payroll_number,
                    data.period_start,
                    data.period_end,
                    data.payment_date,
                    data.description,
                    total_basic,
                    total_allowances,
                    total_deductions,
                    total_net,
                    len(data.allocations),
                    "draft",
                    data.payment_method,
                    UUID(data.bank_account_id) if data.bank_account_id else None,
                    ctx["user_id"],
                )

                # Create allocations
                for alloc in data.allocations:
                    totals = calculate_allocation_totals(alloc.dict())
                    await conn.execute(
                        """
                        INSERT INTO payroll_allocations (
                            tenant_id, payroll_id, employee_id, employee_name,
                            employee_code, position, department, basic_salary,
                            allowances, total_allowances, deductions, total_deductions,
                            net_salary, bank_name, bank_account_number, bank_account_name, notes
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                        """,
                        ctx["tenant_id"],
                        payroll_id,
                        UUID(alloc.employee_id) if alloc.employee_id else None,
                        alloc.employee_name,
                        alloc.employee_code,
                        alloc.position,
                        alloc.department,
                        totals["basic_salary"],
                        json.dumps(alloc.allowances),  # Serialize to JSON string
                        totals["total_allowances"],
                        json.dumps(alloc.deductions),  # Serialize to JSON string
                        totals["total_deductions"],
                        totals["net_salary"],
                        alloc.bank_name,
                        alloc.bank_account_number,
                        alloc.bank_name,
                        alloc.notes,
                    )

                logger.info(f"Created payroll run: {payroll_number}")

                return {
                    "success": True,
                    "message": "Payroll run created successfully",
                    "data": {
                        "id": str(payroll_id),
                        "payroll_number": payroll_number,
                        "status": "draft",
                        "employee_count": len(data.allocations),
                        "total_net_salary": float(total_net),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating payroll run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create payroll run")


# =============================================================================
# UPDATE PAYROLL RUN
# =============================================================================

@router.put("/{payroll_id}")
async def update_payroll_run(request: Request, payroll_id: UUID, data: UpdatePayrollRequest):
    """Update a draft payroll run."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get current payroll
                payroll = await conn.fetchrow(
                    "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    payroll_id,
                    ctx["tenant_id"]
                )

                if not payroll:
                    raise HTTPException(status_code=404, detail="Payroll run not found")

                if payroll["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot update payroll in {payroll['status']} status"
                    )

                # Build update fields
                updates = []
                params = []
                param_idx = 1

                if data.period_start is not None:
                    updates.append(f"period_start = ${param_idx}")
                    params.append(data.period_start)
                    param_idx += 1

                if data.period_end is not None:
                    updates.append(f"period_end = ${param_idx}")
                    params.append(data.period_end)
                    param_idx += 1

                if data.payment_date is not None:
                    updates.append(f"payment_date = ${param_idx}")
                    params.append(data.payment_date)
                    param_idx += 1

                if data.description is not None:
                    updates.append(f"description = ${param_idx}")
                    params.append(data.description)
                    param_idx += 1

                if data.payment_method is not None:
                    updates.append(f"payment_method = ${param_idx}")
                    params.append(data.payment_method)
                    param_idx += 1

                if data.bank_account_id is not None:
                    updates.append(f"bank_account_id = ${param_idx}")
                    params.append(UUID(data.bank_account_id) if data.bank_account_id else None)
                    param_idx += 1

                # Handle allocations update
                if data.allocations is not None:
                    # Delete existing allocations
                    await conn.execute(
                        "DELETE FROM payroll_allocations WHERE payroll_id = $1",
                        payroll_id
                    )

                    # Calculate new totals and insert allocations
                    total_basic = Decimal("0")
                    total_allowances = Decimal("0")
                    total_deductions = Decimal("0")
                    total_net = Decimal("0")

                    for alloc in data.allocations:
                        totals = calculate_allocation_totals(alloc.dict())
                        total_basic += totals["basic_salary"]
                        total_allowances += totals["total_allowances"]
                        total_deductions += totals["total_deductions"]
                        total_net += totals["net_salary"]

                        await conn.execute(
                            """
                            INSERT INTO payroll_allocations (
                                tenant_id, payroll_id, employee_id, employee_name,
                                employee_code, position, department, basic_salary,
                                allowances, total_allowances, deductions, total_deductions,
                                net_salary, bank_name, bank_account_number, bank_account_name, notes
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                            """,
                            ctx["tenant_id"],
                            payroll_id,
                            UUID(alloc.employee_id) if alloc.employee_id else None,
                            alloc.employee_name,
                            alloc.employee_code,
                            alloc.position,
                            alloc.department,
                            totals["basic_salary"],
                            json.dumps(alloc.allowances),  # Serialize to JSON string
                            totals["total_allowances"],
                            json.dumps(alloc.deductions),  # Serialize to JSON string
                            totals["total_deductions"],
                            totals["net_salary"],
                            alloc.bank_name,
                            alloc.bank_account_number,
                            alloc.bank_name,
                            alloc.notes,
                        )

                    updates.append(f"total_basic_salary = ${param_idx}")
                    params.append(total_basic)
                    param_idx += 1

                    updates.append(f"total_allowances = ${param_idx}")
                    params.append(total_allowances)
                    param_idx += 1

                    updates.append(f"total_deductions = ${param_idx}")
                    params.append(total_deductions)
                    param_idx += 1

                    updates.append(f"total_net_salary = ${param_idx}")
                    params.append(total_net)
                    param_idx += 1

                    updates.append(f"employee_count = ${param_idx}")
                    params.append(len(data.allocations))
                    param_idx += 1

                if updates:
                    updates.append(f"updated_at = NOW()")
                    updates.append(f"updated_by = ${param_idx}")
                    params.append(ctx["user_id"])
                    param_idx += 1

                    params.append(payroll_id)
                    params.append(ctx["tenant_id"])

                    query = f"""
                        UPDATE payroll_runs
                        SET {", ".join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """
                    await conn.execute(query, *params)

                logger.info(f"Updated payroll run: {payroll_id}")

                return {
                    "success": True,
                    "message": "Payroll run updated successfully",
                    "data": {"id": str(payroll_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating payroll run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update payroll run")


# =============================================================================
# DELETE PAYROLL RUN
# =============================================================================

@router.delete("/{payroll_id}")
async def delete_payroll_run(request: Request, payroll_id: UUID):
    """Delete a draft payroll run."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get current payroll
                payroll = await conn.fetchrow(
                    "SELECT status FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
                    payroll_id,
                    ctx["tenant_id"]
                )

                if not payroll:
                    raise HTTPException(status_code=404, detail="Payroll run not found")

                if payroll["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot delete payroll in {payroll['status']} status. Use void instead."
                    )

                # Delete allocations (cascade should handle this, but be explicit)
                await conn.execute(
                    "DELETE FROM payroll_allocations WHERE payroll_id = $1",
                    payroll_id
                )

                # Delete payroll run
                await conn.execute(
                    "DELETE FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
                    payroll_id,
                    ctx["tenant_id"]
                )

                logger.info(f"Deleted payroll run: {payroll_id}")

                return {
                    "success": True,
                    "message": "Payroll run deleted successfully",
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting payroll run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete payroll run")


# =============================================================================
# SUBMIT FOR APPROVAL
# =============================================================================

@router.post("/{payroll_id}/submit")
async def submit_payroll_for_approval(request: Request, payroll_id: UUID):
    """Submit payroll run for approval."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                payroll = await conn.fetchrow(
                    "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    payroll_id,
                    ctx["tenant_id"]
                )

                if not payroll:
                    raise HTTPException(status_code=404, detail="Payroll run not found")

                if payroll["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot submit payroll in {payroll['status']} status"
                    )

                if payroll["employee_count"] == 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot submit payroll with no employees"
                    )

                # Update status to pending_approval
                await conn.execute(
                    """
                    UPDATE payroll_runs
                    SET status = 'pending_approval',
                        submitted_at = NOW(),
                        submitted_by = $3,
                        updated_at = NOW(),
                        updated_by = $3
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    payroll_id,
                    ctx["tenant_id"],
                    ctx["user_id"]
                )

                logger.info(f"Submitted payroll for approval: {payroll_id}")

                return {
                    "success": True,
                    "message": "Payroll submitted for approval",
                    "data": {
                        "id": str(payroll_id),
                        "status": "pending_approval",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting payroll: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to submit payroll")


# =============================================================================
# APPROVE PAYROLL
# =============================================================================

@router.post("/{payroll_id}/approve")
async def approve_payroll(request: Request, payroll_id: UUID):
    """Approve payroll run."""
    try:
        ctx = get_user_context(request)

        # Check business role - only OWNER or FINANCE_MGR can approve
        business_role = ctx.get("business_role_code")
        if business_role and business_role not in ["OWNER", "FINANCE_MGR"]:
            raise HTTPException(status_code=403, detail="Only Owner or Finance Manager can approve payroll")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                payroll = await conn.fetchrow(
                    "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    payroll_id,
                    ctx["tenant_id"]
                )

                if not payroll:
                    raise HTTPException(status_code=404, detail="Payroll run not found")

                if payroll["status"] != "pending_approval":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot approve payroll in {payroll['status']} status"
                    )

                # Update status to approved
                await conn.execute(
                    """
                    UPDATE payroll_runs
                    SET status = 'approved',
                        approved_at = NOW(),
                        approved_by = $3,
                        updated_at = NOW(),
                        updated_by = $3
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    payroll_id,
                    ctx["tenant_id"],
                    ctx["user_id"]
                )

                logger.info(f"Approved payroll: {payroll_id}")

                return {
                    "success": True,
                    "message": "Payroll approved",
                    "data": {
                        "id": str(payroll_id),
                        "status": "approved",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error approving payroll: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to approve payroll")


# =============================================================================
# REJECT PAYROLL
# =============================================================================

@router.post("/{payroll_id}/reject")
async def reject_payroll(request: Request, payroll_id: UUID, data: RejectPayrollRequest):
    """Reject payroll run."""
    try:
        ctx = get_user_context(request)

        # Check business role
        business_role = ctx.get("business_role_code")
        if business_role and business_role not in ["OWNER", "FINANCE_MGR"]:
            raise HTTPException(status_code=403, detail="Only Owner or Finance Manager can reject payroll")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                payroll = await conn.fetchrow(
                    "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    payroll_id,
                    ctx["tenant_id"]
                )

                if not payroll:
                    raise HTTPException(status_code=404, detail="Payroll run not found")

                if payroll["status"] != "pending_approval":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot reject payroll in {payroll['status']} status"
                    )

                # Update status to rejected
                await conn.execute(
                    """
                    UPDATE payroll_runs
                    SET status = 'rejected',
                        rejected_at = NOW(),
                        rejected_by = $3,
                        rejection_reason = $4,
                        updated_at = NOW(),
                        updated_by = $3
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    payroll_id,
                    ctx["tenant_id"],
                    ctx["user_id"],
                    data.reason
                )

                logger.info(f"Rejected payroll: {payroll_id}")

                return {
                    "success": True,
                    "message": "Payroll rejected",
                    "data": {
                        "id": str(payroll_id),
                        "status": "rejected",
                        "rejection_reason": data.reason,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting payroll: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reject payroll")


# =============================================================================
# POST PAYROLL TO JOURNAL
# =============================================================================

@router.post("/{payroll_id}/post")
async def post_payroll(request: Request, payroll_id: UUID):
    """
    Post approved payroll to accounting journal.

    IRON LAW 6: Creates journal with source_type='PAYROLL'
    IRON LAW 8: Balance changes only via this journal

    Journal Entry:
        Dr. Beban Gaji (6100)           total_net_salary
            Cr. Hutang Gaji (2105)          total_net_salary (accrual)
        OR
            Cr. Kas/Bank                    total_net_salary (direct payment)
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                payroll = await conn.fetchrow(
                    "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    payroll_id,
                    ctx["tenant_id"]
                )

                if not payroll:
                    raise HTTPException(status_code=404, detail="Payroll run not found")

                if payroll["status"] not in ["approved"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post payroll in {payroll['status']} status. Must be approved first."
                    )

                # Check accounting period
                payment_date = payroll["payment_date"] or payroll["period_end"]
                await check_period_is_open(conn, ctx["tenant_id"], payment_date)

                # Get account IDs
                # Beban Gaji - Expense account
                salary_expense = await conn.fetchrow(
                    "SELECT id, name FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                    ctx["tenant_id"],
                    SALARY_EXPENSE_ACCOUNT
                )

                # Hutang Gaji - Liability account (or Bank for direct payment)
                if payroll["payment_method"] == "direct" and payroll["bank_account_id"]:
                    # Direct payment - credit bank account
                    bank_account = await conn.fetchrow(
                        """
                        SELECT ba.coa_account_id as id, a.name
                        FROM bank_accounts ba
                        JOIN accounts a ON a.id = ba.coa_account_id
                        WHERE ba.id = $1 AND ba.tenant_id = $2
                        """,
                        payroll["bank_account_id"],
                        ctx["tenant_id"]
                    )
                    credit_account = bank_account
                else:
                    # Accrual - credit hutang gaji
                    credit_account = await conn.fetchrow(
                        "SELECT id, name FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
                        ctx["tenant_id"],
                        SALARY_PAYABLE_ACCOUNT
                    )

                if not salary_expense:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Salary expense account ({SALARY_EXPENSE_ACCOUNT}) not found"
                    )

                if not credit_account:
                    raise HTTPException(
                        status_code=400,
                        detail="Credit account not found. Please configure bank account or salary payable account."
                    )

                # Create journal entry
                journal_id = uuid_module.uuid4()

                # Get journal number
                try:
                    journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'PR')", ctx["tenant_id"]
                    )
                except Exception:
                    journal_number = f"JNL-PR-{payroll['payroll_number']}"

                total_amount = float(payroll["total_net_salary"])

                # Create journal header
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'PAYROLL', $6, 'POSTED', $7, $7, $8)
                    """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    payment_date,
                    f"Payroll {payroll['payroll_number']} - {payroll['period_start']} to {payroll['period_end']}",
                    payroll_id,
                    total_amount,
                    ctx["user_id"]
                )

                # Create journal lines
                # Line 1: Debit Beban Gaji
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    salary_expense["id"],
                    total_amount,
                    f"Beban Gaji - {payroll['payroll_number']}"
                )

                # Line 2: Credit Hutang Gaji or Bank
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    credit_account["id"],
                    total_amount,
                    f"{credit_account['name']} - {payroll['payroll_number']}"
                )

                # Update payroll status
                await conn.execute(
                    """
                    UPDATE payroll_runs
                    SET status = 'posted',
                        journal_id = $3,
                        posted_at = NOW(),
                        posted_by = $4,
                        updated_at = NOW(),
                        updated_by = $4
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    payroll_id,
                    ctx["tenant_id"],
                    journal_id,
                    ctx["user_id"]
                )

                logger.info(f"Posted payroll: {payroll_id}, journal={journal_id}")

                return {
                    "success": True,
                    "message": "Payroll posted to accounting",
                    "data": {
                        "id": str(payroll_id),
                        "status": "posted",
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting payroll: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post payroll")


# =============================================================================
# VOID PAYROLL
# =============================================================================

@router.post("/{payroll_id}/void")
async def void_payroll(request: Request, payroll_id: UUID, data: VoidPayrollRequest):
    """
    Void a posted payroll run.

    Creates reversing journal entry.
    """
    try:
        ctx = get_user_context(request)

        # Check business role
        business_role = ctx.get("business_role_code")
        if business_role and business_role not in ["OWNER", "FINANCE_MGR"]:
            raise HTTPException(status_code=403, detail="Only Owner or Finance Manager can void payroll")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                payroll = await conn.fetchrow(
                    "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    payroll_id,
                    ctx["tenant_id"]
                )

                if not payroll:
                    raise HTTPException(status_code=404, detail="Payroll run not found")

                if payroll["status"] == "voided":
                    raise HTTPException(status_code=400, detail="Payroll already voided")

                if payroll["status"] == "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void draft payroll. Delete it instead."
                    )

                # Check accounting period
                await check_period_is_open(conn, ctx["tenant_id"], date.today())

                # Create reversal journal if original journal exists
                void_journal_id = None
                void_journal_number = None

                if payroll["journal_id"]:
                    # Get original journal lines
                    original_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1",
                        payroll["journal_id"]
                    )

                    # Get original journal
                    original_journal = await conn.fetchrow(
                        "SELECT total_debit FROM journal_entries WHERE id = $1",
                        payroll["journal_id"]
                    )

                    # Generate void journal number
                    try:
                        void_journal_number = await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'VD')", ctx["tenant_id"]
                        )
                    except Exception:
                        void_journal_number = f"VD-{payroll['payroll_number']}"

                    void_journal_id = uuid_module.uuid4()

                    # Create reversal header
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'PAYROLL', $5, $6, 'POSTED', $7, $7, $8)
                        """,
                        void_journal_id,
                        ctx["tenant_id"],
                        void_journal_number,
                        f"Void {payroll['payroll_number']} - {data.reason}",
                        payroll_id,
                        payroll["journal_id"],
                        float(original_journal["total_debit"]),
                        ctx["user_id"]
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute(
                            """
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
                            f"Reversal - {line['memo']}",
                        )

                    # Mark original journal as reversed
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, status = 'VOID'
                        WHERE id = $1
                        """,
                        payroll["journal_id"],
                        void_journal_id
                    )

                # Update payroll status
                await conn.execute(
                    """
                    UPDATE payroll_runs
                    SET status = 'voided',
                        voided_at = NOW(),
                        voided_by = $3,
                        void_reason = $4,
                        updated_at = NOW(),
                        updated_by = $3
                    WHERE id = $1 AND tenant_id = $2
                    """,
                    payroll_id,
                    ctx["tenant_id"],
                    ctx["user_id"],
                    data.reason
                )

                logger.info(f"Voided payroll: {payroll_id}")

                return {
                    "success": True,
                    "message": "Payroll voided",
                    "data": {
                        "id": str(payroll_id),
                        "status": "voided",
                        "void_journal_id": str(void_journal_id) if void_journal_id else None,
                        "void_journal_number": void_journal_number,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding payroll: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void payroll")

# =============================================================================
# TABNAV ENDPOINTS
# =============================================================================

@router.get("/{payroll_id}/allocations")
async def get_payroll_allocations(request: Request, payroll_id: str):
    """Get allocations for a payroll run (TabNav: Karyawan)"""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            payroll = await conn.fetchrow(
                "SELECT id FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
                UUID(payroll_id),
                ctx["tenant_id"],
            )
            if not payroll:
                raise HTTPException(status_code=404, detail="Payroll not found")

            rows = await conn.fetch(
                """
                SELECT 
                    id, employee_id, employee_name, employee_code,
                    position, department, basic_salary,
                    allowances, total_allowances, deductions, total_deductions,
                    net_salary, bank_name, bank_account_number, bank_account_name, notes
                FROM payroll_allocations
                WHERE payroll_id = $1 AND tenant_id = $2
                ORDER BY employee_name
                """,
                UUID(payroll_id),
                ctx["tenant_id"],
            )

            allocations = []
            for row in rows:
                allocations.append({
                    "id": str(row["id"]),
                    "employee_id": str(row["employee_id"]) if row["employee_id"] else None,
                    "employee_name": row["employee_name"],
                    "employee_code": row["employee_code"],
                    "position": row["position"],
                    "department": row["department"],
                    "basic_salary": float(row["basic_salary"] or 0),
                    "allowances": row["allowances"] or [],
                    "total_allowances": float(row["total_allowances"] or 0),
                    "deductions": row["deductions"] or [],
                    "total_deductions": float(row["total_deductions"] or 0),
                    "net_salary": float(row["net_salary"] or 0),
                    "bank_name": row["bank_name"],
                    "bank_account_number": row["bank_account_number"],
                    "bank_account_name": row["bank_account_name"],
                    "notes": row["notes"],
                })

            return {"success": True, "data": allocations}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching allocations: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch allocations")


@router.get("/{payroll_id}/journal-entries")
async def get_payroll_journal_entries(request: Request, payroll_id: UUID):
    """
    Tab: Journal Entries - Get journal entries linked to this payroll run.
    Checks both journal_id FK and source_id match.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            payroll = await conn.fetchrow(
                "SELECT id, payroll_number, journal_id, status FROM payroll_runs WHERE id = $1 AND tenant_id = $2",
                payroll_id, ctx["tenant_id"]
            )
            if not payroll:
                raise HTTPException(status_code=404, detail="Payroll not found")

            # Collect journal IDs from both FK and source_id
            journal_ids = set()
            if payroll["journal_id"]:
                journal_ids.add(payroll["journal_id"])

            source_journals = await conn.fetch(
                """
                SELECT id FROM journal_entries
                WHERE tenant_id = $1 AND source_id = $2
                """,
                ctx["tenant_id"], payroll_id
            )
            for row in source_journals:
                journal_ids.add(row["id"])

            if not journal_ids:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "summary": {"total_debit": 0, "total_credit": 0, "is_balanced": True}
                }

            journals = await conn.fetch(
                """
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.source_type, je.status, je.total_debit, je.total_credit
                FROM journal_entries je
                WHERE je.id = ANY($1::uuid[])
                ORDER BY je.journal_date, je.created_at
                """,
                list(journal_ids)
            )

            journal_data = []
            total_debit = 0
            total_credit = 0

            for journal in journals:
                lines = await conn.fetch(
                    """
                    SELECT jl.id, jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo,
                           coa.account_code, coa.name as account_name
                    FROM journal_lines jl
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    journal["id"]
                )

                line_data = [
                    {
                        "id": str(line["id"]),
                        "line_number": line["line_number"],
                        "account_id": str(line["account_id"]),
                        "account_code": line["account_code"],
                        "account_name": line["account_name"],
                        "debit": float(line["debit"] or 0),
                        "credit": float(line["credit"] or 0),
                        "memo": line["memo"] or ""
                    }
                    for line in lines
                ]

                journal_debit = float(journal["total_debit"] or 0)
                journal_credit = float(journal["total_credit"] or 0)
                total_debit += journal_debit
                total_credit += journal_credit

                journal_data.append({
                    "id": str(journal["id"]),
                    "journal_number": journal["journal_number"],
                    "journal_date": journal["journal_date"].isoformat() if journal["journal_date"] else None,
                    "description": journal["description"],
                    "source_type": journal["source_type"],
                    "status": journal["status"],
                    "total_debit": journal_debit,
                    "total_credit": journal_credit,
                    "is_balanced": abs(journal_debit - journal_credit) < 0.01,
                    "lines": line_data
                })

            return {
                "success": True,
                "data": journal_data,
                "total": len(journal_data),
                "summary": {
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "is_balanced": abs(total_debit - total_credit) < 0.01
                }
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payroll journal entries: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal entries")
