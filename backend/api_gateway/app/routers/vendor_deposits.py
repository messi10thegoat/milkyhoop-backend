"""
Vendor Deposits Router
======================
Advance payments to vendors before receiving goods.
Creates journal entries on post, apply, and refund.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from ..services import idem_buat  # P-U3b: idempotensi header X-Idempotency-Key (pola PO / W0)
from ..services.so_riwayat import catat_riwayat  # P-U3b: riwayat beraktor (lepas penerapan)
from ..services.jaga_rekonsiliasi import tolak_void_bila_terekonsiliasi
from ..services.pihak_helpers import segarkan_cache_hutang_tagihan
from ..utils.tanggal_tenant import tanggal_dokumen

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
    VoidDepositRequest,
    VendorDepositStatus,
)
from ..services.role_resolver import AccountRole
from ..services import uang_muka_vendor as UMV  # akun PERAN + sisa dari jurnal (P-U1, 22 Okt 2026)
from ..services import po_daftar as PD  # pembentuk dokumen bersama (dok_*), r2, documents_baris
from ..services import uang_muka_vendor_daftar as UMD  # P-U3: sisa/keadaan/aksi/position_text/documents SERVER (satu sumber)
from ..services import uang_muka_vendor_rencana as R  # CW penuh: penentu per aksi = SATU sumber tulis + pratinjau
from typing import Literal
from ..services.bank_sync import (
    create_bank_transaction_for_journal,
    create_reversal_bank_transaction,
)

router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


async def get_bill_remaining_from_journal(conn, tenant_id: str, bill_id) -> Decimal:
    """Sisa tagihan dari JURNAL (compute_ap_outstanding) -- delegasi ke penentu bersama R.sisa_tagihan (satu sumber)."""
    return await R.sisa_tagihan(conn, tenant_id, bill_id)


SQL_BERGANTUNG = R.SQL_BERGANTUNG


def _tolak(bloks: list) -> None:
    """Endpoint TULIS: blok pertama -> galat HTTP. 400 = pesan teks (bentuk lama); selain itu {code, message}."""
    if not bloks:
        return
    b = bloks[0]
    if b["status"] == 400:
        raise HTTPException(status_code=400, detail=b["message"])
    raise HTTPException(status_code=b["status"], detail={"code": b["code"], "message": b["message"]})


def _kunci_idem(request):
    """X-Idempotency-Key (alias Idempotency-Key) atau None; tanpa header = perilaku lama. Objek uji tanpa .headers -> None."""
    if getattr(request, "headers", None) is None:
        return None
    return idem_buat.kunci_dari(request)


def _f(v) -> float:
    return float(PD.r2(v or 0))


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
    # 22 Okt 2026 (P-U3, aditif): ?search=, page/per_page/offset (prioritas offset > page > skip), sort, pill aksi, total atas SEMUA hasil filter
    search: Optional[str] = Query(None, description="Cari no. uang muka, vendor, referensi, catatan, no. PO (semua kata wajib cocok)"),
    page: int = Query(1, ge=1),
    per_page: Optional[int] = Query(None, ge=1, le=100),
    offset: Optional[int] = Query(None, ge=0),
    sort_by: Literal["deposit_date", "deposit_number", "amount", "created_at"] = Query("deposit_date"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    aksi: Optional[Literal["belum_diterapkan"]] = Query(None),
    with_totals: bool = Query(False),
):
    """Daftar uang muka vendor. sisa/terpakai/dikembalikan TURUNAN jurnal; sisa_keadaan, status_text/position_text, aksi (pill), documents/doc_count
    dihitung server; urutan deterministik (`, vd.id`); has_more."""
    ctx = get_user_context(request)
    pool = await get_pool()
    _i = lambda v, d: v if isinstance(v, int) and not isinstance(v, bool) else d  # noqa: E731  (panggilan langsung = objek Query)
    _limit = _i(per_page, None) or _i(limit, 50)
    _offset = _i(offset, None)
    _page, _skip = _i(page, 1), _i(skip, 0)
    _offset = _offset if _offset is not None and _offset > 0 else ((_page - 1) * _limit if _page > 1 else _skip)

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )
        akun = await UMD.akun_baca(conn, ctx["tenant_id"])

        where_clauses = ["vd.tenant_id = $1", "$2::uuid IS NOT NULL"]
        params = [ctx["tenant_id"], akun]
        param_idx = 3

        if status:
            where_clauses.append(f"vd.status = ${param_idx}")
            params.append(status.value if hasattr(status, "value") else status)
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

        cari, par_cari = UMD.syarat_cari(search, param_idx)
        if cari:
            where_clauses.append(cari)
            params.extend(par_cari)
            param_idx += len(par_cari)

        if isinstance(aksi, str) and aksi in UMD.AKSI_VD:  # panggilan langsung tanpa aksi = objek Query -> mati
            where_clauses.append(UMD.sql_aksi(aksi))

        where_sql = " AND ".join(where_clauses)
        dari = f"FROM vendor_deposits vd JOIN vendors v ON vd.vendor_id = v.id AND v.tenant_id = vd.tenant_id {UMD.sql_agregat('$2')}"
        sort_field = {"deposit_date": "vd.deposit_date", "deposit_number": "vd.deposit_number", "amount": "vd.amount",
                      "created_at": "vd.created_at"}.get(sort_by if isinstance(sort_by, str) else "deposit_date", "vd.deposit_date")
        sort_dir = "ASC" if sort_order == "asc" else "DESC"

        total = await conn.fetchval(f"SELECT COUNT(*) {dari} WHERE {where_sql}", *params)

        rows = await conn.fetch(
            f"""
            SELECT vd.*, v.name as vendor_name, v.code as vendor_code,
                   ba.account_name as bank_account_name, po.po_number as purchase_order_number
            {dari}
            LEFT JOIN bank_accounts ba ON vd.bank_account_id = ba.id AND ba.tenant_id = vd.tenant_id
            LEFT JOIN purchase_orders po ON vd.purchase_order_id = po.id AND po.tenant_id = vd.tenant_id
            WHERE {where_sql}
            ORDER BY {sort_field} {sort_dir}, vd.id
            OFFSET ${param_idx} LIMIT ${param_idx + 1}
            """,
            *params,
            _offset,
            _limit,
        )

        ids = [row["id"] for row in rows]
        fakta = await UMD.fakta_vd(conn, ctx["tenant_id"], akun, ids)
        kel = await UMD.kelompok_untuk(conn, ctx["tenant_id"], ids)
        items = []
        for row in rows:
            f = fakta[row["id"]]
            dok = PD.documents_baris(kel.get(row["id"], []))
            data = dict(row)
            data.update({"sisa": None if f["sisa"] is None else float(f["sisa"]), "terpakai": float(f["terpakai"]),
                         "dikembalikan": float(f["dikembalikan"]), "sisa_keadaan": f["sisa_keadaan"], "status_text": f["status_text"],
                         "position_text": f["position_text"], "aksi": f["aksi"], "documents": dok, "doc_count": len(dok)})
            items.append(VendorDepositResponse(**data))

        hasil = {"items": items, "total": total, "has_more": (_offset + _limit) < total}
        if with_totals is True:  # panggilan langsung (uji) = objek Query -> mati; agregat atas WHERE yang SAMA dengan COUNT
            t = await conn.fetchrow(
                f"""SELECT COALESCE(SUM(vd.amount) FILTER (WHERE vd.status <> 'void'), 0) AS sum_total,
                           COALESCE(SUM(ag.sisa) FILTER (WHERE vd.status NOT IN ('draft', 'void')), 0) AS sum_sisa
                    {dari} WHERE {where_sql}""", *params)
            hasil["sum_total"] = float(PD.r2(t["sum_total"]))
            hasil["sum_sisa"] = float(PD.r2(t["sum_sisa"]))
        return VendorDepositListResponse(**hasil)


@router.get("/summary", response_model=VendorDepositSummary)
async def get_vendor_deposit_summary(request: Request, search: Optional[str] = Query(None)):
    """Ringkasan uang muka vendor. P-U3: angka TURUNAN jurnal (bukan fungsi DB atas kolom cache BIGINT); ?search= (predikat daftar) membatasi
    SEMUA hitungan/nilai; belum_diterapkan_count/value = predikat SAMA dgn ?aksi= daftar."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )
        akun = await UMD.akun_baca(conn, ctx["tenant_id"])
        cari, par_cari = UMD.syarat_cari(search, 3)
        w = f" AND {cari}" if cari else ""
        row = await conn.fetchrow(
            f"""SELECT COUNT(*) FILTER (WHERE vd.status <> 'void') AS deposit_count,
                       COUNT(*) FILTER (WHERE vd.status = 'draft') AS draft_count,
                       COUNT(*) FILTER (WHERE vd.status = 'void') AS void_count,
                       COALESCE(SUM(vd.amount) FILTER (WHERE vd.status <> 'void'), 0) AS total_deposits,
                       COALESCE(SUM(ag.terpakai) FILTER (WHERE vd.status NOT IN ('draft', 'void')), 0) AS total_applied,
                       COALESCE(SUM(ag.sisa) FILTER (WHERE vd.status NOT IN ('draft', 'void')), 0) AS total_remaining,
                       COUNT(*) FILTER (WHERE {UMD.sql_aksi('belum_diterapkan')}) AS belum_n,
                       COALESCE(SUM(ag.sisa) FILTER (WHERE {UMD.sql_aksi('belum_diterapkan')}), 0) AS belum_v
                FROM vendor_deposits vd {UMD.sql_agregat('$2')}
                WHERE vd.tenant_id = $1 AND $2::uuid IS NOT NULL{w}""", ctx["tenant_id"], akun, *par_cari)
        return VendorDepositSummary(
            total_deposits=float(PD.r2(row["total_deposits"])), total_applied=float(PD.r2(row["total_applied"])),
            total_remaining=float(PD.r2(row["total_remaining"])), deposit_count=row["deposit_count"] or 0,
            pending_count=row["belum_n"] or 0, draft_count=row["draft_count"] or 0, void_count=row["void_count"] or 0,
            belum_diterapkan_count=row["belum_n"] or 0, belum_diterapkan_value=float(PD.r2(row["belum_v"])))


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
            LEFT JOIN purchase_orders po ON vd.purchase_order_id = po.id AND po.tenant_id = vd.tenant_id
            WHERE vd.id = $1 AND vd.tenant_id = $2
            """,
            deposit_id,
            ctx["tenant_id"],
        )
        if not vd:
            raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")

        # P-U3: paritas detail == baris (fungsi SAMA: UMD.fakta_vd / UMD.kelompok_untuk)
        _akun = await UMD.akun_baca(conn, ctx["tenant_id"])
        _f = (await UMD.fakta_vd(conn, ctx["tenant_id"], _akun, [deposit_id]))[deposit_id]
        _dok = PD.documents_baris((await UMD.kelompok_untuk(conn, ctx["tenant_id"], [deposit_id])).get(deposit_id, []))

        # P-U3: dua fungsi DB penerapan/pengembalian lama RUSAK sejak dibuat (kolom tak ada; tipe BIGINT != numeric) -- sebab detail diparkir.
        # Diganti kueri langsung (UMD.SQL_APLIKASI / SQL_REFUND), tenant eksplisit.
        applications = await conn.fetch(UMD.SQL_APLIKASI, deposit_id, ctx["tenant_id"])
        refunds = await conn.fetch(UMD.SQL_REFUND, deposit_id, ctx["tenant_id"])

        return VendorDepositDetailResponse(
            **{**dict(vd), "sisa": None if _f["sisa"] is None else float(_f["sisa"]), "terpakai": float(_f["terpakai"]),
               "dikembalikan": float(_f["dikembalikan"]), "sisa_keadaan": _f["sisa_keadaan"], "status_text": _f["status_text"],
               "position_text": _f["position_text"], "aksi": _f["aksi"], "documents": _dok, "doc_count": len(_dok)},
            applications=[
                VendorDepositApplicationResponse(**dict(a)) for a in applications
            ],
            refunds=[VendorDepositRefundResponse(**dict(r)) for r in refunds],
        )


@router.get("/{deposit_id}/documents")
async def get_vendor_deposit_documents(request: Request, deposit_id: UUID):
    """Bundel panel berjangkar UANG MUKA PEMBELIAN (P-U3): skema SAMA dgn bundel SO/faktur {anchor, default, groups[{key,label,docs[]}]};
    pembentuk SATU tempat (services/uang_muka_vendor_daftar.kelompok_untuk) = sumber kolom documents[] daftar. Jangkar SELALU tampil sebagai dirinya
    (draf=`draf`, void=`batal`). anchor: total = jumlah, paid = terpakai (jurnal), balance = sisa (jurnal)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        vd = await conn.fetchrow(
            """SELECT vd.id, vd.deposit_number, vd.amount, vd.status, v.name AS vendor_name, v.phone, v.email
               FROM vendor_deposits vd JOIN vendors v ON v.id = vd.vendor_id AND v.tenant_id = vd.tenant_id
               WHERE vd.id = $1 AND vd.tenant_id = $2""", deposit_id, ctx["tenant_id"])
        if not vd:
            raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
        akun = await UMD.akun_baca(conn, ctx["tenant_id"])
        f = (await UMD.fakta_vd(conn, ctx["tenant_id"], akun, [deposit_id]))[deposit_id]
        kel = (await UMD.kelompok_untuk(conn, ctx["tenant_id"], [deposit_id])).get(deposit_id, [])
        return {
            "anchor": {"kind": "vendor_deposit", "id": str(vd["id"]), "number": vd["deposit_number"], "vendor": vd["vendor_name"] or "",
                       "position_text": f["position_text"], "total": f"{PD.r2(vd['amount']):.2f}",
                       "paid": None if vd["status"] in ("draft", "void") else f"{f['terpakai']:.2f}",
                       "balance": None if f["sisa"] is None else f"{f['sisa']:.2f}",
                       "vendor_phone": vd["phone"] or None, "vendor_email": vd["email"] or None},
            "default": {"group": "uang_muka", "index": 0},
            "groups": kel,
        }


