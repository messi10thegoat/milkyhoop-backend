"""Rute Milkybook v2 di bawah /api/milkybook/v2. Pengguna + tenant dari token; flag `milkybook_v2` mati = 404.
Izin per rute di ROUTE_PERMISSIONS (READ-authz: GET tak terpetakan = tertutup)."""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException, Query, Request, Response

from . import buat_penawaran, cari_pelanggan

FLAG = "milkybook_v2"


def buat_router_v2(*, flag_fn: Callable, conn_fn: Callable, izin_fn: Callable) -> APIRouter:
    """flag_fn(tenant_id) -> awaitable bool; conn_fn() -> async context manager koneksi DB;
    izin_fn(user, jalur[], metode) -> awaitable {jalur: bool} (peta rute yang sama dengan middleware)."""
    r = APIRouter()

    async def _pengguna(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user or not user.get("tenant_id") or not user.get("user_id"):
            raise HTTPException(401, "Tidak terautentikasi")
        if not await flag_fn(user["tenant_id"]):
            raise HTTPException(404, "Tidak ditemukan")
        return user

    @r.get("/pelanggan")
    async def cari_pelanggan_v2(request: Request, q: str = Query("", max_length=80),
                                batas: int = Query(6, ge=1, le=10)):
        user = await _pengguna(request)
        async with conn_fn() as conn:
            return {"items": await cari_pelanggan.cari(conn, user["tenant_id"], q, batas)}

    @r.post("/penawaran")
    async def buat_penawaran_v2(request: Request, body: buat_penawaran.BuatPenawaranV2, response: Response):
        """Simpan draf: pelanggan baru + barang baru + penawaran SATU transaksi (03 §3); X-Idempotency-Key wajib per draf."""
        from ...services import idem_buat
        user = await _pengguna(request)
        kunci = idem_buat.kunci_dari(request)
        async with conn_fn() as conn:
            return await buat_penawaran.buat(conn, user, body, kunci, izin_fn=izin_fn, response=response)

    return r
