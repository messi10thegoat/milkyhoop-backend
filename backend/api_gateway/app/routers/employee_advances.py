"""Employee advances (KASBON) — grant / list / detail / history / summary / previews / void.

Foundation is V281 (employee_advances + append-only employee_advance_movements;
remaining balance DERIVED = SUM(movements); EMPLOYEE_ADVANCE role account).

Grant       : Dr EMPLOYEE_ADVANCE (Piutang Karyawan) / Cr chosen cash-bank.  movement +principal
              opening_balance=true: Cr EQUITY_OPENING_BALANCE (Modal Saldo Awal) -- kasbon yang
              diberikan SEBELUM pakai MilkyHoop; uang tak keluar dari rekening mana pun (30 Sep).
Void (grant): reversal (Dr cash-bank / Cr EMPLOYEE_ADVANCE).                 movement -principal
Cermin bank (BankSync Rule 1/3, FIX_R9_KASBON_MIRROR 30 Sep): akun sumber yang TERTAUT bank_accounts
(reverse lookup coa_id, seperti FIX_R9_RCV_MIRROR) -> grant membuat bank_transactions withdrawal dan void
membuat cermin pembaliknya, di transaksi yang sama. Dulu tak ada -> grapgrap BCA Pengeluaran gap -1.600.000.
Per-run deductions (Cr EMPLOYEE_ADVANCE on the payroll journal) live in payroll_runs.

Modul CW kasbon (V334, 30 Sep): nomor dokumen KSB-YYMM-NNNN (generate_employee_advance_number), SATU
definisi aturan per aksi dipakai rute tulis DAN pratinjau (_rencana_kasbon/_tulis_kasbon,
_rencana_batal_kasbon/_tulis_batal_kasbon): pratinjau menjalankan penulis yang SAMA di transaksi yang SELALU
di-ROLLBACK dan melaporkan SEMUA penghalang. Grant: X-Idempotency-Key (Law 14; kunci sama + isi beda -> 409),
periode tertutup -> 403 (Law 5). Semua rute ber-karyawan disaring pay-group (RULE pay-group).
"""
import logging
import uuid as uuid_module
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..utils.tanggal_tenant import tanggal_dokumen, zona_tenant
from ..services.bank_sync import create_bank_transaction_for_journal, create_reversal_bank_transaction
from ..services.role_resolver import (
    AccountRole,
    AccountRoleUnmappedError,
    resolve_account_id_by_role,
)
from ..utils.idempotency import ambil_replay_klien, hash_payload, kunci_idempotensi_klien, simpan_replay_klien

logger = logging.getLogger(__name__)
router = APIRouter()

_SEN = Decimal("0.01")


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
    # Law 25: Decimal di masuk/hitung/simpan (dulu float -> round(float))
    principal: Decimal = Field(..., gt=0, description="Jumlah kasbon yang diberikan")
    granted_date: date
    source_account_id: Optional[UUID] = Field(None, description="Akun kas/bank sumber (CoA id); wajib kecuali opening_balance")
    opening_balance: bool = Field(False, description="Kasbon saldo awal (sebelum pakai MilkyHoop): Cr Modal Saldo Awal, tanpa rekening")
    notes: Optional[str] = None


class VoidAdvanceRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class VoidAdvancePreviewRequest(BaseModel):
    reason: Optional[str] = None


_UNMAPPED = {
    "code": "ACCOUNT_DEFAULT_UNMAPPED",
    "message": "Akun Piutang Karyawan (kasbon) belum diatur untuk usaha ini. Atur dulu di Pengaturan Akun.",
}


def _blok(code: str, status: int, message: str, detail=None) -> dict:
    """Penghalang: `detail` = isi HTTPException PERSIS yang dinaikkan rute tulis (galat lama tak berubah)."""
    return {"code": code, "status": status, "message": message, "detail": detail if detail is not None else message}


def _publik(blocks: list) -> list:
    return [{"code": b["code"], "message": b["message"]} for b in blocks]


def _f(v):
    return float(v) if v is not None else None


async def _sisa_karyawan(conn, tenant_id: str, employee_id) -> Decimal:
    v = await conn.fetchval(
        "SELECT COALESCE(SUM(amount), 0) FROM employee_advance_movements WHERE tenant_id = $1 AND employee_id = $2",
        tenant_id, employee_id,
    )
    return Decimal(str(v or 0))


async def _saring_pay_group(conn, ctx, alias_emp: str, params: list) -> Optional[str]:
    """Klausa SQL penyaring pay-group (None = semua; "" = tak boleh apa-apa)."""
    from ..services.pay_group_access import accessible_pay_group_filter
    _isall, _acc = await accessible_pay_group_filter(conn, ctx["tenant_id"], ctx["user_id"])
    if _isall:
        return None
    if not _acc:
        return ""
    params.append(_acc)
    return (f"{alias_emp} IN (SELECT e.id FROM employees e WHERE e.tenant_id = $1 "
            f"AND e.pay_group_id = ANY(${len(params)}::uuid[]))")


