"""Employee advances (KASBON) — grant / list / balances / void.

Foundation is V281 (employee_advances + append-only employee_advance_movements;
remaining balance DERIVED = SUM(movements); EMPLOYEE_ADVANCE role account).

Grant       : Dr EMPLOYEE_ADVANCE (Piutang Karyawan) / Cr chosen cash-bank.  movement +principal
Void (grant): reversal (Dr cash-bank / Cr EMPLOYEE_ADVANCE).                 movement -principal
Per-run deductions (Cr EMPLOYEE_ADVANCE on the payroll journal) are added in the
payroll-run integration increment; this router owns the grant lifecycle only.
"""
import logging
import uuid as uuid_module
from datetime import date
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..utils.tanggal_tenant import tanggal_dokumen
from ..services.role_resolver import (
    AccountRole,
    AccountRoleUnmappedError,
    resolve_account_id_by_role,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


class GrantAdvanceRequest(BaseModel):
    employee_id: UUID
    principal: float = Field(..., gt=0, description="Jumlah kasbon yang diberikan")
    granted_date: date
    source_account_id: UUID = Field(..., description="Akun kas/bank sumber (CoA id)")
    notes: Optional[str] = None


class VoidAdvanceRequest(BaseModel):
    reason: str = Field(..., min_length=1)


_UNMAPPED = {
    "code": "ACCOUNT_DEFAULT_UNMAPPED",
    "message": "Akun Piutang Karyawan (kasbon) belum diatur untuk usaha ini. Atur dulu di Pengaturan Akun.",
}


@router.post("", status_code=201)
async def grant_advance(request: Request, body: GrantAdvanceRequest):
    """Grant an employee advance (kasbon): Dr Piutang Karyawan / Cr chosen cash-bank."""
    ctx = get_user_context(request)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="User ID required")
    tenant_id = ctx["tenant_id"]
    principal = round(float(body.principal), 2)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"EMP_ADV_GRANT:{tenant_id}:{body.employee_id}",
            )
            try:
                adv_acct = await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.EMPLOYEE_ADVANCE
                )
            except AccountRoleUnmappedError:
                raise HTTPException(status_code=422, detail=_UNMAPPED)

            src = await conn.fetchrow(
                "SELECT id, name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                body.source_account_id,
                tenant_id,
            )
            if not src:
                raise HTTPException(status_code=404, detail="Akun sumber (kas/bank) tidak ditemukan")
            if src["id"] == adv_acct:
                raise HTTPException(status_code=400, detail="Akun sumber tidak boleh sama dengan akun Piutang Karyawan")

            emp = await conn.fetchrow(
                "SELECT id FROM employees WHERE id = $1 AND tenant_id = $2",
                body.employee_id,
                tenant_id,
            )
            if not emp:
                raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan")
            from ..services.pay_group_access import employee_in_scope
            if not await employee_in_scope(conn, tenant_id, ctx["user_id"], body.employee_id):
                raise HTTPException(status_code=404, detail="Karyawan tidak ditemukan")

            advance_id = uuid_module.uuid4()
            journal_id = uuid_module.uuid4()
            jnum = f"KASBON-{uuid_module.uuid4().hex[:8].upper()}"

            await conn.execute(
                """INSERT INTO journal_entries
                       (id, tenant_id, journal_number, journal_date, description,
                        source_type, source_id, status, total_debit, total_credit, created_by)
                   VALUES ($1,$2,$3,$4,$5,'EMPLOYEE_ADVANCE_GRANT',$6,'DRAFT',$7,$7,$8)""",
                journal_id, tenant_id, jnum, body.granted_date,
                f"Kasbon karyawan{(' - ' + body.notes) if body.notes else ''}",
                advance_id, principal, ctx["user_id"],
            )
            await conn.execute(
                "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,1,$3,$4,0,$5)",
                uuid_module.uuid4(), journal_id, adv_acct, principal, "Piutang Karyawan (kasbon)",
            )
            await conn.execute(
                "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,2,$3,0,$4,$5)",
                uuid_module.uuid4(), journal_id, src["id"], principal, "Pembayaran kasbon",
            )
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
            )

            await conn.execute(
                """INSERT INTO employee_advances
                       (id, tenant_id, employee_id, principal, granted_date, status,
                        grant_journal_id, source_account_id, notes, created_by)
                   VALUES ($1,$2,$3,$4,$5,'active',$6,$7,$8,$9)""",
                advance_id, tenant_id, body.employee_id, principal, body.granted_date,
                journal_id, src["id"], body.notes, ctx["user_id"],
            )
            await conn.execute(
                """INSERT INTO employee_advance_movements
                       (tenant_id, advance_id, employee_id, movement_type, amount, journal_id, created_by)
                   VALUES ($1,$2,$3,'grant',$4,$5,$6)""",
                tenant_id, advance_id, body.employee_id, principal, journal_id, ctx["user_id"],
            )

    return {
        "success": True,
        "advance_id": str(advance_id),
        "employee_id": str(body.employee_id),
        "principal": principal,
        "remaining_balance": principal,
        "journal_id": str(journal_id),
        "journal_number": jnum,
    }


