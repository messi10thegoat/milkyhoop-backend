"""Aksi massal Penawaran (U1b F3, 5 Okt 2026, MASTER GO): Hapus draf + Kirim (= tandai terkirim; TANPA surel/WA dari server).

POST /api/quotes/bulk/{delete|send}/preview  body {ids}  -> per item boleh/ditolak + SEMUA alasan (penentu/inti tunggal yang SAMA:
rencana_hapus_penawaran + hapus_penawaran_core; kirim_penawaran_core), berurutan di SATU transaksi lalu ROLLBACK.
POST /api/quotes/bulk/{delete|send}          body {ids} + header X-Idempotency-Key (WAJIB) -> tiap item transaksi SENDIRI, idempotensi
per item, hasil per item, batas 50. Izin = izin aksi tunggal (kirim = POST penawaran C, hapus = DELETE penawaran D). Aturan hapus =
pola Xero (alasan_tak_bisa_hapus): hanya draf yang belum pernah dikirim/dibagikan dan tak jadi SO."""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services import bulk
from ..services import idem_buat
from . import quotes as Q

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id") or user.get("id")}


class BulkAksiRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id penawaran terpilih (urutan dipertahankan)")


def _kunci_wajib(request: Request) -> str:
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB",
                                                     "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    return kunci


async def _nomor(pool, tenant_id: str, ids) -> dict:
    async with pool.acquire() as conn:
        return {str(r["id"]): r["quote_number"] for r in await conn.fetch(
            "SELECT id, quote_number FROM quotes WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, ids)}


_KODE_KIRIM = {404: "QUOTE_TAK_ADA", 400: "QUOTE_TAK_BISA_DIKIRIM", 422: "QUOTE_SUREL_BELUM_ADA"}


async def _kirim(conn, ctx, i):
    """Jalur tunggal kirim_penawaran_core apa adanya; hanya memberi KODE stabil pada galat teksnya."""
    try:
        return await Q.kirim_penawaran_core(conn, ctx, str(i))
    except HTTPException as e:
        if isinstance(e.detail, dict):
            raise
        raise HTTPException(status_code=e.status_code, detail={
            "code": _KODE_KIRIM.get(e.status_code, f"HTTP_{e.status_code}"), "message": e.detail})


async def _tulis_kirim(conn, ctx, i):
    return await _kirim(conn, ctx, i)


async def _pratinjau_kirim(conn, ctx, i):
    sebelum = await conn.fetchval("SELECT status FROM quotes WHERE id = $1 AND tenant_id = $2", _uuid(i), ctx["tenant_id"])
    h = await _kirim(conn, ctx, i)
    return [], {**h, "sudah_terkirim_sebelumnya": sebelum == "sent"}  # kirim ulang diizinkan (tunggal pun), tapi FE boleh memberi tahu


def _uuid(i):
    import uuid
    return i if isinstance(i, uuid.UUID) else uuid.UUID(str(i))


async def _tulis_hapus(conn, ctx, i):
    """Penentu baca DULU (kode galat sama dengan pratinjau), lalu inti tunggal."""
    blok = await Q.rencana_hapus_penawaran(conn, ctx, i)
    if blok:
        raise HTTPException(status_code=blok[0].get("status", 409), detail={"code": blok[0]["code"], "message": blok[0]["message"]})
    return await Q.hapus_penawaran_core(conn, ctx, i)


async def _pratinjau_hapus(conn, ctx, i):
    blok = await Q.rencana_hapus_penawaran(conn, ctx, i)
    if blok:
        return blok, None
    return [], await Q.hapus_penawaran_core(conn, ctx, i)  # inti yang SAMA, di-rollback bersama transaksi pratinjau


AKSI = {
    "delete": (_tulis_hapus, _pratinjau_hapus),
    "send": (_tulis_kirim, _pratinjau_kirim),
}
assert set(AKSI) <= bulk.AKSI_DIIZINKAN  # setiap aksi di berkas ini harus ada di daftar putih


def _pasang(aksi: str):
    tulis, pratinjau = AKSI[aksi]

    @router.post(f"/bulk/{aksi}/preview")
    async def bulk_preview(request: Request, body: BulkAksiRequest):
        ctx = _ctx(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        return {"success": True, "data": await bulk.pratinjau_per_item(
            pool, ctx, aksi, ids, pratinjau, await _nomor(pool, ctx["tenant_id"], ids))}

    @router.post(f"/bulk/{aksi}")
    async def bulk_tulis(request: Request, body: BulkAksiRequest):
        ctx = _ctx(request)
        kunci = _kunci_wajib(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        return {"success": True, "data": await bulk.jalankan_per_item(
            pool, ctx, aksi, "quotes", ids, tulis, kunci_batch=kunci,
            nomor=await _nomor(pool, ctx["tenant_id"], ids))}

    bulk_preview.__name__, bulk_tulis.__name__ = f"bulk_{aksi}_preview", f"bulk_{aksi}"


for _a in AKSI:
    _pasang(_a)
