"""Aksi massal Barang & Jasa (P3, 13 Nov 2026, putusan pemilik: pilih-banyak tetap ada): AKTIFKAN / NONAKTIFKAN banyak barang.
POST /api/items/bulk/status/preview  body {ids, is_active} -> per barang {ok, blocks, preview{rencana, peringatan[]}}: nonaktif ber-stok / pesanan
terbuka = PERINGATAN (stok X unit senilai Rp Y, N pesanan terbuka), bukan blok; blok hanya "sudah aktif/nonaktif" / "tak ada". READ ONLY.
POST /api/items/bulk/status          body {ids, is_active} + X-Idempotency-Key (WAJIB) -> tiap barang transaksi SENDIRI, idempotensi per barang,
satu baris item_activities per barang (riwayat) + satu audit BULK_STATUS. Penentu SAMA dengan pratinjau tunggal (services/barang_aksi).
Tanpa jurnal; stok dan nilai tak berubah."""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services import barang_aksi as BA
from ..services import bulk
from ..services import idem_buat

logger = logging.getLogger(__name__)
router = APIRouter()
AKSI = "status"
assert AKSI in bulk.AKSI_DIIZINKAN


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id") or user.get("id"), "username": user.get("username")}


class BulkStatusRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id barang terpilih (urutan dipertahankan)")
    is_active: bool = Field(..., description="true = aktifkan, false = nonaktifkan")


async def _nama(pool, tenant_id: str, ids) -> dict:
    async with pool.acquire() as conn:
        return {str(r["id"]): r["nama_produk"] for r in await conn.fetch(
            "SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, ids)}


def _fungsi(ke_aktif: bool):
    async def tulis(conn, ctx, i):
        r = await BA.ukur_rencana_status(conn, ctx["tenant_id"], str(i), ke_aktif)
        if r["blocks"]:
            raise HTTPException(status_code=409 if r["blocks"][0]["code"] != "BARANG_TAK_ADA" else 404,
                                detail={"code": r["blocks"][0]["code"], "message": r["blocks"][0]["message"]})
        await BA.terapkan_status(conn, ctx["tenant_id"], str(i), ke_aktif, ctx.get("user_id"), ctx.get("username"))
        return {"id": str(i), "keadaan": r["rencana"]["ke"], "keadaan_text": "Aktif" if ke_aktif else "Nonaktif", "peringatan": r["peringatan"]}

    async def pratinjau(conn, ctx, i):
        r = await BA.ukur_rencana_status(conn, ctx["tenant_id"], str(i), ke_aktif)
        return r["blocks"], ({**r["rencana"], "peringatan": r["peringatan"]} if not r["blocks"] else None)
    return tulis, pratinjau


@router.post("/bulk/status/preview")
async def bulk_status_preview(request: Request, body: BulkStatusRequest):
    ctx = _ctx(request)
    ids = bulk.validasi_ids(body.ids, "tulis")
    pool = await get_pool()
    _, pratinjau = _fungsi(body.is_active)
    return {"success": True, "data": await bulk.pratinjau_per_item(pool, ctx, AKSI, ids, pratinjau, await _nama(pool, ctx["tenant_id"], ids),
                                                                   baca_saja=True)}


@router.post("/bulk/status")
async def bulk_status(request: Request, body: BulkStatusRequest):
    ctx = _ctx(request)
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB", "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    ids = bulk.validasi_ids(body.ids, "tulis")
    pool = await get_pool()
    tulis, _ = _fungsi(body.is_active)
    return {"success": True, "data": await bulk.jalankan_per_item(
        pool, ctx, AKSI, "items", ids, tulis, kunci_batch=kunci, payload={"is_active": body.is_active},
        nomor=await _nama(pool, ctx["tenant_id"], ids))}