@router.get("")
async def list_advances(
    request: Request,
    employee_id: Optional[UUID] = None,
    status: Optional[str] = Query(None, description="active|settled|void"),
):
    """List advances with DERIVED remaining balance per advance."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        where = ["a.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        if employee_id:
            params.append(employee_id)
            where.append(f"a.employee_id = ${len(params)}")
        if status:
            params.append(status)
            where.append(f"a.status = ${len(params)}")
        from ..services.pay_group_access import accessible_pay_group_filter
        _isall, _acc = await accessible_pay_group_filter(conn, ctx["tenant_id"], ctx["user_id"])
        if not _isall:
            if not _acc:
                return {"success": True, "data": []}
            params.append(_acc)
            where.append(f"a.employee_id IN (SELECT e.id FROM employees e WHERE e.tenant_id = $1 AND e.pay_group_id = ANY(${len(params)}::uuid[]))")
        rows = await conn.fetch(
            f"""SELECT a.id, a.employee_id, a.principal, a.granted_date, a.status,
                       a.notes, a.grant_journal_id,
                       employee_advance_balance(a.id) AS remaining_balance
                FROM employee_advances a
                WHERE {' AND '.join(where)}
                ORDER BY a.granted_date DESC, a.created_at DESC""",
            *params,
        )
    return {"success": True, "data": [dict(r) for r in rows]}


@router.get("/balances")
async def employee_balances(request: Request):
    """Per-employee outstanding kasbon (SISA) — for the payroll/kasbon list screen."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        from ..services.pay_group_access import accessible_pay_group_filter
        _isall, _acc = await accessible_pay_group_filter(conn, ctx["tenant_id"], ctx["user_id"])
        if not _isall and not _acc:
            return {"success": True, "data": []}
        _pgclause = "" if _isall else " AND m.employee_id IN (SELECT e.id FROM employees e WHERE e.tenant_id = $1 AND e.pay_group_id = ANY($2::uuid[]))"
        _pgparams = [ctx["tenant_id"]] if _isall else [ctx["tenant_id"], _acc]
        rows = await conn.fetch(
            f"""SELECT m.employee_id,
                      COALESCE(SUM(m.amount), 0)::numeric(18,2) AS remaining_balance
               FROM employee_advance_movements m
               WHERE m.tenant_id = $1{_pgclause}
               GROUP BY m.employee_id
               HAVING COALESCE(SUM(m.amount), 0) <> 0
               ORDER BY 2 DESC""",
            *_pgparams,
        )
    return {"success": True, "data": [dict(r) for r in rows]}


