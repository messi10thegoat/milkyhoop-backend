"""Rute percakapan Milkybook (kontrak rancangan §11.10). Didaftarkan lewat app/milkybook/pasang.py (cabang aktivasi).

- Pengguna, tenant, dan izin dari token (request.state.user, diisi middleware autentikasi); badan tak pernah memuat tenant.
- Flag tenant `milkybook_v1` mati = 404.
- Penyimpanan sesi bersama (Redis; gateway 2 worker), kunci per sesi: giliran berbarengan untuk sesi sama = 409.
- v1 = JSON sekali jawab; SSE menyusul dengan objek peristiwa yang sama.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .pelaksana import PelaksanaASGI
from .penyimpanan import SesiSibuk

FLAG = "milkybook_v1"
MAKS_TAMPILAN = 400  # peristiwa tampilan per sesi (sesi hidup 6 jam); lebih = yang tertua dibuang


class PesanMasuk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teks: str


def buat_router(putaran_fn: Callable, penyimpanan, *, app_fn: Callable, izin_fn_untuk: Callable,
                flag_fn: Callable, hari_ini_fn: Callable) -> APIRouter:
    """putaran_fn() -> Putaran (dibuat malas: kunci API & model dari env saat permintaan pertama);
    app_fn() -> app FastAPI; izin_fn_untuk(user) -> IzinFn; flag_fn(tenant_id) -> awaitable bool;
    hari_ini_fn(tenant_id) -> awaitable date (tanggal bisnis tenant)."""
    r = APIRouter()

    async def _pengguna(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user or not user.get("tenant_id") or not user.get("user_id"):
            raise HTTPException(401, "Tidak terautentikasi")
        if not await flag_fn(user["tenant_id"]):
            raise HTTPException(404, "Tidak ditemukan")
        return user

    def _pelaksana(request: Request) -> PelaksanaASGI:
        return PelaksanaASGI(app_fn(), request.headers.get("Authorization", ""))

    async def _jalankan(user, sesi_id, fn, masuk: dict):
        try:
            async with penyimpanan.kunci(user["tenant_id"], user["user_id"], sesi_id):
                s = await penyimpanan.ambil(user["tenant_id"], user["user_id"], sesi_id)
                if s is None:
                    raise HTTPException(404, "Sesi tidak ditemukan")
                s.tampilan.append(masuk)
                try:
                    peristiwa = await fn(s)
                    s.tampilan.extend(e for e in peristiwa if e.get("jenis") != "selesai")
                finally:
                    del s.tampilan[:-MAKS_TAMPILAN]
                    await penyimpanan.simpan(s)  # simpan juga bila galat di tengah: riwayat append-only tetap utuh
                return {"peristiwa": peristiwa}
        except SesiSibuk:
            raise HTTPException(409, "Percakapan ini sedang diproses. Tunggu sebentar.")

    @r.post("/sesi")
    async def sesi_baru(request: Request):
        user = await _pengguna(request)
        hari_ini = await hari_ini_fn(user["tenant_id"])
        s = await penyimpanan.buat(user["tenant_id"], user["user_id"], konteks=f"Hari ini {hari_ini.isoformat()}.",
                                   hari_ini=hari_ini)
        return {"sesi_id": s.sesi_id}

    @r.get("/sesi/{sesi_id}")
    async def pulihkan(sesi_id: str, request: Request):
        """#7: tampilan percakapan untuk dimuat ulang FE (tanpa kunci giliran; baca saja). 404 = sesi kedaluwarsa/tenant lain."""
        user = await _pengguna(request)
        s = await penyimpanan.ambil(user["tenant_id"], user["user_id"], sesi_id)
        if s is None:
            raise HTTPException(404, "Sesi tidak ditemukan")
        k = s.kartu_menunggu()
        return {"sesi_id": s.sesi_id, "peristiwa": s.tampilan, "kartu_menunggu": k.kartu_id if k else None}

    @r.post("/sesi/{sesi_id}/pesan")
    async def pesan(sesi_id: str, body: PesanMasuk, request: Request):
        user = await _pengguna(request)
        return await _jalankan(user, sesi_id, lambda s: putaran_fn().giliran(
            s, body.teks, _pelaksana(request), izin_fn_untuk(user)), {"jenis": "pengguna", "teks": body.teks})

    async def _sesi_kartu(user, kartu_id):
        sid = await penyimpanan.sesi_untuk_kartu(user["tenant_id"], user["user_id"], kartu_id)
        if not sid:
            raise HTTPException(404, "Kartu tidak ditemukan")
        return sid

    @r.post("/kartu/{kartu_id}/setuju")
    async def setuju(kartu_id: str, request: Request):
        user = await _pengguna(request)
        sid = await _sesi_kartu(user, kartu_id)
        return await _jalankan(user, sid, lambda s: putaran_fn().setujui(
            s, kartu_id, _pelaksana(request), izin_fn_untuk(user)), {"jenis": "aksi_pengguna", "aksi": "setuju", "kartu_id": kartu_id})

    @r.post("/kartu/{kartu_id}/tolak")
    async def tolak(kartu_id: str, request: Request):
        user = await _pengguna(request)
        sid = await _sesi_kartu(user, kartu_id)
        return await _jalankan(user, sid, lambda s: putaran_fn().tolak(s, kartu_id),
                               {"jenis": "aksi_pengguna", "aksi": "tolak", "kartu_id": kartu_id})

    return r
