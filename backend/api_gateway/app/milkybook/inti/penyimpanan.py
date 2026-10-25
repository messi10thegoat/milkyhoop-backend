"""Penyimpanan sesi Milkybook.

Diukur 24 Okt (read-only): gateway berjalan `uvicorn ... --workers 2` -> sesi di memori satu proses TIDAK cukup
(giliran berikutnya bisa jatuh ke worker lain). Produksi = Redis (settings.REDIS_URL, pola rate_limit_middleware).

Kunci (ruang = tenant x pengguna; sesi orang lain = tidak ditemukan):
  milkybook:sesi:{tenant}:{user}:{sesi_id}   JSON sesi, TTL UMUR_SESI_DETIK (diperpanjang tiap simpan)
  milkybook:kartu:{tenant}:{user}:{kartu_id} -> sesi_id
  milkybook:kunci:{tenant}:{user}:{sesi_id}  kunci giliran (SET NX PX) -> dua permintaan sesi sama tidak berbarengan
Riwayat disimpan apa adanya (append-only; blok thinking + signature utuh).
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Dict, Optional

from .kartu import Kartu, StatusKartu
from .putaran import Sesi

UMUR_SESI_DETIK = 6 * 3600
KUNCI_GILIRAN_MS = 180_000


class SesiSibuk(RuntimeError):
    """Giliran lain untuk sesi yang sama sedang berjalan (router -> 409)."""


def sesi_ke_dict(s: Sesi) -> dict:
    return {
        "sesi_id": s.sesi_id, "tenant_id": s.tenant_id, "user_id": s.user_id, "messages": s.messages,
        "kartu": {k: _kartu_ke_dict(v) for k, v in s.kartu.items()}, "sumber_angka": s.sumber_angka,
        "usage": s.usage, "biaya_usd": s.biaya_usd, "konteks": s.konteks,
        "hari_ini": s.hari_ini.isoformat() if s.hari_ini else None, "mode_tulis": s.mode_tulis,
        "tampilan": s.tampilan,
    }


def sesi_dari_dict(d: dict) -> Sesi:
    s = Sesi(sesi_id=d["sesi_id"], tenant_id=d["tenant_id"], user_id=d["user_id"], messages=d["messages"],
             sumber_angka=d.get("sumber_angka", []), usage=d.get("usage", {}), biaya_usd=d.get("biaya_usd", 0.0),
             konteks=d.get("konteks", ""), mode_tulis=d.get("mode_tulis", False), tampilan=d.get("tampilan", []))
    s.hari_ini = date.fromisoformat(d["hari_ini"]) if d.get("hari_ini") else None
    s.kartu = {k: _kartu_dari_dict(v) for k, v in d.get("kartu", {}).items()}
    return s


def _kartu_ke_dict(k: Kartu) -> dict:
    return {"sesi_id": k.sesi_id, "tool": k.tool, "metode": k.metode, "jalur": k.jalur, "payload": k.payload,
            "pratinjau": k.pratinjau, "judul": k.judul, "kartu_id": k.kartu_id, "kunci": k.kunci,
            "status": k.status.value, "dibuat": k.dibuat, "hasil": k.hasil, "tool_use_id": k.tool_use_id}


def _kartu_dari_dict(d: dict) -> Kartu:
    k = Kartu(sesi_id=d["sesi_id"], tool=d["tool"], metode=d["metode"], jalur=d["jalur"], payload=d["payload"],
              pratinjau=d.get("pratinjau"), judul=d.get("judul", ""), kartu_id=d["kartu_id"], kunci=d["kunci"],
              status=StatusKartu(d["status"]), dibuat=d.get("dibuat", time.time()), hasil=d.get("hasil"),
              tool_use_id=d.get("tool_use_id"))
    return k  # badan + payload_hash dihitung ulang deterministik dari payload (__post_init__)


class PenyimpananSesiMemori:
    """Satu proses (tes / dev). Antarmuka sama dengan Redis."""

    def __init__(self):
        self._isi: Dict[str, tuple] = {}
        self._kartu: Dict[str, str] = {}
        self._kunci: set = set()

    async def buat(self, tenant_id: str, user_id: str, konteks: str = "", hari_ini: Optional[date] = None) -> Sesi:
        s = Sesi(sesi_id=uuid.uuid4().hex, tenant_id=tenant_id, user_id=user_id, konteks=konteks)
        s.hari_ini = hari_ini
        await self.simpan(s)
        return s

    async def ambil(self, tenant_id: str, user_id: str, sesi_id: str) -> Optional[Sesi]:
        v = self._isi.get(f"{tenant_id}:{user_id}:{sesi_id}")
        if v is None or time.time() - v[1] > UMUR_SESI_DETIK:
            return None
        return sesi_dari_dict(json.loads(v[0]))

    async def simpan(self, s: Sesi) -> None:
        self._isi[f"{s.tenant_id}:{s.user_id}:{s.sesi_id}"] = (json.dumps(sesi_ke_dict(s), default=str), time.time())
        for kid in s.kartu:
            self._kartu[f"{s.tenant_id}:{s.user_id}:{kid}"] = s.sesi_id

    async def sesi_untuk_kartu(self, tenant_id: str, user_id: str, kartu_id: str) -> Optional[str]:
        return self._kartu.get(f"{tenant_id}:{user_id}:{kartu_id}")

    @asynccontextmanager
    async def kunci(self, tenant_id: str, user_id: str, sesi_id: str):
        k = f"{tenant_id}:{user_id}:{sesi_id}"
        if k in self._kunci:
            raise SesiSibuk()
        self._kunci.add(k)
        try:
            yield
        finally:
            self._kunci.discard(k)


class PenyimpananSesiRedis(PenyimpananSesiMemori):
    def __init__(self, klien, awalan: str = "milkybook"):
        self._r = klien  # redis.asyncio.Redis (decode_responses=True)
        self._a = awalan

    def _k(self, jenis, tenant_id, user_id, x):
        return f"{self._a}:{jenis}:{tenant_id}:{user_id}:{x}"

    async def ambil(self, tenant_id, user_id, sesi_id):
        mentah = await self._r.get(self._k("sesi", tenant_id, user_id, sesi_id))
        return sesi_dari_dict(json.loads(mentah)) if mentah else None

    async def simpan(self, s):
        await self._r.set(self._k("sesi", s.tenant_id, s.user_id, s.sesi_id), json.dumps(sesi_ke_dict(s), default=str),
                          ex=UMUR_SESI_DETIK)
        for kid in s.kartu:
            await self._r.set(self._k("kartu", s.tenant_id, s.user_id, kid), s.sesi_id, ex=UMUR_SESI_DETIK)

    async def sesi_untuk_kartu(self, tenant_id, user_id, kartu_id):
        return await self._r.get(self._k("kartu", tenant_id, user_id, kartu_id))

    @asynccontextmanager
    async def kunci(self, tenant_id, user_id, sesi_id):
        k = self._k("kunci", tenant_id, user_id, sesi_id)
        token = uuid.uuid4().hex
        if not await self._r.set(k, token, nx=True, px=KUNCI_GILIRAN_MS):
            raise SesiSibuk()
        try:
            yield
        finally:
            # lepas hanya bila masih milik kita (kunci bisa kedaluwarsa lalu diambil giliran lain)
            if await self._r.get(k) == token:
                await self._r.delete(k)