# =============================================================================
# GRANT — satu definisi (rencana + penulis), dipakai POST "" dan POST /preview
# =============================================================================

async def _rencana_kasbon(conn, ctx, body: GrantAdvanceRequest) -> dict:
    """SEMUA penghalang grant (urutan = urutan galat rute lama). Nol tulisan."""
    tid = ctx["tenant_id"]
    blocks = []
    principal = body.principal.quantize(_SEN, rounding=ROUND_HALF_UP)
    if body.opening_balance and body.source_account_id:
        blocks.append(_blok("KASBON_OPENING_WITH_SOURCE", 400, "Kasbon saldo awal tidak memakai akun sumber kas/bank"))
    if not body.opening_balance and not body.source_account_id:
        blocks.append(_blok("KASBON_SOURCE_REQUIRED", 422, "Akun sumber (kas/bank) wajib diisi"))
    adv_acct = None
    try:
        adv_acct = await resolve_account_id_by_role(conn, tid, AccountRole.EMPLOYEE_ADVANCE)
    except AccountRoleUnmappedError:
        blocks.append(_blok("ACCOUNT_DEFAULT_UNMAPPED", 422, _UNMAPPED["message"], _UNMAPPED))
    src = None
    if body.opening_balance and not body.source_account_id:
        try:
            ekuitas = await resolve_account_id_by_role(conn, tid, AccountRole.EQUITY_OPENING_BALANCE)
            src = await conn.fetchrow(
                "SELECT id, name, account_code FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2", ekuitas, tid,
            )
        except AccountRoleUnmappedError:
            d = {"code": "ACCOUNT_DEFAULT_UNMAPPED",
                 "message": "Akun Modal Saldo Awal belum diatur untuk usaha ini. Atur dulu di Pengaturan Akun."}
            blocks.append(_blok("ACCOUNT_OPENING_UNMAPPED", 422, d["message"], d))
    elif body.source_account_id and not body.opening_balance:
        src = await conn.fetchrow(
            "SELECT id, name, account_code FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
            body.source_account_id, tid,
        )
        if not src:
            blocks.append(_blok("KASBON_SOURCE_NOT_FOUND", 404, "Akun sumber (kas/bank) tidak ditemukan"))
    if src and adv_acct and src["id"] == adv_acct:
        blocks.append(_blok("KASBON_SOURCE_IS_ADVANCE", 400, "Akun sumber tidak boleh sama dengan akun Piutang Karyawan"))
    emp = await conn.fetchrow("SELECT id, name FROM employees WHERE id = $1 AND tenant_id = $2", body.employee_id, tid)
    if emp:
        from ..services.pay_group_access import employee_in_scope
        if not await employee_in_scope(conn, tid, ctx["user_id"], body.employee_id):
            emp = None
    if not emp:
        blocks.append(_blok("KASBON_EMPLOYEE_NOT_FOUND", 404, "Karyawan tidak ditemukan"))
    try:
        await check_period_is_open(conn, tid, body.granted_date)  # Law 5 (dulu hanya trigger DB -> 500)
    except HTTPException as e:
        blocks.append(_blok("PERIOD_CLOSED", e.status_code, str(e.detail), e.detail))
    rek = None
    if src and not body.opening_balance:
        rek = await conn.fetchrow(
            "SELECT id, account_name FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2", src["id"], tid,
        )
    sisa = await _sisa_karyawan(conn, tid, body.employee_id) if emp else Decimal("0")
    return {"blocks": blocks, "principal": principal, "adv_acct": adv_acct, "src": src, "emp": emp, "rek": rek,
            "sisa_sebelum": sisa}


