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
from ..services.resolve_account import resolve_account_id

router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


async def get_bill_remaining_from_journal(conn, tenant_id: str, bill_id) -> int:
    """
    Compute bill remaining balance from journal lines on AP account (Law 16).
    Outstanding = SUM(credit) - SUM(debit) on AP for all journals linked to this bill.
    """
    bill_id_str = str(bill_id)
    result = await conn.fetchval(
        """
        SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = $1
          AND je.status = 'POSTED'
          AND coa.account_code = '2-10100'
          AND (
              -- Original bill journal (AP credit)
              (je.source_type = 'BILL' AND je.source_id = $2::uuid)
              -- Bill payment journals (via allocations)
              OR (je.source_type IN ('BILL_PAYMENT', 'PAYMENT_BILL') AND je.source_id IN (
                  SELECT bp.id FROM bill_payments_v2 bp
                  JOIN bill_payment_allocations bpa ON bpa.payment_id = bp.id
                  WHERE bpa.bill_id = $2::uuid AND bp.tenant_id = $1
              ))
              -- Bill payment void journals
              OR (je.source_type = 'BILL_PAYMENT_VOID' AND je.source_id IN (
                  SELECT bp.id FROM bill_payments_v2 bp
                  JOIN bill_payment_allocations bpa ON bpa.payment_id = bp.id
                  WHERE bpa.bill_id = $2::uuid AND bp.tenant_id = $1
              ))
              -- Vendor credit application journals
              OR (je.source_type = 'VENDOR_CREDIT' AND je.source_id IN (
                  SELECT vca.vendor_credit_id FROM vendor_credit_applications vca
                  WHERE vca.bill_id = $2::uuid
              ))
              -- Vendor deposit application journals
              OR (je.source_type = 'DEPOSIT_APPLICATION' AND je.id IN (
                  SELECT vda.journal_id FROM vendor_deposit_applications vda
                  WHERE vda.bill_id = $2::uuid
              ))
          )
    """,
        tenant_id,
        bill_id_str,
    )
    return int(result or 0)


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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

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
            f"SELECT COUNT(*) FROM vendor_deposits vd WHERE {where_sql}", *params
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
            *params,
            skip,
            limit,
        )

        items = [VendorDepositResponse(**dict(row)) for row in rows]
        return VendorDepositListResponse(items=items, total=total)


@router.get("/summary", response_model=VendorDepositSummary)
async def get_vendor_deposit_summary(request: Request):
    """Get vendor deposit summary"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            "SELECT * FROM get_vendor_deposit_summary($1)", ctx["tenant_id"]
        )

        return VendorDepositSummary(**dict(row))


@router.get("/{deposit_id}", response_model=VendorDepositDetailResponse)
async def get_vendor_deposit(request: Request, deposit_id: UUID):
    """Get vendor deposit with applications and refunds"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

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
            deposit_id,
            ctx["tenant_id"],
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        applications = await conn.fetch(
            "SELECT * FROM get_vendor_deposit_applications($1)", deposit_id
        )

        refunds = await conn.fetch(
            "SELECT * FROM get_vendor_deposit_refunds($1)", deposit_id
        )

        return VendorDepositDetailResponse(
            **dict(vd),
            applications=[
                VendorDepositApplicationResponse(**dict(a)) for a in applications
            ],
            refunds=[VendorDepositRefundResponse(**dict(r)) for r in refunds],
        )


@router.post("", response_model=VendorDepositResponse, status_code=201)
async def create_vendor_deposit(request: Request, data: VendorDepositCreate):
    """Create vendor deposit (draft)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        # Validate vendor
        vendor = await conn.fetchrow(
            "SELECT id, name, code FROM vendors WHERE id = $1 AND tenant_id = $2",
            data.vendor_id,
            ctx["tenant_id"],
        )
        if not vendor:
            raise HTTPException(status_code=400, detail="Vendor not found")

        # Generate deposit number
        deposit_number = await conn.fetchval(
            "SELECT generate_vendor_deposit_number($1)", ctx["tenant_id"]
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
            ctx["tenant_id"],
            deposit_number,
            data.deposit_date,
            data.vendor_id,
            data.amount,
            data.payment_method.value,
            data.bank_account_id,
            data.reference,
            data.purchase_order_id,
            data.notes,
            ctx.get("user_id"),
        )

        return VendorDepositResponse(
            **dict(row),
            vendor_name=vendor["name"],
            vendor_code=vendor["code"],
        )


@router.patch("/{deposit_id}", response_model=VendorDepositResponse)
async def update_vendor_deposit(
    request: Request, deposit_id: UUID, data: VendorDepositUpdate
):
    """Update vendor deposit (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        existing = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id,
            ctx["tenant_id"],
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if existing["status"] != "draft":
            raise HTTPException(
                status_code=400, detail="Can only update draft deposits"
            )

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
                deposit_id,
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
            *params,
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
            deposit_id,
        )

        return VendorDepositResponse(**dict(row))


