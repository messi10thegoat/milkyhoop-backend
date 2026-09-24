"""
Payroll Payments Router — Settlement (salary, PPh 21, BPJS)
"""

from fastapi import APIRouter, HTTPException, Request
from uuid import UUID
import logging
import asyncpg

from ..utils.tanggal_tenant import tanggal_dokumen
from ..schemas.payroll import CreatePayrollPaymentRequest, VoidPayrollRequest
from ..services.role_resolver import AccountRole, resolve_account_id_by_role

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


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

        # Calculate amount based on payment type. For salary, split the net by per-employee
        # method (TF/CASH, V284): employees.payment_method with a bank-presence default. The
        # two amounts are SNAPSHOTTED on the payment row -> a later method change never
        # retro-touches a posted payment.
        transfer_amount = None
        cash_amount = None
        cash_account_id = None
        if body.payment_type == "salary":
            method_rows = await conn.fetch(
                """WITH emp_net AS (
                       SELECT employee_id,
                              SUM(CASE WHEN component_type='earning' THEN amount ELSE 0 END)
                            - SUM(CASE WHEN component_type='deduction' THEN amount ELSE 0 END) AS net
                       FROM payroll_slip_lines WHERE payroll_id = $1 GROUP BY employee_id
                   )
                   SELECT COALESCE(e.payment_method,
                            CASE WHEN NULLIF(btrim(e.bank_account_number), '') IS NOT NULL
                                 THEN 'transfer' ELSE 'cash' END) AS method,
                          COALESCE(SUM(en.net), 0) AS total
                   FROM emp_net en JOIN employees e ON e.id = en.employee_id
                   GROUP BY 1""",
                body.payroll_id,
            )
            by = {r["method"]: float(r["total"]) for r in method_rows}
            transfer_amount = round(by.get("transfer", 0.0), 2)
            cash_amount = round(by.get("cash", 0.0), 2)
            amount = round(transfer_amount + cash_amount, 2)
            # Require an account only for a method the run actually uses.
            if transfer_amount > 0 and not body.bank_account_id:
                raise HTTPException(400, detail={"code": "TRANSFER_ACCOUNT_REQUIRED",
                    "message": "Rekening transfer wajib karena ada karyawan yang dibayar transfer."})
            if cash_amount > 0 and not body.cash_account_id:
                raise HTTPException(400, detail={"code": "CASH_ACCOUNT_REQUIRED",
                    "message": "Akun kas wajib karena ada karyawan yang dibayar tunai."})
            cash_account_id = body.cash_account_id if cash_amount > 0 else None
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
                bank_account_id, cash_account_id, transfer_amount, cash_amount,
                reference_number, notes, status, created_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12)
               RETURNING *""",
            ctx["tenant_id"],
            body.payroll_id,
            body.payment_type,
            body.payment_date,
            amount,
            body.bank_account_id,
            cash_account_id,
            transfer_amount,
            cash_amount,
            body.reference_number,
            body.notes,
            uid,
        )
        return {"success": True, "data": dict(row),
                "totals": {"transfer": transfer_amount, "cash": cash_amount}}


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

            ptype = payment["payment_type"]
            source_type = SOURCE_TYPE_MAP[ptype]
            amount = float(payment["amount"])

            async def _bank_coa(bank_account_id):
                r = await conn.fetchrow(
                    "SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                    bank_account_id,
                    ctx["tenant_id"],
                )
                if not r or not r["coa_id"]:
                    raise HTTPException(400, detail="Bank account has no linked CoA")
                return r["coa_id"]

            # Build journal lines: debit the payable(s), credit the settlement account(s).
            debit_accounts = []
            credit_accounts = []
            if ptype == "salary":
                coa = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.SALARY_PAYABLE
                )
                debit_accounts.append((coa, amount, "Bayar Gaji"))
                # TF/CASH (V284): credit the transfer bank + the cash account by the amounts
                # SNAPSHOTTED at create -- never recomputed here, so a later payment_method
                # change cannot retro-touch this posted payment.
                transfer_amt = float(payment["transfer_amount"] or 0)
                cash_amt = float(payment["cash_amount"] or 0)
                if transfer_amt <= 0 and cash_amt <= 0:
                    transfer_amt = amount  # legacy payment (pre-V284): whole amount via bank
                if transfer_amt > 0:
                    t_coa = await _bank_coa(payment["bank_account_id"])
                    credit_accounts.append((t_coa, transfer_amt, "Pembayaran gaji (transfer)"))
                if cash_amt > 0:
                    if not payment["cash_account_id"]:
                        raise HTTPException(400, detail="Akun kas wajib untuk karyawan dibayar tunai")
                    c_coa = await _bank_coa(payment["cash_account_id"])
                    credit_accounts.append((c_coa, cash_amt, "Pembayaran gaji (tunai)"))
            elif ptype == "pph21":
                # Fase D4.3: payroll-exclusive PPH21_PAYABLE -> 2-10310. The
                # legacy COA_HUTANG_PPH21 literal pointed to 2-10300 (generic
                # Hutang Pajak) which violated the PPh 21 PAYROLL BOUNDARY.
                coa = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.PPH21_PAYABLE
                )
                debit_accounts.append((coa, amount, "Setor PPh 21"))
            elif ptype == "bpjs":
                coa_ee = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.BPJS_EE_PAYABLE
                )
                coa_er = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.BPJS_ER_PAYABLE
                )
                # Split amount proportionally
                run = await conn.fetchrow(  # noqa: F841 - dead-code router; FK-existence check side-effect
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

            # Non-salary payments credit a single bank account (unchanged).
            if ptype != "salary":
                bank_coa = await _bank_coa(payment["bank_account_id"])
                credit_accounts.append((bank_coa, total_debit, f"Pembayaran {ptype}"))

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

            # Credit the settlement account(s): one bank for non-salary; transfer + cash legs
            # for a split salary payment.
            for coa_id, amt, memo in credit_accounts:
                await conn.execute(
                    """INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                       VALUES ($1, $2, $3, 0, $4, $5)""",
                    str(journal_id),
                    line_num,
                    coa_id,
                    amt,
                    memo,
                )
                line_num += 1

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


async def check_period_is_open(conn, tenant_id: str, transaction_date) -> None:
    """Law 5: periode akuntansi tanggal ini harus terbuka (salinan expenses.py).

    t10b-3b: tanpa ini, void di periode tertutup hanya tertangkap trigger DB
    prevent_closed_period_journal -> asyncpg RaiseError -> 500 bagi pengguna.
    """
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
                    hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
                    # Law 5 (t10b-3b): periode asal + periode jurnal pembalik, SEBELUM tulis apa pun
                    await check_period_is_open(conn, ctx["tenant_id"], orig["journal_date"])
                    await check_period_is_open(conn, ctx["tenant_id"], hari_ini)
                    rev_id = await conn.fetchval(
                        """INSERT INTO journal_entries (
                            tenant_id, journal_number, journal_date, description,
                            source_type, source_id, status, total_debit, total_credit,
                            reversal_of_id
                        ) VALUES ($1, $2, $9, $3, $4, $5, 'DRAFT', $6, $7, $8)
                        RETURNING id""",
                        ctx["tenant_id"],
                        f"REV-{orig['journal_number']}",
                        f"Reversal: {orig['description']}",
                        orig["source_type"],
                        str(payment_id),
                        float(orig["total_debit"]),
                        float(orig["total_credit"]),
                        orig["id"],
                        hari_ini,  # t10-tanggal-bisnis
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