async def _tulis_kasbon(conn, ctx, body: GrantAdvanceRequest, r: dict) -> dict:
    """Penulis grant (di transaksi PEMANGGIL). Rencana WAJIB bersih."""
    tid = ctx["tenant_id"]
    principal, src, emp, adv_acct = r["principal"], r["src"], r["emp"], r["adv_acct"]
    advance_id = uuid_module.uuid4()
    journal_id = uuid_module.uuid4()
    jnum = f"KASBON-{uuid_module.uuid4().hex[:8].upper()}"
    nomor = await conn.fetchval("SELECT generate_employee_advance_number($1)", tid)

    await conn.execute(
        """INSERT INTO journal_entries
               (id, tenant_id, journal_number, journal_date, description,
                source_type, source_id, status, total_debit, total_credit, created_by)
           VALUES ($1,$2,$3,$4,$5,'EMPLOYEE_ADVANCE_GRANT',$6,'DRAFT',$7,$7,$8)""",
        journal_id, tid, jnum, body.granted_date,
        f"Kasbon karyawan{(' - ' + body.notes) if body.notes else ''}",
        advance_id, principal, ctx["user_id"],
    )
    await conn.execute(
        "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,1,$3,$4,0,$5)",
        uuid_module.uuid4(), journal_id, adv_acct, principal, "Piutang Karyawan (kasbon)",
    )
    await conn.execute(
        "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,2,$3,0,$4,$5)",
        uuid_module.uuid4(), journal_id, src["id"], principal,
        "Kasbon saldo awal" if body.opening_balance else "Pembayaran kasbon",
    )
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
    )
    # FIX_R9_KASBON_MIRROR: akun sumber tertaut rekening -> cermin withdrawal (atomik, jurnal sama).
    # Saldo awal (ekuitas) tak pernah tertaut rekening -> tanpa cermin, benar.
    rek = r["rek"]
    if rek:
        await create_bank_transaction_for_journal(
            conn, tenant_id=tid, bank_account_id=rek["id"], journal_id=journal_id,
            transaction_date=body.granted_date, transaction_type="withdrawal", amount=-principal,
            reference_type="employee_advance", reference_id=advance_id, created_by=ctx["user_id"],
            reference_number=nomor or jnum, description=f"Kasbon karyawan - {emp['name'] or ''}".rstrip(" -"),
            payee_payer=emp["name"],
        )

    await conn.execute(
        """INSERT INTO employee_advances
               (id, tenant_id, employee_id, principal, granted_date, status,
                grant_journal_id, source_account_id, notes, created_by, advance_number)
           VALUES ($1,$2,$3,$4,$5,'active',$6,$7,$8,$9,$10)""",
        advance_id, tid, body.employee_id, principal, body.granted_date,
        journal_id, src["id"], body.notes, ctx["user_id"], nomor,
    )
    await conn.execute(
        """INSERT INTO employee_advance_movements
               (tenant_id, advance_id, employee_id, movement_type, amount, journal_id, created_by)
           VALUES ($1,$2,$3,'grant',$4,$5,$6)""",
        tid, advance_id, body.employee_id, principal, journal_id, ctx["user_id"],
    )
    return {
        "success": True,
        "advance_id": str(advance_id),
        "advance_number": nomor,
        "employee_id": str(body.employee_id),
        "principal": float(principal),          # Law 25: Decimal di dalam, float di respons
        "remaining_balance": float(principal),
        "journal_id": str(journal_id),
        "journal_number": jnum,
    }


@router.post("", status_code=201)
async def grant_advance(request: Request, body: GrantAdvanceRequest):
    """Grant an employee advance (kasbon): Dr Piutang Karyawan / Cr chosen cash-bank (atau Modal Saldo Awal).
    Aturan = _rencana_kasbon (dipakai juga /preview); penghalang pertama -> galat lama."""
    ctx = get_user_context(request)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="User ID required")
    tenant_id = ctx["tenant_id"]
    try:
        kunci_klien = kunci_idempotensi_klien(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"EMP_ADV_GRANT:{tenant_id}:{body.employee_id}",
            )
            kunci_penuh = sidik = None
            if kunci_klien:
                # Law 14: kunci per pembukaan form; sama + isi sama = replay, sama + isi beda = 409
                kunci_penuh = f"EMP_ADV_GRANT:{ctx['user_id']}:{kunci_klien}"
                sidik = hash_payload(body.model_dump(mode="json"))
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"IDEM:{tenant_id}:{kunci_penuh}")
                try:
                    lama = await ambil_replay_klien(conn, tenant_id, kunci_penuh, sidik)
                except LookupError as e:
                    asli = getattr(e, "respons", None) or {}
                    raise HTTPException(status_code=409, detail={
                        "code": "IDEMPOTENCY_KEY_REUSED",
                        "message": "Kunci idempotensi sudah dipakai untuk kasbon lain dengan isi berbeda. Muat ulang formulir.",
                        "advance_id": asli.get("advance_id"), "advance_number": asli.get("advance_number"),
                    })
                if lama:
                    return {**lama, "was_cached": True}
            r = await _rencana_kasbon(conn, ctx, body)
            if r["blocks"]:
                b = r["blocks"][0]
                raise HTTPException(status_code=b["status"], detail=b["detail"])
            hasil = await _tulis_kasbon(conn, ctx, body, r)
            if kunci_penuh:
                await simpan_replay_klien(conn, tenant_id, kunci_penuh, "EMPLOYEE_ADVANCE_GRANT", sidik,
                                          hasil, UUID(hasil["advance_id"]))
    return hasil