@router.delete("/{deposit_id}")
async def delete_vendor_deposit(request: Request, deposit_id: UUID):
    """Delete vendor deposit (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        existing = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id,
            ctx["tenant_id"],
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if existing["status"] != "draft":
            raise HTTPException(
                status_code=400, detail="Can only delete draft deposits"
            )

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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        vd = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id,
            ctx["tenant_id"],
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if vd["status"] != "draft":
            raise HTTPException(status_code=400, detail="Deposit is already posted")

        # Get accounts
        vendor_deposit_account = await resolve_account_id(
            conn, ctx["tenant_id"], "1-10800"
        )
        if not vendor_deposit_account:
            # Seed account if not exists
            await conn.execute(
                "SELECT seed_vendor_deposit_account($1)", ctx["tenant_id"]
            )
            vendor_deposit_account = await resolve_account_id(
                conn, ctx["tenant_id"], "1-10800"
            )

        bank_account = None
        if vd["bank_account_id"]:
            bank_account = await conn.fetchrow(
                "SELECT coa_id FROM bank_accounts WHERE id = $1", vd["bank_account_id"]
            )

        if not bank_account:
            # Use default cash account
            cash_account = await resolve_account_id(conn, ctx["tenant_id"], "1-10100")
        else:
            cash_account = bank_account["coa_id"]

        async with conn.transaction():
            # Law 13: Advisory lock
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"VENDOR_DEPOSIT:{deposit_id}",
            )

            # Law 5: Period check
            period_row = await conn.fetchrow(
                "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                ctx["tenant_id"],
                vd["deposit_date"],
            )
            if period_row and period_row["status"] != "OPEN":
                raise HTTPException(
                    status_code=400,
                    detail=f"Periode akuntansi sudah {period_row['status']}",
                )

            # Generate journal number
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_number_sequences
                    (tenant_id, prefix, year, month, last_number)
                VALUES ($1, 'JV', $2, $3, 1)
                ON CONFLICT (tenant_id, prefix, year, month)
                DO UPDATE SET
                    last_number = journal_number_sequences.last_number + 1,
                    updated_at = NOW()
                RETURNING last_number
                """,
                ctx["tenant_id"],
                vd["deposit_date"].year,
                vd["deposit_date"].month,
            )
            journal_number = f"JV-{vd['deposit_date'].year}-{seq['last_number']:05d}"

            # Law 20: Create journal entry as DRAFT first
            journal = await conn.fetchrow(
                """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, journal_date, description,
                    source_type, source_id, total_debit, total_credit, status, created_by
                ) VALUES ($1, $2, $3, $4, 'VENDOR_DEPOSIT', $5, $6, $6, 'DRAFT', $7)
                RETURNING id, journal_number
                """,
                ctx["tenant_id"],
                journal_number,
                vd["deposit_date"],
                f"Vendor Deposit - {vd['deposit_number']}",
                deposit_id,
                vd["amount"],
                ctx.get("user_id"),
            )

            # Journal lines
            # Dr. Uang Muka Vendor
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, 1, $2, $3, 0, $4)
                """,
                journal["id"],
                vendor_deposit_account,
                vd["amount"],
                f"Vendor Deposit - {vd['deposit_number']}",
            )

            # Cr. Kas/Bank
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, 2, $2, 0, $3, $4)
                """,
                journal["id"],
                cash_account,
                vd["amount"],
                f"Vendor Deposit - {vd['deposit_number']}",
            )

            # Law 20: Post after all lines inserted
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                journal["id"],
            )

            # Update deposit
            await conn.execute(
                """
                UPDATE vendor_deposits SET status = 'posted', journal_id = $2, updated_at = NOW()
                WHERE id = $1
                """,
                deposit_id,
                journal["id"],
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
async def apply_vendor_deposit(
    request: Request, deposit_id: UUID, data: ApplyDepositRequest
):
    """
    Apply vendor deposit to bill - creates journal entry:
    Dr. Hutang Usaha (2-10100)        applied_amount
        Cr. Uang Muka Vendor (1-10800)    applied_amount
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        vd = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id,
            ctx["tenant_id"],
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if vd["status"] not in ("posted", "partial"):
            raise HTTPException(
                status_code=400, detail="Deposit must be posted before applying"
            )

        if vd["remaining_amount"] < data.amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient deposit balance. Available: {vd['remaining_amount']}",
            )

        bill = await conn.fetchrow(
            "SELECT * FROM bills WHERE id = $1 AND vendor_id = $2",
            data.bill_id,
            vd["vendor_id"],
        )
        if not bill:
            raise HTTPException(
                status_code=400, detail="Bill not found or vendor mismatch"
            )

        if bill["status"] not in ("posted", "partial"):
            raise HTTPException(status_code=400, detail="Bill must be posted")

        bill_remaining = await get_bill_remaining_from_journal(
            conn, ctx["tenant_id"], bill["id"]
        )
        if data.amount > bill_remaining:
            raise HTTPException(
                status_code=400,
                detail=f"Amount exceeds bill balance. Bill balance: {bill_remaining}",
            )

        applied_date = data.applied_date or date.today()

        # Get accounts
        ap_account = await resolve_account_id(conn, ctx["tenant_id"], "2-10100")
        vendor_deposit_account = await resolve_account_id(
            conn, ctx["tenant_id"], "1-10800"
        )

        async with conn.transaction():
            # Law 13: Advisory lock
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"DEPOSIT_APPLY:{deposit_id}",
            )

            # Law 5: Period check
            period_row = await conn.fetchrow(
                "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                ctx["tenant_id"],
                applied_date,
            )
            if period_row and period_row["status"] != "OPEN":
                raise HTTPException(
                    status_code=400,
                    detail=f"Periode akuntansi sudah {period_row['status']}",
                )

            # Generate journal
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_number_sequences
                    (tenant_id, prefix, year, month, last_number)
                VALUES ($1, 'JV', $2, $3, 1)
                ON CONFLICT (tenant_id, prefix, year, month)
                DO UPDATE SET
                    last_number = journal_number_sequences.last_number + 1,
                    updated_at = NOW()
                RETURNING last_number
                """,
                ctx["tenant_id"],
                vd["deposit_date"].year,
                vd["deposit_date"].month,
            )
            journal_number = f"JV-{applied_date.year}-{seq['last_number']:05d}"

            # Law 20: Create journal as DRAFT
            journal = await conn.fetchrow(
                """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, journal_date, description,
                    source_type, source_id, total_debit, total_credit, status, created_by
                ) VALUES ($1, $2, $3, $4, 'DEPOSIT_APPLICATION', $5, $6, $6, 'DRAFT', $7)
                RETURNING id
                """,
                ctx["tenant_id"],
                journal_number,
                applied_date,
                f"Apply Deposit {vd['deposit_number']} to Bill {bill['bill_number']}",
                deposit_id,
                data.amount,
                ctx.get("user_id"),
            )

            # Dr. Hutang Usaha
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, 1, $2, $3, 0, $4)
                """,
                journal["id"],
                ap_account,
                data.amount,
                f"Apply Deposit to Bill {bill['bill_number']}",
            )

            # Cr. Uang Muka Vendor
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, 2, $2, 0, $3, $4)
                """,
                journal["id"],
                vendor_deposit_account,
                data.amount,
                f"Apply Deposit {vd['deposit_number']}",
            )

            # Law 20: Post after all lines
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                journal["id"],
            )

            # Create application record
            app = await conn.fetchrow(
                """
                INSERT INTO vendor_deposit_applications (
                    vendor_deposit_id, bill_id, amount, applied_date, journal_id, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                deposit_id,
                data.bill_id,
                data.amount,
                applied_date,
                journal["id"],
                ctx.get("user_id"),
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
                data.bill_id,
                data.amount,
            )

            # Fetch updated values
            updated_vd = await conn.fetchrow(
                "SELECT remaining_amount FROM vendor_deposits WHERE id = $1", deposit_id
            )
            updated_bill = await conn.fetchrow(
                "SELECT total_amount - COALESCE(paid_amount, 0) as remaining FROM bills WHERE id = $1",
                data.bill_id,
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
async def refund_vendor_deposit(
    request: Request, deposit_id: UUID, data: VendorDepositRefundCreate
):
    """
    Refund vendor deposit - creates journal entry:
    Dr. Kas/Bank                      refund_amount
        Cr. Uang Muka Vendor (1-10800)    refund_amount
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        vd = await conn.fetchrow(
            "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2",
            deposit_id,
            ctx["tenant_id"],
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Vendor deposit not found")

        if vd["status"] not in ("posted", "partial"):
            raise HTTPException(status_code=400, detail="Deposit must be posted")

        if vd["remaining_amount"] < data.amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Available: {vd['remaining_amount']}",
            )

        # Get accounts
        vendor_deposit_account = await resolve_account_id(
            conn, ctx["tenant_id"], "1-10800"
        )

        bank_account_coa = None
        if data.bank_account_id:
            bank_account_coa = await conn.fetchval(
                "SELECT coa_id FROM bank_accounts WHERE id = $1", data.bank_account_id
            )
        if not bank_account_coa:
            bank_account_coa = await resolve_account_id(
                conn, ctx["tenant_id"], "1-10100"
            )

        async with conn.transaction():
            # Law 13: Advisory lock
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"DEPOSIT_REFUND:{deposit_id}",
            )

            # Law 5: Period check
            period_row = await conn.fetchrow(
                "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                ctx["tenant_id"],
                data.refund_date,
            )
            if period_row and period_row["status"] != "OPEN":
                raise HTTPException(
                    status_code=400,
                    detail=f"Periode akuntansi sudah {period_row['status']}",
                )

            # Generate journal
            seq = await conn.fetchrow(
                """
                INSERT INTO journal_number_sequences
                    (tenant_id, prefix, year, month, last_number)
                VALUES ($1, 'JV', $2, $3, 1)
                ON CONFLICT (tenant_id, prefix, year, month)
                DO UPDATE SET
                    last_number = journal_number_sequences.last_number + 1,
                    updated_at = NOW()
                RETURNING last_number
                """,
                ctx["tenant_id"],
                data.refund_date.year,
                data.refund_date.month,
            )
            journal_number = f"JV-{data.refund_date.year}-{seq['last_number']:05d}"

            # Law 20: Create journal as DRAFT
            journal = await conn.fetchrow(
                """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, journal_date, description,
                    source_type, source_id, total_debit, total_credit, status, created_by
                ) VALUES ($1, $2, $3, $4, 'DEPOSIT_REFUND', $5, $6, $6, 'DRAFT', $7)
                RETURNING id
                """,
                ctx["tenant_id"],
                journal_number,
                data.refund_date,
                f"Refund Deposit {vd['deposit_number']}",
                deposit_id,
                data.amount,
                ctx.get("user_id"),
            )

            # Dr. Kas/Bank
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, 1, $2, $3, 0, $4)
                """,
                journal["id"],
                bank_account_coa,
                data.amount,
                f"Refund Deposit {vd['deposit_number']}",
            )

            # Cr. Uang Muka Vendor
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, 2, $2, 0, $3, $4)
                """,
                journal["id"],
                vendor_deposit_account,
                data.amount,
                f"Refund Deposit {vd['deposit_number']}",
            )

            # Law 20: Post after all lines
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                journal["id"],
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
                deposit_id,
                data.refund_date,
                data.amount,
                data.bank_account_id,
                data.reference,
                journal["id"],
                data.notes,
                ctx.get("user_id"),
            )

            bank_name = None
            if data.bank_account_id:
                bank_name = await conn.fetchval(
                    "SELECT name FROM bank_accounts WHERE id = $1", data.bank_account_id
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
    """
    Void vendor deposit (only if not applied).

    IRON LAW 2: Immutability - reversal journal, not edit
    IRON LAW 5: Period validation
    IRON LAW 13: Advisory lock VENDOR_DEPOSIT_VOID:{deposit_id}
    IRON LAW 20: DRAFT -> lines -> UPDATE POSTED
    IRON LAW 26: Reversal linking (reversal_of_id + reversed_by_id)
    """
    import uuid as uuid_module
    import logging

    logger = logging.getLogger(__name__)

    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        async with conn.transaction():
            # Law 13: Advisory lock FIRST in transaction
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"VENDOR_DEPOSIT_VOID:{deposit_id}",
            )

            # Read AFTER lock with FOR UPDATE (fixes TOCTOU)
            vd = await conn.fetchrow(
                "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                deposit_id,
                ctx["tenant_id"],
            )
            if not vd:
                raise HTTPException(status_code=404, detail="Vendor deposit not found")

            # C4: Idempotency guard AFTER lock
            if vd["status"] == "void":
                return VoidDepositResponse(
                    deposit_id=deposit_id,
                    deposit_number=vd["deposit_number"],
                    status=VendorDepositStatus.void,
                )

            if vd["status"] == "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot void draft deposit. Delete it instead.",
                )

            # C5: Dependency check — no applications
            if (vd["applied_amount"] or 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot void deposit with applications. Reverse applications first.",
                )

            # Create reversal journal if deposit was posted
            reversal_id = None
            if vd.get("journal_id"):
                original_journal = await conn.fetchrow(
                    "SELECT * FROM journal_entries WHERE id = $1 AND tenant_id = $2 AND status = 'POSTED'",
                    vd["journal_id"],
                    ctx["tenant_id"],
                )
                if not original_journal:
                    raise HTTPException(
                        status_code=500,
                        detail="Original journal not found or not POSTED",
                    )

                # Law 5: Period check on CURRENT_DATE
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= CURRENT_DATE AND end_date >= CURRENT_DATE",
                    ctx["tenant_id"],
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Get journal number
                journal_number = await conn.fetchval(
                    "SELECT get_next_journal_number($1, 'VD')", ctx["tenant_id"]
                )
                if not journal_number:
                    journal_number = f"VD-{vd['deposit_number']}"

                # Law 20 Step 1: Create reversal as DRAFT with reversal_of_id (Law 26)
                reversal_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date, description,
                        source_type, source_id, total_debit, total_credit,
                        status, created_by, reversal_of_id
                    ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'VENDOR_DEPOSIT', $5, $6, $6, 'DRAFT', $7, $8)
                    """,
                    reversal_id,
                    ctx["tenant_id"],
                    journal_number,
                    f"Void {vd['deposit_number']}",
                    str(deposit_id),
                    original_journal["total_debit"],
                    ctx.get("user_id"),
                    original_journal["id"],
                )

                # Law 20 Step 2: Insert reversed lines with line_number (swap debit<->credit)
                original_lines = await conn.fetch(
                    "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                    original_journal["id"],
                )
                for idx, line in enumerate(original_lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        uuid_module.uuid4(),
                        reversal_id,
                        idx,
                        line["account_id"],
                        line["credit"],  # Swap
                        line["debit"],  # Swap
                        f"Reversal - {line['memo'] or ''}",
                    )

                # Law 20 Step 3: Post reversal
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    reversal_id,
                )

                # Law 26: Mark original as reversed
                await conn.execute(
                    "UPDATE journal_entries SET reversed_by_id = $1 WHERE id = $2",
                    reversal_id,
                    original_journal["id"],
                )

            # C7: Bank mirror reversal (BankSync Rule 3)
            if vd.get("bank_account_id"):
                original_btxn = await conn.fetchrow(
                    """
                    SELECT id, bank_account_id, amount, transaction_type
                    FROM bank_transactions
                    WHERE (reference_type = 'vendor_deposit' OR reference_type = 'VENDOR_DEPOSIT')
                      AND reference_id = $1
                      AND status != 'VOIDED'
                    LIMIT 1
                    """,
                    deposit_id,
                )
                if original_btxn:
                    mirror_type = (
                        "deposit"
                        if original_btxn["transaction_type"] == "withdrawal"
                        else "withdrawal"
                    )
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date,
                            transaction_type, amount, running_balance,
                            reference_type, reference_id, reference_number,
                            description, journal_id, status, origin_type, source_module,
                            created_by, posted_by, posted_at
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, $5, 0,
                            'vendor_deposit_void', $6, $7, $8, $9,
                            'POSTED', 'SYSTEM', 'vendor_deposit', $10, $10, NOW())
                        """,
                        uuid_module.uuid4(),
                        ctx["tenant_id"],
                        original_btxn["bank_account_id"],
                        mirror_type,
                        -original_btxn["amount"],
                        deposit_id,
                        f"VOID-{vd['deposit_number']}",
                        f"Void deposit - {vd['deposit_number']}",
                        reversal_id,
                        ctx.get("user_id"),
                    )

            # Mark deposit as VOID
            await conn.execute(
                """
                UPDATE vendor_deposits SET status = 'void', updated_at = NOW()
                WHERE id = $1
                """,
                deposit_id,
            )

            logger.info(f"Vendor deposit voided: {deposit_id}")

            return VoidDepositResponse(
                deposit_id=deposit_id,
                deposit_number=vd["deposit_number"],
                status=VendorDepositStatus.void,
            )


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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        vendor = await conn.fetchrow(
            "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
            vendor_id,
            ctx["tenant_id"],
        )
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

        rows = await conn.fetch(
            "SELECT * FROM get_vendor_deposits($1, $2)",
            vendor_id,
            status.value if status else None,
        )

        items = [
            VendorDepositResponse(
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
            )
            for row in rows
        ]

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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        vendor = await conn.fetchrow(
            "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
            vendor_id,
            ctx["tenant_id"],
        )
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

        rows = await conn.fetch(
            "SELECT * FROM get_available_vendor_deposits($1)", vendor_id
        )

        items = [AvailableDepositItem(**dict(row)) for row in rows]
        total_available = sum(i.remaining_amount for i in items)

        return AvailableDepositsResponse(
            vendor_id=vendor_id,
            vendor_name=vendor["name"],
            items=items,
            total_available=total_available,
        )