@router.post("/{advance_id}/void")
async def void_advance(request: Request, advance_id: UUID, body: VoidAdvanceRequest):
    """Void a grant (Law 2, by reversal). Only allowed while untouched (no deductions yet)."""
    ctx = get_user_context(request)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="User ID required")
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", f"EMP_ADV_VOID:{advance_id}"
            )
            adv = await conn.fetchrow(
                "SELECT id, employee_id, principal, status, grant_journal_id, source_account_id FROM employee_advances WHERE id = $1 AND tenant_id = $2",
                advance_id, tenant_id,
            )
            if not adv:
                raise HTTPException(status_code=404, detail="Kasbon tidak ditemukan")
            from ..services.pay_group_access import employee_in_scope
            if not await employee_in_scope(conn, tenant_id, ctx["user_id"], adv["employee_id"]):
                raise HTTPException(status_code=404, detail="Kasbon tidak ditemukan")
            if adv["status"] == "void":
                raise HTTPException(status_code=400, detail="Kasbon sudah dibatalkan")
            bal = float(await conn.fetchval("SELECT employee_advance_balance($1)", advance_id) or 0)
            if round(bal, 2) != round(float(adv["principal"]), 2):
                raise HTTPException(
                    status_code=400,
                    detail="Kasbon sudah dipotong sebagian dari gaji; tidak bisa dibatalkan. Selesaikan lewat penyesuaian, bukan pembatalan.",
                )
            try:
                adv_acct = await resolve_account_id_by_role(conn, tenant_id, AccountRole.EMPLOYEE_ADVANCE)
            except AccountRoleUnmappedError:
                raise HTTPException(status_code=422, detail=_UNMAPPED)

            principal = round(float(adv["principal"]), 2)
            journal_id = uuid_module.uuid4()
            jnum = f"KASBON-VOID-{uuid_module.uuid4().hex[:8].upper()}"
            hari_ini = await tanggal_dokumen(conn, tenant_id)  # t10-tanggal-bisnis
            await conn.execute(
                """INSERT INTO journal_entries
                       (id, tenant_id, journal_number, journal_date, description,
                        source_type, source_id, status, total_debit, total_credit,
                        reversal_of_id, created_by)
                   VALUES ($1,$2,$3,$9,$4,'EMPLOYEE_ADVANCE_GRANT_REVERSAL',$5,'DRAFT',$6,$6,$7,$8)""",
                journal_id, tenant_id, jnum, f"Pembatalan kasbon: {body.reason}",
                advance_id, principal, adv["grant_journal_id"], ctx["user_id"],
                hari_ini,  # t10-tanggal-bisnis
            )
            # reversal legs: Dr cash-bank / Cr Piutang Karyawan (mirror of the grant)
            await conn.execute(
                "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,1,$3,$4,0,$5)",
                uuid_module.uuid4(), journal_id, adv["source_account_id"], principal, "Pembatalan kasbon",
            )
            await conn.execute(
                "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,2,$3,0,$4,$5)",
                uuid_module.uuid4(), journal_id, adv_acct, principal, "Pembatalan Piutang Karyawan",
            )
            await conn.execute("UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id)
            await conn.execute(
                "UPDATE journal_entries SET reversed_by_id = $1, reversed_at = NOW() WHERE id = $2 AND reversed_by_id IS NULL",
                journal_id, adv["grant_journal_id"],
            )
            grant_mov = await conn.fetchval(
                "SELECT id FROM employee_advance_movements WHERE advance_id = $1 AND movement_type = 'grant' ORDER BY created_at LIMIT 1",
                advance_id,
            )
            await conn.execute(
                """INSERT INTO employee_advance_movements
                       (tenant_id, advance_id, employee_id, movement_type, amount, journal_id, reverses_movement_id, created_by)
                   VALUES ($1,$2,$3,'reversal',$4,$5,$6,$7)""",
                tenant_id, advance_id, adv["employee_id"], -principal, journal_id, grant_mov, ctx["user_id"],
            )
            await conn.execute(
                "UPDATE employee_advances SET status = 'void', voided_at = NOW(), voided_by = $1, void_reason = $2 WHERE id = $3",
                ctx["user_id"], body.reason, advance_id,
            )
    return {"success": True, "advance_id": str(advance_id), "status": "void", "reversal_journal_id": str(journal_id)}
