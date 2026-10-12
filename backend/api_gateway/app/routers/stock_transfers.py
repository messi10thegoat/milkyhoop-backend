"""
Stock Transfers Router -- Pemindahan Barang antargudang
=======================================================
SATU LANGKAH (12 Okt 2026, kontrak R2): draf -> selesai (kirim+terima atomik, 2 baris kartu stok per barang) -> void (pembalik).
TANPA JURNAL (akun persediaan per produk, WAC tak berubah) -- lihat services/pemindahan_barang.py.
Di balik flag `pemindahan_barang`; tanpa flag semua rute TULIS tetap 409 STOCK_TRANSFER_NOT_AVAILABLE (keadaan 23 Sep).
Dua langkah (ship/receive) BELUM: butuh akun transit + jurnal (putusan P5): 409 jujur.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response, Depends
from pydantic import BaseModel, Field

from ..services import pemindahan_barang as pb
from ..services import idem_buat
from ..services.so_riwayat import riwayat_pemindahan
from ..utils.tanggal_tenant import tanggal_dokumen
from ..schemas.stock_transfers import (
    CancelTransferRequest,
    CancelTransferResponse,
    CompleteTransferResponse,
    CreateStockTransferRequest,
    CreateStockTransferResponse,
    InTransitResponse,
    InTransitSummary,
    ReceiveTransferRequest,
    ReceiveTransferResponse,
    ShipTransferRequest,
    ShipTransferResponse,
    StockTransferData,
    StockTransferDetailData,
    StockTransferDetailResponse,
    StockTransferItemData,
    StockTransferListResponse,
    UpdateStockTransferRequest,
    VoidTransferRequest,
)

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


async def _butuh_flag(request: Request):
    """Rute tulis hanya untuk tenant ber-flag `pemindahan_barang`; selain itu 409 jujur SEBELUM badan handler jalan
    (tak ada baris setengah jadi). Koneksi dilepas sebelum handler memakai koneksinya sendiri (Law 32: tak bersarang)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await pb.pastikan_aktif(conn, ctx["tenant_id"])


async def _dua_langkah_belum():
    raise HTTPException(
        status_code=409,
        detail={"code": "STOCK_TRANSFER_DUA_LANGKAH_BELUM_TERSEDIA",
                "message": "Pemindahan dua langkah (kirim lalu terima) belum tersedia; gunakan Selesaikan (satu langkah)."},
    )


_TAHAN = [Depends(_butuh_flag)]
_DUA_LANGKAH = [Depends(_butuh_flag), Depends(_dua_langkah_belum)]


# ============================================================================
# ENDPOINTS
# ============================================================================


async def _detail(conn, tenant_id: str, transfer_id: UUID) -> StockTransferDetailData:
    row = await conn.fetchrow(
        """
        SELECT st.*, fw.name as from_warehouse_name, tw.name as to_warehouse_name
        FROM stock_transfers st
        LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
        LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
        WHERE st.id = $1 AND st.tenant_id = $2
        """,
        transfer_id,
        tenant_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Stock transfer not found")
    items = await conn.fetch(
        "SELECT * FROM stock_transfer_items WHERE stock_transfer_id = $1 ORDER BY line_number ASC", transfer_id
    )
    return StockTransferDetailData(**dict(row), items=[StockTransferItemData(**dict(i)) for i in items])


@router.get("", response_model=StockTransferListResponse)
async def list_stock_transfers(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status: Optional[str] = None,
    from_warehouse_id: Optional[UUID] = None,
    to_warehouse_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    search: Optional[str] = Query(None, max_length=100),
):
    """List stock transfers. `search`: nomor PB, referensi, nomor SO, nama barang (tanpa peka huruf)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        where_clauses = ["st.tenant_id = $1"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if status:
            where_clauses.append(f"st.status = ${param_idx}")
            params.append(status)
            param_idx += 1

        if from_warehouse_id:
            where_clauses.append(f"st.from_warehouse_id = ${param_idx}")
            params.append(from_warehouse_id)
            param_idx += 1

        if to_warehouse_id:
            where_clauses.append(f"st.to_warehouse_id = ${param_idx}")
            params.append(to_warehouse_id)
            param_idx += 1

        if date_from:
            where_clauses.append(f"st.transfer_date >= ${param_idx}")
            params.append(date_from)
            param_idx += 1

        if date_to:
            where_clauses.append(f"st.transfer_date <= ${param_idx}")
            params.append(date_to)
            param_idx += 1

        if search and search.strip():
            kata = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where_clauses.append(
                f"""(st.transfer_number ILIKE '%' || ${param_idx} || '%' OR st.reference ILIKE '%' || ${param_idx} || '%'
                    OR EXISTS (SELECT 1 FROM sales_orders so WHERE so.id = st.sales_order_id AND so.tenant_id = st.tenant_id
                               AND so.order_number ILIKE '%' || ${param_idx} || '%')
                    OR EXISTS (SELECT 1 FROM stock_transfer_items sti WHERE sti.stock_transfer_id = st.id
                               AND (sti.item_name ILIKE '%' || ${param_idx} || '%' OR sti.item_code ILIKE '%' || ${param_idx} || '%')))""")
            params.append(kata)
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM stock_transfers st WHERE {where_sql}", *params
        )

        rows = await conn.fetch(
            f"""
            SELECT st.*,
                   fw.name as from_warehouse_name,
                   tw.name as to_warehouse_name
            FROM stock_transfers st
            LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
            LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
            WHERE {where_sql}
            ORDER BY st.created_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params,
            limit,
            skip,
        )

        data = [StockTransferData(**dict(row)) for row in rows]

        return StockTransferListResponse(
            data=data, total=total, has_more=(skip + limit) < total
        )


