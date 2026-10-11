"""Cara serah (Ship Method) + preferensi pengiriman + bawaan pengiriman (11 Okt 2026, kontrak pengiriman serah).

GET/POST   /api/metode-serah            daftar / tambah (nama + jenis dasar kurir|antar_sendiri|ambil_sendiri)
PATCH      /api/metode-serah/{id}       nama / jenis / aktif / urutan (jenis terkunci bila sudah dipakai Surat Jalan)
GET/PUT    /api/pengiriman/preferensi   lokasi + cara serah bawaan tenant
GET        /api/default-dokumen/pengiriman?sales_order_id=   bawaan yang dipakai pratinjau (lokasi + SUMBER, cara, alamat)

Semua di belakang flag `pengiriman_serah` (tanpa flag -> 409 SERAH_NONAKTIF). NOL BUKU: tak ada jurnal/stok/HPP.
Law 24: tenant_id eksplisit tiap SQL. Daftar kecil per tenant; nonaktifkan, bukan hapus (FK RESTRICT dari snapshot)."""
import logging
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..services import pengiriman_serah as S
from ..services.so_pengiriman import _gudang

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    uid = user.get("user_id") or user.get("id")
    return {"tenant_id": tenant_id, "user_id": str(uid) if uid else None}


def _baris(r) -> dict:
    return {"id": str(r["id"]), "nama": r["nama"], "jenis": r["jenis"], "aktif": r["aktif"], "urutan": r["urutan"]}


class MetodeBaru(BaseModel):
    nama: str = Field(..., min_length=1, max_length=100)
    jenis: str
    urutan: int = 0


class MetodeUbah(BaseModel):
    nama: Optional[str] = Field(None, min_length=1, max_length=100)
    jenis: Optional[str] = None
    aktif: Optional[bool] = None
    urutan: Optional[int] = None


class PreferensiUbah(BaseModel):
    default_lokasi_kirim_id: Optional[str] = None
    default_metode_serah_id: Optional[str] = None


def _jenis_sah(j: str) -> str:
    if j not in S.JENIS:
        raise HTTPException(status_code=422, detail={
            "code": "METODE_SERAH_JENIS", "message": "Jenis cara serah: kurir, antar_sendiri, atau ambil_sendiri."})
    return j


def _nama_dipakai() -> HTTPException:
    return HTTPException(status_code=409, detail={
        "code": "METODE_SERAH_NAMA_DIPAKAI", "message": "Nama cara serah ini sudah ada."})