@router.post("/preview")
async def preview_grant_advance(request: Request, body: GrantAdvanceRequest):
    """PRATINJAU kasbon: badan SAMA dengan POST "", NOL tulisan. Rencana + penulis yang SAMA di transaksi yang
    SELALU di-ROLLBACK; SEMUA penghalang; 200 walau tak bisa. `payload` = badan persis untuk POST ""."""
    ctx = get_user_context(request)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="User ID required")
    tid = ctx["tenant_id"]
    pool = await get_pool()
    hasil, baris, cermin = None, [], None
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            r = await _rencana_kasbon(conn, ctx, body)
            if not r["blocks"]:
                try:
                    async with conn.transaction():  # savepoint
                        hasil = await _tulis_kasbon(conn, ctx, body, r)
                        baris = [dict(x) for x in await conn.fetch(
                            """SELECT coa.account_code, coa.name AS account_name, jl.debit, jl.credit
                               FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
                               WHERE jl.journal_id = $1 ORDER BY jl.line_number""", UUID(hasil["journal_id"]))]
                        c = await conn.fetchrow(
                            """SELECT bt.bank_account_id, ba.account_name, bt.amount, bt.transaction_type
                               FROM bank_transactions bt JOIN bank_accounts ba ON ba.id = bt.bank_account_id
                               WHERE bt.journal_id = $1 AND bt.tenant_id = $2""", UUID(hasil["journal_id"]), tid)
                        cermin = dict(c) if c else None
                except HTTPException as e:
                    r["blocks"].append(_blok("KASBON_REJECTED", e.status_code, str(e.detail), e.detail))
                    hasil = None
        finally:
            await tr.rollback()  # SELALU: pratinjau tak pernah menulis
    emp, src, rek = r["emp"], r["src"], r["rek"]
    sisa = r["sisa_sebelum"]
    ok = not r["blocks"]
    return {"success": True, "data": {
        "can_grant": ok,
        "blocks": _publik(r["blocks"]),
        "employee": ({"id": str(emp["id"]), "name": emp["name"], "remaining_before": _f(sisa),
                      "remaining_after": _f(sisa + r["principal"]) if ok else _f(sisa)} if emp else None),
        "source": ({"account_id": str(src["id"]), "account_code": src["account_code"], "name": src["name"],
                    "bank_account_id": str(rek["id"]) if rek else None, "bank_account_name": rek["account_name"] if rek else None,
                    "is_opening_balance": body.opening_balance} if src else None),
        "principal": _f(r["principal"]),
        "granted_date": body.granted_date.isoformat(),
        "number_preview": hasil["advance_number"] if hasil else None,
        "journal_lines": [{"account_code": x["account_code"], "account_name": x["account_name"],
                           "debit": _f(x["debit"]), "credit": _f(x["credit"])} for x in baris],
        "bank_mirror": ({"bank_account_id": str(cermin["bank_account_id"]), "bank_account_name": cermin["account_name"],
                         "amount": _f(cermin["amount"]), "transaction_type": cermin["transaction_type"]} if cermin else None),
        "payload": (body.model_dump(mode="json") if ok else None),
    }}


# =============================================================================
# LIST / SUMMARY / BALANCES (rute statis SEBELUM /{advance_id})
# =============================================================================