@router.get("/in-transit", response_model=InTransitResponse)
async def get_in_transit_transfers(request: Request):
    """Get all in-transit transfers"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        rows = await conn.fetch(
            """
            SELECT st.*,
                   fw.name as from_warehouse_name,
                   tw.name as to_warehouse_name
            FROM stock_transfers st
            LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
            LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
            WHERE st.tenant_id = $1 AND st.status = 'in_transit'
            ORDER BY st.shipped_date ASC
            """,
            ctx["tenant_id"],
        )

        data = [StockTransferData(**dict(row)) for row in rows]

        summary_row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::INT as total_transfers,
                COALESCE(SUM(total_items), 0)::INT as total_items,
                COALESCE(SUM(total_value), 0)::BIGINT as total_value,
                MIN(shipped_date) as oldest_transfer_date
            FROM stock_transfers
            WHERE tenant_id = $1 AND status = 'in_transit'
            """,
            ctx["tenant_id"],
        )

        return InTransitResponse(
            summary=InTransitSummary(**dict(summary_row)), data=data
        )


def _f(x):
    return float(x) if isinstance(x, Decimal) else x


def _pv(r: dict) -> dict:
    """Bentuk JSON pratinjau (Decimal -> float, UUID -> str)."""
    def baris(ln):
        return {k: (str(v) if isinstance(v, UUID) else _f(v)) for k, v in ln.items()}
    return {
        "ok": r["ok"],
        "blocks": [{"code": b["code"], "message": b["message"], "item_id": b.get("item_id")} for b in r["blocks"]],
        "lines": [baris(ln) for ln in r["lines"]],
        "from": r.get("dari"), "to": r.get("ke"),
        "total_quantity": _f(r["total_quantity"]), "total_value": _f(r["total_value"]),
        "sales_order": r.get("sales_order"), "catatan_fase": r.get("catatan_fase"),
        "transfer_number": r.get("transfer_number"), "ulang": bool(r.get("ulang")), "payload": r.get("payload"),
    }


class AlasanIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