@router.post("", response_model=VendorDepositResponse, status_code=201)
async def create_vendor_deposit(request: Request, data: VendorDepositCreate, response: Response = None):
    """Buat uang muka pembelian (draf). P-U3b (22 Okt 2026): Law 14 X-Idempotency-Key (kunci sama + isi sama = respons asli; isi beda
    = 409; deposit terhapus = kunci basi -> proses baru, SUMBER_BUAT VD_CREATE); nominal 2 desimal."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )
        kp, sidik, lama = await idem_buat.mulai(conn, ctx, _kunci_idem(request), "VD_CREATE", data, "vendor_deposits", "deposit", response)
        if lama is not None:
            return lama

        # Penentu SAMA dengan /vendor-deposits/preview (vendor, PO, rekening; + posting bila simpan_dan_posting)
        rb = await R.rencana_buat(conn, ctx, data)
        _tolak(rb["blocks"])  # blok posting ikut bila simpan_dan_posting (penentu posting yang SAMA)
        vendor = await conn.fetchrow("SELECT id, name, code FROM vendors WHERE id = $1 AND tenant_id = $2", data.vendor_id, ctx["tenant_id"])

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

        if data.simpan_dan_posting:  # "Simpan & posting" satu langkah (pola QBO/NetSuite Vendor Prepayment): inti posting YANG SAMA, tx yang sama
            await _posting_inti(conn, ctx, row["id"])
            row = await conn.fetchrow("SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2", row["id"], ctx["tenant_id"])
        resp = VendorDepositResponse(
            **dict(row),
            vendor_name=vendor["name"],
            vendor_code=vendor["code"],
        )
        return await idem_buat.simpan(conn, ctx, kp, sidik, "VD_CREATE", resp, row["id"])


@router.patch("/{deposit_id}", response_model=VendorDepositResponse)
async def update_vendor_deposit(
    request: Request, deposit_id: UUID, data: VendorDepositUpdate
):
    """Update vendor deposit (draft only)"""
    ctx = get_user_context(request)
    pool = await get_pool()
    # Optimistic concurrency (opt-in If-Match): reject a stale write.
    from ..services.optimistic_concurrency import assert_if_match_row
    await assert_if_match_row(request, "vendor_deposits", deposit_id)

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
            raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")

        _tolak((await R.rencana_hapus(conn, ctx, existing))["blocks"])  # = /delete/preview

        await conn.execute("DELETE FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 AND status = 'draft'", deposit_id, ctx["tenant_id"])
        return {"message": "Vendor deposit deleted"}


# ============================================================================
# POST DEPOSIT (Creates Journal Entry)
# ============================================================================


async def _posting_inti(conn, ctx: dict, deposit_id) -> "PostDepositResponse":
    """SATU penulis posting: /post DAN "Simpan & posting" di /vendor-deposits. Pemanggil memegang transaksi."""
    # Law 13: lock DULU, baru baca & periksa di DALAM (G4: dulu status dibaca di luar lock -> dua post sama-sama 'draft' -> dua jurnal)
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))",
        f"VENDOR_DEPOSIT:{deposit_id}",
    )
    vd = await conn.fetchrow(
        "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
        deposit_id,
        ctx["tenant_id"],
    )
    if not vd:
        raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
    _tolak((await R.rencana_posting(conn, ctx, vd))["blocks"])  # = /post/preview (status, peran akun, rekening, periode)
    # Law 27: akun dari PERAN (dulu kode hardcode 1-10800 = PPN Masukan di semua tenant)
    vendor_deposit_account = await UMV.akun_peran(conn, ctx["tenant_id"], AccountRole.AP_PREPAID)
    bank_account = None
    if vd["bank_account_id"]:
        bank_account = await conn.fetchrow(
            "SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",  # Law 24: tenant eksplisit
            vd["bank_account_id"], ctx["tenant_id"],
        )
    if not bank_account:
        cash_account = await UMV.akun_peran(conn, ctx["tenant_id"], AccountRole.CASH_GENERAL)
    else:
        cash_account = bank_account["coa_id"]

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
    # Self-healing canonical generator (V176): emits canonical JV-YYMM-NNNN
    # (was JV-YYYY-NNNNN, an off-format that the JV regex could not reconcile).
    journal_number = await conn.fetchval(
        "SELECT get_next_journal_number($1, $2, $3)",
        ctx["tenant_id"],
        "JV",
        vd["deposit_date"],
    )

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

    # === bank_transaction mirror (BankSync Rule 1, R9) — 14 Sep 2026 ===
    # Dulu NOL cermin: setiap uang muka vendor lewat rekening bank = jurnal yatim + celah R9 (banksync
    # catatan kaki 3). Pola uang muka pelanggan (customer_deposits.py FIX_R9_DEP_MIRROR): cari bank_accounts
    # dari CoA yang DIKREDIT (bukan dari field bank_account_id), idempoten per jurnal, helper kanonik.
    bank_acct = await conn.fetchrow(
        "SELECT id FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2",
        cash_account,
        ctx["tenant_id"],
    )
    if bank_acct:
        already = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM bank_transactions WHERE journal_id = $1 AND bank_account_id = $2)",
            journal["id"],
            bank_acct["id"],
        )
        if not already:
            await create_bank_transaction_for_journal(
                conn,
                tenant_id=ctx["tenant_id"],
                bank_account_id=bank_acct["id"],
                journal_id=journal["id"],
                transaction_date=vd["deposit_date"],
                transaction_type="withdrawal",
                amount=-vd["amount"],
                reference_type="VENDOR_DEPOSIT",
                reference_id=deposit_id,
                reference_number=vd["reference"],
                description=f"Vendor Deposit - {vd['deposit_number']}",
                created_by=ctx["user_id"],
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

    resp = PostDepositResponse(
        deposit_id=deposit_id,
        deposit_number=vd["deposit_number"],
        status=VendorDepositStatus.posted,
        journal_id=journal["id"],
        journal_number=journal["journal_number"],
    )
    return resp


@router.post("/{deposit_id}/post", response_model=PostDepositResponse)
async def post_vendor_deposit(request: Request, deposit_id: UUID, response: Response = None):
    """
    Post vendor deposit - creates journal entry:
    Dr. Uang Muka Pembelian (peran AP_PREPAID)    amount
        Cr. Kas/Bank                      amount
    P-U3b: kirim ulang dgn X-Idempotency-Key sama = respons asli (bukan 400 "sudah diposting").
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        async with conn.transaction():
            kp, sidik, lama = await idem_buat.mulai_aksi(conn, ctx, _kunci_idem(request), "VD_POST", deposit_id, {}, response)
            if lama is not None:
                return lama
            resp = await _posting_inti(conn, ctx, deposit_id)
            return await idem_buat.simpan(conn, ctx, kp, sidik, "VD_POST", resp, deposit_id)


