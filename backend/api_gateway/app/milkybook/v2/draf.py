"""Draf percakapan Milkybook v2 (paket v2 03 §1, tabel V469 milkybook_v2_percakapan): simpan/muat draf per percakapan
supaya bisa dilanjutkan dari perangkat lain dan dari Riwayat/Lanjutkan.

- Pemilik = (tenant_id, user_id) dari token. Percakapan orang lain / tenant lain / kedaluwarsa = 404 (tidak dibedakan).
- Umur 7 hari sejak diubah: saringan baca + hapus sungguhan (SQL_BUANG_BASI) tiap pengguna menulis.
- Konkurensi optimistis: PUT membawa versi yang dibaca; versi basi = 409 VERSI_BASI + versi kini (dua perangkat tak
  saling menimpa diam-diam).
- Maks satu draf aktif (kolom draf) + draf yang di-park (parkir, maks MAKS_PARKIR).
- Isi draf = data pelanggan: tidak pernah ditulis ke log."""
from __future__ import annotations

import json
import uuid
from typing import Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

UMUR_HARI = 7
MAKS_DAFTAR = 50
MAKS_PARKIR = 10
MAKS_BAIT = 262144  # == chk_mb2_percakapan_ukuran (V469)

TAHAP = Literal["cust", "custConfirm", "custNew", "custHp", "clarify", "items", "more", "itemPrice", "tax",
                "valid", "dp", "review", "saved"]


class DrafV2(BaseModel):
    """Bentuk QuoteDraft 03 §1. Medan yang dikenal diperiksa tipenya; medan lain dibiarkan (FE masih bergerak)."""
    model_config = ConfigDict(extra="allow")
    stage: TAHAP
    customer: Optional[dict] = None
    lines: list[dict] = Field(default_factory=list, max_length=200)
    newMasters: list[dict] = Field(default_factory=list, max_length=50)
    savedQuoteId: Optional[str] = None


class BuatPercakapan(BaseModel):
    judul: Optional[str] = Field(None, max_length=120)


class SimpanDraf(BaseModel):
    versi: int = Field(..., ge=1)
    judul: Optional[str] = Field(None, max_length=120)
    draf: Optional[DrafV2] = None                                    # None = draf dibatalkan / belum ada
    parkir: list[DrafV2] = Field(default_factory=list, max_length=MAKS_PARKIR)


SAH = f"diubah >= NOW() - INTERVAL '{UMUR_HARI} days'"
SQL_BUANG_BASI = f"DELETE FROM milkybook_v2_percakapan WHERE tenant_id = $1 AND user_id = $2 AND diubah < NOW() - INTERVAL '{UMUR_HARI} days'"
SQL_BUAT = ("INSERT INTO milkybook_v2_percakapan (id, tenant_id, user_id, judul) VALUES ($1, $2, $3, $4) "
            "RETURNING id, judul, draf, parkir, versi, dibuat, diubah")
SQL_AMBIL = ("SELECT id, judul, draf, parkir, versi, dibuat, diubah FROM milkybook_v2_percakapan "
             f"WHERE id = $1 AND tenant_id = $2 AND user_id = $3 AND {SAH}")
SQL_SIMPAN = ("UPDATE milkybook_v2_percakapan SET draf = $5::jsonb, parkir = $6::jsonb, judul = COALESCE($7, judul), "
              "versi = versi + 1, diubah = NOW() "
              f"WHERE id = $1 AND tenant_id = $2 AND user_id = $3 AND versi = $4 AND {SAH} "
              "RETURNING id, judul, draf, parkir, versi, dibuat, diubah")
SQL_VERSI = f"SELECT versi FROM milkybook_v2_percakapan WHERE id = $1 AND tenant_id = $2 AND user_id = $3 AND {SAH}"
SQL_DAFTAR = ("SELECT id, judul, draf, jsonb_array_length(parkir) AS n_parkir, versi, dibuat, diubah "
              f"FROM milkybook_v2_percakapan WHERE tenant_id = $1 AND user_id = $2 AND {SAH} "
              f"ORDER BY diubah DESC LIMIT {MAKS_DAFTAR}")