@router.get("")
async def list_advances(
    request: Request,
    employee_id: Optional[UUID] = None,
    status: Optional[str] = Query(None, description="active|settled|void"),
    search: Optional[str] = Query(None, description="nomor kasbon atau nama karyawan"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List advances with DERIVED remaining balance per advance (+ nomor, nama karyawan, sumber)."""
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
        if search and search.strip():
            params.append(f"%{search.strip()}%")
            where.append(f"(a.advance_number ILIKE ${len(params)} OR e.name ILIKE ${len(params)})")
        pg = await _saring_pay_group(conn, ctx, "a.employee_id", params)
        if pg == "":
            return {"success": True, "data": [], "total": 0, "limit": limit, "offset": offset}
        if pg:
            where.append(pg)
        w = " AND ".join(where)
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM employee_advances a LEFT JOIN employees e ON e.id = a.employee_id AND e.tenant_id = a.tenant_id WHERE {w}",
            *params,
        )
        params_hal = params + [limit, offset]
        rows = await conn.fetch(
            f"""SELECT a.id, a.advance_number, a.employee_id, e.name AS employee_name, a.principal, a.granted_date,
                       a.status, a.notes, a.grant_journal_id, a.source_account_id, coa.name AS source_account_name,
                       (a.source_account_id = (SELECT account_id FROM account_roles
                                               WHERE tenant_id = a.tenant_id AND role_key = 'EQUITY_OPENING_BALANCE'))
                           AS is_opening_balance,
                       employee_advance_balance(a.id) AS remaining_balance
                FROM employee_advances a
                LEFT JOIN employees e ON e.id = a.employee_id AND e.tenant_id = a.tenant_id
                LEFT JOIN chart_of_accounts coa ON coa.id = a.source_account_id AND coa.tenant_id = a.tenant_id
                WHERE {w}
                ORDER BY a.granted_date DESC, a.created_at DESC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}""",
            *params_hal,
        )
    return {"success": True, "data": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/summary")
async def advances_summary(request: Request, period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Ringkasan kasbon (TURUNAN movements, disaring pay-group). period = YYYY-MM (bawaan: bulan bisnis ini)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not period:
            period = (await tanggal_dokumen(conn, ctx["tenant_id"])).strftime("%Y-%m")
        th, bl = int(period[:4]), int(period[5:7])
        awal = date(th, bl, 1)
        akhir = date(th + (bl == 12), bl % 12 + 1, 1)
        params = [ctx["tenant_id"], awal, akhir]
        pg = await _saring_pay_group(conn, ctx, "m.employee_id", params)
        if pg == "":
            return {"success": True, "data": {"period": period, "total_active_remaining": 0.0, "employees_with_kasbon": 0,
                                              "granted_this_period": 0.0, "granted_count": 0, "deducted_this_period": 0.0}}
        f_m = f" AND {pg}" if pg else ""
        f_a = f" AND {pg.replace('m.employee_id', 'a.employee_id')}" if pg else ""
        per_emp = await conn.fetch(
            f"""SELECT m.employee_id, SUM(m.amount) AS sisa FROM employee_advance_movements m
                WHERE m.tenant_id = $1{f_m} GROUP BY m.employee_id""", *params[:1], *params[3:])
        diberi = await conn.fetchrow(
            f"""SELECT COALESCE(SUM(a.principal), 0) AS jml, COUNT(*) AS n FROM employee_advances a
                WHERE a.tenant_id = $1 AND a.status <> 'void' AND a.granted_date >= $2 AND a.granted_date < $3{f_a}""",
            *params)
        dipotong = await conn.fetchval(
            f"""SELECT COALESCE(-SUM(m.amount), 0) FROM employee_advance_movements m
                JOIN payroll_runs pr ON pr.id = m.payroll_id AND pr.tenant_id = m.tenant_id
                WHERE m.tenant_id = $1 AND m.payroll_id IS NOT NULL
                  AND pr.period_start >= $2 AND pr.period_start < $3{f_m}""", *params)
    total = sum((Decimal(str(x["sisa"] or 0)) for x in per_emp), Decimal("0"))
    return {"success": True, "data": {
        "period": period,
        "total_active_remaining": _f(total),
        "employees_with_kasbon": sum(1 for x in per_emp if (x["sisa"] or 0) > 0),
        "granted_this_period": _f(diberi["jml"]),
        "granted_count": diberi["n"],
        "deducted_this_period": _f(dipotong),
    }}


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


# =============================================================================
# DETAIL / HISTORY
# =============================================================================

async def _muat_kasbon(conn, ctx, advance_id) -> dict:
    """Baris kasbon + pagar pay-group. Tak ada / di luar cakupan -> 404 (sama)."""
    adv = await conn.fetchrow(
        """SELECT a.*, e.name AS employee_name FROM employee_advances a
           LEFT JOIN employees e ON e.id = a.employee_id AND e.tenant_id = a.tenant_id
           WHERE a.id = $1 AND a.tenant_id = $2""",
        advance_id, ctx["tenant_id"],
    )
    if not adv:
        raise HTTPException(status_code=404, detail="Kasbon tidak ditemukan")
    from ..services.pay_group_access import employee_in_scope
    if not await employee_in_scope(conn, ctx["tenant_id"], ctx["user_id"], adv["employee_id"]):
        raise HTTPException(status_code=404, detail="Kasbon tidak ditemukan")
    return adv


async def _nama_pengguna(conn, ids) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    return {u["id"]: u["nama"] for u in await conn.fetch(
        """SELECT id, COALESCE(NULLIF(fullname, ''), NULLIF(name, ''), email) AS nama FROM "User" WHERE id = ANY($1::text[])""",
        ids)}


async def _gerakan(conn, ctx, advance_id) -> list:
    return await conn.fetch(
        """SELECT m.id, m.movement_type, m.amount, m.payroll_id, m.journal_id, m.created_at, m.created_by,
                  pr.payroll_number, pr.period_start, je.journal_number, je.journal_date
           FROM employee_advance_movements m
           LEFT JOIN payroll_runs pr ON pr.id = m.payroll_id AND pr.tenant_id = m.tenant_id
           LEFT JOIN journal_entries je ON je.id = m.journal_id AND je.tenant_id = m.tenant_id
           WHERE m.advance_id = $1 AND m.tenant_id = $2
           ORDER BY m.created_at, m.id""",
        advance_id, ctx["tenant_id"],
    )


@router.get("/{advance_id}")
async def get_advance(request: Request, advance_id: UUID):
    """Detail kasbon: sisa = TURUNAN movements (employee_advance_balance); sumber + rekening tertaut."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        adv = await _muat_kasbon(conn, ctx, advance_id)
        src = await conn.fetchrow(
            "SELECT id, name, account_code FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
            adv["source_account_id"], ctx["tenant_id"],
        ) if adv["source_account_id"] else None
        rek = await conn.fetchrow(
            "SELECT id, account_name FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2",
            adv["source_account_id"], ctx["tenant_id"],
        ) if adv["source_account_id"] else None
        ekuitas = await conn.fetchval(
            "SELECT account_id FROM account_roles WHERE tenant_id = $1 AND role_key = 'EQUITY_OPENING_BALANCE'",
            ctx["tenant_id"],
        )
        gerak = await _gerakan(conn, ctx, advance_id)
        sisa = Decimal(str(await conn.fetchval("SELECT employee_advance_balance($1)", advance_id) or 0))
        gj = await conn.fetchval("SELECT journal_number FROM journal_entries WHERE id = $1 AND tenant_id = $2",
                                 adv["grant_journal_id"], ctx["tenant_id"]) if adv["grant_journal_id"] else None
        nama = await _nama_pengguna(conn, [adv["created_by"], adv["voided_by"]])
    dipotong = -sum((Decimal(str(m["amount"])) for m in gerak if m["payroll_id"]), Decimal("0"))
    return {"success": True, "data": {
        "id": str(adv["id"]),
        "advance_number": adv["advance_number"],
        "employee": {"id": str(adv["employee_id"]), "name": adv["employee_name"]},
        "principal": _f(adv["principal"]),
        "deducted_total": _f(dipotong),
        "remaining": _f(sisa),
        "status": adv["status"],
        "granted_date": adv["granted_date"].isoformat(),
        "notes": adv["notes"],
        "grant_journal_number": gj,
        "source": ({"account_id": str(src["id"]), "account_code": src["account_code"], "name": src["name"],
                    "bank_account_id": str(rek["id"]) if rek else None,
                    "bank_account_name": rek["account_name"] if rek else None,
                    "is_opening_balance": bool(ekuitas and src["id"] == ekuitas)} if src else None),
        "movements": [{"type": m["movement_type"], "amount": _f(m["amount"]),
                       "date": (m["journal_date"] or m["created_at"].date()).isoformat(),
                       "payroll_id": str(m["payroll_id"]) if m["payroll_id"] else None,
                       "payroll_number": m["payroll_number"], "journal_number": m["journal_number"]} for m in gerak],
        "created_by": ({"id": str(adv["created_by"]), "name": nama.get(str(adv["created_by"]))} if adv["created_by"] else None),
        "void": ({"at": adv["voided_at"].isoformat(),
                  "by": {"id": str(adv["voided_by"]), "name": nama.get(str(adv["voided_by"]))} if adv["voided_by"] else None,
                  "reason": adv["void_reason"]} if adv["voided_at"] else None),
    }}


def _rp(x) -> str:
    return "Rp" + f"{float(x):,.2f}".replace(",", "#").replace(".", ",").replace("#", ".").removesuffix(",00")


@router.get("/{advance_id}/history")
async def get_advance_history(request: Request, advance_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """Riwayat kasbon, bentuk SAMA dengan GET /sales-orders/{id}/history: {events[{at (zona tenant), jenis,
    ringkas, aktor{id,nama}|null, dokumen{tipe,id,nomor}|null, sumber}], total}, terbaru dulu.
    Turunan kolom kasbon + movements (potongan/pembalik gaji); pembalik grant = kejadian DIBATALKAN."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        adv = await _muat_kasbon(conn, ctx, advance_id)
        gerak = await _gerakan(conn, ctx, advance_id)
        src = await conn.fetchval("SELECT name FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                                  adv["source_account_id"], ctx["tenant_id"]) if adv["source_account_id"] else None
        zona = await zona_tenant(conn, ctx["tenant_id"])
        ev = []
        nomor = adv["advance_number"] or "kasbon"
        ev.append((adv["created_at"], "KASBON_DIBERIKAN",
                   f"Kasbon {nomor} {_rp(adv['principal'])} diberikan ke {adv['employee_name'] or 'karyawan'}"
                   + (f" dari {src}" if src else ""), adv["created_by"], None))
        for m in gerak:
            if not m["payroll_id"]:
                continue  # grant / pembalik grant: sudah diwakili DIBERIKAN / DIBATALKAN
            dok = {"tipe": "payroll", "id": str(m["payroll_id"]), "nomor": m["payroll_number"]}
            if m["movement_type"] == "deduction":
                ev.append((m["created_at"], "KASBON_DIPOTONG_GAJI",
                           f"Dipotong dari gaji {m['payroll_number'] or ''} {_rp(-m['amount'])}".replace("  ", " "),
                           m["created_by"], dok))
            else:
                ev.append((m["created_at"], "POTONGAN_GAJI_DIBATALKAN",
                           f"Potongan gaji {m['payroll_number'] or ''} dibatalkan (+{_rp(m['amount'])})".replace("  ", " "),
                           m["created_by"], dok))
        if adv["voided_at"]:
            ev.append((adv["voided_at"], "KASBON_DIBATALKAN",
                       f"Kasbon {nomor} dibatalkan" + (f": {adv['void_reason']}" if adv["void_reason"] else ""),
                       adv["voided_by"], None))
        nama = await _nama_pengguna(conn, [e[3] for e in ev])
    ev = [e for _, e in sorted(enumerate(ev), key=lambda x: (x[1][0], x[0]), reverse=True)]
    return {"success": True, "data": {
        "advance_id": str(adv["id"]),
        "advance_number": adv["advance_number"],
        "events": [{"at": e[0].astimezone(zona).isoformat(), "jenis": e[1], "ringkas": e[2],
                    "aktor": ({"id": str(e[3]), "nama": nama.get(str(e[3]))} if e[3] else None),
                    "dokumen": e[4], "sumber": "dokumen"} for e in ev[:limit]],
        "total": len(ev),
    }}


# =============================================================================
# VOID — satu definisi (rencana + penulis), dipakai POST /{id}/void dan /{id}/void/preview
# =============================================================================

async def _rencana_batal_kasbon(conn, ctx, advance_id, reason: Optional[str]) -> dict:
    """SEMUA penghalang void (urutan = galat rute lama). 404 dinaikkan langsung. Nol tulisan."""
    tid = ctx["tenant_id"]
    adv = await conn.fetchrow(
        "SELECT id, employee_id, principal, status, grant_journal_id, source_account_id, granted_date, advance_number FROM employee_advances WHERE id = $1 AND tenant_id = $2",
        advance_id, tid,
    )
    if not adv:
        raise HTTPException(status_code=404, detail="Kasbon tidak ditemukan")
    from ..services.pay_group_access import employee_in_scope
    if not await employee_in_scope(conn, tid, ctx["user_id"], adv["employee_id"]):
        raise HTTPException(status_code=404, detail="Kasbon tidak ditemukan")
    blocks = []
    if not (reason or "").strip():
        blocks.append(_blok("KASBON_REASON_REQUIRED", 422, "Alasan pembatalan wajib diisi"))
    if adv["status"] == "void":
        blocks.append(_blok("KASBON_ALREADY_VOID", 400, "Kasbon sudah dibatalkan"))
    bal = Decimal(str(await conn.fetchval("SELECT employee_advance_balance($1)", advance_id) or 0))
    principal = Decimal(str(adv["principal"])).quantize(_SEN, rounding=ROUND_HALF_UP)
    if adv["status"] != "void" and bal.quantize(_SEN) != principal:
        blocks.append(_blok("KASBON_ALREADY_DEDUCTED", 400,
                            "Kasbon sudah dipotong sebagian dari gaji; tidak bisa dibatalkan. Selesaikan lewat penyesuaian, bukan pembatalan."))
    adv_acct = None
    try:
        adv_acct = await resolve_account_id_by_role(conn, tid, AccountRole.EMPLOYEE_ADVANCE)
    except AccountRoleUnmappedError:
        blocks.append(_blok("ACCOUNT_DEFAULT_UNMAPPED", 422, _UNMAPPED["message"], _UNMAPPED))
    hari_ini = await tanggal_dokumen(conn, tid)  # t10-tanggal-bisnis
    # Law 5 (t10b-3b): periode asal + periode jurnal pembalik, SEBELUM tulis apa pun
    try:
        await check_period_is_open(conn, tid, adv["granted_date"])  # periode asal
    except HTTPException as e:
        blocks.append(_blok("PERIOD_CLOSED", e.status_code, str(e.detail), e.detail))
    try:
        await check_period_is_open(conn, tid, hari_ini)  # periode jurnal pembalik
    except HTTPException as e:
        if not any(b["code"] == "PERIOD_CLOSED" and b["detail"] == e.detail for b in blocks):
            blocks.append(_blok("PERIOD_CLOSED", e.status_code, str(e.detail), e.detail))
    orig_bt = await conn.fetchrow(
        """SELECT bt.id, bt.bank_account_id, bt.amount, ba.account_name FROM bank_transactions bt
           LEFT JOIN bank_accounts ba ON ba.id = bt.bank_account_id
           WHERE bt.journal_id = $1 AND bt.tenant_id = $2 ORDER BY bt.created_at ASC LIMIT 1""",
        adv["grant_journal_id"], tid,
    ) if adv["grant_journal_id"] else None
    return {"blocks": blocks, "adv": adv, "bal": bal, "principal": principal, "adv_acct": adv_acct,
            "hari_ini": hari_ini, "orig_bt": orig_bt}


async def _tulis_batal_kasbon(conn, ctx, advance_id, reason: str, r: dict) -> dict:
    tid = ctx["tenant_id"]
    adv, principal, adv_acct, hari_ini = r["adv"], r["principal"], r["adv_acct"], r["hari_ini"]
    journal_id = uuid_module.uuid4()
    jnum = f"KASBON-VOID-{uuid_module.uuid4().hex[:8].upper()}"
    await conn.execute(
        """INSERT INTO journal_entries
               (id, tenant_id, journal_number, journal_date, description,
                source_type, source_id, status, total_debit, total_credit,
                reversal_of_id, created_by)
           VALUES ($1,$2,$3,$9,$4,'EMPLOYEE_ADVANCE_GRANT_REVERSAL',$5,'DRAFT',$6,$6,$7,$8)""",
        journal_id, tid, jnum, f"Pembatalan kasbon: {reason}",
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
    # FIX_R9_KASBON_MIRROR (BankSync Rule 3): grant bercermin -> pembalik WAJIB bercermin (cari lewat
    # journal_id, seperti void penerimaan). Grant lama tanpa cermin -> tak ada yang dibalik, benar.
    orig_bt = r["orig_bt"]
    if orig_bt:
        await create_reversal_bank_transaction(
            conn, tenant_id=tid, original_bank_transaction_id=orig_bt["id"],
            reversal_journal_id=journal_id, created_by=ctx["user_id"], description_prefix="[VOID]",
        )
    grant_mov = await conn.fetchval(
        "SELECT id FROM employee_advance_movements WHERE advance_id = $1 AND movement_type = 'grant' ORDER BY created_at LIMIT 1",
        advance_id,
    )
    await conn.execute(
        """INSERT INTO employee_advance_movements
               (tenant_id, advance_id, employee_id, movement_type, amount, journal_id, reverses_movement_id, created_by)
           VALUES ($1,$2,$3,'reversal',$4,$5,$6,$7)""",
        tid, advance_id, adv["employee_id"], -principal, journal_id, grant_mov, ctx["user_id"],
    )
    await conn.execute(
        "UPDATE employee_advances SET status = 'void', voided_at = NOW(), voided_by = $1, void_reason = $2 WHERE id = $3",
        ctx["user_id"], reason, advance_id,
    )
    return {"success": True, "advance_id": str(advance_id), "advance_number": adv["advance_number"], "status": "void",
            "reversal_journal_id": str(journal_id), "reversal_journal_number": jnum}


@router.post("/{advance_id}/void")
async def void_advance(request: Request, advance_id: UUID, body: VoidAdvanceRequest):
    """Void a grant (Law 2, by reversal). Only allowed while untouched (no deductions yet).
    Aturan = _rencana_batal_kasbon (dipakai juga /void/preview); penghalang pertama -> galat lama."""
    ctx = get_user_context(request)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="User ID required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", f"EMP_ADV_VOID:{advance_id}"
            )
            r = await _rencana_batal_kasbon(conn, ctx, advance_id, body.reason)
            if r["blocks"]:
                b = r["blocks"][0]
                raise HTTPException(status_code=b["status"], detail=b["detail"])
            return await _tulis_batal_kasbon(conn, ctx, advance_id, body.reason, r)


@router.post("/{advance_id}/void/preview")
async def preview_void_advance(request: Request, advance_id: UUID, body: VoidAdvancePreviewRequest = None):
    """PRATINJAU batal kasbon: aturan + penulis yang SAMA, transaksi SELALU di-ROLLBACK, SEMUA penghalang."""
    ctx = get_user_context(request)
    if not ctx["user_id"]:
        raise HTTPException(status_code=401, detail="User ID required")
    alasan = ((body.reason if body else None) or "").strip() or None
    pool = await get_pool()
    baris = []
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            r = await _rencana_batal_kasbon(conn, ctx, advance_id, alasan)
            if not r["blocks"]:
                try:
                    async with conn.transaction():  # savepoint
                        h = await _tulis_batal_kasbon(conn, ctx, advance_id, alasan, r)
                        baris = [dict(x) for x in await conn.fetch(
                            """SELECT coa.account_code, coa.name AS account_name, jl.debit, jl.credit
                               FROM journal_lines jl JOIN chart_of_accounts coa ON coa.id = jl.account_id
                               WHERE jl.journal_id = $1 ORDER BY jl.line_number""", UUID(h["reversal_journal_id"]))]
                except HTTPException as e:
                    r["blocks"].append(_blok("KASBON_REJECTED", e.status_code, str(e.detail), e.detail))
        finally:
            await tr.rollback()  # SELALU: pratinjau tak pernah menulis
    ok = not r["blocks"]
    bt = r["orig_bt"]
    return {"success": True, "data": {
        "advance_number": r["adv"]["advance_number"],
        "status": r["adv"]["status"],
        "can_void": ok,
        "blocks": _publik(r["blocks"]),
        "remaining_before": _f(r["bal"]),
        "remaining_after": _f(r["bal"] - r["principal"]) if ok else _f(r["bal"]),
        "reversal_date": r["hari_ini"].isoformat(),
        "journal_lines": [{"account_code": x["account_code"], "account_name": x["account_name"],
                           "debit": _f(x["debit"]), "credit": _f(x["credit"])} for x in baris],
        "bank_reversal": ({"bank_account_id": str(bt["bank_account_id"]), "bank_account_name": bt["account_name"],
                           "amount": _f(-bt["amount"])} if bt else None),
    }}