# ============================================================================
# APPLY TO BILL
# ============================================================================


@router.post("/{deposit_id}/apply", response_model=ApplyDepositResponse)
async def apply_vendor_deposit(
    request: Request, deposit_id: UUID, data: ApplyDepositRequest, response: Response = None
):
    """
    Apply vendor deposit to bill - creates journal entry:
    Dr. Hutang Usaha (peran AP_TRADE)        applied_amount
        Cr. Uang Muka Pembelian (peran AP_PREPAID)    applied_amount
    P-U3b: Law 14 X-Idempotency-Key (tap ganda = respons asli, bukan penerapan kedua); nominal Decimal 2dp; sisa tagihan
    compute_ap_outstanding; respons sisa = JURNAL (bukan kolom cache).
    """
    ctx = get_user_context(request)
    pool = await get_pool()
    jumlah = Decimal(str(data.amount))

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        async with conn.transaction():
            kp, sidik, lama = await idem_buat.mulai_aksi(
                conn, ctx, _kunci_idem(request), "VD_APPLY", deposit_id, lambda: data.model_dump(mode="json"), response)
            if lama is not None:
                return lama
            # Urutan kunci GLOBAL: baris TAGIHAN dulu, lalu uang muka (sama dgn lepas_penerapan & void faktur BACKEND) -> tanpa deadlock silang
            await conn.fetchrow("SELECT id FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE", data.bill_id, ctx["tenant_id"])
            # Law 13: kunci SALDO bersama apply/refund/void + kunci apply; semua pemeriksaan di DALAM lock (G4: dulu di luar -> over-apply)
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"VENDOR_DEPOSIT_SALDO:{deposit_id}",
            )
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"DEPOSIT_APPLY:{deposit_id}",
            )
            vd = await conn.fetchrow(
                "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                deposit_id,
                ctx["tenant_id"],
            )
            if not vd:
                raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
            await conn.fetchrow("SELECT id FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE", data.bill_id, ctx["tenant_id"])
            _tolak((await R.rencana_terapkan(conn, ctx, vd, data, data.applied_date or await tanggal_dokumen(conn, ctx["tenant_id"])))["blocks"])  # = /apply/preview
            # sisa dari JURNAL (Law 1/16), bukan kolom cache remaining_amount
            sisa = await UMV.sisa_jurnal(conn, ctx["tenant_id"], deposit_id)
            if jumlah > sisa:
                raise HTTPException(
                    status_code=400,
                    detail=f"Saldo uang muka tidak cukup. Tersedia: {sisa}",
                )
            bill = await conn.fetchrow(
                "SELECT * FROM bills WHERE id = $1 AND vendor_id = $2 AND tenant_id = $3 FOR UPDATE",
                data.bill_id,
                vd["vendor_id"],
                ctx["tenant_id"],
            )
            if not bill:
                raise HTTPException(
                    status_code=400, detail="Tagihan tidak ditemukan atau bukan milik vendor ini."
                )
            if bill["status"] not in ("posted", "partial"):
                raise HTTPException(status_code=400, detail="Tagihan harus sudah diposting.")
            bill_remaining = await get_bill_remaining_from_journal(
                conn, ctx["tenant_id"], bill["id"]
            )
            if jumlah > bill_remaining:
                raise HTTPException(
                    status_code=400,
                    detail=f"Jumlah melebihi sisa tagihan. Sisa tagihan: {bill_remaining}",
                )
            applied_date = data.applied_date or await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
            ap_account = await UMV.akun_peran(conn, ctx["tenant_id"], AccountRole.AP_TRADE)
            vendor_deposit_account = await UMV.akun_peran(conn, ctx["tenant_id"], AccountRole.AP_PREPAID)

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
            # Self-healing canonical generator (V176): emits canonical JV-YYMM-NNNN
            # keyed to applied_date (the journal_date), fixing the prior split where the
            # counter used deposit_date but the emitted year used applied_date.
            journal_number = await conn.fetchval(
                "SELECT get_next_journal_number($1, $2, $3)",
                ctx["tenant_id"],
                "JV",
                applied_date,
            )

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
                f"Apply Deposit {vd['deposit_number']} to Bill {bill['invoice_number']}",
                deposit_id,
                jumlah,
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
                jumlah,
                f"Apply Deposit to Bill {bill['invoice_number']}",
            )

            # Cr. Uang Muka Vendor
            await conn.execute(
                """
                INSERT INTO journal_lines (journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1, 2, $2, 0, $3, $4)
                """,
                journal["id"],
                vendor_deposit_account,
                jumlah,
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
                jumlah,
                applied_date,
                journal["id"],
                ctx.get("user_id"),
            )

            # Cache tagihan = SATU turunan (pihak_helpers.segarkan_cache_hutang_tagihan, dari compute_ap_outstanding)
            # untuk SEMUA penulis — tak ada lagi aritmetika amount_paid/status sendiri (26 Sep 2026, 4b).
            await segarkan_cache_hutang_tagihan(conn, ctx["tenant_id"], data.bill_id)

            # Sisa sesudah penerapan = JURNAL (deposit: UMV.sisa_jurnal; tagihan: compute_ap_outstanding), bukan kolom cache
            resp = ApplyDepositResponse(
                application_id=app["id"],
                deposit_id=deposit_id,
                deposit_number=vd["deposit_number"],
                bill_id=data.bill_id,
                bill_number=bill["invoice_number"],
                applied_amount=jumlah,
                deposit_remaining=await UMV.sisa_jurnal(conn, ctx["tenant_id"], deposit_id),
                bill_remaining=await get_bill_remaining_from_journal(conn, ctx["tenant_id"], data.bill_id),
                journal_id=journal["id"],
            )
            return await idem_buat.simpan(conn, ctx, kp, sidik, "VD_APPLY", resp, deposit_id)


