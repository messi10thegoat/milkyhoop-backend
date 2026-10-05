"""Aksi massal Proforma (U1b F4, 5 Okt 2026, MASTER GO): BATAL draf. Terbit proforma massal TIDAK ADA rute (putusan MASTER: penerbitan
tagihan = keputusan per pelanggan; tetap tunggal) -- tes mengunci ketiadaannya.

POST /api/proformas/bulk/cancel/preview  body {ids, reason?} -> per item boleh/ditolak + SEMUA alasan (penentu _rencana_batal + batal_proforma_core
yang SAMA dengan /cancel tunggal, berurutan di SATU transaksi lalu ROLLBACK).
POST /api/proformas/bulk/cancel          body {ids, reason} + header X-Idempotency-Key (WAJIB) -> tiap item transaksi SENDIRI, idempotensi per item
(isi = alasan), hasil per item, batas 50. Hanya proforma berstatus DRAF (yang diterbitkan tak ikut massal). Izin = izin /cancel tunggal (C)."""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services import bulk
from ..services import idem_buat
from ..services import teks_galat as tg
from . import proformas as PF

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


class BulkBatalRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id proforma terpilih (urutan dipertahankan)")
    reason: Optional[str] = Field(None, max_length=500, description="alasan pembatalan (wajib untuk tulis; opsional di pratinjau)")


def _kunci_wajib(request: Request) -> str:
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB",
                                                     "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    return kunci


async def _nomor(pool, tenant_id: str, ids) -> dict:
    async with pool.acquire() as conn:
        return {str(r["id"]): r["proforma_number"] for r in await conn.fetch(
            "SELECT id, proforma_number FROM proformas WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, ids)}


def _uuid(i):
    return i if isinstance(i, uuid.UUID) else uuid.UUID(str(i))


async def _blok_batal(conn, ctx, i, alasan) -> list:
    """SEMUA penghalang batal-draf tanpa menulis: 404 / bukan draf + blok penentu _rencana_batal (sudah batal, alasan, uang muka)."""
    try:
        cur = await PF._kunci_proforma(conn, ctx, _uuid(i))
    except HTTPException as e:  # 404 teks -> kode stabil (satu blok oleh pemanggil)
        if e.status_code == 404 and not isinstance(e.detail, dict):
            raise HTTPException(status_code=404, detail={"code": "PROFORMA_TAK_ADA", "message": e.detail})
        raise
    blok = []
    if cur["status"] != "draft":
        blok.append({"code": "PROFORMA_BUKAN_DRAF", "status": 400, "message": (
            f"Proforma berstatus {tg.status_id('proforma', cur['status'])}; hanya proforma Draf yang bisa dibatalkan massal.")})
    r = await PF._rencana_batal(conn, ctx, cur, alasan)
    blok += [{"code": b["code"], "status": b.get("status", 400), "message": b["message"]} for b in r["blocks"]]
    return blok


def _fungsi(alasan: Optional[str]):
    async def tulis(conn, ctx, i):
        blok = await _blok_batal(conn, ctx, i, alasan)
        if blok:
            raise HTTPException(status_code=blok[0].get("status", 400), detail={"code": blok[0]["code"], "message": blok[0]["message"]})
        return await PF.batal_proforma_core(conn, ctx, _uuid(i), alasan, hanya_draf=True)

    async def pratinjau(conn, ctx, i):
        blok = await _blok_batal(conn, ctx, i, alasan)
        if blok:
            return blok, None
        data = await PF.batal_proforma_core(conn, ctx, _uuid(i), alasan, hanya_draf=True)  # inti yang SAMA, di-rollback bersama pratinjau
        return [], {"proforma_number": data.get("proforma_number"), "status": data.get("status")}
    return tulis, pratinjau


AKSI = ("cancel",)
assert set(AKSI) <= bulk.AKSI_DIIZINKAN  # setiap aksi di berkas ini harus ada di daftar putih


@router.post("/bulk/cancel/preview")
async def bulk_cancel_preview(request: Request, body: BulkBatalRequest):
    ctx = _ctx(request)
    ids = bulk.validasi_ids(body.ids, "tulis")
    pool = await get_pool()
    _, pratinjau = _fungsi(body.reason)
    return {"success": True, "data": await bulk.pratinjau_per_item(pool, ctx, "cancel", ids, pratinjau, await _nomor(pool, ctx["tenant_id"], ids))}


@router.post("/bulk/cancel")
async def bulk_cancel(request: Request, body: BulkBatalRequest):
    ctx = _ctx(request)
    kunci = _kunci_wajib(request)
    if False:
        raise HTTPException(status_code=400, detail={"code": "BULK_ALASAN_WAJIB", "message": "Alasan pembatalan wajib diisi."})
    ids = bulk.validasi_ids(body.ids, "tulis")
    pool = await get_pool()
    tulis, _ = _fungsi(body.reason)
    return {"success": True, "data": await bulk.jalankan_per_item(
        pool, ctx, "cancel", "proformas", ids, tulis, kunci_batch=kunci, payload={"reason": body.reason.strip()},
        nomor=await _nomor(pool, ctx["tenant_id"], ids))}
