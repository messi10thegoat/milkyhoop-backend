"""Ubah HARGA massal per daftar harga (D3, 13 Nov 2026, putusan pemilik): persen ATAU nominal, tanggal berlaku, pratinjau per barang.
POST /api/price-lists/{id}/bulk/price/preview  {item_ids?, mode, nilai, berlaku_mulai?} -> per barang {ok, blocks, preview{rencana, peringatan[]}} READ ONLY.
POST /api/price-lists/{id}/bulk/price          sama + X-Idempotency-Key (WAJIB) -> tiap barang transaksi SENDIRI, idempotensi per barang, audit BULK_HARGA.
item_ids kosong = SEMUA barang di daftar itu. Penentu SAMA dgn pratinjau (services/harga_massal). Tanpa jurnal."""
import logging
from datetime import date
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from ..services import bulk
from ..services import harga_massal as HM
from ..services import idem_buat

logger = logging.getLogger(__name__)
router = APIRouter()
AKSI = "harga"
assert AKSI in bulk.AKSI_DIIZINKAN


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id") or user.get("id"), "username": user.get("username")}


class BulkHargaRequest(BaseModel):
    item_ids: Optional[List[str]] = Field(None, max_length=2000, description="barang terpilih; kosong/null = semua barang di daftar")
    mode: Literal["persen", "nominal"]
    nilai: Decimal = Field(..., description="persen (mis. 10 = naik 10%, -5 = turun 5%) atau nominal Rupiah (+/-)")
    berlaku_mulai: Optional[date] = Field(None, description="start_date baris yang diubah; kosong = harga langsung berlaku")

    @model_validator(mode="after")
    def _periksa(self):
        if self.nilai == 0:
            raise ValueError("Nilai perubahan tidak boleh 0.")
        if self.mode == "persen" and self.nilai < Decimal("-100"):
            raise ValueError("Penurunan persen tidak boleh lebih dari 100%.")
        return self


async def _siapkan(pool, ctx: dict, price_list_id: UUID, body: BulkHargaRequest):
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM price_lists WHERE id = $1 AND tenant_id = $2", price_list_id, ctx["tenant_id"]):
            raise HTTPException(status_code=404, detail="Daftar harga tidak ditemukan.")
        if body.item_ids:
            ids = bulk.validasi_ids(body.item_ids, "tulis")
        else:
            ids = bulk.validasi_ids([str(r["item_id"]) for r in await conn.fetch(
                """SELECT DISTINCT pli.item_id FROM price_list_items pli JOIN price_lists pl ON pl.id = pli.price_list_id
                   WHERE pl.tenant_id = $1 AND pli.price_list_id = $2""", ctx["tenant_id"], price_list_id)] or [], "tulis")
        nama = {str(r["id"]): r["nama_produk"] for r in await conn.fetch(
            "SELECT id, nama_produk FROM products WHERE tenant_id = $1 AND id = ANY($2::uuid[])", ctx["tenant_id"], ids)}
    return ids, nama


def _fungsi(price_list_id: UUID, body: BulkHargaRequest):
    async def tulis(conn, ctx, i):
        r = await HM.ukur_rencana(conn, ctx["tenant_id"], price_list_id, i, body.mode, body.nilai, body.berlaku_mulai, kunci=True)
        if r["blocks"]:
            raise HTTPException(status_code=409 if r["blocks"][0]["code"] != "DH_BARANG_TAK_DI_DAFTAR" else 404,
                                detail={"code": r["blocks"][0]["code"], "message": r["blocks"][0]["message"]})
        await HM.terapkan(conn, ctx["tenant_id"], price_list_id, r["rencana"])
        return {"id": str(i), "jumlah_baris": r["rencana"]["jumlah_baris"], "baris": r["rencana"]["baris"], "peringatan": r["peringatan"]}

    async def pratinjau(conn, ctx, i):
        r = await HM.ukur_rencana(conn, ctx["tenant_id"], price_list_id, i, body.mode, body.nilai, body.berlaku_mulai)
        return r["blocks"], ({**r["rencana"], "peringatan": r["peringatan"]} if not r["blocks"] else None)
    return tulis, pratinjau


@router.post("/{price_list_id}/bulk/price/preview")
async def bulk_price_preview(request: Request, price_list_id: UUID, body: BulkHargaRequest):
    ctx = _ctx(request)
    pool = await get_pool()
    ids, nama = await _siapkan(pool, ctx, price_list_id, body)
    _, pratinjau = _fungsi(price_list_id, body)
    return {"success": True, "data": await bulk.pratinjau_per_item(pool, ctx, AKSI, ids, pratinjau, nama, baca_saja=True)}


@router.post("/{price_list_id}/bulk/price")
async def bulk_price(request: Request, price_list_id: UUID, body: BulkHargaRequest):
    ctx = _ctx(request)
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB", "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    pool = await get_pool()
    ids, nama = await _siapkan(pool, ctx, price_list_id, body)
    tulis, _ = _fungsi(price_list_id, body)
    return {"success": True, "data": await bulk.jalankan_per_item(
        pool, ctx, AKSI, "price_lists", ids, tulis, kunci_batch=kunci,
        payload={"price_list_id": str(price_list_id), "mode": body.mode, "nilai": str(body.nilai),
                 "berlaku_mulai": body.berlaku_mulai.isoformat() if body.berlaku_mulai else None},
        nomor=nama)}