# ============================================================================
# LEPAS PENERAPAN (UNAPPLY) -- P-U3b, 22 Okt 2026
# ============================================================================


@router.post("/{deposit_id}/applications/{application_id}/unapply")
async def unapply_vendor_deposit(request: Request, deposit_id: UUID, application_id: UUID):
    """Lepas SATU penerapan uang muka pembelian dari tagihan (pola QBO/Xero "unapply"). Penulis = services/uang_muka_vendor_lepas.lepas_penerapan
    (SATU penulis, dipakai juga void faktur pembelian -- putusan pemilik 22 Okt: lepas otomatis). Idempoten alami (lepas kedua = 200)."""
    from ..services.uang_muka_vendor_lepas import lepas_penerapan
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        async with conn.transaction():
            return await lepas_penerapan(conn, ctx, deposit_id, application_id)


# ============================================================================
# REFUND
# ============================================================================


@router.post("/{deposit_id}/refund", response_model=VendorDepositRefundResponse)
async def refund_vendor_deposit(
    request: Request, deposit_id: UUID, data: VendorDepositRefundCreate, response: Response = None
):
    """
    Refund vendor deposit - creates journal entry:
    Dr. Kas/Bank                      refund_amount
        Cr. Uang Muka Pembelian (peran AP_PREPAID)    refund_amount
    P-U3b: Law 14 X-Idempotency-Key (tap ganda = respons asli, bukan refund kedua); nominal Decimal 2dp.
    """
    ctx = get_user_context(request)
    pool = await get_pool()
    jumlah = Decimal(str(data.amount))

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        async with conn.transaction():
            kp, sidik, lama = await idem_buat.mulai_aksi(
                conn, ctx, _kunci_idem(request), "VD_REFUND", deposit_id, lambda: data.model_dump(mode="json"), response)
            if lama is not None:
                return lama
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"VENDOR_DEPOSIT_SALDO:{deposit_id}",
            )
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"DEPOSIT_REFUND:{deposit_id}",
            )
            vd = await conn.fetchrow(
                "SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                deposit_id,
                ctx["tenant_id"],
            )
            if not vd:
                raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
            rk = await R.rencana_kembali(conn, ctx, vd, data)
            _tolak(rk["blocks"])  # = /refund/preview (status, saldo jurnal, rekening/akun kas WAJIB, periode)
            sisa = await UMV.sisa_jurnal(conn, ctx["tenant_id"], deposit_id)  # dari JURNAL (G4), bukan kolom cache
            if jumlah > sisa:
                raise HTTPException(
                    status_code=400,
                    detail=f"Saldo uang muka tidak cukup. Tersedia: {sisa}",
                )
            vendor_deposit_account = await UMV.akun_peran(conn, ctx["tenant_id"], AccountRole.AP_PREPAID)
            bank_account_coa = rk["akun_kas"]  # dari penentu: rekening (tenant eksplisit) atau akun kas ASSET is_cash; wajib salah satu

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
            # Self-healing canonical generator (V176): emits canonical JV-YYMM-NNNN.
            journal_number = await conn.fetchval(
                "SELECT get_next_journal_number($1, $2, $3)",
                ctx["tenant_id"],
                "JV",
                data.refund_date,
            )

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
                jumlah,
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
                jumlah,
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
                jumlah,
                f"Refund Deposit {vd['deposit_number']}",
            )

            # Law 20: Post after all lines
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                journal["id"],
            )

            # === bank_transaction mirror (BankSync Rule 1, R9) — 14 Sep 2026: dulu NOL cermin (pola uang muka pelanggan) ===
            bank_acct = await conn.fetchrow(
                "SELECT id FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2",
                bank_account_coa,
                ctx["tenant_id"],
            )
            if bank_acct:
                already = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM bank_transactions WHERE journal_id = $1 AND bank_account_id = $2)",
                    journal["id"],
                    bank_acct["id"],
                )
                if not already:
                    await create_bank_transaction_for_journal(
                        conn,
                        tenant_id=ctx["tenant_id"],
                        bank_account_id=bank_acct["id"],
                        journal_id=journal["id"],
                        transaction_date=data.refund_date,
                        transaction_type="deposit",
                        amount=jumlah,
                        reference_type="VENDOR_DEPOSIT_REFUND",
                        reference_id=deposit_id,
                        reference_number=data.reference,
                        description=f"Refund Deposit {vd['deposit_number']}",
                        created_by=ctx["user_id"],
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
                jumlah,
                data.bank_account_id,
                data.reference,
                journal["id"],
                data.notes,
                ctx.get("user_id"),
            )

            bank_name = None
            if data.bank_account_id:
                bank_name = await conn.fetchval(
                    "SELECT account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2", data.bank_account_id, ctx["tenant_id"]
                )

            resp = VendorDepositRefundResponse(
                **dict(refund),
                bank_account_name=bank_name,
            )
            return await idem_buat.simpan(conn, ctx, kp, sidik, "VD_REFUND", resp, deposit_id)


