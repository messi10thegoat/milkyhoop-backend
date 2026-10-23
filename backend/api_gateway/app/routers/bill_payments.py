"""
Bill Payments Router - Pembayaran Keluar (Payment Out)

Endpoints for managing vendor payments for purchase invoices (bills).
"""

from fastapi import APIRouter, Body, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
from ..services import teks_galat as tg
from ..services.jaga_rekonsiliasi import tolak_void_bila_terekonsiliasi
from ..services.rp_periode import batas_periode, argumen_kueri
from ..services.pihak_helpers import segarkan_cache_hutang_tagihan
from ..utils.tanggal_tenant import tanggal_dokumen
from ..services.pihak_helpers import normalisasi_pihak, pastikan_pihak_sama  # noqa: F401
from ..services import bayar_keluar_rencana as BKR
import uuid as uuid_module
import logging
import asyncpg
from datetime import date
from decimal import Decimal

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
from ..services.resolve_account import (  # noqa: F401
    resolve_account_id,
    resolve_account_id_or_none,
)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
    resolve_account_id_by_role_if_pkp,  # noqa: F401
)
from ..utils.idempotency import (  # Law 14 (kunci HANYA dari klien sejak 22 Okt 2026)
    execute_idempotent,
    kunci_idempotensi_klien,
)
from ..services.role_precondition import assert_required_roles_for_path

# Fase D2-wrap B: PURCHASE_DISCOUNT + AP_PREPAID now resolved at runtime via
# role mapping (V156). Legacy hardcoded literals removed.
# See /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md.

# Fase D2.3 + D2-wrap B: required role mappings for bill_payments posting path.
BILL_PAYMENTS_REQUIRED_ROLES = [
    AccountRole.AP_TRADE,
    AccountRole.CASH_GENERAL,
    AccountRole.WHT_PPH_PAYABLE,
    AccountRole.AP_PREPAID,
    AccountRole.PURCHASE_DISCOUNT,
]

# Per-tenant precondition cache.
# FIX_ROLE_PRECOND_PER_TENANT (2026-06-16): scope the audit to the ACTING
# tenant so one misconfigured tenant cannot fail-loud-block posting for all.
_bill_payments_precondition_checked_tenants: set = set()


async def _ensure_bill_payments_role_preconditions(pool, tenant_id=None):
    """Run role-mapping precondition for the ACTING tenant on bill_payments.

    Fails loud (PreconditionFailedError) if THIS tenant lacks any required
    role mapping. With tenant_id=None falls back to the legacy all-tenant
    audit. After first successful check the per-tenant audit is skipped.
    """
    if tenant_id is None:
        await assert_required_roles_for_path(
            pool, "bill_payments", BILL_PAYMENTS_REQUIRED_ROLES
        )
        return
    if tenant_id in _bill_payments_precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "bill_payments", BILL_PAYMENTS_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _bill_payments_precondition_checked_tenants.add(tenant_id)


logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")
    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


def validate_uuid(value: str, field_name: str = "id") -> UUID:
    """Validate UUID format and return UUID object."""
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400, detail=f"{field_name} harus berupa UUID yang valid"
        )


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
            detail=tg.periode_tertutup(period['period_name'], period['status']),
        )


async def get_bill_remaining_from_journal(conn, tenant_id: str, bill_id) -> int:
    """Sisa hutang tagihan dari JURNAL = compute_ap_rincian (V440/V441), SATU sumber dgn daftar/detail Faktur Pembelian + nota debit.
    U3a (22 Okt 2026): dulu SQL sendiri tanpa pembagian proporsional V245 dan tanpa menyaring jurnal BILL yang DIBALIK -> kaos 8 tagihan
    ber-jurnal-dibalik masih "bersisa" 525.000/1.120.000 dan bisa dibayar. 0 = tagihan tidak aktif."""
    from ..services.tagihan_daftar import sisa_tagihan_jurnal
    v = await sisa_tagihan_jurnal(conn, tenant_id, bill_id if isinstance(bill_id, UUID) else UUID(str(bill_id)))
    return int(v) if v is not None else 0


async def generate_payment_number(conn, tenant_id: str) -> str:
    from datetime import datetime

    year_month = (await tanggal_dokumen(conn, tenant_id)).strftime("%Y-%m")  # t10-tanggal-bisnis
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
    pph_tax_code_id=None,
    pph_amount: Decimal = Decimal("0"),
) -> tuple:
    """
    Create journal entry for bill payment.

    Journal Entry (when payment is posted):
    - Dr. Accounts Payable (2-10100) = allocated_amount (reduces what we owe)
    - Dr. Biaya Bank (expense) = bank_fee_amount (expense for bank fees)
    - Cr. Bank/Cash Account = total_amount + bank_fee_amount (money out)
    - Cr. Potongan Pembelian (5-10200) = discount_amount (discount received from vendor)
    - Cr. Utang Pajak (2-10300) = pph_amount (PPh withheld by buyer, if any)

    Returns: (journal_id, journal_number)
    """
    # Baris = BKR.baris_jurnal_bayar (PAY-PREVIEW U1 23 Okt 2026): SATU sumber akun/nominal/memo dengan POST /bill-payments/preview.
    baris, total_debit = await BKR.baris_jurnal_bayar(
        conn, ctx["tenant_id"], payment_number=payment_number, vendor_name=vendor_name, bank_account_id=bank_account_id,
        total_amount=total_amount, allocated_amount=allocated_amount, discount_amount=discount_amount,
        discount_account_id=discount_account_id, bank_fee_amount=bank_fee_amount, bank_fee_account_id=bank_fee_account_id,
        source_type=source_type, source_deposit_id=source_deposit_id, unapplied_amount=unapplied_amount,
        pph_tax_code_id=pph_tax_code_id, pph_amount=pph_amount,
    )

    # Generate journal number using database function
    journal_number = await conn.fetchval(
        "SELECT get_next_journal_number($1, 'BP')", ctx["tenant_id"]
    )
    if not journal_number:
        journal_number = f"JRN-{payment_number}"

    journal_id = uuid_module.uuid4()
    trace_id = uuid_module.uuid4()

    # Create journal entry header
    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            status, total_debit, total_credit, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'BILL_PAYMENT', $6, $7, 'DRAFT', $8, $8, $9)
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

    for line_number, (_akun, _d, _k, _memo) in enumerate(baris, 1):
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            uuid_module.uuid4(),
            journal_id,
            line_number,
            _akun,
            _d,
            _k,
            _memo,
        )

    # Law 4 GUARD: baris HARUS seimbang dan HARUS sama dengan header, sebelum POSTED.
    # CHECK(total_debit=total_credit) hanya menguji header — header bisa konsisten
    # sendiri di atas baris yang timpang (persis bug biaya bank 2.500). Guard ini
    # menangkap SEMUA jalur `if ... and account_id` yang diam-diam membuang baris.
    sums = await conn.fetchrow(
        "SELECT COALESCE(SUM(debit),0) AS d, COALESCE(SUM(credit),0) AS c "
        "FROM journal_lines WHERE journal_id = $1",
        journal_id,
    )
    _ld, _lc = Decimal(str(sums["d"])), Decimal(str(sums["c"]))
    _hdr = Decimal(str(total_debit))
    if _ld != _lc or _ld != _hdr:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Law 4: jurnal pembayaran tidak seimbang — "
                f"baris D={_ld} K={_lc}, header={_hdr}. Pembayaran dibatalkan."
            ),
        )

    # Law 20: DRAFT->POSTED after all journal lines inserted
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
        journal_id,
    )
    logger.info(f"Bill payment journal created: {journal_id}, number: {journal_number}")

    return journal_id, journal_number


# === Journal Preview (read-only, for DirectAction confirmation card) ===


