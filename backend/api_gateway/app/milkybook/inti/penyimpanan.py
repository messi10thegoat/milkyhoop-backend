"""Penyimpanan sesi Milkybook.

Diukur 24 Okt (read-only): gateway berjalan `uvicorn ... --workers 2` -> sesi di memori satu proses TIDAK cukup
(giliran berikutnya bisa jatuh ke worker lain). Produksi = Redis (settings.REDIS_URL, pola rate_limit_middleware).

Kunci (ruang = tenant x pengguna; sesi orang lain = tidak ditemukan):
  milkybook:sesi:{tenant}:{user}:{sesi_id}   JSON sesi, TTL UMUR_SESI_DETIK (diperpanjang tiap simpan)
  milkybook:kartu:{tenant}:{user}:{kartu_id} -> sesi_id
  milkybook:kunci:{tenant}:{user}:{sesi_id}  kunci giliran (SET NX PX) -> dua permintaan sesi sama tidak berbarengan
  milkybook:antre:{tenant}:{user}:{sesi_id}  LIST kotak masuk (#10) JSON {id_pesan, teks}; maks aliran.MAKS_ANTRE
  milkybook:dibaca:{tenant}:{user}:{sesi_id} SET id_pesan antre yang sudah dibaca model (DELETE -> 409, ulang -> duplikat)
Riwayat disimpan apa adanya (append-only; blok thinking + signature utuh).
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Dict, Optional

from .aliran import MAKS_ANTRE
from .kartu import Kartu, StatusKartu
from .putaran import Sesi

UMUR_SESI_DETIK = 6 * 3600
KUNCI_GILIRAN_MS = 180_000


class SesiSibuk(RuntimeError):
    """Giliran lain untuk sesi yang sama sedang berjalan (router -> 409 / pesan masuk antrean)."""


class AntreanPenuh(RuntimeError):
    """Kotak masuk sesi penuh (router -> 429 kode antrean_penuh)."""


class _Pegangan:
    """Dipegang selama giliran; perpanjang() menyegarkan TTL kunci (giliran lanjutan dari kotak masuk)."""

    def __init__(self, perpanjang=None):
        self._p = perpanjang

    async def perpanjang(self) -> None:
        if self._p:
            await self._p()


def sesi_ke_dict(s: Sesi) -> dict:
    return {
        "sesi_id": s.sesi_id, "tenant_id": s.tenant_id, "user_id": s.user_id, "messages": s.messages,
        "kartu": {k: _kartu_ke_dict(v) for k, v in s.kartu.items()}, "sumber_angka": s.sumber_angka,
        "usage": s.usage, "biaya_usd": s.biaya_usd, "konteks": s.konteks,
        "hari_ini": s.hari_ini.isoformat() if s.hari_ini else None, "mode_tulis": s.mode_tulis,
        "tampilan": s.tampilan, "diproses_id": s.diproses_id[-200:], "tulis_tertahan": s.tulis_tertahan, "ubah_idem": s.ubah_idem,
    }


def sesi_dari_dict(d: dict) -> Sesi:
    s = Sesi(sesi_id=d["sesi_id"], tenant_id=d["tenant_id"], user_id=d["user_id"], messages=d["messages"],
             sumber_angka=d.get("sumber_angka", []), usage=d.get("usage", {}), biaya_usd=d.get("biaya_usd", 0.0),
             konteks=d.get("konteks", ""), mode_tulis=d.get("mode_tulis", False), tampilan=d.get("tampilan", []),
             diproses_id=d.get("diproses_id", []), tulis_tertahan=d.get("tulis_tertahan", False), ubah_idem=d.get("ubah_idem", {}))
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
        self._antre: Dict[str, list] = {}
        self._dibaca: Dict[str, set] = {}

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
            yield _Pegangan()
        finally:
            self._kunci.discard(k)

    async def sibuk(self, tenant_id, user_id, sesi_id) -> bool:
        return f"{tenant_id}:{user_id}:{sesi_id}" in self._kunci

    # ---- kotak masuk (#10)
    async def antre_tambah(self, tenant_id, user_id, sesi_id, id_pesan: str, teks: str) -> int:
        q = self._antre.setdefault(f"{tenant_id}:{user_id}:{sesi_id}", [])
        if len(q) >= MAKS_ANTRE:
            raise AntreanPenuh()
        q.append({"id_pesan": id_pesan, "teks": teks})
        return len(q)

    async def antre_daftar(self, tenant_id, user_id, sesi_id) -> list:
        return [{"id_pesan": m["id_pesan"], "posisi": i + 1}
                for i, m in enumerate(self._antre.get(f"{tenant_id}:{user_id}:{sesi_id}", []))]

    async def antre_ambil_semua(self, tenant_id, user_id, sesi_id) -> list:
        k = f"{tenant_id}:{user_id}:{sesi_id}"
        q, self._antre[k] = self._antre.get(k, []), []
        self._dibaca.setdefault(k, set()).update(m["id_pesan"] for m in q)
        return q

    async def antre_status(self, tenant_id, user_id, sesi_id, id_pesan) -> Optional[str]:
        k = f"{tenant_id}:{user_id}:{sesi_id}"
        if any(m["id_pesan"] == id_pesan for m in self._antre.get(k, [])):
            return "antre"
        return "dibaca" if id_pesan in self._dibaca.get(k, set()) else None

    async def antre_batal(self, tenant_id, user_id, sesi_id, id_pesan) -> Optional[str]:
        """-> 'dibatalkan' | 'sudah_dibaca' | None (tak dikenal)."""
        k = f"{tenant_id}:{user_id}:{sesi_id}"
        q = self._antre.get(k, [])
        for m in list(q):
            if m["id_pesan"] == id_pesan:
                q.remove(m)
                return "dibatalkan"
        return "sudah_dibaca" if id_pesan in self._dibaca.get(k, set()) else None


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

        async def perpanjang():
            if await self._r.get(k) == token:
                await self._r.set(k, token, px=KUNCI_GILIRAN_MS)
        try:
            yield _Pegangan(perpanjang)
        finally:
            # lepas hanya bila masih milik kita (kunci bisa kedaluwarsa lalu diambil giliran lain)
            if await self._r.get(k) == token:
                await self._r.delete(k)

    async def sibuk(self, tenant_id, user_id, sesi_id) -> bool:
        return bool(await self._r.get(self._k("kunci", tenant_id, user_id, sesi_id)))

    # ---- kotak masuk (#10): LIST per sesi; setiap elemen dipop atomik (LPOP), batal = LREM nilai persis
    async def antre_tambah(self, tenant_id, user_id, sesi_id, id_pesan, teks):
        k = self._k("antre", tenant_id, user_id, sesi_id)
        nilai = json.dumps({"id_pesan": id_pesan, "teks": teks}, ensure_ascii=False)
        n = await self._r.rpush(k, nilai)
        if n > MAKS_ANTRE:  # dua penambah berbarengan: yang melampaui dicabut lagi
            await self._r.lrem(k, 1, nilai)
            raise AntreanPenuh()
        await self._r.expire(k, UMUR_SESI_DETIK)
        return n

    async def _antre_isi(self, tenant_id, user_id, sesi_id) -> list:
        return [json.loads(x) for x in await self._r.lrange(self._k("antre", tenant_id, user_id, sesi_id), 0, -1)]

    async def antre_daftar(self, tenant_id, user_id, sesi_id):
        return [{"id_pesan": m["id_pesan"], "posisi": i + 1} for i, m in enumerate(await self._antre_isi(tenant_id, user_id, sesi_id))]

    async def antre_ambil_semua(self, tenant_id, user_id, sesi_id):
        k = self._k("antre", tenant_id, user_id, sesi_id)
        kd = self._k("dibaca", tenant_id, user_id, sesi_id)
        hasil = []
        while (x := await self._r.lpop(k)) is not None:
            m = json.loads(x)
            hasil.append(m)
            await self._r.sadd(kd, m["id_pesan"])
        if hasil:
            await self._r.expire(kd, UMUR_SESI_DETIK)
        return hasil

    async def antre_status(self, tenant_id, user_id, sesi_id, id_pesan):
        if any(m["id_pesan"] == id_pesan for m in await self._antre_isi(tenant_id, user_id, sesi_id)):
            return "antre"
        return "dibaca" if await self._r.sismember(self._k("dibaca", tenant_id, user_id, sesi_id), id_pesan) else None

    async def antre_batal(self, tenant_id, user_id, sesi_id, id_pesan):
        k = self._k("antre", tenant_id, user_id, sesi_id)
        for x in await self._r.lrange(k, 0, -1):
            if json.loads(x)["id_pesan"] == id_pesan and await self._r.lrem(k, 1, x):
                return "dibatalkan"
        return "sudah_dibaca" if await self._r.sismember(self._k("dibaca", tenant_id, user_id, sesi_id), id_pesan) else None