# ============================================================================
# VOID
# ============================================================================


@router.post("/{deposit_id}/void", response_model=VoidDepositResponse)
async def void_vendor_deposit(request: Request, deposit_id: UUID, body: Optional[VoidDepositRequest] = None):
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
            # P-U3b: kunci SALDO bersama apply/refund/unapply -> void tak bisa menyela penerapan/pelepasan yang sedang berjalan
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"VENDOR_DEPOSIT_SALDO:{deposit_id}")
            await tolak_void_bila_terekonsiliasi(conn, ctx["tenant_id"], deposit_id)  # rekon bank: 409 sebelum tulisan

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
                    # 23 Okt: pembalik yang SUDAH ada (jurnal asli reversed_by_id), sama dgn balasan void pertama
                    reversal_journal_id=(await conn.fetchval(
                        "SELECT reversed_by_id FROM journal_entries WHERE id = $1 AND tenant_id = $2", vd["journal_id"], ctx["tenant_id"])
                        if vd["journal_id"] else None),
                )

            # Penentu SAMA dengan /void/preview (draf, alasan WAJIB, rekonsiliasi, penerapan aktif/refund, periode)
            alasan = ((body.reason if body else None) or "").strip()
            _tolak((await R.rencana_batal(conn, ctx, vd, alasan, await tanggal_dokumen(conn, ctx["tenant_id"])))["blocks"])

            # C5: Dependency check -- penerapan AKTIF atau refund menahan void (P-U3b: penerapan yang sudah DILEPAS tidak;
            # dulu kolom cache applied_amount yang ikut menjumlah penerapan terlepas)
            bergantung = await conn.fetchrow(SQL_BERGANTUNG, deposit_id)
            if bergantung["n_terap"] or bergantung["n_refund"]:
                raise HTTPException(status_code=409, detail={
                    "code": "UANG_MUKA_MASIH_DIPAKAI",
                    "message": "Uang muka ini masih diterapkan ke tagihan atau sudah dikembalikan. Lepas penerapannya dulu.",
                    "penerapan_aktif": bergantung["n_terap"], "refund": bergantung["n_refund"]})

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

                # Law 5: Period check on HARI INI (tanggal bisnis = tanggal jurnal pembalik)
                hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    hari_ini,
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
                    ) VALUES ($1, $2, $3, $9, $4, 'VENDOR_DEPOSIT', $5, $6, $6, 'DRAFT', $7, $8)
                    """,
                    reversal_id,
                    ctx["tenant_id"],
                    journal_number,
                    f"Void {vd['deposit_number']}",
                    str(deposit_id),
                    original_journal["total_debit"],
                    ctx.get("user_id"),
                    original_journal["id"],
                    hari_ini,  # t10-tanggal-bisnis
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

            # C7: Bank mirror reversal (BankSync Rule 3) — 14 Sep 2026: dulu digerbang field bank_account_id dan
            # INSERT mentah (running_balance 0) mencari reference_type; kini pola uang muka pelanggan: cari bank_txn
            # dari JURNAL asli, helper kanonik menulis cermin penegasi yang terkait jurnal pembalik.
            if vd.get("journal_id") and reversal_id is not None:
                original_btxn = await conn.fetchrow(
                    """
                    SELECT id FROM bank_transactions
                    WHERE journal_id = $1 AND tenant_id = $2
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    vd["journal_id"],
                    ctx["tenant_id"],
                )
                if original_btxn:
                    await create_reversal_bank_transaction(
                        conn,
                        tenant_id=ctx["tenant_id"],
                        original_bank_transaction_id=original_btxn["id"],
                        reversal_journal_id=reversal_id,
                        created_by=ctx["user_id"],
                        description_prefix="[VOID]",
                    )

            # Mark deposit as VOID
            await conn.execute(
                """
                UPDATE vendor_deposits SET status = 'void', updated_at = NOW()
                WHERE id = $1
                """,
                deposit_id,
            )

            await catat_riwayat(conn, ctx["tenant_id"], "vendor_deposit", deposit_id, vd["deposit_number"], "VD_VOIDED", ctx.get("user_id"),
                                f"Uang muka pembelian {vd['deposit_number']} dibatalkan: {alasan}", meta={"reason": alasan},
                                source="api:vendor_deposits.void")
            logger.info(f"Vendor deposit voided: {deposit_id}")

            return VoidDepositResponse(
                deposit_id=deposit_id,
                deposit_number=vd["deposit_number"],
                status=VendorDepositStatus.void,
                reversal_journal_id=reversal_id,  # 23 Okt: dulu tak diisi (null) padahal jurnal pembalik ditulis
            )


