"""
Bill Payments Router - Pembayaran Keluar (Payment Out)

Endpoints for managing vendor payments for purchase invoices (bills).
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import uuid as uuid_module
import logging
import asyncpg
from datetime import date

from ..schemas.bill_payments import (
    CreateBillPaymentRequest,
    VoidBillPaymentRequest,
    BillPaymentDetailResponse,
    BillPaymentListResponse,
    BillPaymentResponse,
    BillPaymentSummaryResponse,
    BillPaymentDetail,
    BillPaymentListItem,
    BillAllocationResponse,
    OpenBillsResponse,
    OpenBillItem,
)
from ..config import settings

# Account codes for bill payment journals
AP_ACCOUNT = "2-10100"  # Hutang Usaha (Accounts Payable)
PURCHASE_DISCOUNT_ACCOUNT = "5-10200"  # Diskon Pembelian (Purchase Discount)

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


async def check_period_is_open(conn, tenant_id: str, transaction_date) -> None:
    period = await conn.fetchrow(
        """SELECT id, period_name, status FROM fiscal_periods
           WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
           ORDER BY start_date DESC LIMIT 1""",
        tenant_id,
        transaction_date,
    )
    if period and period["status"] in ("CLOSED", "LOCKED"):
        raise HTTPException(
            status_code=403,
            detail=f"Cannot post to {period['status'].lower()} period ({period['period_name']})",
        )


async def generate_payment_number(conn, tenant_id: str) -> str:
    from datetime import datetime

    year_month = datetime.now().strftime("%Y-%m")
    try:
        row = await conn.fetchrow(
            """INSERT INTO bill_payment_sequences (tenant_id, year_month, prefix, last_number)
               VALUES ($1, $2, 'PAY', 1)
               ON CONFLICT (tenant_id, year_month)
               DO UPDATE SET last_number = bill_payment_sequences.last_number + 1
               RETURNING last_number""",
            tenant_id,
            year_month,
        )
        return f"PAY-{year_month.replace('-', '')}-{row['last_number']:04d}"
    except Exception:
        return (
            f"PAY-{year_month.replace('-', '')}-{uuid_module.uuid4().hex[:6].upper()}"
        )


async def create_bill_payment_journal(
    conn,
    ctx: dict,
    payment_id,
    payment_number: str,
    payment_date,
    vendor_name: str,
    bank_account_id,
    total_amount: int,
    allocated_amount: int,
    discount_amount: int = 0,
    discount_account_id=None,
    bank_fee_amount: int = 0,
    bank_fee_account_id=None,
    source_type: str = "cash",
    source_deposit_id=None,
    unapplied_amount: int = 0,
) -> tuple:
    """
    Create journal entry for bill payment.

    Journal Entry (when payment is posted):
    - Dr. Accounts Payable (2-10100) = allocated_amount (reduces what we owe)
    - Dr. Biaya Bank (expense) = bank_fee_amount (expense for bank fees)
    - Cr. Bank/Cash Account = total_amount + bank_fee_amount (money out)
    - Cr. Potongan Pembelian (5-10200) = discount_amount (discount received from vendor)

    Returns: (journal_id, journal_number)
    """
    # Get bank account COA ID
    bank_coa_id = None
    if bank_account_id:
        bank = await conn.fetchrow(
            "SELECT coa_id, account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
            bank_account_id
            if isinstance(bank_account_id, UUID)
            else UUID(str(bank_account_id)),
            ctx["tenant_id"],
        )
        if bank:
            bank_coa_id = bank["coa_id"]

    # Get AP account (Hutang Usaha)
    ap_account_id = await conn.fetchval(
        "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
        ctx["tenant_id"],
        AP_ACCOUNT,
    )
    if not ap_account_id:
        raise HTTPException(
            status_code=500, detail=f"AP account ({AP_ACCOUNT}) not found"
        )

    # Get or default purchase discount account
    purchase_discount_account_id = discount_account_id
    if discount_amount > 0 and not purchase_discount_account_id:
        purchase_discount_account_id = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2",
            ctx["tenant_id"],
            PURCHASE_DISCOUNT_ACCOUNT,
        )

    # Get vendor deposit account for unapplied amounts (overpayments to vendor)
    vendor_deposit_account_id = None
    if unapplied_amount > 0:
        vendor_deposit_account_id = await conn.fetchval(
            """SELECT id FROM chart_of_accounts
               WHERE tenant_id = $1 AND (account_code = '1-10500' OR name ILIKE '%uang muka vendor%')
               LIMIT 1""",
            ctx["tenant_id"],
        )

    # Generate journal number using database function
    journal_number = await conn.fetchval(
        "SELECT get_next_journal_number($1, 'BP')", ctx["tenant_id"]
    )
    if not journal_number:
        journal_number = f"JRN-{payment_number}"

    # Calculate total debit (must equal total credit for balanced journal)
    total_debit = float(allocated_amount + bank_fee_amount)
    if unapplied_amount > 0:
        total_debit += float(unapplied_amount)

    journal_id = uuid_module.uuid4()
    trace_id = uuid_module.uuid4()

    # Create journal entry header
    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            status, total_debit, total_credit, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'BILL_PAYMENT', $6, $7, 'POSTED', $8, $8, $9)
        """,
        journal_id,
        ctx["tenant_id"],
        journal_number,
        payment_date,
        f"Pembayaran ke {vendor_name} - {payment_number}",
        payment_id if isinstance(payment_id, UUID) else UUID(str(payment_id)),
        str(trace_id),
        total_debit,
        ctx["user_id"],
    )

    line_number = 0

    # Dr. Accounts Payable (reduces liability)
    if allocated_amount > 0:
        line_number += 1
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            ap_account_id,
            float(allocated_amount),
            f"Pelunasan hutang usaha - {vendor_name}",
        )

    # Dr. Bank Fee (expense)
    if bank_fee_amount > 0 and bank_fee_account_id:
        line_number += 1
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            bank_fee_account_id
            if isinstance(bank_fee_account_id, UUID)
            else UUID(str(bank_fee_account_id)),
            float(bank_fee_amount),
            "Biaya administrasi bank",
        )

    # Dr. Vendor Deposit (if overpaying)
    if unapplied_amount > 0 and vendor_deposit_account_id:
        line_number += 1
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            vendor_deposit_account_id,
            float(unapplied_amount),
            f"Uang muka vendor - {vendor_name}",
        )

    # Cr. Bank/Cash (money going out)
    cash_out = total_amount + bank_fee_amount
    if cash_out > 0 and bank_coa_id:
        line_number += 1
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            bank_coa_id,
            float(cash_out),
            f"Pembayaran dari bank - {payment_number}",
        )
    elif cash_out > 0 and source_type == "deposit" and source_deposit_id:
        deposit_coa = await conn.fetchval(
            """SELECT id FROM chart_of_accounts
               WHERE tenant_id = $1 AND (account_code = '1-10500' OR name ILIKE '%uang muka vendor%')
               LIMIT 1""",
            ctx["tenant_id"],
        )
        if deposit_coa:
            line_number += 1
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                uuid_module.uuid4(),
                journal_id,
                line_number,
                deposit_coa,
                float(cash_out),
                f"Aplikasi uang muka vendor - {payment_number}",
            )

    # Cr. Potongan Pembelian (discount received)
    if discount_amount > 0 and purchase_discount_account_id:
        line_number += 1
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            purchase_discount_account_id
            if isinstance(purchase_discount_account_id, UUID)
            else UUID(str(purchase_discount_account_id)),
            float(discount_amount),
            "Potongan pembelian",
        )

    logger.info(f"Bill payment journal created: {journal_id}, number: {journal_number}")

    return journal_id, journal_number