@router.post("/preview", dependencies=_TAHAN)
async def preview_stock_transfer(request: Request, body: pb.PratinjauIn):
    """Tinjau pemindahan BARU (draf / simpan-selesai): angka SERVER, nol tulis, 200 walau diblok, SEMUA blok sekaligus.
    Kalkulator = jalur tulis (`pemindahan_barang._rencana`)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        return {"success": True, "data": _pv(await pb.pratinjau_baru(conn, ctx, body))}


@router.get("/summary", dependencies=_TAHAN)
async def summary_stock_transfers(request: Request):
    """Ringkasan: draf, selesai bulan ini, void bulan ini (bulan = bulan bisnis tenant; tanggal = tanggal pemindahan)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        hari = await tanggal_dokumen(conn, ctx["tenant_id"])
        awal = hari.replace(day=1)
        akhir = date(awal.year + (awal.month == 12), awal.month % 12 + 1, 1)
        r = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE status = 'draft') AS draft,
                      count(*) FILTER (WHERE status = 'completed' AND received_date >= $2 AND received_date < $3) AS selesai_bulan_ini,
                      count(*) FILTER (WHERE status = 'voided' AND COALESCE(received_date, transfer_date) >= $2
                                         AND COALESCE(received_date, transfer_date) < $3) AS void_bulan_ini,
                      COALESCE(SUM(total_value) FILTER (WHERE status = 'completed' AND received_date >= $2
                                         AND received_date < $3), 0) AS nilai_selesai_bulan_ini
               FROM stock_transfers WHERE tenant_id = $1""", ctx["tenant_id"], awal, akhir)
        return {"success": True, "data": {"bulan": awal.strftime("%Y-%m"), "draft": r["draft"],
                                           "selesai_bulan_ini": r["selesai_bulan_ini"], "void_bulan_ini": r["void_bulan_ini"],
                                           "nilai_selesai_bulan_ini": _f(r["nilai_selesai_bulan_ini"])}}


@router.post("/create-and-complete", response_model=CompleteTransferResponse, dependencies=_TAHAN)
async def create_and_complete_stock_transfer(request: Request, response: Response, body: CreateStockTransferRequest):
    """ATOMIK: buat + selesaikan dalam SATU transaksi (pola Accurate: Simpan = Selesai). Gagal -> tak ada draf yatim.
    X-Idempotency-Key (alias Idempotency-Key): kunci sama + isi sama = respons asli; isi beda = 409 IDEMPOTENCY_KEY_REUSED.
    Izin: aksi P (proses) -- sengaja jalur TERPISAH dari POST "" (C) agar izin tak bisa dilompati lewat ?param."""
    ctx = get_user_context(request)
    kunci = idem_buat.kunci_dari(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sidik, lama = await idem_buat.mulai(conn, ctx, kunci, "STOCK_TRANSFER_CC", body, "stock_transfers", "transfer", response)
            if lama is not None:
                return lama
            hasil = await pb.buat_dan_selesaikan(conn, ctx, pb.PemindahanIn(**body.model_dump()))
            resp = CompleteTransferResponse(
                data=await _detail(conn, ctx["tenant_id"], hasil["id"]), ulang=hasil["ulang"],
                catatan_fase=hasil["catatan_fase"], message="Pemindahan barang dibuat dan diselesaikan")
            return await idem_buat.simpan(conn, ctx, kp, sidik, "STOCK_TRANSFER_CC", resp, hasil["id"])


@router.post("/{transfer_id}/complete/preview", dependencies=_TAHAN)
async def preview_complete_stock_transfer(request: Request, transfer_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        return {"success": True, "data": _pv(await pb.pratinjau_selesai(conn, ctx, transfer_id))}


@router.post("/{transfer_id}/void/preview", dependencies=_TAHAN)
async def preview_void_stock_transfer(request: Request, transfer_id: UUID, body: AlasanIn = None):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        return {"success": True, "data": _pv(await pb.pratinjau_void(conn, ctx, transfer_id, body.reason if body else None))}


@router.post("/{transfer_id}/cancel/preview", dependencies=_TAHAN)
async def preview_cancel_stock_transfer(request: Request, transfer_id: UUID, body: AlasanIn = None):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        return {"success": True, "data": _pv(await pb.pratinjau_batal(conn, ctx, transfer_id, body.reason if body else None))}


@router.get("/{transfer_id}/history", dependencies=_TAHAN)
async def history_stock_transfer(request: Request, transfer_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """Riwayat pemindahan (bentuk riwayat SO): dibuat, diubah, selesai, void/batal + aktor. Disaring izin BACA (`omitted`)."""
    from ..services.dashboard_izin import boleh_baca
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_pemindahan(conn, ctx["tenant_id"], transfer_id, lambda m: boleh_baca(request, m), limit)
    if data is None:
        raise HTTPException(status_code=404, detail="Stock transfer not found")
    return {"success": True, "data": data}


@router.get("/{transfer_id}", response_model=StockTransferDetailResponse)
async def get_stock_transfer(request: Request, transfer_id: UUID):
    """Get stock transfer details with items"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            """
            SELECT st.*,
                   fw.name as from_warehouse_name,
                   tw.name as to_warehouse_name
            FROM stock_transfers st
            LEFT JOIN warehouses fw ON st.from_warehouse_id = fw.id
            LEFT JOIN warehouses tw ON st.to_warehouse_id = tw.id
            WHERE st.id = $1 AND st.tenant_id = $2
            """,
            transfer_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Stock transfer not found")

        items = await conn.fetch(
            """
            SELECT * FROM stock_transfer_items
            WHERE stock_transfer_id = $1
            ORDER BY line_number ASC
            """,
            transfer_id,
        )

        data = StockTransferDetailData(
            **dict(row), items=[StockTransferItemData(**dict(item)) for item in items]
        )

        return StockTransferDetailResponse(data=data)




@router.post("", response_model=CreateStockTransferResponse, dependencies=_TAHAN)
async def create_stock_transfer(request: Request, response: Response, body: CreateStockTransferRequest):
    """Buat pemindahan (draf). Nilai TIDAK diambil dari klien: WAC server saat diselesaikan.
    X-Idempotency-Key (alias Idempotency-Key): kunci sama + isi sama = respons asli (X-Idempotent-Replay); isi beda = 409."""
    ctx = get_user_context(request)
    kunci = idem_buat.kunci_dari(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            kp, sidik, lama = await idem_buat.mulai(conn, ctx, kunci, "STOCK_TRANSFER", body, "stock_transfers", "transfer", response)
            if lama is not None:
                return lama
            tid = await pb.buat_draf(conn, ctx, pb.PemindahanIn(**body.model_dump()))
            resp = CreateStockTransferResponse(data=await _detail(conn, ctx["tenant_id"], tid))
            return await idem_buat.simpan(conn, ctx, kp, sidik, "STOCK_TRANSFER", resp, tid)


@router.patch("/{transfer_id}", response_model=StockTransferDetailResponse, dependencies=_TAHAN)
async def update_stock_transfer(request: Request, transfer_id: UUID, body: UpdateStockTransferRequest):
    """Ubah draf: kolom yang tak dikirim dipertahankan; items (bila ada) menggantikan seluruh baris."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            lama = await conn.fetchrow(
                "SELECT * FROM stock_transfers WHERE id = $1 AND tenant_id = $2", transfer_id, ctx["tenant_id"])
            if not lama:
                raise HTTPException(status_code=404, detail="Stock transfer not found")
            if body.items is None:
                baris = await conn.fetch(
                    "SELECT item_id, quantity_requested, notes FROM stock_transfer_items WHERE stock_transfer_id = $1 "
                    "ORDER BY line_number", transfer_id)
                items = [{"item_id": b["item_id"], "quantity_requested": b["quantity_requested"], "notes": b["notes"]}
                         for b in baris]
            else:
                items = [i.model_dump() for i in body.items]
            data = pb.PemindahanIn(
                from_warehouse_id=body.from_warehouse_id or lama["from_warehouse_id"],
                to_warehouse_id=body.to_warehouse_id or lama["to_warehouse_id"],
                transfer_date=body.transfer_date, expected_date=body.expected_date or lama["expected_date"],
                reference=body.reference if body.reference is not None else lama["reference"],
                notes=body.notes if body.notes is not None else lama["notes"],
                sales_order_id=body.sales_order_id or lama["sales_order_id"], items=items)
            await pb.ubah_draf(conn, ctx, transfer_id, data)
            return StockTransferDetailResponse(data=await _detail(conn, ctx["tenant_id"], transfer_id))


