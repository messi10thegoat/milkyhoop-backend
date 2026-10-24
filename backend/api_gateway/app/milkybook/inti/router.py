"""Rute percakapan Milkybook (kontrak rancangan §11.5). BELUM didaftarkan di main.py.

Sebelum didaftarkan (tiket terpisah, izin pemilik + jendela):
- tambah pola izin `/api/milkybook/*` di permission_middleware + inventaris rute (tests/unit),
- terapkan migrasi asisten_aksi, ganti AuditMemori -> AuditPostgres,
- ganti PenyimpananSesiMemori -> penyimpanan bersama (Redis) bila worker > 1,
- kunci API Milkybook terpisah di .env server.

SSE token-per-token menyusul T3; sekarang respons = daftar peristiwa per giliran (JSON).
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .pelaksana import PelaksanaASGI
from .putaran import Putaran, Sesi

FLAG = "milkybook_v1"
UMUR_SESI_DETIK = 6 * 3600


class PesanMasuk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teks: str


class PenyimpananSesiMemori:
    """Satu proses saja. Kunci = (tenant, user, sesi): sesi orang lain = 404 (bukan 403)."""

    def __init__(self):
        self._isi: Dict[tuple, tuple] = {}

    def buat(self, tenant_id: str, user_id: str, konteks: str = "") -> Sesi:
        s = Sesi(sesi_id=uuid.uuid4().hex, tenant_id=tenant_id, user_id=user_id, konteks=konteks)
        self._isi[(tenant_id, user_id, s.sesi_id)] = (s, time.time())
        return s

    def ambil(self, tenant_id: str, user_id: str, sesi_id: str) -> Optional[Sesi]:
        v = self._isi.get((tenant_id, user_id, sesi_id))
        if v is None or time.time() - v[1] > UMUR_SESI_DETIK:
            return None
        return v[0]

    def cari_kartu(self, tenant_id: str, user_id: str, kartu_id: str) -> Optional[Sesi]:
        for (t, u, _), (s, _) in self._isi.items():
            if t == tenant_id and u == user_id and kartu_id in s.kartu:
                return s
        return None


def buat_router(putaran: Putaran, penyimpanan: PenyimpananSesiMemori, *, app_fn: Callable, izin_fn_untuk: Callable,
                flag_fn: Callable) -> APIRouter:
    """app_fn() -> app FastAPI; izin_fn_untuk(user) -> IzinFn; flag_fn(tenant_id) -> awaitable bool."""
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

    @r.post("/sesi")
    async def sesi_baru(request: Request):
        user = await _pengguna(request)
        s = penyimpanan.buat(user["tenant_id"], user["user_id"], konteks=f"Hari ini {time.strftime('%Y-%m-%d')}.")
        return {"sesi_id": s.sesi_id}

    @r.post("/sesi/{sesi_id}/pesan")
    async def pesan(sesi_id: str, body: PesanMasuk, request: Request):
        user = await _pengguna(request)
        s = penyimpanan.ambil(user["tenant_id"], user["user_id"], sesi_id)
        if s is None:
            raise HTTPException(404, "Sesi tidak ditemukan")
        return {"peristiwa": await putaran.giliran(s, body.teks, _pelaksana(request), izin_fn_untuk(user))}

    @r.post("/kartu/{kartu_id}/setuju")
    async def setuju(kartu_id: str, request: Request):
        user = await _pengguna(request)
        s = penyimpanan.cari_kartu(user["tenant_id"], user["user_id"], kartu_id)
        if s is None:
            raise HTTPException(404, "Kartu tidak ditemukan")
        return {"peristiwa": await putaran.setujui(s, kartu_id, _pelaksana(request), izin_fn_untuk(user))}

    @r.post("/kartu/{kartu_id}/tolak")
    async def tolak(kartu_id: str, request: Request):
        user = await _pengguna(request)
        s = penyimpanan.cari_kartu(user["tenant_id"], user["user_id"], kartu_id)
        if s is None:
            raise HTTPException(404, "Kartu tidak ditemukan")
        return {"peristiwa": await putaran.tolak(s, kartu_id)}

    return r
