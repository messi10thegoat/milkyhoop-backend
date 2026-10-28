"""Aksi massal Pesanan Pembelian (B1b-1, 28 Okt 2026, arahan MASTER: Pembelian simetris Penjualan): KIRIM + HAPUS draf + BATAL.
Kembar bulk_quote (kirim/hapus) dan bulk_proforma (batal ber-alasan). Tanpa uang: kirim/hapus/batal PO tak membuat jurnal.

POST /api/purchase-orders/bulk/{send|delete|cancel}/preview  body {ids[, reason]} -> per item boleh/ditolak + SEMUA alasan (penentu YANG SAMA
dengan rute tunggal dan pratinjau tunggal: po_rencana.rencana_kirim / rencana_hapus / rencana_batal), BACA SAJA (transaksi READ ONLY, tanpa
kunci; tulis memvalidasi ulang di bawah kunci).
POST /api/purchase-orders/bulk/{send|delete|cancel}          body {ids[, reason]} + header X-Idempotency-Key (WAJIB) -> tiap item transaksi
SENDIRI lewat inti tunggal (kirim_po_core / hapus_po_draf_core / batal_po_core), idempotensi per item, hasil per item, batas 50.
Izin = izin aksi tunggal (kirim P, batal V, hapus D -- sama dengan /{id}/send, /{id}/cancel, DELETE /{id})."""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services import bulk
from ..services import idem_buat
from ..services import po_rencana as PR
from . import purchase_orders as PO

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    """ctx bentuk SAMA dengan rute tunggal (PO.get_user_context: user_id UUID) -> inti tunggal menerima ctx yang sama."""
    return PO.get_user_context(request)


class BulkAksiRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id pesanan pembelian terpilih (urutan dipertahankan)")
    reason: Optional[str] = Field(None, max_length=500, description="alasan pembatalan (cancel: wajib untuk tulis; opsional di pratinjau)")


def _kunci_wajib(request: Request) -> str:
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB",
                                                     "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    return kunci


async def _nomor(pool, tenant_id: str, ids) -> dict:
    async with pool.acquire() as conn:
        return {str(r["id"]): r["po_number"] for r in await conn.fetch(
            "SELECT id, po_number FROM purchase_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, ids)}


def _uuid(i):
    return i if isinstance(i, uuid.UUID) else uuid.UUID(str(i))


TAK_ADA = {"code": "PO_TAK_ADA", "status": 404, "message": "Pesanan pembelian tidak ditemukan."}


async def _blok(conn, ctx, i, aksi: str, alasan: Optional[str], kunci: bool) -> tuple:
    """SEMUA penghalang aksi untuk satu PO tanpa menulis -> (blok, po). kunci=True (tulis): PO._kunci_po (advisory + FOR UPDATE, sama dengan
    inti); kunci=False (pratinjau): baca biasa. Penentu = po_rencana yang SAMA dengan rute tunggal."""
    tid = ctx["tenant_id"]
    if kunci:
        po = await PO._kunci_po(conn, tid, _uuid(i), "id, status, po_number")
    else:
        po = await conn.fetchrow("SELECT id, status, po_number FROM purchase_orders WHERE id = $1 AND tenant_id = $2", _uuid(i), tid)
    if not po:
        return [TAK_ADA], None
    if aksi == "send":
        r = PR.rencana_kirim(po)
    elif aksi == "delete":
        r = PR.rencana_hapus(po)
    else:
        r = await PR.rencana_batal(conn, tid, po, alasan)
    return r["blocks"], (po, r)


def _fungsi(aksi: str, alasan: Optional[str]):
    async def tulis(conn, ctx, i):
        """Penentu di bawah kunci DULU (kode galat stabil = pratinjau), lalu inti tunggal (memvalidasi ulang, perilaku = rute tunggal).
        app.tenant_id diset seperti rute tunggal (transaksi item = transaksi jalankan_per_item)."""
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        blok, _ = await _blok(conn, ctx, i, aksi, alasan, kunci=True)
        if blok:
            raise HTTPException(status_code=blok[0].get("status", 400), detail={"code": blok[0]["code"], "message": blok[0]["message"]})
        if aksi == "send":
            return await PO.kirim_po_core(conn, ctx, _uuid(i))
        if aksi == "delete":
            return await PO.hapus_po_draf_core(conn, ctx, _uuid(i))
        return await PO.batal_po_core(conn, ctx, _uuid(i), alasan)

    async def pratinjau(conn, ctx, i):
        """Penentu YANG SAMA tanpa kunci; ringkasan bentuk SAMA dengan hasil inti (+ catatan batal, mis. uang muka tetap tercatat)."""
        blok, isi = await _blok(conn, ctx, i, aksi, alasan, kunci=False)
        if blok:
            return blok, None
        po, r = isi
        if aksi == "send":
            return [], {"id": str(po["id"]), "po_number": po["po_number"], "status": r["status_sesudah"]}
        if aksi == "delete":
            return [], {"id": str(po["id"]), "po_number": po["po_number"]}
        return [], {"id": str(po["id"]), "po_number": po["po_number"], "status": r["status_sesudah"], "notes": PR.untuk_respons(r["notes"])}
    return tulis, pratinjau


AKSI = ("send", "delete", "cancel")
assert set(AKSI) <= bulk.AKSI_DIIZINKAN  # setiap aksi di berkas ini harus ada di daftar putih
AKSI_BUTUH_USER = ("send", "cancel")  # = rute tunggal: kirim/batal menolak 401 tanpa user_id; hapus tidak


def _pasang(aksi: str):
    @router.post(f"/bulk/{aksi}/preview")
    async def bulk_preview(request: Request, body: BulkAksiRequest):
        ctx = _ctx(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        alasan = (body.reason or "").strip() or None
        pool = await get_pool()
        _, pratinjau = _fungsi(aksi, alasan)
        return {"success": True, "data": await bulk.pratinjau_per_item(
            pool, ctx, aksi, ids, pratinjau, await _nomor(pool, ctx["tenant_id"], ids), baca_saja=True)}

    @router.post(f"/bulk/{aksi}")
    async def bulk_tulis(request: Request, body: BulkAksiRequest):
        ctx = _ctx(request)
        if aksi in AKSI_BUTUH_USER and not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
        kunci = _kunci_wajib(request)
        alasan = None
        if aksi == "cancel":
            alasan = (body.reason or "").strip()
            if not alasan:
                raise HTTPException(status_code=400, detail={"code": "BULK_ALASAN_WAJIB", "message": "Alasan pembatalan wajib diisi."})
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        tulis, _ = _fungsi(aksi, alasan)
        return {"success": True, "data": await bulk.jalankan_per_item(
            pool, ctx, aksi, "purchase-orders", ids, tulis, kunci_batch=kunci, payload={"reason": alasan} if aksi == "cancel" else None,
            nomor=await _nomor(pool, ctx["tenant_id"], ids))}

    bulk_preview.__name__, bulk_tulis.__name__ = f"bulk_{aksi}_preview", f"bulk_{aksi}"


for _a in AKSI:
    _pasang(_a)
