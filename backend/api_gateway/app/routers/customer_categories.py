"""BUG-338A: /api/customer-categories -- Kategori Pelanggan (master; pelanggan menempel; kategori menunjuk SATU daftar harga). Penentu: services/kategori_pelanggan."""
import logging
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..services import kategori_pelanggan as KP

router = APIRouter()
logger = logging.getLogger(__name__)
GAGAL = "Terjadi kesalahan. Silakan coba lagi."


class BuatKategori(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    price_list_id: Optional[UUID] = None


class UbahKategori(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    price_list_id: Optional[UUID] = None
    is_active: Optional[bool] = None


async def get_pool() -> asyncpg.Pool:
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _tid(request: Request) -> str:
    u = getattr(request.state, "user", None)
    if not u or not u.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return u["tenant_id"]


@router.get("")
async def daftar_kategori(request: Request):
    tid = _tid(request)
    try:
        async with (await get_pool()).acquire() as conn:
            return {"success": True, "data": {"items": await KP.daftar(conn, tid)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.post("")
async def buat_kategori(request: Request, body: BuatKategori):
    tid = _tid(request)
    try:
        async with (await get_pool()).acquire() as conn:
            async with conn.transaction():
                kid = await KP.buat(conn, tid, body.name, body.price_list_id)
                return {"success": True, "message": "Kategori pelanggan dibuat.", "data": KP.bentuk(await KP.satu(conn, tid, kid))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.patch("/{kategori_id}")
async def ubah_kategori(request: Request, kategori_id: UUID, body: UbahKategori):
    tid = _tid(request)
    try:
        async with (await get_pool()).acquire() as conn:
            async with conn.transaction():
                await KP.ubah(conn, tid, kategori_id, body.model_dump(exclude_unset=True))
                return {"success": True, "message": "Kategori pelanggan diubah.", "data": KP.bentuk(await KP.satu(conn, tid, kategori_id))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.delete("/{kategori_id}")
async def hapus_kategori(request: Request, kategori_id: UUID):
    tid = _tid(request)
    try:
        async with (await get_pool()).acquire() as conn:
            async with conn.transaction():
                await KP.hapus(conn, tid, kategori_id)
                return {"success": True, "message": "Kategori pelanggan dihapus.", "data": {"id": str(kategori_id)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)


@router.post("/{kategori_id}/delete/preview")
async def pratinjau_hapus_kategori(request: Request, kategori_id: UUID, body: Optional[dict] = None):
    """Pratinjau hapus (read-only, nol tulis): penentu SAMA dengan DELETE (KP.rencana_hapus). Pola POST /price-lists/{id}/delete/preview."""
    tid = _tid(request)
    try:
        async with (await get_pool()).acquire() as conn:
            tr = conn.transaction(readonly=True)
            await tr.start()
            try:
                h = await KP.rencana_hapus(conn, tid, kategori_id)
            finally:
                await tr.rollback()
        if h is None:
            raise HTTPException(status_code=404, detail="Kategori pelanggan tidak ditemukan.")
        return {"success": True, "data": {**h, "payload": body or {}}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=GAGAL)
