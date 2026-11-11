"""Barang MATRIKS / varian (BUG-159 B1, 12 Nov 2026). Induk + anak per kombinasi ukuran x warna dalam SATU POST; aturan di
services/barang_varian (batas 2 dimensi / 20 nilai / 100 varian; tulis lewat jalur resmi tulis_barang_baru, atomik). Izin = modul Barang
(item R/C) lewat permission_middleware. Router lama (tak pernah dipasang) diganti seluruhnya."""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..schemas.barang_varian import (
    BuatMatriksRequest, BuatMatriksResponse, DaftarMatriksResponse, DetailMatriksResponse, PratinjauResponse, TambahVarianRequest,
)
from ..services import barang_varian as BV
from ..services import idem_buat

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool():
    """Pool koneksi tunggal (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    user = request.state.user
    if not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id")}


def _nama_pengguna(request: Request):
    u = request.state.user
    return u.get("username") or u.get("email")


def _galat_tulis(e: Exception, pesan: str) -> HTTPException:
    from .items import galat_nama_barang_dipakai, nama_barang_bentrok
    if nama_barang_bentrok(e):  # balapan serentak: indeks unik nama menolak -> 409 berkode, bukan 500
        return galat_nama_barang_dipakai()
    if getattr(e, "constraint_name", None) in ("idx_products_tenant_item_code", "idx_products_tenant_sku"):  # pengaman: penulis barang lain serentak
        return HTTPException(status_code=409, detail={"code": "KODE_BARANG_BENTROK", "message": "Kode barang otomatis bentrok dengan pembuatan barang lain yang serentak. Coba lagi."})
    logger.error(f"{pesan}: {e}", exc_info=True)
    return HTTPException(status_code=500, detail=pesan)


@router.get("", response_model=DaftarMatriksResponse)
async def daftar_matriks(request: Request, cari: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200),
                         offset: int = Query(0, ge=0)):
    """Daftar barang induk (matriks) + jumlah varian."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, **await BV.daftar(conn, ctx["tenant_id"], cari, limit, offset)}


@router.post("/preview", response_model=PratinjauResponse)
async def pratinjau_matriks(request: Request, body: BuatMatriksRequest):
    """Pratinjau BUAT: induk + varian yang akan terbentuk + nama yang bentrok. Baca saja (penentu SAMA dengan POST)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, "data": await BV.rencana(conn, ctx["tenant_id"], body)}


@router.post("", response_model=BuatMatriksResponse, status_code=201)
async def buat_matriks(request: Request, body: BuatMatriksRequest, response: Response = None):
    """Buat barang induk + semua varian (ukuran x warna) atomik. X-Idempotency-Key opsional (replay = respons asli)."""
    ctx = get_user_context(request)
    kunci = idem_buat.kunci_dari(request)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, kunci, "MATRIX_CREATE", "batch", lambda: body.model_dump(), response)
                if lama is not None:
                    return lama
                data = await BV.tulis(conn, ctx, body, ctx["user_id"], _nama_pengguna(request))
                resp = {"success": True, "message": f"Barang matriks '{body.name}' dibuat dengan {len(data['varian'])} varian.", "data": data}
                return await idem_buat.simpan(conn, ctx, kp, sd, "MATRIX_CREATE", resp, None)
    except HTTPException:
        raise
    except Exception as e:
        raise _galat_tulis(e, "Gagal membuat barang matriks.") from e


@router.post("/{item_id}/variants/preview", response_model=PratinjauResponse)
async def pratinjau_tambah_varian(request: Request, item_id: UUID, body: TambahVarianRequest):
    """Pratinjau TAMBAH varian ke induk yang ada (baca saja)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, "data": await BV.rencana_tambah(conn, ctx["tenant_id"], item_id, body.dimensi)}


@router.post("/{item_id}/variants", response_model=BuatMatriksResponse, status_code=201)
async def tambah_varian(request: Request, item_id: UUID, body: TambahVarianRequest, response: Response = None):
    """Tambah varian ke induk (dimensi harus sama dengan varian yang ada; harga/satuan/kategori mengikuti induk)."""
    ctx = get_user_context(request)
    kunci = idem_buat.kunci_dari(request)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                kp, sd, lama = await idem_buat.mulai_aksi(conn, ctx, kunci, "MATRIX_VARIAN", str(item_id), lambda: body.model_dump(), response)
                if lama is not None:
                    return lama
                data = await BV.tambah(conn, ctx, item_id, body.dimensi, ctx["user_id"], _nama_pengguna(request))
                resp = {"success": True, "message": f"{len(data['varian'])} varian ditambahkan ke '{data['name']}'.", "data": data}
                return await idem_buat.simpan(conn, ctx, kp, sd, "MATRIX_VARIAN", resp, None)
    except HTTPException:
        raise
    except Exception as e:
        raise _galat_tulis(e, "Gagal menambah varian.") from e


@router.get("/{item_id}", response_model=DetailMatriksResponse)
async def detail_matriks(request: Request, item_id: UUID):
    """Detail barang induk + seluruh varian (atribut, kode, harga)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return {"success": True, "data": await BV.detail(conn, ctx["tenant_id"], item_id)}
