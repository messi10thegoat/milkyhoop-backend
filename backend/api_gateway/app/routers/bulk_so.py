"""Aksi massal SO (U1b F2, 5 Okt 2026, MASTER GO): Konfirmasi draf + Hapus draf, DENGAN pratinjau per item.

POST /api/sales-orders/bulk/{confirm|delete}/preview  body {ids}  -> per item boleh/ditolak + SEMUA alasan (penentu = jalur tunggal yang
SAMA: _konfirmasi_so / rencana_hapus_so + hapus_so_draf_core, dijalankan berurutan di SATU transaksi lalu di-ROLLBACK, jadi kode order
yang terbit berurutan persis seperti tulis nyata).
POST /api/sales-orders/bulk/{confirm|delete}          body {ids} + header X-Idempotency-Key (WAJIB) -> tiap item transaksi SENDIRI,
idempotensi per item BULK_{AKSI}:{user}:{id}:{kunci}, hasil per item. Batas 50. Izin = izin aksi tunggalnya (confirm = POST SO,
delete = DELETE SO). Aksi uang tidak ada di sini."""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services import bulk
from ..services import idem_buat
from . import sales_orders as SO

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id") or user.get("id")}


class BulkAksiRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id pesanan terpilih (urutan dipertahankan)")


def _kunci_wajib(request: Request) -> str:
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB",
                                                     "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    return kunci


async def _nomor(pool, tenant_id: str, ids) -> dict:
    async with pool.acquire() as conn:
        return {str(r["id"]): r["order_number"] for r in await conn.fetch(
            "SELECT id, order_number FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, ids)}


# ---- aksi: fungsi item (tulis) dan pratinjau (blok + ringkasan) ----
_KODE_KONFIRMASI = {404: "SO_TAK_ADA", 400: "SO_BUKAN_DRAF", 409: "SO_BERUBAH"}


async def _konfirmasi(conn, ctx, i, fn=None, *arg):
    """Jalur tunggal _konfirmasi_so (tulis) / pratinjau_konfirmasi_so (pratinjau, penentu yang sama); hanya memberi KODE stabil
    pada galat teksnya (FE/pratinjau/tulis seragam)."""
    try:
        if fn is None:
            return await SO._konfirmasi_so(conn, ctx, str(i))
        return await fn(conn, ctx, str(i), *arg)
    except HTTPException as e:
        if isinstance(e.detail, dict):
            raise
        raise HTTPException(status_code=e.status_code, detail={
            "code": _KODE_KONFIRMASI.get(e.status_code, f"HTTP_{e.status_code}"), "message": e.detail})


async def _tulis_konfirmasi(conn, ctx, i):
    return await _konfirmasi(conn, ctx, i)


async def _tulis_hapus(conn, ctx, i):
    """Penentu baca DULU (kode galat sama dengan pratinjau), lalu inti tunggal."""
    blok = await SO.rencana_hapus_so(conn, ctx, str(i))
    if blok:
        raise HTTPException(status_code=blok[0].get("status", 400), detail={"code": blok[0]["code"], "message": blok[0]["message"]})
    return await SO.hapus_so_draf_core(conn, ctx, str(i))


async def _pratinjau_konfirmasi(conn, ctx, i):
    """AS-G7a: penentu + perkiraan BACA SAJA (tanpa kunci/penghitung); ctx = satu per permintaan -> 'dipesan' membuat kode
    item berikutnya berurutan persis tulis berurutan. HTTPException = satu blok (404 / bukan draf), kode stabil sama."""
    return [], await _konfirmasi(conn, ctx, i, SO.pratinjau_konfirmasi_so, ctx.setdefault("_kode_dipesan", {}))


async def _pratinjau_hapus(conn, ctx, i):
    """AS-G7-M1: penentu YANG SAMA dengan _tulis_hapus (rencana_hapus_so, baca saja) + nomor; inti penulis tak dijalankan."""
    blok = await SO.rencana_hapus_so(conn, ctx, str(i))
    if blok:
        return blok, None
    nomor = await conn.fetchval("SELECT order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2", SO._so_uuid(str(i)),
                                ctx["tenant_id"])
    return [], {"order_number": nomor}  # bentuk SAMA dengan hapus_so_draf_core


AKSI = {
    "confirm": (_tulis_konfirmasi, _pratinjau_konfirmasi),
    "delete": (_tulis_hapus, _pratinjau_hapus),
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
            pool, ctx, aksi, ids, pratinjau, await _nomor(pool, ctx["tenant_id"], ids), baca_saja=True)}  # confirm (G7a) + delete (M1)

    @router.post(f"/bulk/{aksi}")
    async def bulk_tulis(request: Request, body: BulkAksiRequest):
        ctx = _ctx(request)
        kunci = _kunci_wajib(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        return {"success": True, "data": await bulk.jalankan_per_item(
            pool, ctx, aksi, "sales-orders", ids, tulis, kunci_batch=kunci,
            nomor=await _nomor(pool, ctx["tenant_id"], ids))}

    bulk_preview.__name__, bulk_tulis.__name__ = f"bulk_{aksi}_preview", f"bulk_{aksi}"
    return bulk_preview, bulk_tulis


for _a in AKSI:
    _pasang(_a)