# ============================================================================


SQL_PER_VENDOR = """
    SELECT vd.*, v.name AS vendor_name, v.code AS vendor_code, ba.account_name AS bank_account_name, po.po_number AS purchase_order_number
    FROM vendor_deposits vd
    JOIN vendors v ON v.id = vd.vendor_id AND v.tenant_id = vd.tenant_id
    LEFT JOIN bank_accounts ba ON ba.id = vd.bank_account_id AND ba.tenant_id = vd.tenant_id
    LEFT JOIN purchase_orders po ON po.id = vd.purchase_order_id AND po.tenant_id = vd.tenant_id
    WHERE vd.tenant_id = $1 AND vd.vendor_id = $2 AND ($3::text IS NULL OR vd.status = $3)
    ORDER BY vd.deposit_date DESC, vd.deposit_number DESC, vd.id"""


def sql_tersedia() -> str:
    """Uang muka vendor yang BISA diterapkan = predikat pill `belum_diterapkan` YANG SAMA (UMD.sql_aksi) atas agregat jurnal YANG SAMA."""
    return f"""
    SELECT vd.id, vd.deposit_number, vd.deposit_date, vd.amount, vd.reference, ag.sisa, ag.terpakai, ag.dikembalikan
    FROM vendor_deposits vd {UMD.sql_agregat('$3')}
    WHERE vd.tenant_id = $1 AND vd.vendor_id = $2 AND {UMD.sql_aksi(UMD.AKSI_BELUM_DITERAPKAN)}
    ORDER BY vd.deposit_date, vd.deposit_number, vd.id"""


