"""
Vendor Deposits Router
======================
Advance payments to vendors before receiving goods.
Creates journal entries on post, apply, and refund.
"""
from datetime import date
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..schemas.vendor_deposits import (
    VendorDepositCreate,
    VendorDepositUpdate,
    VendorDepositResponse,
    VendorDepositDetailResponse,
    VendorDepositListResponse,
    VendorDepositApplicationResponse,
    VendorDepositRefundResponse,
    VendorDepositRefundCreate,
    ApplyDepositRequest,
    ApplyDepositResponse,
    AvailableDepositItem,
    AvailableDepositsResponse,
    VendorDepositsForVendorResponse,
    VendorDepositSummary,
    PostDepositResponse,
    VoidDepositResponse,
    VendorDepositStatus,
)

router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


# ============================================================================
# VENDOR DEPOSIT CRUD
# ============================================================================

@router.get("", response_model=VendorDepositListResponse)
async def list_vendor_deposits(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[VendorDepositStatus] = None,
    vendor_id: Optional[UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    """List vendor deposits"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["vd.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"vd.status = ${param_idx}")
            params.append(status.value)
            param_idx += 1

        if vendor_id:
            where_clauses.append(f"vd.vendor_id = ${param_idx}")
            params.append(vendor_id)
            param_idx += 1

        if start_date:
            where_clauses.append(f"vd.deposit_date >= ${param_idx}")
            params.append(start_date)
            param_idx += 1

        if end_date:
            where_clauses.append(f"vd.deposit_date <= ${param_idx}")
            params.append(end_date)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM vendor_deposits vd WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT vd.*, v.name as vendor_name, v.code as vendor_code,
                   ba.account_name as bank_account_name, po.po_number as purchase_order_number
            FROM vendor_deposits vd
            JOIN vendors v ON vd.vendor_id = v.id
            LEFT JOIN bank_accounts ba ON vd.bank_account_id = ba.id
            LEFT JOIN purchase_orders po ON vd.purchase_order_id = po.id
            WHERE {where_sql}
            ORDER BY vd.deposit_date DESC
            OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """,
            *params, skip, limit
        )

        items = [VendorDepositResponse(**dict(row)) for row in rows]
        return VendorDepositListResponse(items=items, total=total)


@router.get("/summary", response_model=VendorDepositSummary)
async def get_vendor_deposit_summary(request: Request):
    """Get vendor deposit summary"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            "SELECT * FROM get_vendor_deposit_summary($1)",
            ctx["tenant_id"]
        )

        return VendorDepositSummary(**dict(row))


@router.get("/{deposit_id}", response_model=VendorDepositDetailResponse)
async def get_vendor_deposit(request: Request, deposit_id: UUID):
    """Get vendor deposit with applications and refunds"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        vd = await conn.fetchrow(
            """
            SELECT vd.*, v.name as vendor_name, v.code as vendor_code,
                   ba.account_name as bank_account_name, po.po_number as purchase_order_number
            FROM vendor_deposits vd
            JOIN vendors v ON vd.vendor_id = v.id
            LEFT JOIN bank_accounts ba ON vd.bank_account_id = ba.id
            LEFT JOIN purchase_orders po ON vd.purchase_order_id = po.id
            WHERE vd.id = $1 AND vd.tenant_id = $2
            """,
            deposit_id, ctx["tenant_id"]
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        applications = await conn.fetch(
            "SELECT * FROM get_vendor_deposit_applications($1)",
            deposit_id
        )

        refunds = await conn.fetch(
            "SELECT * FROM get_vendor_deposit_refunds($1)",
            deposit_id
        )

        return VendorDepositDetailResponse(
            **dict(vd),
            applications=[VendorDepositApplicationResponse(**dict(a)) for a in applications],
            refunds=[VendorDepositRefundResponse(**dict(r)) for r in refunds],
        )


@router.post("", response_model=VendorDepositResponse, status_code=201)
async def create_vendor_deposit(request: Request, data: VendorDepositCreate):
    """Create vendor deposit (draft)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Validate vendor
        vendor = await conn.fetchrow(
            "SELECT id, name, code FROM vendors WHERE id = $1 AND tenant_id = $2",
            data.vendor_id, ctx["tenant_id"]
        )
        if not vendor:
            raise HTTPException(status_code=400, detail="Vendor not found")

        # Generate deposit number
        deposit_number = await conn.fetchval(
            "SELECT generate_vendor_deposit_number($1)",
            ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            """
            INSERT INTO vendor_deposits (
                tenant_id, deposit_number, deposit_date, vendor_id, amount,
                payment_method, bank_account_id, reference, purchase_order_id,
                notes, created_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING *
            """,
            ctx["tenant_id"], deposit_number, data.deposit_date, data.vendor_id,
            data.amount, data.payment_method.value, data.bank_account_id,
            data.reference, data.purchase_order_id, data.notes, ctx.get("user_id")
        )

        return VendorDepositResponse(
            **dict(row),
            vendor_name=vendor["name"],
            vendor_code=vendor["code"],
        )


@router.patch("/{deposit_id}", response_model=VendorDepositResponse)
async def update_vendor_deposit(request: Request, deposit_id: UUID, data: VendorDepositUpdate):
    """Update vendor deposit (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if existing["status"] != "draft":
            raise HTTPException(status_code=400, detail="Can only update draft deposits")

        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            vd = await conn.fetchrow(
                """
                SELECT vd.*, v.name as vendor_name, v.code as vendor_code,
                       ba.account_name as bank_account_name
                FROM vendor_deposits vd
                JOIN vendors v ON vd.vendor_id = v.id
                LEFT JOIN bank_accounts ba ON vd.bank_account_id = ba.id
                WHERE vd.id = $1
                """,
                deposit_id
            )
            return VendorDepositResponse(**dict(vd))

        if "payment_method" in update_data:
            update_data["payment_method"] = update_data["payment_method"].value

        set_clauses = []
        params = []
        for i, (key, value) in enumerate(update_data.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            params.append(value)

        set_clauses.append("updated_at = NOW()")
        params.extend([deposit_id, ctx["tenant_id"]])

        await conn.execute(
            f"""
            UPDATE vendor_deposits SET {', '.join(set_clauses)}
            WHERE id = ${len(params) - 1} AND tenant_id = ${len(params)}
            """,
            *params
        )

        row = await conn.fetchrow(
            """
            SELECT vd.*, v.name as vendor_name, v.code as vendor_code,
                   ba.account_name as bank_account_name
            FROM vendor_deposits vd
            JOIN vendors v ON vd.vendor_id = v.id
            LEFT JOIN bank_accounts ba ON vd.bank_account_id = ba.id
            WHERE vd.id = $1
            """,
            deposit_id
        )

        return VendorDepositResponse(**dict(row))


@router.delete("/{deposit_id}")
async def delete_vendor_deposit(request: Request, deposit_id: UUID):
    """Delete vendor deposit (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id, ctx["tenant_id"]
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if existing["status"] != "draft":
            raise HTTPException(status_code=400, detail="Can only delete draft deposits")

        await conn.execute("DELETE FROM vendor_deposits WHERE id = $1", deposit_id)
        return {"message": "Vendor deposit deleted"}


# ============================================================================
# POST DEPOSIT (Creates Journal Entry)
# ============================================================================

@router.post("/{deposit_id}/post", response_model=PostDepositResponse)
async def post_vendor_deposit(request: Request, deposit_id: UUID):
    """
    Post vendor deposit - creates journal entry:
    Dr. Uang Muka Vendor (1-10800)    amount
        Cr. Kas/Bank                      amount
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        vd = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id, ctx["tenant_id"]
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if vd["status"] != "draft":
            raise HTTPException(status_code=400, detail="Deposit is already posted")

        # Get accounts
        vendor_deposit_account = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10800'",
            ctx["tenant_id"]
        )
        if not vendor_deposit_account:
            # Seed account if not exists
            await conn.execute("SELECT seed_vendor_deposit_account($1)", ctx["tenant_id"])
            vendor_deposit_account = await conn.fetchval(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10800'",
                ctx["tenant_id"]
            )

        bank_account = None
        if vd["bank_account_id"]:
            bank_account = await conn.fetchrow(
                "SELECT account_id FROM bank_accounts WHERE id = $1",
                vd["bank_account_id"]
            )

        if not bank_account:
            # Use default cash account
            cash_account = await conn.fetchval(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10100'",
                ctx["tenant_id"]
            )
        else:
            cash_account = bank_account["account_id"]

        async with conn.transaction():
            # Generate journal number
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_sequences (tenant_id, last_number)
                VALUES ($1, 1)
                ON CONFLICT (tenant_id)
                DO UPDATE SET last_number = journal_sequences.last_number + 1
                RETURNING last_number
                """,
                ctx["tenant_id"]
            )
            journal_number = f"JV-{vd['deposit_date'].year}-{seq['last_number']:05d}"

            # Create journal entry
            journal = await conn.fetchrow(
                """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, entry_date, reference, description,
                    source_type, source_id, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'VENDOR_DEPOSIT', $6, 'POSTED', $7)
                RETURNING id, journal_number
                """,
                ctx["tenant_id"], journal_number, vd["deposit_date"],
                vd["deposit_number"], f"Vendor Deposit - {vd['deposit_number']}",
                deposit_id, ctx.get("user_id")
            )

            # Journal lines
            # Dr. Uang Muka Vendor
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
                VALUES ($1, $2, $3, 0, $4)
                """,
                journal["id"], vendor_deposit_account, vd["amount"],
                f"Vendor Deposit - {vd['deposit_number']}"
            )

            # Cr. Kas/Bank
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
                VALUES ($1, $2, 0, $3, $4)
                """,
                journal["id"], cash_account, vd["amount"],
                f"Vendor Deposit - {vd['deposit_number']}"
            )

            # Update deposit
            await conn.execute(
                """
                UPDATE vendor_deposits SET status = 'posted', journal_id = $2, updated_at = NOW()
                WHERE id = $1
                """,
                deposit_id, journal["id"]
            )

            return PostDepositResponse(
                deposit_id=deposit_id,
                deposit_number=vd["deposit_number"],
                status=VendorDepositStatus.posted,
                journal_id=journal["id"],
                journal_number=journal["journal_number"],
            )


# ============================================================================
# APPLY TO BILL
# ============================================================================

@router.post("/{deposit_id}/apply", response_model=ApplyDepositResponse)
async def apply_vendor_deposit(request: Request, deposit_id: UUID, data: ApplyDepositRequest):
    """
    Apply vendor deposit to bill - creates journal entry:
    Dr. Hutang Usaha (2-10100)        applied_amount
        Cr. Uang Muka Vendor (1-10800)    applied_amount
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        vd = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id, ctx["tenant_id"]
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if vd["status"] not in ("posted", "partial"):
            raise HTTPException(status_code=400, detail="Deposit must be posted before applying")

        if vd["remaining_amount"] < data.amount:
            raise HTTPException(status_code=400, detail=f"Insufficient deposit balance. Available: {vd['remaining_amount']}")

        bill = await conn.fetchrow(
            "SELECT * FROM bills WHERE id = $1 AND vendor_id = $2",
            data.bill_id, vd["vendor_id"]
        )
        if not bill:
            raise HTTPException(status_code=400, detail="Bill not found or vendor mismatch")

        if bill["status"] not in ("posted", "partial"):
            raise HTTPException(status_code=400, detail="Bill must be posted")

        bill_remaining = bill["total_amount"] - (bill["paid_amount"] or 0)
        if data.amount > bill_remaining:
            raise HTTPException(status_code=400, detail=f"Amount exceeds bill balance. Bill balance: {bill_remaining}")

        applied_date = data.applied_date or date.today()

        # Get accounts
        ap_account = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '2-10100'",
            ctx["tenant_id"]
        )
        vendor_deposit_account = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10800'",
            ctx["tenant_id"]
        )

        async with conn.transaction():
            # Generate journal
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_sequences (tenant_id, last_number)
                VALUES ($1, 1)
                ON CONFLICT (tenant_id)
                DO UPDATE SET last_number = journal_sequences.last_number + 1
                RETURNING last_number
                """,
                ctx["tenant_id"]
            )
            journal_number = f"JV-{applied_date.year}-{seq['last_number']:05d}"

            journal = await conn.fetchrow(
                """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, entry_date, reference, description,
                    source_type, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'DEPOSIT_APPLICATION', 'POSTED', $6)
                RETURNING id
                """,
                ctx["tenant_id"], journal_number, applied_date,
                f"{vd['deposit_number']} -> {bill['bill_number']}",
                f"Apply Deposit {vd['deposit_number']} to Bill {bill['bill_number']}",
                ctx.get("user_id")
            )

            # Dr. Hutang Usaha
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
                VALUES ($1, $2, $3, 0, $4)
                """,
                journal["id"], ap_account, data.amount,
                f"Apply Deposit to Bill {bill['bill_number']}"
            )

            # Cr. Uang Muka Vendor
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
                VALUES ($1, $2, 0, $3, $4)
                """,
                journal["id"], vendor_deposit_account, data.amount,
                f"Apply Deposit {vd['deposit_number']}"
            )

            # Create application record
            app = await conn.fetchrow(
                """
                INSERT INTO vendor_deposit_applications (
                    vendor_deposit_id, bill_id, amount, applied_date, journal_id, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                deposit_id, data.bill_id, data.amount, applied_date,
                journal["id"], ctx.get("user_id")
            )

            # Update bill paid_amount
            await conn.execute(
                """
                UPDATE bills SET
                    paid_amount = COALESCE(paid_amount, 0) + $2,
                    status = CASE
                        WHEN COALESCE(paid_amount, 0) + $2 >= total_amount THEN 'paid'
                        ELSE 'partial'
                    END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                data.bill_id, data.amount
            )

            # Fetch updated values
            updated_vd = await conn.fetchrow(
                "SELECT remaining_amount FROM vendor_deposits WHERE id = $1",
                deposit_id
            )
            updated_bill = await conn.fetchrow(
                "SELECT total_amount - COALESCE(paid_amount, 0) as remaining FROM bills WHERE id = $1",
                data.bill_id
            )

            return ApplyDepositResponse(
                application_id=app["id"],
                deposit_id=deposit_id,
                deposit_number=vd["deposit_number"],
                bill_id=data.bill_id,
                bill_number=bill["bill_number"],
                applied_amount=data.amount,
                deposit_remaining=updated_vd["remaining_amount"],
                bill_remaining=updated_bill["remaining"],
                journal_id=journal["id"],
            )


# ============================================================================
# REFUND
# ============================================================================

@router.post("/{deposit_id}/refund", response_model=VendorDepositRefundResponse)
async def refund_vendor_deposit(request: Request, deposit_id: UUID, data: VendorDepositRefundCreate):
    """
    Refund vendor deposit - creates journal entry:
    Dr. Kas/Bank                      refund_amount
        Cr. Uang Muka Vendor (1-10800)    refund_amount
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        vd = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id, ctx["tenant_id"]
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if vd["status"] not in ("posted", "partial"):
            raise HTTPException(status_code=400, detail="Deposit must be posted")

        if vd["remaining_amount"] < data.amount:
            raise HTTPException(status_code=400, detail=f"Insufficient balance. Available: {vd['remaining_amount']}")

        # Get accounts
        vendor_deposit_account = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10800'",
            ctx["tenant_id"]
        )

        bank_account_coa = None
        if data.bank_account_id:
            bank_account_coa = await conn.fetchval(
                "SELECT account_id FROM bank_accounts WHERE id = $1",
                data.bank_account_id
            )
        if not bank_account_coa:
            bank_account_coa = await conn.fetchval(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '1-10100'",
                ctx["tenant_id"]
            )

        async with conn.transaction():
            # Generate journal
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_sequences (tenant_id, last_number)
                VALUES ($1, 1)
                ON CONFLICT (tenant_id)
                DO UPDATE SET last_number = journal_sequences.last_number + 1
                RETURNING last_number
                """,
                ctx["tenant_id"]
            )
            journal_number = f"JV-{data.refund_date.year}-{seq['last_number']:05d}"

            journal = await conn.fetchrow(
                """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, entry_date, reference, description,
                    source_type, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'DEPOSIT_REFUND', 'POSTED', $6)
                RETURNING id
                """,
                ctx["tenant_id"], journal_number, data.refund_date,
                vd["deposit_number"], f"Refund Deposit {vd['deposit_number']}",
                ctx.get("user_id")
            )

            # Dr. Kas/Bank
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
                VALUES ($1, $2, $3, 0, $4)
                """,
                journal["id"], bank_account_coa, data.amount,
                f"Refund Deposit {vd['deposit_number']}"
            )

            # Cr. Uang Muka Vendor
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, account_id, debit, credit, description)
                VALUES ($1, $2, 0, $3, $4)
                """,
                journal["id"], vendor_deposit_account, data.amount,
                f"Refund Deposit {vd['deposit_number']}"
            )

            # Create refund record
            refund = await conn.fetchrow(
                """
                INSERT INTO vendor_deposit_refunds (
                    vendor_deposit_id, refund_date, amount, bank_account_id,
                    reference, journal_id, notes, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING *
                """,
                deposit_id, data.refund_date, data.amount, data.bank_account_id,
                data.reference, journal["id"], data.notes, ctx.get("user_id")
            )

            bank_name = None
            if data.bank_account_id:
                bank_name = await conn.fetchval(
                    "SELECT name FROM bank_accounts WHERE id = $1",
                    data.bank_account_id
                )

            return VendorDepositRefundResponse(
                **dict(refund),
                bank_account_name=bank_name,
            )


# ============================================================================
# VOID
# ============================================================================

@router.post("/{deposit_id}/void", response_model=VoidDepositResponse)
async def void_vendor_deposit(request: Request, deposit_id: UUID):
    """Void vendor deposit (only if not applied)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        vd = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id, ctx["tenant_id"]
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if vd["applied_amount"] > 0:
            raise HTTPException(status_code=400, detail="Cannot void deposit with applications")

        if vd["status"] == "void":
            raise HTTPException(status_code=400, detail="Deposit already voided")

        async with conn.transaction():
            # TODO: Create reversal journal if posted
            await conn.execute(
                """
                UPDATE vendor_deposits SET status = 'void', updated_at = NOW()
                WHERE id = $1
                """,
                deposit_id
            )

            return VoidDepositResponse(
                deposit_id=deposit_id,
                deposit_number=vd["deposit_number"],
                status=VendorDepositStatus.void,
            )


# ============================================================================
# VENDOR SPECIFIC ENDPOINTS
# ============================================================================

@router.get("/by-vendor/{vendor_id}", response_model=VendorDepositsForVendorResponse)
async def get_vendor_deposits_for_vendor(
    request: Request,
    vendor_id: UUID,
    status: Optional[VendorDepositStatus] = None,
):
    """Get all deposits for a vendor"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        vendor = await conn.fetchrow(
            "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
            vendor_id, ctx["tenant_id"]
        )
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

        rows = await conn.fetch(
            "SELECT * FROM get_vendor_deposits($1, $2)",
            vendor_id, status.value if status else None
        )

        items = [VendorDepositResponse(
            id=row["id"],
            tenant_id=ctx["tenant_id"],
            deposit_number=row["deposit_number"],
            deposit_date=row["deposit_date"],
            vendor_id=vendor_id,
            amount=row["amount"],
            applied_amount=row["applied_amount"],
            remaining_amount=row["remaining_amount"],
            status=row["status"],
            reference=row["reference"],
            purchase_order_id=row["purchase_order_id"],
            payment_method="transfer",
            vendor_name=vendor["name"],
        ) for row in rows]

        total_deposits = sum(i.amount for i in items)
        total_applied = sum(i.applied_amount for i in items)

        return VendorDepositsForVendorResponse(
            vendor_id=vendor_id,
            vendor_name=vendor["name"],
            items=items,
            total_deposits=total_deposits,
            total_applied=total_applied,
            total_remaining=total_deposits - total_applied,
        )


@router.get("/available/{vendor_id}", response_model=AvailableDepositsResponse)
async def get_available_deposits_for_vendor(request: Request, vendor_id: UUID):
    """Get available deposits for application"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        vendor = await conn.fetchrow(
            "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
            vendor_id, ctx["tenant_id"]
        )
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

        rows = await conn.fetch(
            "SELECT * FROM get_available_vendor_deposits($1)",
            vendor_id
        )

        items = [AvailableDepositItem(**dict(row)) for row in rows]
        total_available = sum(i.remaining_amount for i in items)

        return AvailableDepositsResponse(
            vendor_id=vendor_id,
            vendor_name=vendor["name"],
            items=items,
            total_available=total_available,
        )