@router.get("/metode-serah")
async def daftar_metode(request: Request, semua: bool = Query(False, description="true = termasuk nonaktif")):
    c = _ctx(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await S.pastikan_aktif(conn, c["tenant_id"])
        rows = await conn.fetch(
            "SELECT id, nama, jenis, aktif, urutan FROM metode_serah WHERE tenant_id = $1"
            + ("" if semua else " AND aktif") + " ORDER BY urutan, lower(nama)", c["tenant_id"])
    return {"success": True, "data": [_baris(r) for r in rows]}


@router.post("/metode-serah")
async def tambah_metode(request: Request, body: MetodeBaru):
    c = _ctx(request)
    jenis = _jenis_sah(body.jenis)
    nama = body.nama.strip()
    if not nama:
        raise HTTPException(status_code=422, detail={"code": "METODE_SERAH_NAMA", "message": "Nama cara serah wajib diisi."})
    pool = await get_pool()
    async with pool.acquire() as conn:
        await S.pastikan_aktif(conn, c["tenant_id"])
        try:
            r = await conn.fetchrow(
                """INSERT INTO metode_serah (tenant_id, nama, jenis, urutan, created_by)
                   VALUES ($1, $2, $3, $4, $5::uuid) RETURNING id, nama, jenis, aktif, urutan""",
                c["tenant_id"], nama, jenis, body.urutan, c["user_id"])
        except asyncpg.UniqueViolationError:
            raise _nama_dipakai()
    return {"success": True, "data": _baris(r)}


@router.patch("/metode-serah/{metode_id}")
async def ubah_metode(request: Request, metode_id: UUID, body: MetodeUbah):
    c = _ctx(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await S.pastikan_aktif(conn, c["tenant_id"])
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"METODE_SERAH:{c['tenant_id']}:{metode_id}")
            m = await conn.fetchrow(
                "SELECT id, nama, jenis, aktif, urutan FROM metode_serah WHERE id = $1 AND tenant_id = $2",
                metode_id, c["tenant_id"])
            if not m:
                raise HTTPException(status_code=404, detail="Cara serah tidak ditemukan.")
            nama = body.nama.strip() if body.nama is not None else m["nama"]
            if not nama:
                raise HTTPException(status_code=422, detail={"code": "METODE_SERAH_NAMA", "message": "Nama cara serah wajib diisi."})
            jenis = _jenis_sah(body.jenis) if body.jenis is not None else m["jenis"]
            if jenis != m["jenis"] and await conn.fetchval(
                    "SELECT 1 FROM pengiriman_serah WHERE tenant_id = $1 AND metode_serah_id = $2 LIMIT 1",
                    c["tenant_id"], metode_id):
                raise HTTPException(status_code=409, detail={
                    "code": "METODE_SERAH_SUDAH_DIPAKAI",
                    "message": "Jenis tidak bisa diubah: cara serah ini sudah dipakai Surat Jalan. Buat cara serah baru."})
            try:
                r = await conn.fetchrow(
                    """UPDATE metode_serah SET nama = $3, jenis = $4, aktif = $5, urutan = $6, updated_at = now()
                       WHERE id = $1 AND tenant_id = $2 RETURNING id, nama, jenis, aktif, urutan""",
                    metode_id, c["tenant_id"], nama, jenis,
                    m["aktif"] if body.aktif is None else body.aktif,
                    m["urutan"] if body.urutan is None else body.urutan)
            except asyncpg.UniqueViolationError:
                raise _nama_dipakai()
    return {"success": True, "data": _baris(r)}


async def _pref_terbaca(conn, tid: str) -> dict:
    p = await S.preferensi(conn, tid)
    lokasi = metode = None
    if p["lokasi_id"]:
        w = await conn.fetchrow("SELECT id, name, is_active FROM warehouses WHERE id = $1 AND tenant_id = $2",
                                p["lokasi_id"], tid)
        if w:
            lokasi = {"id": str(w["id"]), "nama": w["name"], "aktif": w["is_active"]}
    if p["metode_id"]:
        m = await S.ambil_metode(conn, tid, p["metode_id"])
        if m:
            metode = {"id": str(m["id"]), "nama": m["nama"], "jenis": m["jenis"], "aktif": m["aktif"]}
    return {"lokasi": lokasi, "metode": metode}


@router.get("/pengiriman/preferensi")
async def baca_preferensi(request: Request):
    c = _ctx(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await S.pastikan_aktif(conn, c["tenant_id"])
        return {"success": True, "data": await _pref_terbaca(conn, c["tenant_id"])}


@router.put("/pengiriman/preferensi")
async def ubah_preferensi(request: Request, body: PreferensiUbah):
    c = _ctx(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await S.pastikan_aktif(conn, c["tenant_id"])
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"PENGIRIMAN_PREF:{c['tenant_id']}")
            lama = await S.preferensi(conn, c["tenant_id"])
            lokasi, metode = lama["lokasi_id"], lama["metode_id"]
            if "default_lokasi_kirim_id" in body.model_fields_set:
                if body.default_lokasi_kirim_id:
                    try:
                        u = UUID(body.default_lokasi_kirim_id)
                    except ValueError:
                        raise HTTPException(status_code=422, detail="Gudang tidak dikenal.")
                    w = await conn.fetchrow("SELECT is_active FROM warehouses WHERE id = $1 AND tenant_id = $2",
                                            u, c["tenant_id"])
                    if not w:
                        raise HTTPException(status_code=422, detail="Gudang tidak dikenal.")
                    if not w["is_active"]:
                        raise HTTPException(status_code=422, detail="Gudang tidak aktif.")
                    lokasi = u
                else:
                    lokasi = None
            if "default_metode_serah_id" in body.model_fields_set:
                if body.default_metode_serah_id:
                    m = await S.ambil_metode(conn, c["tenant_id"], body.default_metode_serah_id)
                    if not m:
                        raise HTTPException(status_code=422, detail="Cara serah tidak dikenal.")
                    if not m["aktif"]:
                        raise HTTPException(status_code=422, detail="Cara serah tidak aktif.")
                    metode = m["id"]
                else:
                    metode = None
            await conn.execute(
                """INSERT INTO pengiriman_preferensi (tenant_id, default_lokasi_kirim_id, default_metode_serah_id, updated_by)
                   VALUES ($1, $2, $3, $4::uuid)
                   ON CONFLICT (tenant_id) DO UPDATE SET default_lokasi_kirim_id = EXCLUDED.default_lokasi_kirim_id,
                       default_metode_serah_id = EXCLUDED.default_metode_serah_id, updated_at = now(),
                       updated_by = EXCLUDED.updated_by""",
                c["tenant_id"], lokasi, metode, c["user_id"])
            return {"success": True, "data": await _pref_terbaca(conn, c["tenant_id"])}


@router.get("/default-dokumen/pengiriman")
async def bawaan_pengiriman(request: Request, sales_order_id: Optional[UUID] = Query(None)):
    """Yang akan dipakai pratinjau bila FE tak mengirim apa pun: lokasi + SUMBER-nya (teks siap tampil), cara serah,
    alamat kirim + sumbernya. Logika lokasi = `so_pengiriman._gudang` (satu sumber kebenaran dengan pratinjau)."""
    c = _ctx(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await S.pastikan_aktif(conn, c["tenant_id"])
        pref = await S.preferensi(conn, c["tenant_id"])
        so = {"warehouse_id": None, "fase_lokasi": None, "fase_gudang_id": None}
        if sales_order_id is not None:
            r = await conn.fetchrow(
                "SELECT warehouse_id, fase_lokasi, fase_gudang_id FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                sales_order_id, c["tenant_id"])
            if not r:
                raise HTTPException(status_code=404, detail="Pesanan penjualan tidak ditemukan.")
            so = dict(r)
        g, blok = await _gudang(conn, c["tenant_id"], None, so, True, pref["lokasi_id"])
        s = await S.selesaikan(conn, c["tenant_id"], sales_order_id, None)
    return {"success": True, "data": {
        "lokasi": ({"id": g["id"], "nama": g["name"], "sumber": g["source"], "sumber_teks": g["source_label"]} if g else None),
        "lokasi_blok": blok,
        "serah": S.bentuk(s),
    }}
