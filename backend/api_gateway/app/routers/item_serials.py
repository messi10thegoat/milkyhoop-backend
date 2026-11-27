"""
Item Serials Router (Serial S4, 27 Nov 2026; DESAIN-serial-20261127 §4-§5)
===========================================================================
BACA nomor seri: daftar, detail + riwayat, tersedia per barang+gudang (pemilih dokumen keluar), per gudang.
TULIS lepas lama (buat, bulk, transfer, adjust) DIPARKIR 409 berarah: nomor seri lahir & bergerak HANYA lewat dokumen
(Penerimaan Barang / Penyesuaian / Pengiriman / Faktur / Retur / Pemindahan) melalui pintu kartu stok (serial_gerak).
Teks tampil dari server (services/serial_baca).
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..services import serial_baca as SB
from ..services.fitur_parkir import fitur_belum_tersedia_dengan

router = APIRouter()

PESAN_LAHIR = ("Nomor seri tidak dibuat manual. Isi nomor seri di Penerimaan Barang, Penyesuaian Persediaan (masuk), "
               "atau Retur Penjualan.")
PESAN_GERAK = ("Nomor seri tidak dipindah/diubah manual. Pakai Pemindahan Barang untuk pindah gudang, "
               "atau Penyesuaian Persediaan (keluar) untuk rusak/hilang.")
_parkir_lahir = fitur_belum_tersedia_dengan(PESAN_LAHIR)
_parkir_gerak = fitur_belum_tersedia_dengan(PESAN_GERAK)


async def get_pool():
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def _tid(request: Request) -> str:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return request.state.user["tenant_id"]


def _status(status: Optional[str]) -> Optional[str]:
    if status is not None and status not in SB.STATUS_SAH:
        raise HTTPException(status_code=422, detail={"code": "STATUS_TIDAK_DIKENAL", "message": "Status nomor seri tidak dikenal."})
    return status


async def _baca(request: Request, fn, *a, **kw):
    tid = _tid(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            return await fn(conn, tid, *a, **kw)


@router.get("")
async def list_item_serials(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    item_id: Optional[UUID] = None,
    warehouse_id: Optional[UUID] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    """Daftar nomor seri (cari nomor/nama/kode barang; saring barang, gudang, status). Teks tampil dari server."""
    hasil = await _baca(request, SB.daftar, item_id=item_id, warehouse_id=warehouse_id, status=_status(status),
                        cari=search, skip=skip, limit=limit)
    return {"success": True, **hasil}


@router.get("/search")
async def search_serial_number(request: Request, serial_number: str = Query(..., min_length=1)):
    """Cari nomor seri (alias daftar ?search=)."""
    hasil = await _baca(request, SB.daftar, cari=serial_number, limit=50)
    return {"success": True, "query": serial_number, **hasil}


@router.get("/items/{item_id}/serials/available")
async def get_available_serials_for_item(
    request: Request, item_id: UUID, warehouse_id: UUID = Query(...), search: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
):
    """Pemilih dokumen keluar: nomor seri 'Tersedia' barang ini di gudang itu."""
    return {"success": True, **await _baca(request, SB.tersedia, item_id, warehouse_id, cari=search, limit=limit)}


@router.get("/warehouses/{warehouse_id}/serials")
async def get_warehouse_serials(
    request: Request, warehouse_id: UUID, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query("available"),
):
    """Nomor seri di satu gudang (bawaan: Tersedia)."""
    hasil = await _baca(request, SB.daftar, warehouse_id=warehouse_id, status=_status(status), skip=skip, limit=limit)
    return {"success": True, **hasil}


@router.get("/{serial_id}")
async def get_item_serial(request: Request, serial_id: UUID):
    """Detail nomor seri + riwayat gerak (urut waktu)."""
    d = await _baca(request, SB.detail, serial_id)
    if not d:
        raise HTTPException(status_code=404, detail={"code": "SERIAL_TIDAK_DITEMUKAN", "message": "Nomor seri tidak ditemukan."})
    return {"success": True, "data": d}


@router.get("/{serial_id}/history")
async def get_serial_history(request: Request, serial_id: UUID):
    """Riwayat gerak satu nomor seri."""
    d = await _baca(request, SB.detail, serial_id)
    if not d:
        raise HTTPException(status_code=404, detail={"code": "SERIAL_TIDAK_DITEMUKAN", "message": "Nomor seri tidak ditemukan."})
    return {"success": True, "serial_id": d["id"], "serial_number": d["serial_number"], "data": d["riwayat"], "total": len(d["riwayat"])}


# ---- TULIS lepas lama: DIPARKIR (409 berarah sebelum badan dibaca) ----

@router.post("", dependencies=[Depends(_parkir_lahir)])
async def create_item_serial():
    """Diparkir S4: nomor seri lahir hanya dari dokumen masuk."""


@router.post("/bulk", dependencies=[Depends(_parkir_lahir)])
async def bulk_create_serials():
    """Diparkir S4: nomor seri lahir hanya dari dokumen masuk."""


@router.post("/{serial_id}/transfer", dependencies=[Depends(_parkir_gerak)])
async def transfer_serial(serial_id: UUID):
    """Diparkir S4: pindah gudang lewat Pemindahan Barang."""


@router.post("/{serial_id}/adjust", dependencies=[Depends(_parkir_gerak)])
async def adjust_serial_status(serial_id: UUID):
    """Diparkir S4: ubah status lewat Penyesuaian Persediaan."""