async def _vendor_tenant(conn, tenant_id: str, vendor_id: UUID):
    v = await conn.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", vendor_id, tenant_id)
    if not v:
        raise HTTPException(status_code=404, detail="Vendor tidak ditemukan.")
    return v


@router.get("/by-vendor/{vendor_id}", response_model=VendorDepositsForVendorResponse)
async def get_vendor_deposits_for_vendor(
    request: Request,
    vendor_id: UUID,
    status: Optional[VendorDepositStatus] = None,
):
    """Semua uang muka satu vendor (P-U3b, buka parkir). Dulu fungsi DB get_vendor_deposits (RUSAK: BIGINT != numeric). Kini kueri langsung,
    tenant eksplisit; tiap baris memakai fakta_vd YANG SAMA dgn daftar/detail (sisa/terpakai/dikembalikan dari JURNAL).
    Total: deposit = Σ jumlah (tanpa void); applied = Σ terpakai + dikembalikan (jurnal); remaining = Σ sisa (jurnal, terbit)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        vendor = await _vendor_tenant(conn, ctx["tenant_id"], vendor_id)
        rows = await conn.fetch(SQL_PER_VENDOR, ctx["tenant_id"], vendor_id, status.value if status else None)
        akun = await UMD.akun_baca(conn, ctx["tenant_id"])
        fakta = await UMD.fakta_vd(conn, ctx["tenant_id"], akun, [r["id"] for r in rows])
        items, nol = [], Decimal("0")
        t_dep = t_pakai = t_sisa = nol
        for r in rows:
            f = fakta[r["id"]]
            items.append(VendorDepositResponse(**{
                **dict(r), "sisa": None if f["sisa"] is None else float(f["sisa"]), "terpakai": float(f["terpakai"]),
                "dikembalikan": float(f["dikembalikan"]), "sisa_keadaan": f["sisa_keadaan"], "status_text": f["status_text"],
                "position_text": f["position_text"], "aksi": f["aksi"]}))
            if r["status"] != "void":
                t_dep += Decimal(str(r["amount"] or 0))
                t_pakai += f["terpakai"] + f["dikembalikan"]
            if f["sisa"] is not None:
                t_sisa += f["sisa"]
        return VendorDepositsForVendorResponse(
            vendor_id=vendor_id, vendor_name=vendor["name"], items=items,
            total_deposits=_f(t_dep), total_applied=_f(t_pakai), total_remaining=_f(t_sisa))


@router.get("/available/{vendor_id}", response_model=AvailableDepositsResponse)
async def get_available_deposits_for_vendor(request: Request, vendor_id: UUID):
    """Uang muka vendor yang masih bisa diterapkan (P-U3b, buka parkir). Dulu fungsi DB get_available_vendor_deposits (RUSAK). Kini predikat
    pill `belum_diterapkan` (terbit, bukan draf/void, sisa JURNAL > 0) -- himpunan SAMA dgn ?aksi=belum_diterapkan pada daftar."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        vendor = await _vendor_tenant(conn, ctx["tenant_id"], vendor_id)
        akun = await UMD.akun_baca(conn, ctx["tenant_id"])
        rows = await conn.fetch(sql_tersedia(), ctx["tenant_id"], vendor_id, akun)
        items = [AvailableDepositItem(
            id=r["id"], deposit_number=r["deposit_number"], deposit_date=r["deposit_date"], amount=_f(r["amount"]),
            applied_amount=_f(Decimal(str(r["terpakai"] or 0)) + Decimal(str(r["dikembalikan"] or 0))),
            remaining_amount=_f(r["sisa"]), reference=r["reference"]) for r in rows]
        return AvailableDepositsResponse(
            vendor_id=vendor_id, vendor_name=vendor["name"], items=items,
            total_available=_f(sum((Decimal(str(r["sisa"] or 0)) for r in rows), Decimal("0"))))


# ============================================================================
# PRATINJAU + RIWAYAT + CALON FAKTUR (CW penuh Uang Muka Pembelian, 22 Okt 2026)
# Bentuk = kembar uang muka pelanggan (de24dcdb/d7dd4fd7): {deposit_id, action, can_proceed, blocks[], before, after, journals[],
# bank_transactions[], bills[], payload, preview:true}. READ ONLY tanpa kunci (pratinjau = baca; tulis memvalidasi ulang di bawah lock).
# Jurnal = RENCANA (journal_number null: nomor lahir saat tulis). Penentu = services/uang_muka_vendor_rencana (SAMA dengan tulis).
# ============================================================================


async def _baca_pratinjau(request: Request, isi) -> dict:
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tr = conn.transaction(readonly=True)
        await tr.start()
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            return await isi(conn, ctx)
        finally:
            await tr.rollback()  # pratinjau tak pernah menulis


async def _vd_baca(conn, ctx, deposit_id):
    vd = await conn.fetchrow("SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2", deposit_id, ctx["tenant_id"])
    if not vd:
        raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
    return vd


def _jawab(deposit_id, aksi: str, r: dict, payload) -> dict:
    return {"success": True, "data": {
        "deposit_id": str(deposit_id) if deposit_id else None, "action": aksi, "can_proceed": not r["blocks"],
        "blocks": R.untuk_respons(r["blocks"]), "before": r["before"], "after": r["after"], "journals": r["journals"],
        "bank_transactions": r["bank_transactions"], "bills": r["bills"], "payload": payload, "preview": True}}


@router.post("/preview")
async def preview_create_vendor_deposit(request: Request, data: VendorDepositCreate):
    """Pratinjau BUAT (badan = POST /vendor-deposits). simpan_dan_posting=true -> + rencana jurnal posting. Blok: vendor/PO/rekening/
    periode/peran akun. Nominal >0 & <=2 desimal dijaga skema (422)."""
    async def isi(conn, ctx):
        r = await R.rencana_buat(conn, ctx, data)
        return _jawab(None, "create", r, data.model_dump(mode="json"))
    return await _baca_pratinjau(request, isi)


@router.post("/{deposit_id}/post/preview")
async def preview_post_vendor_deposit(request: Request, deposit_id: UUID):
    """Pratinjau POSTING: Dr Uang Muka Pembelian (AP_PREPAID) / Cr Kas-Bank; cermin bank bila rekening. payload = {}."""
    async def isi(conn, ctx):
        vd = await _vd_baca(conn, ctx, deposit_id)
        return _jawab(deposit_id, "post", await R.rencana_posting(conn, ctx, vd, pratinjau=True), {})
    return await _baca_pratinjau(request, isi)