@router.post("/preview-journal")
async def preview_journal(request: Request, body: dict = Body(...)):
    """
    Dry-run journal preview for bill payment. Returns debit/credit lines WITHOUT posting.
    Used by DirectAction confirmation card to show accounting impact.
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        total_amount = float(body.get("total_amount", 0) or 0)
        bank_account_id = body.get("bank_account_id", "")

        # Resolve bank account CoA (Law 27)
        bank_name = "Bank/Kas"
        bank_code = ""
        if bank_account_id:
            try:
                from uuid import UUID

                ba_row = await conn.fetchrow(
                    """SELECT ba.coa_id, ca.name, ca.account_code
                       FROM bank_accounts ba
                       JOIN chart_of_accounts ca ON ca.id = ba.coa_id AND ca.tenant_id = ba.tenant_id
                       WHERE ba.id = $1::uuid AND ba.tenant_id = $2""",
                    UUID(bank_account_id),
                    ctx["tenant_id"],
                )
                if ba_row:
                    bank_name = ba_row["name"]
                    bank_code = ba_row["account_code"]
            except Exception:
                pass

        # Resolve AP account (Law 27)
        ap_name = "Hutang Usaha"
        # Fase D2.3: resolve AP via role then look up by id.
        try:
            ap_acct_id = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.AP_TRADE
            )
            ap_row = await conn.fetchrow(
                "SELECT name, account_code FROM chart_of_accounts WHERE id = $1 AND is_active = true",
                ap_acct_id,
            )
            ap_code = ap_row["account_code"] if ap_row else "AP_TRADE"
            ap_row = ap_row  # alias kept for downstream branches
            if ap_row:
                ap_name = ap_row["name"]
                ap_code = ap_row["account_code"]
        except Exception:
            pass

        lines = [
            {
                "account_name": ap_name,
                "account_code": ap_code,
                "debit": total_amount,
                "credit": 0,
            },
            {
                "account_name": bank_name,
                "account_code": bank_code,
                "debit": 0,
                "credit": total_amount,
            },
        ]

        return {"journal_lines": lines}


# === PRATINJAU BUAT (PAY-PREVIEW U1, 23 Okt 2026; pola NetSuite/QBO "tinjau sebelum bayar", kembar /vendor-deposits/preview) ===
# READ ONLY tanpa kunci; penentu = services/bayar_keluar_rencana.rencana_bayar (SAMA dengan POST /bill-payments, yang memanggilnya
# lagi di bawah kunci). Jurnal = RENCANA tanpa nomor (journal_number null; nomor pembayaran & jurnal lahir saat simpan -- pratinjau
# tak pernah menyentuh sequence). Bentuk: {action, can_proceed, blocks[], before, after, journals[], bank_transactions[], bills[],
# payload, preview:true}.


@router.post("/preview")
async def preview_create_bill_payment(request: Request, payload: CreateBillPaymentRequest):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            r = await BKR.rencana_bayar(conn, ctx, payload, pratinjau=True)
        finally:
            await tr.rollback()  # pratinjau tak pernah menulis
    return {"success": True, "data": {
        "payment_id": None, "action": "create", "can_proceed": not r["blocks"],
        "blocks": [{"code": b["code"], "message": b["message"]} for b in r["blocks"]],
        "before": r["before"], "after": r["after"], "journals": r["journals"], "bank_transactions": r["bank_transactions"],
        "bills": r["bills"], "payload": payload.model_dump(mode="json"), "preview": True}}


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
    # Kontrak seragam (22 Okt 2026, kembar receive_payments): pill aksi (nilai asing = 422) + total atas SEMUA hasil filter (opt-in)
    aksi: Optional[Literal["belum_dialokasikan"]] = Query(None),
    with_totals: bool = Query(False),
):
    """
    List bill payments -- WRAPPER bill_payments_v2 (PEMBELIAN-SERAGAM (2), 22 Okt 2026); dialokasikan/belum = turunan alokasi.
    Sisa hutang per tagihan tetap dari jurnal (compute_ap_outstanding / compute_ap_rincian).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Build dynamic filters for the outer query (applied on joined result)
            outer_conditions = []
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                outer_conditions.append(f"q.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if vendor_id:
                outer_conditions.append(f"q.vendor_id = ${param_idx}::uuid")
                params.append(vendor_id)
                param_idx += 1

            if payment_method:
                outer_conditions.append(f"q.payment_method = ${param_idx}")
                params.append(payment_method)
                param_idx += 1

            from ..services import pembayaran_keluar_daftar as _pk
            _c, _p = _pk.syarat_cari(search, "q", param_idx)  # SATU predikat dgn /summary?search=
            if _c:
                outer_conditions.append(_c)
                params.extend(_p)
                param_idx += len(_p)

            if aksi == "belum_dialokasikan":  # == aksi baris (alokasi_fakta, toleransi sama)
                outer_conditions.append(f"q.belum > {_pk.TOLERANSI}")

            if date_from:
                outer_conditions.append(f"q.payment_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                outer_conditions.append(f"q.payment_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            outer_where = (
                (" AND " + " AND ".join(outer_conditions)) if outer_conditions else ""
            )

            # Sort column mapping (on outer query aliases)
            sort_col = {
                "payment_date": "q.payment_date",
                "payment_number": "q.payment_number",
                "vendor_name": "q.vendor_name",
                "total_amount": "q.total_amount",
                "created_at": "q.created_at",
            }.get(sort_by, "q.created_at")
            order = "ASC" if sort_order == "asc" else "DESC"

            limit_idx = param_idx
            skip_idx = param_idx + 1

            # PEMBELIAN-SERAGAM (2), 22 Okt 2026: daftar berbasis WRAPPER bill_payments_v2 (dulu: jurnal ber-debit AP + LEFT JOIN wrapper ->
            # jurnal MANUAL/VENDOR_OPENING_BALANCE tampil sebagai pembayaran hantu ber-detail 404; voided/draf tak pernah tampil). Dialokasikan/
            # belum = TURUNAN alokasi (services/pembayaran_keluar_daftar), bukan cache wrapper.
            base_cte = f"""
                WITH joined AS (
                    SELECT
                        bp.id::text AS id,
                        bp.payment_number,
                        bp.vendor_id,
                        COALESCE(bp.vendor_name, '') AS vendor_name,
                        bp.payment_date,
                        COALESCE(bp.payment_method, 'bank_transfer') AS payment_method,
                        bp.total_amount,
                        bp.allocated_amount,
                        bp.unapplied_amount,
                        {_pk.sql_bruto('bp')} AS bruto,
                        {_pk.sql_dialokasi('bp')} AS dialokasi,
                        {_pk.sql_belum('bp')} AS belum,
                        bp.reference_number,
                        bp.notes,
                        COALESCE(bp.status, 'draft') AS status,
                        bp.created_at,
                        (SELECT COUNT(*) FROM bill_payment_allocations bpa WHERE bpa.payment_id = bp.id) AS bill_count
                    FROM bill_payments_v2 bp
                    WHERE bp.tenant_id = $1
                )
            """

            # Count with filters applied
            count_query = f"""
                {base_cte}
                SELECT COUNT(*) FROM joined q WHERE 1=1{outer_where}
            """
            total = await conn.fetchval(count_query, *params)
            jumlah = {}
            if with_totals is True:  # WHERE + parameter SAMA dgn COUNT; sum_total tanpa voided (uang tak keluar), sum_sisa = Σ belum_dialokasikan
                _t = await conn.fetchrow(count_query.replace(
                    "SELECT COUNT(*) FROM joined q",
                    "SELECT COALESCE(SUM(q.total_amount) FILTER (WHERE q.status <> 'voided'), 0) AS st, "
                    f"COALESCE(SUM(q.belum) FILTER (WHERE q.belum > {_pk.TOLERANSI}), 0) AS ss FROM joined q", 1), *params)
                jumlah = {"sum_total": float(_t["st"]), "sum_sisa": float(_t["ss"])}

            # List with filters, sort, pagination
            list_query = f"""
                {base_cte}
                SELECT q.* FROM joined q WHERE 1=1{outer_where}
                ORDER BY {sort_col} {order}, q.id {order}
                LIMIT ${limit_idx} OFFSET ${skip_idx}
            """
            params.append(limit)
            params.append(skip)
            rows = await conn.fetch(list_query, *params)
            _kel = await _pk.kelompok_untuk(conn, ctx["tenant_id"], [UUID(r["id"]) for r in rows])

            items = []
            for r in rows:
                _f = _pk.dengan_turunan(_pk.alokasi_fakta(r["status"], r["bruto"], r["dialokasi"]))
                _dok = _pk.documents_baris(_kel.get(UUID(r["id"]), []))
                items.append(BillPaymentListItem(
                    id=str(r["id"]),
                    payment_number=r["payment_number"] or "",
                    vendor_id=str(r["vendor_id"]) if r["vendor_id"] else None,
                    vendor_name=r["vendor_name"] or "",
                    payment_date=r["payment_date"].isoformat()
                    if r["payment_date"]
                    else "",
                    payment_method=r["payment_method"] or "bank_transfer",
                    total_amount=r["total_amount"] or 0,
                    allocated_amount=_f.get("allocated_amount", r["allocated_amount"] or 0),
                    unapplied_amount=_f.get("unapplied_amount", r["unapplied_amount"] or 0),
                    status=r["status"] or "draft",
                    bill_count=r["bill_count"] or 0,
                    created_at=r["created_at"].isoformat() if r["created_at"] else "",
                    dialokasikan=_f["dialokasikan"], belum_dialokasikan=_f["belum_dialokasikan"], aksi=_f["aksi"],
                    alokasi_keadaan=_f["alokasi_keadaan"], alokasi_teks=_f["alokasi_teks"],
                    position_text=_pk.posisi_bayar(r["status"], _f), documents=_dok, doc_count=len(_dok),
                ))

            return BillPaymentListResponse(
                items=items, total=total or 0, has_more=(skip + limit) < (total or 0), **jumlah
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing bill payments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list bill payments")


@router.get("/summary", response_model=BillPaymentSummaryResponse)
async def get_bill_payments_summary(request: Request, search: Optional[str] = Query(None)):
    """
    Summary -- WRAPPER bill_payments_v2, sumber SAMA dgn daftar (PEMBELIAN-SERAGAM (2), 22 Okt 2026).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Sapuan tanggal bisnis A (26 Sep 2026): hari ini = tanggal_dokumen (zona tenant), bukan CURRENT_DATE/date.today() UTC.
            # Periode = services/rp_periode.batas_periode (SAMA dengan Penerimaan): hari ini, Senin–Minggu, tgl 1–akhir bulan.
            _b = batas_periode(await tanggal_dokumen(conn, ctx["tenant_id"]))
            # Kontrak seragam (22 Okt 2026): ?search= = predikat SAMA dgn daftar (syarat_cari), membatasi SEMUA hitungan/uang
            from ..services import pembayaran_keluar_daftar as _pk
            _c7, _p7 = _pk.syarat_cari(search, "bp", 7)
            _c2, _p2 = _pk.syarat_cari(search, "bp", 2)
            # PEMBELIAN-SERAGAM (2), 22 Okt 2026: SUMBER SAMA dgn daftar = wrapper bill_payments_v2 (dulu jurnal ber-debit AP -> jurnal
            # MANUAL/saldo awal ikut terhitung; draf tak pernah terhitung). Voided dikecualikan seperti dulu.
            summary = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COALESCE(SUM(total_amount), 0) AS total_paid,
                    COALESCE(SUM(CASE WHEN status = 'draft' THEN total_amount ELSE 0 END), 0) AS draft_amount,
                    COUNT(CASE WHEN status = 'draft' THEN 1 END) AS draft_count,
                    COALESCE(SUM(CASE WHEN status = 'posted' THEN total_amount ELSE 0 END), 0) AS posted_amount,
                    COUNT(CASE WHEN status = 'posted' THEN 1 END) AS posted_count,
                    COALESCE(SUM(CASE WHEN payment_date = $2::date THEN total_amount ELSE 0 END), 0) AS today_amount,
                    COALESCE(SUM(CASE WHEN payment_date BETWEEN $3::date AND $4::date THEN total_amount ELSE 0 END), 0) AS week_amount,
                    COALESCE(SUM(CASE WHEN payment_date BETWEEN $5::date AND $6::date THEN total_amount ELSE 0 END), 0) AS month_amount
                FROM bill_payments_v2 bp
                WHERE tenant_id = $1 AND COALESCE(status, 'draft') != 'voided'
                """ + (f" AND {_c7}" if _c7 else ""),
                *argumen_kueri(ctx["tenant_id"], _b), *_p7,
            )

            method_breakdown = await conn.fetch(
                """
                SELECT COALESCE(payment_method, 'bank_transfer') AS payment_method, COUNT(*) AS count,
                       COALESCE(SUM(total_amount), 0) AS amount
                FROM bill_payments_v2 bp
                WHERE tenant_id = $1 AND COALESCE(status, 'draft') != 'voided'
                """ + (f" AND {_c2}" if _c2 else "") + """
                GROUP BY COALESCE(payment_method, 'bank_transfer')
                """,
                ctx["tenant_id"], *_p2,
            )
            # pill AKSI: ungkapan SAMA dgn ?aksi=belum_dialokasikan daftar (sql_belum, toleransi sama)
            belum = await conn.fetchrow(
                f"SELECT COUNT(*) FILTER (WHERE b > {_pk.TOLERANSI}) AS n, COALESCE(SUM(b) FILTER (WHERE b > {_pk.TOLERANSI}), 0) AS v FROM "
                f"(SELECT {_pk.sql_belum('bp')} AS b FROM bill_payments_v2 bp WHERE bp.tenant_id = $1 AND bp.status = 'posted'"
                + (f" AND {_c2}" if _c2 else "") + ") x", ctx["tenant_id"], *_p2)

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
                    "belum_dialokasikan_count": int(belum["n"] or 0),
                    "belum_dialokasikan_value": float(belum["v"] or 0),
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
        # Validate UUID format (returns 400 for invalid format)
        validate_uuid(payment_id, "payment_id")
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
                # --- Law 29 fallback: try as journal_entries.id ---
                journal_row = await conn.fetchrow(
                    """
                    SELECT je.id, je.journal_number, je.journal_date, je.description,
                           je.source_type, je.source_id, je.status,
                           je.created_at, je.created_by
                    FROM journal_entries je
                    WHERE je.id = $1::uuid AND je.tenant_id = $2
                      AND je.source_type IN ('BILL_PAYMENT', 'PAYMENT_BILL')
                    """,
                    payment_id,
                    ctx["tenant_id"],
                )

                if not journal_row:
                    raise HTTPException(status_code=404, detail="Payment not found")

                # Try reverse lookup via source_id
                if journal_row["source_id"]:
                    payment = await conn.fetchrow(
                        """
                        SELECT bp.*, ba.account_name as bank_account_name_lookup
                        FROM bill_payments_v2 bp
                        LEFT JOIN bank_accounts ba ON ba.id = bp.bank_account_id
                        WHERE bp.id = $1::uuid AND bp.tenant_id = $2
                        """,
                        journal_row["source_id"],
                        ctx["tenant_id"],
                    )

                if not payment:
                    # Journal-only: construct response from journal data
                    settlement_amount = await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(jl.debit), 0)
                        FROM journal_lines jl
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id
                        WHERE jl.journal_id = $1
                          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
                        """,
                        journal_row["id"],
                    )

                    # Parse vendor name from description
                    desc = journal_row["description"] or ""
                    vendor_name = "Settlement"
                    for prefix in [
                        "Pembayaran ke ",
                        "Payment to ",
                        "Bill payment to ",
                        "Pembayaran untuk ",
                    ]:
                        if desc.startswith(prefix):
                            vendor_name = desc[len(prefix) :]
                            break

                    je_status = (journal_row["status"] or "POSTED").upper()
                    mapped_status = (
                        "posted"
                        if je_status == "POSTED"
                        else "draft"
                        if je_status == "DRAFT"
                        else "voided"
                    )

                    return {
                        "success": True,
                        "data": {
                            "id": str(journal_row["id"]),
                            "payment_number": journal_row["journal_number"],
                            "vendor_id": "",
                            "vendor_name": vendor_name,
                            "payment_date": journal_row["journal_date"].isoformat()
                            if journal_row["journal_date"]
                            else None,
                            "payment_method": "bank_transfer",
                            "source_type": "cash",
                            "bank_account_id": "",
                            "bank_account_name": "",
                            "total_amount": str(settlement_amount),
                            "allocated_amount": str(settlement_amount),
                            "unapplied_amount": 0,
                            "discount_amount": 0,
                            "discount_account_id": "",
                            "status": mapped_status,
                            "reference_number": "",
                            "notes": desc or None,
                            "journal_id": str(journal_row["id"]),
                            "journal_number": journal_row["journal_number"],
                            "void_journal_id": "",
                            "allocations": [],
                            "posted_at": journal_row["created_at"].isoformat()
                            if journal_row["created_at"]
                            else None,
                            "posted_by": str(journal_row["created_by"])
                            if journal_row["created_by"]
                            else None,
                            "voided_at": None,
                            "voided_by": None,
                            "void_reason": None,
                            "created_at": journal_row["created_at"].isoformat()
                            if journal_row["created_at"]
                            else None,
                            "updated_at": journal_row["created_at"].isoformat()
                            if journal_row["created_at"]
                            else None,
                            "created_by": str(journal_row["created_by"])
                            if journal_row["created_by"]
                            else None,
                            "is_journal_only": True,
                            "currency_code": "IDR",
                            "exchange_rate": 1,
                            "amount_in_base_currency": str(settlement_amount),
                            "bank_fee_amount": 0,
                            "bank_fee_account_id": "",
                            "check_number": "",
                            "check_due_date": "",
                            "check_bank_name": "",
                            "attachments": [],
                            "tags": [],
                        },
                    }

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

            # PEMBELIAN-SERAGAM (2): dialokasikan/belum = TURUNAN alokasi (fungsi SAMA dgn daftar), bukan cache wrapper
            from ..services import pembayaran_keluar_daftar as _pk
            _f = _pk.dengan_turunan(_pk.alokasi_fakta(
                payment["status"], (payment["total_amount"] or 0) + (payment.get("discount_amount") or 0),
                sum((r["amount_applied"] or 0) for r in allocations)))
            # kontrak seragam: documents/position_text == baris daftar (fungsi SAMA)
            _dok = _pk.documents_baris((await _pk.kelompok_untuk(conn, ctx["tenant_id"], [payment["id"]])).get(payment["id"], []))

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
                allocated_amount=_f.get("allocated_amount", payment["allocated_amount"] or 0),
                unapplied_amount=_f.get("unapplied_amount", payment["unapplied_amount"] or 0),
                dialokasikan=_f["dialokasikan"], belum_dialokasikan=_f["belum_dialokasikan"], aksi=_f["aksi"],
                alokasi_keadaan=_f["alokasi_keadaan"], alokasi_teks=_f["alokasi_teks"],
                position_text=_pk.posisi_bayar(payment["status"], _f), documents=_dok, doc_count=len(_dok),
                discount_amount=payment.get("discount_amount") or 0,
                discount_account_id=str(payment["discount_account_id"])
                if payment.get("discount_account_id")
                else None,
                bank_fee_amount=payment.get("bank_fee_amount") or 0,
                bank_fee_account_id=str(payment["bank_fee_account_id"])
                if payment.get("bank_fee_account_id")
                else None,
                currency_code=payment.get("currency_code") or "IDR",
                exchange_rate=str(payment.get("exchange_rate") or 1.0),
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
                pph_tax_code_id=str(payment["pph_tax_code_id"])
                if payment.get("pph_tax_code_id")
                else None,
                pph_amount=int(payment["pph_amount"] or 0),
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

        await _ensure_bill_payments_role_preconditions(pool, ctx["tenant_id"])
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # ── Law 14: kunci idempotency = HANYA header klien (putusan pemilik LANGSUNG 22 Okt 2026, sejajar SO/SI) ──
                # Dulu kunci SERVER-DEFAULT (vendor+tanggal+rekening+nominal+alokasi, TTL 24 jam): dua pembayaran SAH identik
                # (mis. dua cicilan sama di hari yang sama) -> yang kedua 200 tanpa pembayaran baru (tertelan diam). Kini: tanpa
                # header = proses biasa (dokumen sah boleh kembar); perlindungan kirim-ulang = kunci klien (FE per buka form, agen
                # chat = pending_action_id).
                try:
                    idem_key = kunci_idempotensi_klien(request)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))

                # ── DUA lock, sengaja. LOCK cegah RACE; IDEMPOTENCY cegah DUPLIKAT. ──
                # (1) DOMAIN lock per-vendor — LAPIS KEDUA, bukan penjaga utama.
                #     ⚠️ KOREKSI 2026-08-06 (uji-merah): anti-over-application pada
                #     jalur ini SEBENARNYA dijaga oleh row-lock
                #     `SELECT ... FROM bills WHERE id=$1 FOR UPDATE` (lihat bawah)
                #     + remaining yang dihitung journal-derived sesudahnya.
                #     Terbukti: dengan domain-lock DILUMPUHKAN, dua request konkuren
                #     tetap menghasilkan tepat SATU sukses (400/201).
                #     Jadi JANGAN mewarisi klaim "tanpa lock ini terjadi
                #     over-application" — itu tidak benar untuk jalur ini.
                #     Dipertahankan karena nol biaya dan menjadi pertahanan berlapis
                #     bila FOR UPDATE hilang saat refactor.
                # (2) IDEM lock per-kunci: men-serialkan request IDENTIK supaya
                #     keduanya tak sama-sama MISS SELECT di execute_idempotent.
                #     INI yang benar-benar diperlukan oleh mekanisme idempotency.
                # URUTAN TETAP: domain dulu, baru idem — SAMA seperti
                # receive_payments, supaya tak ada deadlock silang.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BILL_PAYMENT_CREATE:{ctx['tenant_id']}:{payload.vendor_id}",
                )
                if idem_key:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"IDEM:{ctx['tenant_id']}:{idem_key}",
                    )

                # Law 14: bungkus seluruh operasi -> request kedua me-REPLAY hasil,
                # bukan menerima error atas sesuatu yang sudah berhasil.
                async def _op():
                    # PENENTU SATU dengan POST /bill-payments/preview (services/bayar_keluar_rencana, PAY-PREVIEW U1 23 Okt 2026):
                    # periode · UUID opsional · vendor · rekening · tagihan milik tenant & vendor sama (PIHAK + TENANT 13 Sep) ·
                    # alokasi ≤ total + potongan · per alokasi FOR UPDATE + sisa jurnal (Law 16). URUTAN + PESAN == kode lama;
                    # blok pertama dilempar sebagai HTTPException yang sama. Nomor pembayaran lahir SESUDAH semua cek lolos.
                    _r = await BKR.rencana_bayar(conn, ctx, payload)
                    vendor_name, bank_account_name = _r["vendor_name"], _r["bank_account_name"]
                    allocated_amount, unapplied_amount = _r["allocated_amount"], _r["unapplied_amount"]
                    payment_number = await generate_payment_number(conn, ctx["tenant_id"])

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
                            created_by, created_at, updated_at, posted_at, posted_by, idempotency_key,
                            pph_tax_code_id, pph_amount
                        ) VALUES (
                            $1, $2, $3, $4::uuid, $5, $6, $7, $8::uuid, $9, $10, $11, $12, $13, $14::uuid,
                            $15, $16::uuid, $17, $18, $19, $20, $21, $22, $23, $24::uuid, $25, $26, $27, $28,
                            $29, NOW(), NOW(),
                            CASE WHEN $28::varchar = 'posted' THEN NOW() ELSE NULL END,
                            CASE WHEN $28::varchar = 'posted' THEN $29::uuid ELSE NULL END, $30,
                            $31::uuid, $32
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
                        int(
                            Decimal(str(payload.total_amount))
                            * Decimal(str(payload.exchange_rate))
                        ),
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
                        UUID(payload.pph_tax_code_id) if payload.pph_tax_code_id else None,
                        Decimal(str(payload.pph_amount or 0)),
                    )

                    for _al in _r["alokasi"]:  # tagihan sudah FOR UPDATE + sisa dicek di rencana_bayar
                        await conn.execute(
                            """
                            INSERT INTO bill_payment_allocations (id, payment_id, bill_id, remaining_before, amount_applied, remaining_after, created_at)
                            VALUES (gen_random_uuid(), $1, $2::uuid, $3, $4, $5, NOW())""",
                            payment_id,
                            _al["bill_id"],
                            _al["remaining_before"],
                            _al["amount_applied"],
                            _al["remaining_after"],
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
                            pph_tax_code_id=payload.pph_tax_code_id,
                            pph_amount=payload.pph_amount or 0,
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
                        # Cache tagihan = SATU turunan (pihak_helpers.segarkan_cache_hutang_tagihan, dari compute_ap_outstanding)
                        # untuk SEMUA penulis — tak ada lagi aritmetika amount_paid/status sendiri (26 Sep 2026, 4b).
                        for alloc in payload.allocations:
                            await segarkan_cache_hutang_tagihan(conn, ctx["tenant_id"], UUID(str(alloc.bill_id)))

                        # === PPh RIDER: withholding_tax_records (Fase 2.3) ===
                        if (payload.pph_amount or 0) > 0 and payload.pph_tax_code_id:
                            pph_tc = await conn.fetchrow(
                                "SELECT code, rate, tax_type FROM tax_codes WHERE id = $1",
                                UUID(payload.pph_tax_code_id),
                            )
                            vendor_npwp = None  # vendors table has no npwp column yet
                            pph_rate = (
                                Decimal(str(pph_tc["rate"])) if pph_tc else Decimal("2")
                            )
                            pph_dpp = (
                                (
                                    Decimal(str(payload.pph_amount)) / pph_rate * 100
                                ).quantize(Decimal("1"))
                                if pph_rate > 0
                                else Decimal("0")
                            )
                            tax_period = (
                                payload.payment_date.strftime("%Y%m")
                                if hasattr(payload.payment_date, "strftime")
                                else str(payload.payment_date)[:7].replace("-", "")
                            )

                            await conn.execute(
                                """
                                INSERT INTO withholding_tax_records (
                                    id, tenant_id, direction, tax_code_id,
                                    document_type, document_id, payment_id, journal_id,
                                    vendor_id, npwp, tax_period,
                                    base_amount, tax_amount, status
                                ) VALUES (
                                    $1, $2, 'cut', $3,
                                    'BILL_PAYMENT', $4, $5, $6,
                                    $7, $8, $9,
                                    $10, $11, 'recorded'
                                )
                                """,
                                uuid_module.uuid4(),
                                ctx["tenant_id"],
                                UUID(payload.pph_tax_code_id),
                                payment_id,
                                payment_id,
                                journal_id,
                                UUID(payload.vendor_id) if payload.vendor_id else None,
                                vendor_npwp,
                                tax_period,
                                pph_dpp,
                                payload.pph_amount,
                            )

                        # === ARTIFACT 4: bank_transaction (BankSync Rule 1) ===
                        if payload.bank_account_id:
                            _cb = await BKR.cermin_bank(conn, ctx["tenant_id"], payload.bank_account_id, payload.total_amount,
                                                        payload.pph_amount or 0)  # SATU sumber dgn pratinjau
                            if _cb:
                                bt_amount, bt_type = _cb["amount"], _cb["transaction_type"]

                                await conn.execute(
                                    """
                                    INSERT INTO bank_transactions (
                                        id, tenant_id, bank_account_id, transaction_date,
                                        transaction_type, amount, running_balance,
                                        reference_type, reference_id, reference_number,
                                        description, payee_payer, journal_id,
                                        status, origin_type, source_module,
                                        created_by, posted_by, posted_at
                                    ) VALUES (
                                        $1, $2, $3, $4, $5, $6, 0,
                                        'bill_payment', $7::uuid, $8,
                                        $9, $10, $11,
                                        'POSTED', 'MANUAL', 'bill_payment',
                                        $12, $12, NOW()
                                    )
                                    """,
                                    uuid_module.uuid4(),
                                    ctx["tenant_id"],
                                    _cb["bank_account_id"],
                                    payload.payment_date,
                                    bt_type,
                                    bt_amount,
                                    payment_id,
                                    payment_number,
                                    f"Payment to {vendor_name}",
                                    vendor_name,
                                    journal_id,
                                    ctx["user_id"],
                                )

                    # dict, BUKAN BillPaymentResponse: execute_idempotent
                    # menyimpan hasil sebagai JSON untuk di-replay.
                    return {
                        "success": True,
                        "message": (
                            f"Payment {payment_number} "
                            f"{'created as draft' if payload.save_as_draft else 'posted'}"
                        ),
                        "data": {
                            "id": str(payment_id),
                            "payment_number": payment_number,
                            "status": status,
                        },
                    }

                if not idem_key:  # tanpa kunci klien = tanpa dedup (putusan pemilik 22 Okt)
                    return {**(await _op()), "was_cached": False}
                _res = await execute_idempotent(
                    conn, ctx["tenant_id"], idem_key, "BILL_PAYMENT", _op, kunci_default=False,
                )
                return {**_res.data, "was_cached": _res.was_cached}
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

        await _ensure_bill_payments_role_preconditions(pool, ctx["tenant_id"])
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 13: Advisory lock on bill payment posting
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BILL_PAYMENT:{payment_id}",
                )

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
                    # Law 13: Lock bill row before update to prevent concurrent modification
                    # (tenant_id: jangan pernah mengunci baris tenant lain)
                    await conn.fetchrow(
                        "SELECT id FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                        alloc["bill_id"],
                        ctx["tenant_id"],
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
                    pph_tax_code_id=str(full_payment["pph_tax_code_id"])
                    if full_payment.get("pph_tax_code_id")
                    else None,
                    pph_amount=full_payment.get("pph_amount") or 0,
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
                # Cache tagihan = SATU turunan (pihak_helpers.segarkan_cache_hutang_tagihan, dari compute_ap_outstanding)
                # untuk SEMUA penulis — tak ada lagi aritmetika amount_paid/status sendiri (26 Sep 2026, 4b).
                for alloc in allocations:
                    await segarkan_cache_hutang_tagihan(conn, ctx["tenant_id"], alloc["bill_id"])

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
    """
    Void a posted bill payment.

    Creates reversing journal entry by iterating ALL original journal lines.
    Restores bill balances.
    Creates mirror bank transaction for bank-paid payments.

    IRON LAW 2: Immutability - reversal journal, not edit
    IRON LAW 5: Period validation
    IRON LAW 13: Advisory lock BILL_PAYMENT_VOID:{payment_id}
    IRON LAW 20: DRAFT -> lines -> UPDATE POSTED
    IRON LAW 26: Reversal linking (reversal_of_id + reversed_by_id)
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        await _ensure_bill_payments_role_preconditions(pool, ctx["tenant_id"])
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 13: Advisory lock on bill payment void
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BILL_PAYMENT_VOID:{payment_id}",
                )
                await tolak_void_bila_terekonsiliasi(conn, ctx["tenant_id"], payment_id)  # rekon bank: 409 sebelum tulisan

                # Get payment with FOR UPDATE to prevent TOCTOU
                payment = await conn.fetchrow(
                    """
                    SELECT bp.*, ba.coa_id as bank_coa_id
                    FROM bill_payments_v2 bp
                    LEFT JOIN bank_accounts ba ON ba.id = bp.bank_account_id
                    WHERE bp.id = $1::uuid AND bp.tenant_id = $2
                    FOR UPDATE OF bp
                    """,
                    payment_id,
                    ctx["tenant_id"],
                )

                if not payment:
                    raise HTTPException(status_code=404, detail="Payment not found")

                # C4: Idempotency guard AFTER lock
                if payment["status"] == "voided":
                    return BillPaymentResponse(
                        success=True,
                        message="Payment already voided",
                        data={"id": payment_id, "status": "voided"},
                    )

                if payment["status"] == "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void draft payment. Delete it instead.",
                    )

                if payment["status"] != "posted":
                    raise HTTPException(
                        status_code=400, detail="Only posted payments can be voided"
                    )

                # C8: Period check
                await check_period_is_open(
                    conn, ctx["tenant_id"], payment["payment_date"]
                )
                # Jurnal pembalik bertanggal HARI INI (tanggal bisnis): periodenya juga harus terbuka
                hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
                await check_period_is_open(conn, ctx["tenant_id"], hari_ini)

                # C6: Create reversal journal by iterating ALL original lines
                void_journal_id = uuid_module.uuid4()

                if payment["journal_id"]:
                    # Get original journal
                    original_journal = await conn.fetchrow(
                        "SELECT id, total_debit FROM journal_entries WHERE id = $1",
                        payment["journal_id"],
                    )

                    # Get ALL original journal lines
                    original_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                        payment["journal_id"],
                    )

                    if not original_lines:
                        raise HTTPException(
                            status_code=500,
                            detail="Original journal lines not found",
                        )

                    # Generate void journal number
                    void_journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'VD')", ctx["tenant_id"]
                    )
                    if not void_journal_number:
                        void_journal_number = f"VD-{payment['payment_number']}"

                    # Law 20 Step 1: INSERT as DRAFT with reversal_of_id (Law 26)
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $9, $4, 'BILL_PAYMENT', $5::uuid, $6, 'DRAFT', $7, $7, $8)
                        """,
                        void_journal_id,
                        ctx["tenant_id"],
                        void_journal_number,
                        f"Void {payment['payment_number']} - {payment['vendor_name']} - {payload.void_reason}",
                        payment_id,
                        payment["journal_id"],
                        original_journal["total_debit"],
                        ctx["user_id"],
                        hari_ini,  # t10-tanggal-bisnis
                    )

                    # Law 20 Step 2: INSERT reversed lines (swap debit/credit)
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
                            line["credit"],  # Swap: original credit -> debit
                            line["debit"],  # Swap: original debit -> credit
                            f"Reversal - {line['memo'] or ''}",
                        )

                    # Law 20 Step 3: UPDATE to POSTED (triggers hash chain)
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        void_journal_id,
                    )

                    # Law 26: Mark original journal as reversed
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2
                        WHERE id = $1
                        """,
                        payment["journal_id"],
                        void_journal_id,
                    )

                # C7: Bank mirror reversal (BankSync Rule 3)
                if payment.get("bank_account_id"):
                    original_btxn = await conn.fetchrow(
                        """
                        SELECT id, bank_account_id, amount, transaction_type
                        FROM bank_transactions
                        WHERE reference_type = 'bill_payment' AND reference_id = $1::uuid
                          AND status != 'VOIDED'
                        LIMIT 1
                        """,
                        payment_id,
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
                            ) VALUES ($1, $2, $3, $11, $4, $5, 0,
                                'bill_payment_void', $6::uuid, $7, $8, $9,
                                'POSTED', 'SYSTEM', 'bill_payment', $10, $10, NOW())
                            """,
                            uuid_module.uuid4(),
                            ctx["tenant_id"],
                            original_btxn["bank_account_id"],
                            mirror_type,
                            -original_btxn["amount"],
                            payment_id,
                            f"VOID-{payment['payment_number']}",
                            f"Void payment - {payload.void_reason}",
                            void_journal_id,
                            ctx["user_id"],
                            hari_ini,  # t10-tanggal-bisnis
                        )

                # Reverse bill allocations (reduce paid_amount on bills)
                allocations = await conn.fetch(
                    "SELECT bill_id, amount_applied FROM bill_payment_allocations WHERE payment_id = $1::uuid",
                    payment_id,
                )

                for alloc in allocations:
                    # Law 13: Lock bill row before update
                    # (tenant_id: jangan pernah mengunci baris tenant lain)
                    await conn.fetchrow(
                        "SELECT id FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                        alloc["bill_id"],
                        ctx["tenant_id"],
                    )
                    # Cache tagihan = SATU turunan (pihak_helpers.segarkan_cache_hutang_tagihan, dari compute_ap_outstanding)
                    # untuk SEMUA penulis — tak ada lagi aritmetika amount_paid/status sendiri (26 Sep 2026, 4b).
                    await segarkan_cache_hutang_tagihan(conn, ctx["tenant_id"], alloc["bill_id"])

                # Fase 2.3: Void withholding_tax_records linked to this payment
                # (22 Okt 2026) DI DALAM transaksi: dulu di luar (autocommit) -> jurnal void bisa terbalik sementara wrapper tetap posted.
                await conn.execute(
                    """
                    UPDATE withholding_tax_records
                    SET status = 'void', updated_at = NOW()
                    WHERE payment_id = $1::uuid AND tenant_id = $2
                      AND status != 'void'
                """,
                    payment_id,
                    ctx["tenant_id"],
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

            # Law 16: compute paid/remaining from journal lines on AP account
            bills = await conn.fetch(
                """
                SELECT b.id, b.invoice_number, b.issue_date as bill_date, b.due_date,
                    b.amount as total_amount,
                    b.due_date < $3::date as is_overdue,  -- tanggal bisnis (sapuan A)
                    GREATEST(0, $3::date - b.due_date) as overdue_days,
                    COALESCE(apr.outstanding, 0) as journal_remaining
                FROM bills b
                LEFT JOIN compute_ap_rincian($1) apr ON apr.bill_id = b.id  -- U3a: satu sumber (V440/V441)
                WHERE b.tenant_id = $1 AND b.vendor_id = $2::uuid
                  AND COALESCE(b.status_v2, 'draft') NOT IN ('draft', 'void')
                ORDER BY b.due_date ASC, b.issue_date ASC""",
                ctx["tenant_id"],
                vendor_id,
                await tanggal_dokumen(conn, ctx["tenant_id"]),  # Sapuan tanggal bisnis A (26 Sep 2026): hari ini = tanggal_dokumen (zona tenant), bukan CURRENT_DATE/date.today() UTC.
            )

            # Filter to only bills with remaining > 0
            bills = [r for r in bills if (r["journal_remaining"] or 0) > 0]

            bill_list = [
                OpenBillItem(
                    id=str(r["id"]),
                    bill_number=r["invoice_number"] or "",
                    bill_date=r["bill_date"].isoformat() if r["bill_date"] else "",
                    due_date=r["due_date"].isoformat() if r["due_date"] else "",
                    total_amount=r["total_amount"] or 0,
                    paid_amount=(r["total_amount"] or 0)
                    - int(r["journal_remaining"] or 0),
                    remaining_amount=int(r["journal_remaining"] or 0),
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


# =============================================================================
# TABNAV ENDPOINTS
# =============================================================================


@router.get("/{payment_id}/activities")
async def get_payment_activities(
    request: Request,
    payment_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Tab: Activities - Get audit trail/activity log for a payment.
    """
    try:
        validate_uuid(payment_id, "payment_id")
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Verify payment exists
            payment = await conn.fetchrow(
                "SELECT id, payment_number FROM bill_payments_v2 WHERE id = $1::uuid AND tenant_id = $2",
                payment_id,
                ctx["tenant_id"],
            )
            if not payment:
                raise HTTPException(status_code=404, detail="Payment not found")

            offset = (page - 1) * limit

            # Get activities from audit_logs table
            activities = await conn.fetch(
                """
                SELECT id, action, description, created_at, user_id,
                       (SELECT email FROM users WHERE id = audit_logs.user_id) as user_email
                FROM audit_logs
                WHERE tenant_id = $1 AND entity_type = 'bill_payment' AND entity_id = $2::uuid
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                ctx["tenant_id"],
                payment_id,
                limit,
                offset,
            )

            total = await conn.fetchval(
                "SELECT COUNT(*) FROM audit_logs WHERE tenant_id = $1 AND entity_type = 'bill_payment' AND entity_id = $2::uuid",
                ctx["tenant_id"],
                payment_id,
            )

            # If no audit logs, return payment lifecycle events
            if not activities:
                activities = await conn.fetch(
                    """
                    SELECT
                        gen_random_uuid() as id,
                        CASE
                            WHEN status = 'voided' THEN 'voided'
                            WHEN status = 'posted' THEN 'posted'
                            ELSE 'created'
                        END as action,
                        CASE
                            WHEN status = 'voided' THEN 'Pembayaran dibatalkan: ' || COALESCE(void_reason, '')
                            WHEN status = 'posted' THEN 'Pembayaran diposting'
                            ELSE 'Pembayaran dibuat'
                        END as description,
                        COALESCE(
                            CASE WHEN status = 'voided' THEN voided_at
                                 WHEN status = 'posted' THEN posted_at
                                 ELSE created_at END,
                            created_at
                        ) as created_at,
                        COALESCE(
                            CASE WHEN status = 'voided' THEN voided_by
                                 WHEN status = 'posted' THEN posted_by
                                 ELSE created_by END,
                            created_by
                        ) as user_id,
                        '' as user_email
                    FROM bill_payments_v2
                    WHERE id = $1::uuid
                    """,
                    payment_id,
                )
                total = len(activities)

            return {
                "success": True,
                "data": [
                    {
                        "id": str(a["id"]) if a["id"] else None,
                        "action": a["action"],
                        "description": a["description"],
                        "created_at": a["created_at"].isoformat()
                        if a["created_at"]
                        else None,
                        "user_id": str(a["user_id"]) if a["user_id"] else None,
                        "user_email": a["user_email"] or "",
                    }
                    for a in activities
                ],
                "total": total or 0,
                "page": page,
                "limit": limit,
                "has_more": offset + limit < (total or 0),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payment activities: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get activities")


@router.get("/{payment_id}/journal-entries")
async def get_payment_journal_entries(request: Request, payment_id: str):
    """
    Tab: Journal Entries - Get journal entries related to this payment.
    """
    try:
        validate_uuid(payment_id, "payment_id")
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get payment with journal IDs
            payment = await conn.fetchrow(
                """
                SELECT id, payment_number, journal_id, journal_number,
                       void_journal_id, status
                FROM bill_payments_v2
                WHERE id = $1::uuid AND tenant_id = $2
                """,
                payment_id,
                ctx["tenant_id"],
            )

            if not payment:
                # Law 29 fallback: check if payment_id IS a journal_entries.id
                je_exists = await conn.fetchval(
                    "SELECT id FROM journal_entries WHERE id = $1::uuid AND tenant_id = $2",
                    payment_id,
                    ctx["tenant_id"],
                )
                if not je_exists:
                    raise HTTPException(status_code=404, detail="Payment not found")
                # Use payment_id directly as journal_id
                journal_ids = [je_exists]
            else:
                journal_ids = []
                if payment["journal_id"]:
                    journal_ids.append(payment["journal_id"])
                if payment["void_journal_id"]:
                    journal_ids.append(payment["void_journal_id"])

            if not journal_ids:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "summary": {
                        "total_debit": 0,
                        "total_credit": 0,
                        "is_balanced": True,
                    },
                }

            # Get journal entries
            journals = await conn.fetch(
                """
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.source_type, je.status, je.total_debit, je.total_credit
                FROM journal_entries je
                WHERE je.id = ANY($1::uuid[])
                ORDER BY je.journal_date, je.created_at
                """,
                journal_ids,
            )

            journal_data = []
            total_debit = 0
            total_credit = 0

            for journal in journals:
                # Get journal lines
                lines = await conn.fetch(
                    """
                    SELECT jl.id, jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo,
                           coa.account_code, coa.name as account_name
                    FROM journal_lines jl
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    journal["id"],
                )

                line_data = [
                    {
                        "id": str(line["id"]),
                        "line_number": line["line_number"],
                        "account_id": str(line["account_id"]),
                        "account_code": line["account_code"],
                        "account_name": line["account_name"],
                        "debit": int(line["debit"] or 0),
                        "credit": int(line["credit"] or 0),
                        "memo": line["memo"] or "",
                    }
                    for line in lines
                ]

                journal_debit = int(journal["total_debit"] or 0)
                journal_credit = int(journal["total_credit"] or 0)
                total_debit += journal_debit
                total_credit += journal_credit

                journal_data.append(
                    {
                        "id": str(journal["id"]),
                        "journal_number": journal["journal_number"],
                        "journal_date": journal["journal_date"].isoformat()
                        if journal["journal_date"]
                        else None,
                        "description": journal["description"],
                        "source_type": journal["source_type"],
                        "status": journal["status"],
                        "total_debit": journal_debit,
                        "total_credit": journal_credit,
                        "is_balanced": abs(journal_debit - journal_credit) < 0.01,
                        "lines": line_data,
                    }
                )

            return {
                "success": True,
                "data": journal_data,
                "total": len(journal_data),
                "summary": {
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "is_balanced": abs(total_debit - total_credit) < 0.01,
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payment journal entries: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal entries")


@router.get("/{payment_id}/transactions")
async def get_payment_transactions(
    request: Request,
    payment_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Tab: Transactions - Get bills allocated to this payment.
    """
    try:
        validate_uuid(payment_id, "payment_id")
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Verify payment exists
            payment = await conn.fetchrow(
                "SELECT id, payment_number FROM bill_payments_v2 WHERE id = $1::uuid AND tenant_id = $2",
                payment_id,
                ctx["tenant_id"],
            )
            if not payment:
                raise HTTPException(status_code=404, detail="Payment not found")

            offset = (page - 1) * limit

            # Get bill allocations
            allocations = await conn.fetch(
                """
                SELECT bpa.id, bpa.bill_id, bpa.remaining_before, bpa.amount_applied,
                       bpa.remaining_after, bpa.created_at,
                       b.invoice_number as bill_number, b.issue_date as bill_date,
                       b.amount as bill_amount, b.status as bill_status
                FROM bill_payment_allocations bpa
                JOIN bills b ON b.id = bpa.bill_id
                WHERE bpa.payment_id = $1::uuid
                ORDER BY bpa.created_at
                LIMIT $2 OFFSET $3
                """,
                payment_id,
                limit,
                offset,
            )

            total = await conn.fetchval(
                "SELECT COUNT(*) FROM bill_payment_allocations WHERE payment_id = $1::uuid",
                payment_id,
            )

            return {
                "success": True,
                "data": [
                    {
                        "id": str(a["id"]),
                        "bill_id": str(a["bill_id"]),
                        "bill_number": a["bill_number"] or "",
                        "bill_date": a["bill_date"].isoformat()
                        if a["bill_date"]
                        else None,
                        "bill_amount": a["bill_amount"] or 0,
                        "bill_status": a["bill_status"] or "draft",
                        "remaining_before": a["remaining_before"] or 0,
                        "amount_applied": a["amount_applied"] or 0,
                        "remaining_after": a["remaining_after"] or 0,
                        "created_at": a["created_at"].isoformat()
                        if a["created_at"]
                        else None,
                    }
                    for a in allocations
                ],
                "total": total or 0,
                "page": page,
                "limit": limit,
                "has_more": offset + limit < (total or 0),
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payment transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get transactions")



# =============================================================================
# LAMPIRAN PEMBAYARAN KELUAR (Unit 1a, 24 Sep 2026)
#
# Penyimpanan: `documents` + `document_attachments` dengan entity_type='payment'
# -- entity_type yang SAMA dipakai receive_payments. Karena itu setiap bacaan di
# sini meng-JOIN `bill_payments_v2` + tenant: id penerimaan tak boleh bisa
# membaca/mengunduh lampiran lewat rute pembayaran keluar (dan sebaliknya).
#
# `url` = path relatif rute download di bawah (stream lewat gateway). Dulu:
# presign MinIO (host publik :9000, mati sejak port ditutup 23 Sep) dengan
# cadangan `/api/documents/{id}/download` yang tak pernah terpakai.
# =============================================================================
from ..services.storage_service import get_storage_service  # noqa: E402
from ..utils.lampiran_unduh import (  # noqa: E402
    stream_lampiran,
    url_unduh_lampiran,
)

_BP_ATT_MODUL_URL = "bill-payments"

_BP_ATT_SQL_DAFTAR = """
    SELECT d.id, d.file_name, d.file_size, d.file_type AS mime_type,
           d.thumbnail_path AS thumbnail_url, d.description,
           d.uploaded_at, d.created_at, d.uploaded_by,
           da.attachment_type, da.display_order
    FROM document_attachments da
    JOIN documents d ON d.id = da.document_id
    JOIN bill_payments_v2 bp ON bp.id = da.entity_id
    WHERE da.entity_type = 'payment'
      AND da.entity_id = $1
      AND bp.tenant_id = $2
      AND da.tenant_id = $2
      AND d.tenant_id = $2
      AND d.deleted_at IS NULL
    ORDER BY da.display_order, da.attached_at DESC
"""

_BP_ATT_SQL_UNDUH = """
    SELECT d.file_name, d.file_path, d.file_type, d.storage_type
    FROM document_attachments da
    JOIN documents d ON d.id = da.document_id
    JOIN bill_payments_v2 bp ON bp.id = da.entity_id
    WHERE d.id = $1
      AND da.entity_id = $2
      AND da.entity_type = 'payment'
      AND bp.tenant_id = $3
      AND da.tenant_id = $3
      AND d.tenant_id = $3
      AND d.deleted_at IS NULL
"""


async def _bp_att_daftar(conn, payment_id: UUID, tenant_id: str):
    """Baris lampiran pembayaran keluar milik tenant (JOIN induk)."""
    return await conn.fetch(_BP_ATT_SQL_DAFTAR, payment_id, tenant_id)


@router.get("/{payment_id}/documents")
async def get_payment_documents(request: Request, payment_id: str):
    """Bundel panel berjangkar PEMBAYARAN KELUAR (kontrak seragam 22 Okt 2026, kembar /receive-payments/{id}/documents): skema SAMA dgn
    bundel faktur/PO/tagihan {anchor, default, groups}; pembentuk SATU tempat (pembayaran_keluar_daftar.kelompok_untuk) = documents[] baris.
    Dulu rute ini = daftar LAMPIRAN (salinan /attachments); diukur 22 Okt: 0 permintaan nginx 14 hari + FE tak memanggilnya. Lampiran tetap
    di GET /{payment_id}/attachments (pola Penjualan). anchor.position_text/paid/balance = posisi / dialokasikan / belum dialokasikan."""
    from ..services import pembayaran_keluar_daftar as _pk
    try:
        pid = validate_uuid(payment_id, "payment_id")
        ctx = get_user_context(request)
        tid = ctx["tenant_id"]
        pool = await get_pool()
        async with pool.acquire() as conn:
            p = await conn.fetchrow(
                f"""SELECT bp.id, bp.payment_number, bp.vendor_name, bp.vendor_id, bp.total_amount, COALESCE(bp.discount_amount, 0) AS discount_amount,
                           COALESCE(bp.status, 'draft') AS status,
                           {_pk.sql_bruto('bp')} AS bruto, {_pk.sql_dialokasi('bp')} AS dialokasi
                    FROM bill_payments_v2 bp WHERE bp.id = $1 AND bp.tenant_id = $2""", pid, tid)
            if not p:
                raise HTTPException(status_code=404, detail="Pembayaran tidak ditemukan.")
            kel = (await _pk.kelompok_untuk(conn, tid, [pid])).get(pid, [])
            f = _pk.alokasi_fakta(p["status"], p["bruto"], p["dialokasi"])
            kontak = await conn.fetchrow(
                "SELECT COALESCE(mobile_phone, phone) AS phone, email FROM vendors WHERE id = $1 AND tenant_id = $2",
                p["vendor_id"], tid) if p["vendor_id"] else None
            i_bayar = next(i for i, g in enumerate(kel) if g["key"] == "bayar")
            return {
                "anchor": {"kind": "payment_out", "id": str(p["id"]), "number": p["payment_number"], "vendor": p["vendor_name"] or "",
                           # total = kas keluar; discount = potongan pelunasan; total + discount = paid + balance (bruto, rumus alokasi) -- HANYA posted.
                           # Draf/batal: paid = balance = null (belum/tak lagi mengalokasikan; pola SAMA bundel UM vendor, temuan WORKSPACE 23 Okt).
                           "position_text": _pk.posisi_bayar(p["status"], f), "total": f"{Decimal(str(p['total_amount'] or 0)):.2f}",
                           "discount": f"{Decimal(str(p['discount_amount'] or 0)):.2f}",
                           "paid": f"{Decimal(str(f['dialokasikan'])):.2f}" if p["status"] == "posted" else None,
                           "balance": f"{Decimal(str(f['belum_dialokasikan'])):.2f}" if p["status"] == "posted" else None,
                           "vendor_phone": (kontak["phone"] if kontak else None) or None,
                           "vendor_email": (kontak["email"] if kontak else None) or None},
                "default": {"group": "bayar", "index": 0} if i_bayar is not None else None,
                "groups": kel,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payment documents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat dokumen pembayaran.")


@router.get("/{payment_id}/history")
async def get_payment_history(request: Request, payment_id: str, limit: int = Query(200, ge=1, le=500)):
    """Riwayat Pembayaran Keluar, bentuk SAMA dengan riwayat SO/PO/tagihan (services/bayar_keluar_riwayat). Terbaru dulu."""
    from ..services.dashboard_izin import boleh_baca
    from ..services.bayar_keluar_riwayat import riwayat_bayar_keluar
    pid = validate_uuid(payment_id, "payment_id")
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_bayar_keluar(conn, ctx["tenant_id"], pid, lambda m: boleh_baca(request, m), limit if isinstance(limit, int) else 200)
    if data is None:
        raise HTTPException(status_code=404, detail="Pembayaran tidak ditemukan.")
    return {"success": True, "data": data}


@router.get("/{payment_id}/attachments")
async def list_payment_attachments(request: Request, payment_id: str):
    """List attachments for a payment via document_attachments table."""
    pid = validate_uuid(payment_id, "payment_id")
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            rows = await _bp_att_daftar(conn, pid, tenant_id)

    result = []
    for r in rows:
        result.append(
            {
                "id": str(r["id"]),
                "file_name": r["file_name"],
                "file_size": r["file_size"],
                "mime_type": r["mime_type"],
                "url": url_unduh_lampiran(_BP_ATT_MODUL_URL, pid, r["id"]),
                "thumbnail_url": r["thumbnail_url"],
                "uploaded_at": r["uploaded_at"].isoformat()
                if r["uploaded_at"]
                else None,
                "attachment_type": r["attachment_type"],
            }
        )

    return {"success": True, "data": result}


@router.get("/{payment_id}/attachments/{attachment_id}/download")
async def download_payment_attachment(
    request: Request, payment_id: UUID, attachment_id: UUID
):
    """Stream satu lampiran pembayaran keluar lewat gateway (Unit 1a).

    Izin: pola READ `send_payment` (sama dengan GET .../attachments).
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await conn.fetchrow(
                _BP_ATT_SQL_UNDUH, attachment_id, payment_id, tenant_id
            )
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    return stream_lampiran(row, get_storage_service())
