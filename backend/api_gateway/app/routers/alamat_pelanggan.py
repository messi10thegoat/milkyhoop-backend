"""Rute alamat pelanggan PER BARIS (V428; kontrak backend/docs/KONTRAK-alamat-terstruktur-20261016.md).

Tak ada rute "ganti seluruh daftar". Izin: baca = customer R, tulis (tambah/ubah/bawaan/hapus) = customer U
(mengubah master pelanggan, bukan menghapus pelanggan). Aturan & cermin kolom datar: services/alamat_pelanggan.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from ..schemas.customers import AlamatPelangganMasuk, BawaanAlamat
from ..services import alamat_pelanggan as AP
from .customers import get_pool, get_user_context

logger = logging.getLogger(__name__)
router = APIRouter()


def _pelaku(request: Request):
    u = getattr(request.state, "user", None) or {}
    return u.get("user_id"), (u.get("username") or u.get("email"))


@router.get("/{customer_id}/addresses")
async def daftar_alamat(request: Request, customer_id: UUID):
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM customers WHERE id = $1 AND tenant_id = $2", customer_id, ctx["tenant_id"]):
            raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan.")
        return {"success": True, "data": await AP.daftar(conn, ctx["tenant_id"], customer_id)}


@router.post("/{customer_id}/addresses", status_code=201)
async def tambah_alamat(request: Request, customer_id: UUID, body: AlamatPelangganMasuk):
    ctx = get_user_context(request)
    oleh, nama = _pelaku(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            d = await AP.tambah(conn, ctx["tenant_id"], customer_id, body.model_dump(include=set(AP.MEDAN) & body.model_fields_set),
                                tagih=bool(body.is_default_billing), kirim=bool(body.is_default_shipping), oleh=oleh, nama=nama)
    return {"success": True, "data": d}


@router.patch("/{customer_id}/addresses/{address_id}")
async def ubah_alamat(request: Request, customer_id: UUID, address_id: UUID, body: AlamatPelangganMasuk):
    ctx = get_user_context(request)
    oleh, nama = _pelaku(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            d = await AP.ubah(conn, ctx["tenant_id"], customer_id, address_id,
                              body.model_dump(include=set(AP.MEDAN) & body.model_fields_set), oleh=oleh, nama=nama)
            if body.is_default_billing or body.is_default_shipping:
                d = await AP.jadikan_bawaan(conn, ctx["tenant_id"], customer_id, address_id, bool(body.is_default_billing),
                                            bool(body.is_default_shipping), oleh=oleh, nama=nama)
    return {"success": True, "data": d}


@router.post("/{customer_id}/addresses/{address_id}/default")
async def jadikan_alamat_bawaan(request: Request, customer_id: UUID, address_id: UUID, body: BawaanAlamat):
    if not (body.billing or body.shipping):
        raise HTTPException(status_code=422, detail="Pilih bawaan tagih dan/atau kirim.")
    ctx = get_user_context(request)
    oleh, nama = _pelaku(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            d = await AP.jadikan_bawaan(conn, ctx["tenant_id"], customer_id, address_id, bool(body.billing),
                                        bool(body.shipping), oleh=oleh, nama=nama)
    return {"success": True, "data": d}


@router.delete("/{customer_id}/addresses/{address_id}")
async def hapus_alamat(request: Request, customer_id: UUID, address_id: UUID):
    ctx = get_user_context(request)
    oleh, nama = _pelaku(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await AP.nonaktifkan(conn, ctx["tenant_id"], customer_id, address_id, oleh=oleh, nama=nama)
    return {"success": True, "data": {"id": str(address_id)}}