def _pemilik(user: dict) -> tuple:
    return str(user["tenant_id"]), str(user["user_id"])


def _id_sah(pid: str) -> uuid.UUID:
    try:
        return uuid.UUID(pid)
    except (ValueError, TypeError):
        raise HTTPException(404, "Percakapan tidak ditemukan")


def _json(x):
    return json.loads(x) if isinstance(x, str) else x


def _waktu(t):
    return t.isoformat() if t is not None else None


def ke_hasil(b) -> dict:
    return {"id": str(b["id"]), "judul": b["judul"], "draf": _json(b["draf"]), "parkir": _json(b["parkir"]) or [],
            "versi": b["versi"], "dibuat": _waktu(b["dibuat"]), "diubah": _waktu(b["diubah"])}


def ringkas(draf) -> Optional[dict]:
    """Untuk Riwayat/Lanjutkan: tanpa harga, tanpa isi baris (daftar ringan)."""
    d = _json(draf)
    if not d:
        return None
    c = d.get("customer") or {}
    return {"stage": d.get("stage"), "pelanggan": c.get("name"), "jumlah_baris": len(d.get("lines") or []),
            "tersimpan": bool(d.get("savedQuoteId"))}


def ke_daftar(b) -> dict:
    return {"id": str(b["id"]), "judul": b["judul"], "draf": ringkas(b["draf"]), "parkir": b["n_parkir"],
            "versi": b["versi"], "dibuat": _waktu(b["dibuat"]), "diubah": _waktu(b["diubah"])}


def isi_simpan(body: SimpanDraf) -> tuple:
    """-> (draf_json | None, parkir_json); ukuran > MAKS_BAIT = 413 sebelum menyentuh DB."""
    draf = json.dumps(body.draf.model_dump(), ensure_ascii=False) if body.draf is not None else None
    parkir = json.dumps([p.model_dump() for p in body.parkir], ensure_ascii=False)
    if len((draf or "").encode()) + len(parkir.encode()) > MAKS_BAIT:
        raise HTTPException(413, detail={"code": "DRAF_TERLALU_BESAR", "message": "Draf terlalu besar"})
    return draf, parkir


async def daftar(conn, user: dict) -> list:
    return [ke_daftar(b) for b in await conn.fetch(SQL_DAFTAR, *_pemilik(user))]


async def buat(conn, user: dict, body: BuatPercakapan) -> dict:
    t, u = _pemilik(user)
    async with conn.transaction():
        await conn.execute(SQL_BUANG_BASI, t, u)
        return ke_hasil(await conn.fetchrow(SQL_BUAT, uuid.uuid4(), t, u, body.judul))


async def ambil(conn, user: dict, pid: str) -> dict:
    b = await conn.fetchrow(SQL_AMBIL, _id_sah(pid), *_pemilik(user))
    if not b:
        raise HTTPException(404, "Percakapan tidak ditemukan")
    return ke_hasil(b)


async def simpan(conn, user: dict, pid: str, body: SimpanDraf) -> dict:
    pid = _id_sah(pid)
    t, u = _pemilik(user)
    draf, parkir = isi_simpan(body)
    async with conn.transaction():
        await conn.execute(SQL_BUANG_BASI, t, u)
        b = await conn.fetchrow(SQL_SIMPAN, pid, t, u, body.versi, draf, parkir, body.judul)
        if b:
            return ke_hasil(b)
        kini = await conn.fetchval(SQL_VERSI, pid, t, u)
    if kini is None:
        raise HTTPException(404, "Percakapan tidak ditemukan")
    raise HTTPException(409, detail={"code": "VERSI_BASI", "message": "Draf sudah diubah di tempat lain",
                                     "versi_kini": kini})
