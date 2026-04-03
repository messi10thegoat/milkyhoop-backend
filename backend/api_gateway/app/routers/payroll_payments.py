"""
Payroll Payments Router — Settlement (salary, PPh 21, BPJS)
"""

from fastapi import APIRouter, HTTPException, Request
from typing import Optional
from uuid import UUID
import logging
import asyncpg

from ..schemas.payroll import CreatePayrollPaymentRequest, VoidPayrollRequest
from ..services.payroll_calc import (
    COA_HUTANG_GAJI,
    COA_HUTANG_PPH21,
    COA_HUTANG_BPJS_EE,
    COA_HUTANG_BPJS_ER,
)
from ..services.resolve_account import resolve_account_id
from ..config import settings

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
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


SOURCE_TYPE_MAP = {
    "salary": "PAYROLL_PAYMENT",
    "pph21": "PAYROLL_TAX_PAYMENT",
    "bpjs": "PAYROLL_BPJS_PAYMENT",
}


@router.post("")
async def create_payment(request: Request, body: CreatePayrollPaymentRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        run = await conn.fetchrow(
            "SELECT * FROM payroll_runs WHERE id = $1 AND tenant_id = $2 AND status = 'posted'",
            body.payroll_id,
            ctx["tenant_id"],
        )
        if not run:
            raise HTTPException(400, detail="Payroll run must be posted before payment")

        # Calculate amount based on payment type
        if body.payment_type == "salary":
            amount = await conn.fetchval(
                """SELECT SUM(CASE WHEN component_type = 'earning' THEN amount ELSE 0 END) -
                          SUM(CASE WHEN component_type = 'deduction' THEN amount ELSE 0 END)
                   FROM payroll_slip_lines WHERE payroll_id = $1""",
                body.payroll_id,
            )
        elif body.payment_type == "pph21":
            amount = await conn.fetchval(
                """SELECT SUM(amount) FROM payroll_slip_lines
                   WHERE payroll_id = $1 AND component_category IN ('pph21', 'pph21_employer')""",
                body.payroll_id,
            )
        elif body.payment_type == "bpjs":
            amount = await conn.fetchval(
                """SELECT SUM(amount) FROM payroll_slip_lines
                   WHERE payroll_id = $1
                     AND (component_category LIKE 'bpjs_%')""",
                body.payroll_id,
            )

        amount = float(amount or 0)
        if amount <= 0:
            raise HTTPException(400, detail="Payment amount must be positive")

        uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
        row = await conn.fetchrow(
            """INSERT INTO payroll_payments
               (tenant_id, payroll_id, payment_type, payment_date, amount,
                bank_account_id, reference_number, notes, status, created_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'draft', $9)
               RETURNING *""",
            ctx["tenant_id"],
            body.payroll_id,
            body.payment_type,
            body.payment_date,
            amount,
            body.bank_account_id,
            body.reference_number,
            body.notes,
            uid,
        )
        return {"success": True, "data": dict(row)}


@router.post("/{payment_id}/post")
async def post_payment(request: Request, payment_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"PAYROLL_PAYMENT:{payment_id}",
            )

            payment = await conn.fetchrow(
                "SELECT * FROM payroll_payments WHERE id = $1 AND tenant_id = $2",
                payment_id,
                ctx["tenant_id"],
            )
            if not payment:
                raise HTTPException(404, detail="Payment not found")
            if payment["status"] != "draft":
                raise HTTPException(400, detail="Can only post draft payments")

            # Get bank account CoA
            bank = await conn.fetchrow(
                "SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                payment["bank_account_id"],
                ctx["tenant_id"],
            )
            if not bank or not bank["coa_id"]:
                raise HTTPException(400, detail="Bank account has no linked CoA")

            ptype = payment["payment_type"]
            source_type = SOURCE_TYPE_MAP[ptype]
            amount = float(payment["amount"])

            # Build journal lines
            debit_accounts = []
            if ptype == "salary":
                coa = await resolve_account_id(conn, ctx["tenant_id"], COA_HUTANG_GAJI)
                debit_accounts.append((coa, amount, "Bayar Gaji"))
            elif ptype == "pph21":
                coa = await resolve_account_id(conn, ctx["tenant_id"], COA_HUTANG_PPH21)
                debit_accounts.append((coa, amount, "Setor PPh 21"))
            elif ptype == "bpjs":
                coa_ee = await resolve_account_id(
                    conn, ctx["tenant_id"], COA_HUTANG_BPJS_EE
                )
                coa_er = await resolve_account_id(
                    conn, ctx["tenant_id"], COA_HUTANG_BPJS_ER
                )
                # Split amount proportionally
                run = await conn.fetchrow(
                    "SELECT id FROM payroll_runs WHERE id = $1", payment["payroll_id"]
                )
                bpjs_ee = await conn.fetchval(
                    """SELECT COALESCE(SUM(amount), 0) FROM payroll_slip_lines
                       WHERE payroll_id = $1 AND component_type = 'deduction'
                         AND component_category LIKE 'bpjs_%'""",
                    payment["payroll_id"],
                )
                bpjs_er = await conn.fetchval(
                    """SELECT COALESCE(SUM(amount), 0) FROM payroll_slip_lines
                       WHERE payroll_id = $1 AND component_type = 'employer_cost'
                         AND component_category LIKE 'bpjs_%'""",
                    payment["payroll_id"],
                )
                bpjs_ee = float(bpjs_ee)
                bpjs_er = float(bpjs_er)
                if bpjs_ee > 0:
                    debit_accounts.append((coa_ee, bpjs_ee, "Setor BPJS Karyawan"))
                if bpjs_er > 0:
                    debit_accounts.append((coa_er, bpjs_er, "Setor BPJS Perusahaan"))

            total_debit = sum(a[1] for a in debit_accounts)

            # Create journal (DRAFT -> lines -> POSTED)
            journal_number = f"JV-PP-{ptype.upper()}-{payment_id.hex[:8]}"
            journal_id = await conn.fetchval(
                """INSERT INTO journal_entries (
                    tenant_id, journal_number, journal_date, description,
                    source_type, source_id, status, total_debit, total_credit
                ) VALUES ($1, $2, $3, $4, $5, $6, 'DRAFT', $7, $8)
                RETURNING id""",
                ctx["tenant_id"],
                journal_number,
                payment["payment_date"],
                f"Payroll payment: {ptype}",
                source_type,
                str(payment_id),
                total_debit,
                total_debit,
            )

            line_num = 1
            for coa_id, amt, memo in debit_accounts:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, $4, 0, $5)""",
                    str(journal_id),
                    line_num,
                    coa_id,
                    amt,
                    memo,
                )
                line_num += 1

            # Cr Bank
            await conn.execute(
                """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                   VALUES ($1, $2, $3, 0, $4, $5)""",
                str(journal_id),
                line_num,
                str(bank["coa_id"]),
                total_debit,
                f"Pembayaran {ptype}",
            )

            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
            )

            uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
            await conn.execute(
                """UPDATE payroll_payments SET status = 'posted',
                   journal_id = $2, posted_at = now(), posted_by = $3
                   WHERE id = $1""",
                payment_id,
                journal_id,
                uid,
            )

        return {
            "success": True,
            "message": "Payment posted",
            "journal_id": str(journal_id),
        }


@router.post("/{payment_id}/void")
async def void_payment(request: Request, payment_id: UUID, body: VoidPayrollRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        payment = await conn.fetchrow(
            "SELECT * FROM payroll_payments WHERE id = $1 AND tenant_id = $2",
            payment_id,
            ctx["tenant_id"],
        )
        if not payment:
            raise HTTPException(404, detail="Payment not found")
        if payment["status"] != "posted":
            raise HTTPException(400, detail="Can only void posted payments")

        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"PAYROLL_PAYMENT:{payment_id}",
            )

            if payment["journal_id"]:
                orig = await conn.fetchrow(
                    "SELECT * FROM journal_entries WHERE id = $1", payment["journal_id"]
                )
                if orig:
                    rev_id = await conn.fetchval(
                        """INSERT INTO journal_entries (
                            tenant_id, journal_number, journal_date, description,
                            source_type, source_id, status, total_debit, total_credit,
                            reversal_of_id
                        ) VALUES ($1, $2, CURRENT_DATE, $3, $4, $5, 'DRAFT', $6, $7, $8)
                        RETURNING id""",
                        ctx["tenant_id"],
                        f"REV-{orig['journal_number']}",
                        f"Reversal: {orig['description']}",
                        orig["source_type"],
                        str(payment_id),
                        float(orig["total_debit"]),
                        float(orig["total_credit"]),
                        orig["id"],
                    )
                    orig_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1",
                        str(orig["id"]),
                    )
                    for ol in orig_lines:
                        await conn.execute(
                            """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                               VALUES ($1, $2, $3, $4, $5, $6)""",
                            str(rev_id),
                            ol["line_number"],
                            ol["account_id"],
                            float(ol["credit"]),
                            float(ol["debit"]),
                            f"Reversal: {ol['memo'] or ''}",
                        )
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        rev_id,
                    )
                    await conn.execute(
                        "UPDATE journal_entries SET reversed_by_id = $1 WHERE id = $2",
                        rev_id,
                        orig["id"],
                    )

            uid = UUID(ctx["user_id"]) if ctx.get("user_id") else None
            await conn.execute(
                """UPDATE payroll_payments SET status = 'voided',
                   voided_at = now(), voided_by = $2, void_reason = $3
                   WHERE id = $1""",
                payment_id,
                uid,
                body.reason,
            )

        return {"success": True, "message": "Payment voided"}


@router.get("/by-payroll/{payroll_id}")
async def list_payments(request: Request, payroll_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        rows = await conn.fetch(
            "SELECT * FROM payroll_payments WHERE payroll_id = $1 AND tenant_id = $2 ORDER BY created_at",
            payroll_id,
            ctx["tenant_id"],
        )
        return {"success": True, "data": [dict(r) for r in rows]}
