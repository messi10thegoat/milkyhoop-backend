"""
Stock Transfers Router -- Pemindahan Barang antargudang
=======================================================
SATU LANGKAH (12 Okt 2026, kontrak R2): draf -> selesai (kirim+terima atomik, 2 baris kartu stok per barang) -> void (pembalik).
TANPA JURNAL (akun persediaan per produk, WAC tak berubah) -- lihat services/pemindahan_barang.py.
Di balik flag `pemindahan_barang`; tanpa flag semua rute TULIS tetap 409 STOCK_TRANSFER_NOT_AVAILABLE (keadaan 23 Sep).
Dua langkah (ship/receive) BELUM: butuh akun transit + jurnal (putusan P5): 409 jujur.
"""
from datetime import date
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Depends

from ..services import pemindahan_barang as pb
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
):
    """List stock transfers"""
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
async def create_stock_transfer(request: Request, body: CreateStockTransferRequest):
    """Buat pemindahan (draf). Nilai TIDAK diambil dari klien: WAC server saat diselesaikan."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
            tid = await pb.buat_draf(conn, ctx, pb.PemindahanIn(**body.model_dump()))
            return CreateStockTransferResponse(data=await _detail(conn, ctx["tenant_id"], tid))


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