@router.get("", response_model=BillPaymentListResponse)
async def list_bill_payments(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "voided"]] = Query("all"),
    vendor_id: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    sort_by: Literal[
        "payment_date", "payment_number", "vendor_name", "total_amount", "created_at"
    ] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["bp.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"bp.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if vendor_id:
                conditions.append(f"bp.vendor_id = ${param_idx}::uuid")
                params.append(vendor_id)
                param_idx += 1

            if payment_method:
                conditions.append(f"bp.payment_method = ${param_idx}")
                params.append(payment_method)
                param_idx += 1

            if search:
                conditions.append(
                    f"(bp.payment_number ILIKE ${param_idx} OR bp.vendor_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            if date_from:
                conditions.append(f"bp.payment_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"bp.payment_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            sort_column = {
                "payment_date": "bp.payment_date",
                "payment_number": "bp.payment_number",
                "vendor_name": "bp.vendor_name",
                "total_amount": "bp.total_amount",
                "created_at": "bp.created_at",
            }.get(sort_by, "bp.created_at")

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bill_payments_v2 bp WHERE {where_clause}",
                *params,
            )

            list_query = f"""
                SELECT bp.id, bp.payment_number, bp.vendor_id, bp.vendor_name, bp.payment_date,
                       bp.payment_method, bp.total_amount, bp.allocated_amount, bp.unapplied_amount,
                       bp.status, bp.created_at,
                       (SELECT COUNT(*) FROM bill_payment_allocations bpa WHERE bpa.payment_id = bp.id) as bill_count
                FROM bill_payments_v2 bp WHERE {where_clause}
                ORDER BY {sort_column} {sort_order.upper()}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}"""
            params.extend([limit, skip])
            rows = await conn.fetch(list_query, *params)

            items = [
                BillPaymentListItem(
                    id=str(r["id"]),
                    payment_number=r["payment_number"] or "",
                    vendor_id=str(r["vendor_id"]) if r["vendor_id"] else None,
                    vendor_name=r["vendor_name"] or "",
                    payment_date=r["payment_date"].isoformat()
                    if r["payment_date"]
                    else "",
                    payment_method=r["payment_method"] or "bank_transfer",
                    total_amount=r["total_amount"] or 0,
                    allocated_amount=r["allocated_amount"] or 0,
                    unapplied_amount=r["unapplied_amount"] or 0,
                    status=r["status"] or "draft",
                    bill_count=r["bill_count"] or 0,
                    created_at=r["created_at"].isoformat() if r["created_at"] else "",
                )
                for r in rows
            ]

            return BillPaymentListResponse(
                items=items, total=total or 0, has_more=(skip + limit) < (total or 0)
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing bill payments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list bill payments")


@router.get("/summary", response_model=BillPaymentSummaryResponse)
async def get_bill_payments_summary(request: Request):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            summary = await conn.fetchrow(
                """
                SELECT COUNT(*) as total_count, COALESCE(SUM(total_amount), 0) as total_paid,
                    COALESCE(SUM(CASE WHEN status = 'draft' THEN total_amount ELSE 0 END), 0) as draft_amount,
                    COUNT(CASE WHEN status = 'draft' THEN 1 END) as draft_count,
                    COALESCE(SUM(CASE WHEN status = 'posted' THEN total_amount ELSE 0 END), 0) as posted_amount,
                    COUNT(CASE WHEN status = 'posted' THEN 1 END) as posted_count,
                    COALESCE(SUM(CASE WHEN payment_date = CURRENT_DATE THEN total_amount ELSE 0 END), 0) as today_amount,
                    COALESCE(SUM(CASE WHEN payment_date >= date_trunc('week', CURRENT_DATE) THEN total_amount ELSE 0 END), 0) as week_amount,
                    COALESCE(SUM(CASE WHEN payment_date >= date_trunc('month', CURRENT_DATE) THEN total_amount ELSE 0 END), 0) as month_amount
                FROM bill_payments_v2 WHERE tenant_id = $1 AND status != 'voided'""",
                ctx["tenant_id"],
            )

            method_breakdown = await conn.fetch(
                """
                SELECT payment_method, COUNT(*) as count, COALESCE(SUM(total_amount), 0) as amount
                FROM bill_payments_v2 WHERE tenant_id = $1 AND status != 'voided'
                GROUP BY payment_method""",
                ctx["tenant_id"],
            )

            by_method = {
                r["payment_method"]: {"count": r["count"], "amount": r["amount"]}
                for r in method_breakdown
            }

            return BillPaymentSummaryResponse(
                success=True,
                data={
                    "total_paid": summary["total_paid"],
                    "total_count": summary["total_count"],
                    "by_status": {
                        "draft": {
                            "count": summary["draft_count"],
                            "amount": summary["draft_amount"],
                        },
                        "posted": {
                            "count": summary["posted_count"],
                            "amount": summary["posted_amount"],
                        },
                    },
                    "by_method": by_method,
                    "today_amount": summary["today_amount"],
                    "this_week_amount": summary["week_amount"],
                    "this_month_amount": summary["month_amount"],
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


@router.get("/{payment_id}", response_model=BillPaymentDetailResponse)
async def get_bill_payment(request: Request, payment_id: str):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            payment = await conn.fetchrow(
                """
                SELECT bp.*, ba.account_name as bank_account_name_lookup
                FROM bill_payments_v2 bp LEFT JOIN bank_accounts ba ON ba.id = bp.bank_account_id
                WHERE bp.id = $1::uuid AND bp.tenant_id = $2""",
                payment_id,
                ctx["tenant_id"],
            )

            if not payment:
                raise HTTPException(status_code=404, detail="Payment not found")

            allocations = await conn.fetch(
                """
                SELECT bpa.id, bpa.bill_id, b.invoice_number as bill_number, b.amount as bill_amount,
                       bpa.remaining_before, bpa.amount_applied, bpa.remaining_after
                FROM bill_payment_allocations bpa JOIN bills b ON b.id = bpa.bill_id
                WHERE bpa.payment_id = $1::uuid ORDER BY bpa.created_at""",
                payment_id,
            )

            allocation_list = [
                BillAllocationResponse(
                    id=str(r["id"]),
                    bill_id=str(r["bill_id"]),
                    bill_number=r["bill_number"] or "",
                    bill_amount=r["bill_amount"] or 0,
                    remaining_before=r["remaining_before"] or 0,
                    amount_applied=r["amount_applied"] or 0,
                    remaining_after=r["remaining_after"] or 0,
                )
                for r in allocations
            ]

            detail = BillPaymentDetail(
                id=str(payment["id"]),
                payment_number=payment["payment_number"] or "",
                vendor_id=str(payment["vendor_id"]) if payment["vendor_id"] else None,
                vendor_name=payment["vendor_name"] or "",
                payment_date=payment["payment_date"].isoformat()
                if payment["payment_date"]
                else "",
                payment_method=payment["payment_method"] or "bank_transfer",
                bank_account_id=str(payment["bank_account_id"])
                if payment["bank_account_id"]
                else "",
                bank_account_name=payment["bank_account_name"]
                or payment["bank_account_name_lookup"]
                or "",
                source_type=payment.get("source_type") or "cash",
                source_deposit_id=str(payment["source_deposit_id"])
                if payment.get("source_deposit_id")
                else None,
                total_amount=payment["total_amount"] or 0,
                allocated_amount=payment["allocated_amount"] or 0,
                unapplied_amount=payment["unapplied_amount"] or 0,
                discount_amount=payment.get("discount_amount") or 0,
                discount_account_id=str(payment["discount_account_id"])
                if payment.get("discount_account_id")
                else None,
                bank_fee_amount=payment.get("bank_fee_amount") or 0,
                bank_fee_account_id=str(payment["bank_fee_account_id"])
                if payment.get("bank_fee_account_id")
                else None,
                currency_code=payment.get("currency_code") or "IDR",
                exchange_rate=float(payment.get("exchange_rate") or 1.0),
                amount_in_base_currency=payment.get("amount_in_base_currency")
                or payment["total_amount"]
                or 0,
                check_number=payment.get("check_number"),
                check_due_date=payment["check_due_date"].isoformat()
                if payment.get("check_due_date")
                else None,
                check_bank_name=payment.get("check_bank_name"),
                status=payment["status"] or "draft",
                reference_number=payment.get("reference_number"),
                notes=payment.get("notes"),
                tags=payment.get("tags") or [],
                journal_id=str(payment["journal_id"])
                if payment.get("journal_id")
                else None,
                journal_number=payment.get("journal_number"),
                void_journal_id=str(payment["void_journal_id"])
                if payment.get("void_journal_id")
                else None,
                created_deposit_id=str(payment["created_deposit_id"])
                if payment.get("created_deposit_id")
                else None,
                allocations=allocation_list,
                posted_at=payment["posted_at"].isoformat()
                if payment.get("posted_at")
                else None,
                posted_by=str(payment["posted_by"])
                if payment.get("posted_by")
                else None,
                voided_at=payment["voided_at"].isoformat()
                if payment.get("voided_at")
                else None,
                voided_by=str(payment["voided_by"])
                if payment.get("voided_by")
                else None,
                void_reason=payment.get("void_reason"),
                created_at=payment["created_at"].isoformat()
                if payment["created_at"]
                else "",
                updated_at=payment["updated_at"].isoformat()
                if payment.get("updated_at")
                else "",
                created_by=str(payment["created_by"])
                if payment.get("created_by")
                else None,
            )

            return BillPaymentDetailResponse(success=True, data=detail)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get payment")


@router.post("", response_model=BillPaymentResponse, status_code=201)
async def create_bill_payment(request: Request, payload: CreateBillPaymentRequest):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Idempotency check (Law 14)
                if payload.idempotency_key:
                    existing = await conn.fetchrow(
                        "SELECT id, payment_number, status FROM bill_payments_v2 WHERE tenant_id = $1 AND idempotency_key = $2",
                        ctx["tenant_id"], payload.idempotency_key
                    )
                    if existing:
                        return BillPaymentResponse(
                            success=True,
                            message=f"Payment {existing['payment_number']} already exists (idempotent)",
                            data={"id": str(existing["id"]), "payment_number": existing['payment_number'], "status": existing["status"]}
                        )


                if not payload.save_as_draft:
                    await check_period_is_open(
                        conn, ctx["tenant_id"], payload.payment_date
                    )

                # Auto-lookup vendor_name if not provided
                if not payload.vendor_name:
                    vendor = await conn.fetchrow(
                        "SELECT name FROM vendors WHERE id = $1 AND tenant_id = $2",
                        UUID(payload.vendor_id),
                        ctx["tenant_id"],
                    )
                    if not vendor:
                        raise HTTPException(status_code=400, detail="Vendor not found")
                    vendor_name = vendor["name"]
                else:
                    vendor_name = payload.vendor_name

                # Auto-lookup bank_account_name if not provided
                if not payload.bank_account_name:
                    bank = await conn.fetchrow(
                        "SELECT account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                        UUID(payload.bank_account_id),
                        ctx["tenant_id"],
                    )
                    if not bank:
                        raise HTTPException(
                            status_code=400, detail="Bank account not found"
                        )
                    bank_account_name = bank["account_name"]
                else:
                    bank_account_name = payload.bank_account_name

                payment_number = await generate_payment_number(conn, ctx["tenant_id"])
                allocated_amount = sum(a.amount_applied for a in payload.allocations)
                unapplied_amount = (
                    payload.total_amount - allocated_amount - payload.discount_amount
                )

                if allocated_amount + payload.discount_amount > payload.total_amount:
                    raise HTTPException(
                        status_code=400,
                        detail="Allocations plus discount exceeds payment",
                    )

                status = "draft" if payload.save_as_draft else "posted"
                payment_id = uuid_module.uuid4()

                await conn.execute(
                    """
                    INSERT INTO bill_payments_v2 (
                        id, tenant_id, payment_number, vendor_id, vendor_name, payment_date,
                        payment_method, bank_account_id, bank_account_name, total_amount,
                        allocated_amount, unapplied_amount, discount_amount, discount_account_id,
                        bank_fee_amount, bank_fee_account_id, currency_code, exchange_rate,
                        amount_in_base_currency, check_number, check_due_date, check_bank_name,
                        source_type, source_deposit_id, reference_number, notes, tags, status,
                        created_by, created_at, updated_at, posted_at, posted_by, idempotency_key
                    ) VALUES (
                        $1, $2, $3, $4::uuid, $5, $6, $7, $8::uuid, $9, $10, $11, $12, $13, $14::uuid,
                        $15, $16::uuid, $17, $18, $19, $20, $21, $22, $23, $24::uuid, $25, $26, $27, $28,
                        $29, NOW(), NOW(),
                        CASE WHEN $28::varchar = 'posted' THEN NOW() ELSE NULL END,
                        CASE WHEN $28::varchar = 'posted' THEN $29::uuid ELSE NULL END, $30
                    )""",
                    payment_id,
                    ctx["tenant_id"],
                    payment_number,
                    payload.vendor_id,
                    vendor_name,
                    payload.payment_date,
                    payload.payment_method,
                    payload.bank_account_id,
                    bank_account_name,
                    payload.total_amount,
                    allocated_amount,
                    unapplied_amount,
                    payload.discount_amount,
                    payload.discount_account_id,
                    payload.bank_fee_amount,
                    payload.bank_fee_account_id,
                    payload.currency_code,
                    payload.exchange_rate,
                    int(payload.total_amount * payload.exchange_rate),
                    payload.check_number,
                    payload.check_due_date,
                    payload.check_bank_name,
                    payload.source_type,
                    payload.source_deposit_id,
                    payload.reference_number,
                    payload.notes,
                    payload.tags,
                    status,
                    ctx["user_id"],
                    payload.idempotency_key,
                )

                for alloc in payload.allocations:
                    bill = await conn.fetchrow(
                        "SELECT amount as total_amount, COALESCE(amount_paid, 0) as amount_paid FROM bills WHERE id = $1::uuid FOR UPDATE",
                        alloc.bill_id,
                    )
                    if not bill:
                        raise HTTPException(
                            status_code=400, detail=f"Bill {alloc.bill_id} not found"
                        )

                    remaining_before = bill["total_amount"] - bill["amount_paid"]
                    if alloc.amount_applied > remaining_before:
                        raise HTTPException(
                            status_code=400, detail="Amount exceeds remaining"
                        )

                    remaining_after = remaining_before - alloc.amount_applied

                    await conn.execute(
                        """
                        INSERT INTO bill_payment_allocations (id, payment_id, bill_id, remaining_before, amount_applied, remaining_after, created_at)
                        VALUES (gen_random_uuid(), $1, $2::uuid, $3, $4, $5, NOW())""",
                        payment_id,
                        alloc.bill_id,
                        remaining_before,
                        alloc.amount_applied,
                        remaining_after,
                    )

                    if not payload.save_as_draft:
                        await conn.execute(
                            """
                            UPDATE bills SET amount_paid = COALESCE(amount_paid, 0) + $1,
                                status = CASE WHEN COALESCE(amount_paid, 0) + $1 >= amount THEN 'paid'
                                              WHEN COALESCE(amount_paid, 0) + $1 > 0 THEN 'partial' ELSE status END,
                                updated_at = NOW()
                            WHERE id = $2::uuid""",
                            alloc.amount_applied,
                            alloc.bill_id,
                        )

                # Create journal entry for posted payments
                journal_id = None
                journal_number = None
                if not payload.save_as_draft:
                    journal_id, journal_number = await create_bill_payment_journal(
                        conn=conn,
                        ctx=ctx,
                        payment_id=payment_id,
                        payment_number=payment_number,
                        payment_date=payload.payment_date,
                        vendor_name=vendor_name,
                        bank_account_id=payload.bank_account_id,
                        total_amount=payload.total_amount,
                        allocated_amount=allocated_amount,
                        discount_amount=payload.discount_amount,
                        discount_account_id=payload.discount_account_id,
                        bank_fee_amount=payload.bank_fee_amount,
                        bank_fee_account_id=payload.bank_fee_account_id,
                        source_type=payload.source_type,
                        source_deposit_id=payload.source_deposit_id,
                        unapplied_amount=unapplied_amount,
                    )

                    # Update payment with journal info
                    await conn.execute(
                        """UPDATE bill_payments_v2
                           SET journal_id = $1, journal_number = $2
                           WHERE id = $3""",
                        journal_id,
                        journal_number,
                        payment_id,
                    )

                return BillPaymentResponse(
                    success=True,
                    message=f"Payment {payment_number} {'created as draft' if payload.save_as_draft else 'posted'}",
                    data={
                        "id": str(payment_id),
                        "payment_number": payment_number,
                        "status": status,
                    },
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating payment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create payment")


@router.delete("/{payment_id}", response_model=BillPaymentResponse)
async def delete_bill_payment(request: Request, payment_id: str):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                payment = await conn.fetchrow(
                    "SELECT id, payment_number, status FROM bill_payments_v2 WHERE id = $1::uuid AND tenant_id = $2",
                    payment_id,
                    ctx["tenant_id"],
                )

                if not payment:
                    raise HTTPException(status_code=404, detail="Payment not found")
                if payment["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft payments can be deleted"
                    )

                await conn.execute(
                    "DELETE FROM bill_payment_allocations WHERE payment_id = $1::uuid",
                    payment_id,
                )
                await conn.execute(
                    "DELETE FROM bill_payments_v2 WHERE id = $1::uuid", payment_id
                )

                return BillPaymentResponse(
                    success=True,
                    message=f"Payment {payment['payment_number']} deleted",
                    data={"id": payment_id},
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting payment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete payment")


@router.post("/{payment_id}/post", response_model=BillPaymentResponse)
async def post_bill_payment(request: Request, payment_id: str):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                payment = await conn.fetchrow(
                    "SELECT id, payment_number, payment_date, status FROM bill_payments_v2 WHERE id = $1::uuid AND tenant_id = $2",
                    payment_id,
                    ctx["tenant_id"],
                )

                if not payment:
                    raise HTTPException(status_code=404, detail="Payment not found")
                if payment["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft payments can be posted"
                    )

                await check_period_is_open(
                    conn, ctx["tenant_id"], payment["payment_date"]
                )

                allocations = await conn.fetch(
                    "SELECT bill_id, amount_applied FROM bill_payment_allocations WHERE payment_id = $1::uuid",
                    payment_id,
                )

                for alloc in allocations:
                    await conn.execute(
                        """
                        UPDATE bills SET amount_paid = COALESCE(amount_paid, 0) + $1,
                            status = CASE WHEN COALESCE(amount_paid, 0) + $1 >= amount THEN 'paid'
                                          WHEN COALESCE(amount_paid, 0) + $1 > 0 THEN 'partial' ELSE status END,
                            updated_at = NOW()
                        WHERE id = $2""",
                        alloc["amount_applied"],
                        alloc["bill_id"],
                    )

                # Get full payment details for journal creation
                full_payment = await conn.fetchrow(
                    """SELECT * FROM bill_payments_v2 WHERE id = $1::uuid""",
                    payment_id,
                )

                # Create journal entry
                journal_id, journal_number = await create_bill_payment_journal(
                    conn=conn,
                    ctx=ctx,
                    payment_id=payment_id,
                    payment_number=payment["payment_number"],
                    payment_date=payment["payment_date"],
                    vendor_name=full_payment["vendor_name"],
                    bank_account_id=full_payment["bank_account_id"],
                    total_amount=full_payment["total_amount"],
                    allocated_amount=full_payment["allocated_amount"] or 0,
                    discount_amount=full_payment["discount_amount"] or 0,
                    discount_account_id=full_payment["discount_account_id"],
                    bank_fee_amount=full_payment["bank_fee_amount"] or 0,
                    bank_fee_account_id=full_payment["bank_fee_account_id"],
                    source_type=full_payment.get("source_type") or "cash",
                    source_deposit_id=full_payment.get("source_deposit_id"),
                    unapplied_amount=full_payment["unapplied_amount"] or 0,
                )

                await conn.execute(
                    """UPDATE bill_payments_v2
                       SET status = 'posted', posted_at = NOW(), posted_by = $1,
                           journal_id = $2, journal_number = $3, updated_at = NOW()
                       WHERE id = $4::uuid""",
                    ctx["user_id"],
                    journal_id,
                    journal_number,
                    payment_id,
                )

                return BillPaymentResponse(
                    success=True,
                    message=f"Payment {payment['payment_number']} posted",
                    data={
                        "id": payment_id,
                        "status": "posted",
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                    },
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting payment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post payment")


@router.post("/{payment_id}/void", response_model=BillPaymentResponse)
async def void_bill_payment(
    request: Request, payment_id: str, payload: VoidBillPaymentRequest
):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Get full payment details including account IDs
                payment = await conn.fetchrow(
                    """
                    SELECT bp.*, ba.coa_id as bank_coa_id
                    FROM bill_payments_v2 bp
                    LEFT JOIN bank_accounts ba ON ba.id = bp.bank_account_id
                    WHERE bp.id = $1::uuid AND bp.tenant_id = $2
                    """,
                    payment_id,
                    ctx["tenant_id"],
                )

                if not payment:
                    raise HTTPException(status_code=404, detail="Payment not found")
                if payment["status"] != "posted":
                    raise HTTPException(
                        status_code=400, detail="Only posted payments can be voided"
                    )

                await check_period_is_open(
                    conn, ctx["tenant_id"], payment["payment_date"]
                )

                # Get AP account (Hutang Usaha)
                ap_account_id = await conn.fetchval(
                    "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = '2-10100'",
                    ctx["tenant_id"],
                )
                if not ap_account_id:
                    raise HTTPException(
                        status_code=500,
                        detail="AP account not found in chart of accounts",
                    )

                # Calculate amounts for journal
                allocated_amount = payment["allocated_amount"] or 0
                discount_amount = payment["discount_amount"] or 0
                bank_fee_amount = payment["bank_fee_amount"] or 0
                total_amount = payment["total_amount"] or 0
                bank_coa_id = payment["bank_coa_id"]

                # Generate void journal number
                void_journal_number = await conn.fetchval(
                    "SELECT get_next_journal_number($1, 'VD')", ctx["tenant_id"]
                )
                if not void_journal_number:
                    void_journal_number = f"VD-{payment['payment_number']}"

                void_journal_id = uuid_module.uuid4()

                # Calculate total for journal (must balance)
                # Reversing entry is OPPOSITE of original payment journal:
                # Original: Dr AP, Dr BankFee, Cr Bank, Cr Discount
                # Reversal: Cr AP, Cr BankFee, Dr Bank, Dr Discount
                journal_total = float(total_amount + bank_fee_amount)

                # Create reversing journal entry header
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'BILL_PAYMENT_VOID', $5::uuid, 'POSTED', $6, $6, $7)
                    """,
                    void_journal_id,
                    ctx["tenant_id"],
                    void_journal_number,
                    f"Void {payment['payment_number']} - {payment['vendor_name']} - {payload.void_reason}",
                    payment_id,
                    journal_total,
                    ctx["user_id"],
                )

                line_number = 0

                # Dr. Bank/Cash (reverses the original credit to bank)
                if bank_coa_id and (total_amount + bank_fee_amount) > 0:
                    line_number += 1
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid_module.uuid4(),
                        void_journal_id,
                        line_number,
                        bank_coa_id,
                        float(total_amount + bank_fee_amount),
                        f"Reversal - Bank {payment['bank_account_name'] or ''}",
                    )

                # Dr. Potongan Pembelian / Purchase Discount (reverses the original credit)
                if discount_amount > 0 and payment.get("discount_account_id"):
                    line_number += 1
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid_module.uuid4(),
                        void_journal_id,
                        line_number,
                        payment["discount_account_id"],
                        float(discount_amount),
                        "Reversal - Purchase Discount",
                    )

                # Cr. Accounts Payable (reverses the original debit)
                if allocated_amount > 0:
                    line_number += 1
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                        uuid_module.uuid4(),
                        void_journal_id,
                        line_number,
                        ap_account_id,
                        float(allocated_amount),
                        f"Reversal - AP {payment['vendor_name']}",
                    )

                # Cr. Biaya Bank / Bank Fee (reverses the original debit)
                if bank_fee_amount > 0 and payment.get("bank_fee_account_id"):
                    line_number += 1
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                        uuid_module.uuid4(),
                        void_journal_id,
                        line_number,
                        payment["bank_fee_account_id"],
                        float(bank_fee_amount),
                        "Reversal - Bank Fee",
                    )

                # Reverse bill allocations (reduce paid_amount on bills)
                allocations = await conn.fetch(
                    "SELECT bill_id, amount_applied FROM bill_payment_allocations WHERE payment_id = $1::uuid",
                    payment_id,
                )

                for alloc in allocations:
                    await conn.execute(
                        """
                        UPDATE bills SET amount_paid = GREATEST(0, COALESCE(amount_paid, 0) - $1),
                            status = CASE WHEN GREATEST(0, COALESCE(amount_paid, 0) - $1) = 0 THEN 'posted'
                                          WHEN GREATEST(0, COALESCE(amount_paid, 0) - $1) < amount THEN 'partial'
                                          ELSE status END,
                            updated_at = NOW()
                        WHERE id = $2""",
                        alloc["amount_applied"],
                        alloc["bill_id"],
                    )

                # Update payment status and link void journal
                await conn.execute(
                    """
                    UPDATE bill_payments_v2
                    SET status = 'voided',
                        void_journal_id = $1,
                        voided_at = NOW(),
                        voided_by = $2,
                        void_reason = $3,
                        updated_at = NOW()
                    WHERE id = $4::uuid
                    """,
                    void_journal_id,
                    ctx["user_id"],
                    payload.void_reason,
                    payment_id,
                )

                logger.info(
                    f"Bill payment voided: {payment_id}, void_journal_id: {void_journal_id}"
                )

                return BillPaymentResponse(
                    success=True,
                    message=f"Payment {payment['payment_number']} voided",
                    data={
                        "id": payment_id,
                        "status": "voided",
                        "void_journal_id": str(void_journal_id),
                    },
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding payment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void payment")


@router.get("/vendors/{vendor_id}/open-bills", response_model=OpenBillsResponse)
async def get_vendor_open_bills(request: Request, vendor_id: str):
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            bills = await conn.fetch(
                """
                SELECT id, invoice_number, issue_date as bill_date, due_date, amount as total_amount,
                    COALESCE(amount_paid, 0) as paid_amount,
                    amount - COALESCE(amount_paid, 0) as remaining_amount,
                    due_date < CURRENT_DATE as is_overdue,
                    GREATEST(0, CURRENT_DATE - due_date) as overdue_days
                FROM bills
                WHERE tenant_id = $1 AND vendor_id = $2::uuid
                  AND status IN ('posted', 'partial', 'overdue')
                  AND amount > COALESCE(amount_paid, 0)
                ORDER BY due_date ASC, bill_date ASC""",
                ctx["tenant_id"],
                vendor_id,
            )

            bill_list = [
                OpenBillItem(
                    id=str(r["id"]),
                    bill_number=r["invoice_number"] or "",
                    bill_date=r["bill_date"].isoformat() if r["bill_date"] else "",
                    due_date=r["due_date"].isoformat() if r["due_date"] else "",
                    total_amount=r["total_amount"] or 0,
                    paid_amount=r["paid_amount"] or 0,
                    remaining_amount=r["remaining_amount"] or 0,
                    is_overdue=r["is_overdue"] or False,
                    overdue_days=r["overdue_days"] or 0,
                )
                for r in bills
            ]

            total_remaining = sum(b.remaining_amount for b in bill_list)
            total_overdue = sum(b.remaining_amount for b in bill_list if b.is_overdue)

            return OpenBillsResponse(
                bills=bill_list,
                summary={
                    "total_bills": len(bill_list),
                    "total_remaining": total_remaining,
                    "total_overdue": total_overdue,
                    "overdue_count": sum(1 for b in bill_list if b.is_overdue),
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting open bills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get open bills")