@router.delete("/{transfer_id}", dependencies=_TAHAN)
async def delete_stock_transfer(request: Request, transfer_id: UUID):
    """Hapus DRAF (yang sudah selesai di-void, bukan dihapus)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            await pb.hapus_draf(conn, ctx, transfer_id)
    return {"success": True, "message": "Stock transfer deleted"}


@router.post("/{transfer_id}/complete", response_model=CompleteTransferResponse, dependencies=_TAHAN)
async def complete_stock_transfer(request: Request, transfer_id: UUID):
    """Selesaikan: kirim+terima atomik. Idempoten (ulang -> 200, tanpa tulis kedua)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            hasil = await pb.selesaikan(conn, ctx, transfer_id)
            return CompleteTransferResponse(
                data=await _detail(conn, ctx["tenant_id"], transfer_id), ulang=hasil["ulang"],
                catatan_fase=hasil["catatan_fase"])


@router.post("/{transfer_id}/void", response_model=CompleteTransferResponse, dependencies=_TAHAN)
async def void_stock_transfer(request: Request, transfer_id: UUID, body: VoidTransferRequest):
    """Void pemindahan selesai: pembalik ledger kedua kaki; ditolak bila stok tujuan sudah terpakai."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            hasil = await pb.void(conn, ctx, transfer_id, body.reason)
            return CompleteTransferResponse(
                data=await _detail(conn, ctx["tenant_id"], transfer_id), message="Pemindahan barang di-void",
                ulang=hasil["ulang"], catatan_fase=hasil["catatan_fase"])


@router.post("/{transfer_id}/ship", response_model=ShipTransferResponse, dependencies=_DUA_LANGKAH)
async def ship_stock_transfer(request: Request, transfer_id: UUID, body: ShipTransferRequest = None):
    """Dua langkah belum tersedia (putusan P5): selalu 409 sebelum badan jalan."""
    raise HTTPException(status_code=409, detail="dua langkah belum tersedia")


@router.post("/{transfer_id}/receive", response_model=ReceiveTransferResponse, dependencies=_DUA_LANGKAH)
async def receive_stock_transfer(request: Request, transfer_id: UUID, body: ReceiveTransferRequest = None):
    """Dua langkah belum tersedia (putusan P5): selalu 409 sebelum badan jalan."""
    raise HTTPException(status_code=409, detail="dua langkah belum tersedia")


@router.post("/{transfer_id}/cancel", response_model=CancelTransferResponse, dependencies=_TAHAN)
async def cancel_stock_transfer(request: Request, transfer_id: UUID, body: CancelTransferRequest):
    """Batalkan DRAF (tanpa ledger). Pemindahan yang sudah selesai dibatalkan lewat /void."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            await pb.batalkan_draf(conn, ctx, transfer_id, body.reason)
    return CancelTransferResponse()