@router.post("/{deposit_id}/apply/preview")
async def preview_apply_vendor_deposit(request: Request, deposit_id: UUID, data: ApplyDepositRequest):
    """Pratinjau TERAPKAN ke tagihan: Dr Hutang Usaha / Cr Uang Muka Pembelian; bills[] sisa sebelum/sesudah (compute_ap_outstanding)."""
    async def isi(conn, ctx):
        vd = await _vd_baca(conn, ctx, deposit_id)
        r = await R.rencana_terapkan(conn, ctx, vd, data, data.applied_date or await tanggal_dokumen(conn, ctx["tenant_id"]), pratinjau=True)
        return _jawab(deposit_id, "apply", r, data.model_dump(mode="json"))
    return await _baca_pratinjau(request, isi)


@router.post("/{deposit_id}/applications/{application_id}/unapply/preview")
async def preview_unapply_vendor_deposit(request: Request, deposit_id: UUID, application_id: UUID):
    """Pratinjau LEPAS penerapan: jurnal pembalik (Dr Uang Muka Pembelian / Cr Hutang Usaha, source tagihan). Sudah dilepas = blok
    SUDAH_DILEPAS (tulis menjawab 200 idempoten). 404 bila penerapan bukan milik uang muka ini."""
    async def isi(conn, ctx):
        vd = await _vd_baca(conn, ctx, deposit_id)
        r = await R.rencana_lepas(conn, ctx, vd, application_id, await tanggal_dokumen(conn, ctx["tenant_id"]), kunci=False, pratinjau=True)
        if r["penerapan"] is None:
            raise HTTPException(status_code=404, detail="Penerapan uang muka tidak ditemukan.")
        return _jawab(deposit_id, "unapply", r, {})
    return await _baca_pratinjau(request, isi)


@router.post("/{deposit_id}/refund/preview")
async def preview_refund_vendor_deposit(request: Request, deposit_id: UUID, data: VendorDepositRefundCreate):
    """Pratinjau KEMBALIKAN: Dr Kas-Bank / Cr Uang Muka Pembelian. Rekening (bank_account_id) ATAU akun kas (account_id) WAJIB."""
    async def isi(conn, ctx):
        vd = await _vd_baca(conn, ctx, deposit_id)
        r = await R.rencana_kembali(conn, ctx, vd, data, pratinjau=True)
        return _jawab(deposit_id, "refund", r, data.model_dump(mode="json"))
    return await _baca_pratinjau(request, isi)


@router.post("/{deposit_id}/void/preview")
async def preview_void_vendor_deposit(request: Request, deposit_id: UUID, body: Optional[VoidDepositRequest] = None):
    """Pratinjau BATAL: alasan WAJIB (VOID_REASON_REQUIRED), penerapan aktif/refund menahan, jurnal pembalik posting + cermin bank."""
    alasan = ((body.reason if body else None) or "").strip()
    async def isi(conn, ctx):
        vd = await _vd_baca(conn, ctx, deposit_id)
        r = await R.rencana_batal(conn, ctx, vd, alasan, await tanggal_dokumen(conn, ctx["tenant_id"]), pratinjau=True)
        return _jawab(deposit_id, "void", r, {"reason": alasan or None})
    return await _baca_pratinjau(request, isi)


@router.post("/{deposit_id}/delete/preview")
async def preview_delete_vendor_deposit(request: Request, deposit_id: UUID):
    """Pratinjau HAPUS (draf saja). after.exists = false. payload = null (DELETE tanpa badan)."""
    async def isi(conn, ctx):
        vd = await _vd_baca(conn, ctx, deposit_id)
        return _jawab(deposit_id, "delete", await R.rencana_hapus(conn, ctx, vd, pratinjau=True), None)
    return await _baca_pratinjau(request, isi)


@router.get("/{deposit_id}/applicable-bills")
async def get_applicable_bills(request: Request, deposit_id: UUID):
    """Calon tagihan untuk TERAPKAN: tagihan vendor yang SAMA, terposting, sisa > 0 dari compute_ap_outstanding (sumber yang SAMA dgn
    penjaga /apply). Uang string 2 desimal (pola bundel). Urut jatuh tempo, nomor."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        vd = await _vd_baca(conn, ctx, deposit_id)
        rows = await conn.fetch(
            """SELECT b.id, b.invoice_number, b.issue_date, b.due_date, b.amount, o.outstanding
               FROM bills b JOIN compute_ap_outstanding($1) o ON o.bill_id = b.id
               WHERE b.tenant_id = $1 AND b.vendor_id = $2 AND b.status IN ('posted', 'partial') AND o.outstanding > 0
               ORDER BY b.due_date NULLS LAST, b.invoice_number""", ctx["tenant_id"], vd["vendor_id"])
        sisa_um = await UMV.sisa_jurnal(conn, ctx["tenant_id"], deposit_id) if vd["status"] in R.STATUS_TERBIT else Decimal("0")
        return {"success": True, "data": {
            "deposit_id": str(deposit_id), "deposit_remaining": f"{PD.r2(sisa_um):.2f}",
            "bills": [{"bill_id": str(r["id"]), "bill_number": r["invoice_number"], "issue_date": r["issue_date"].isoformat() if r["issue_date"] else None,
                       "due_date": r["due_date"].isoformat() if r["due_date"] else None, "total": f"{PD.r2(r['amount']):.2f}",
                       "remaining": f"{PD.r2(r['outstanding']):.2f}"} for r in rows]}}


@router.get("/{deposit_id}/history")
async def get_vendor_deposit_history(request: Request, deposit_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """Riwayat uang muka pembelian, bentuk SAMA dengan GET /sales-orders/{id}/history (services/um_vendor_riwayat)."""
    from ..services.dashboard_izin import boleh_baca
    from ..services.um_vendor_riwayat import riwayat_um_vendor
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_um_vendor(conn, ctx["tenant_id"], deposit_id, lambda m: boleh_baca(request, m), limit if isinstance(limit, int) else 200)
    if data is None:
        raise HTTPException(status_code=404, detail="Uang muka vendor tidak ditemukan.")
    return {"success": True, "data": data}
