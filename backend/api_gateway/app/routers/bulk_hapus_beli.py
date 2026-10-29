"""Hapus DRAF massal Pembelian (B1b-2, 29 Okt 2026, arahan MASTER: Pembelian simetris Penjualan): Faktur Pembelian, Pembayaran Keluar,
Uang Muka Pembelian, Nota Debit. Kembar bulk_so/bulk_quote hapus draf. Hanya DRAF: dokumen draf belum berjurnal, jadi menghapusnya bukan aksi
uang (posting/void/refund/terapkan TIDAK punya rute massal).

POST /api/{modul}/bulk/delete/preview  body {ids} -> per item boleh/ditolak + SEMUA alasan, BACA SAJA (transaksi READ ONLY, tanpa kunci), penentu
YANG SAMA dengan pratinjau/rute tunggal (tagihan_aksi.blok_hapus+ref_hapus, BKR.rencana_hapus_draf_bayar, UMVR.rencana_hapus, VC.rencana_hapus_nd).
POST /api/{modul}/bulk/delete          body {ids} + header X-Idempotency-Key (WAJIB) -> tiap item transaksi SENDIRI lewat inti tunggal yang SAMA
dengan DELETE /{id} (hapus_draf_tagihan_core / hapus_draf_bayar_core / hapus_draf_umv_core / hapus_draf_nd_core), idempotensi per item, hasil
per item, batas 50. Galat teks inti diberi KODE stabil (= kode blok pratinjau). Izin = izin DELETE tunggal modul (D).
Router ini WAJIB didaftarkan SEBELUM router bills/bill-payments (POST /{id}/delete/preview menelan /bulk/delete/preview)."""
import logging
import uuid
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services import bayar_keluar_rencana as BKR
from ..services import bills_service as BS
from ..services import bulk
from ..services import idem_buat
from ..services import tagihan_aksi as TA
from ..services import uang_muka_vendor_rencana as UMVR
from ..services.lampiran_milik import hapus_objek_sesudah_commit
from ..services.po_daftar import po_terkait_bill
from . import bill_payments as RBP
from . import vendor_credits as RVC
from . import vendor_deposits as RVD

logger = logging.getLogger(__name__)


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    uid = user.get("user_id") or user.get("id")
    return {"tenant_id": user["tenant_id"], "user_id": uuid.UUID(str(uid)) if uid else None}


class BulkHapusRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id dokumen draf terpilih (urutan dipertahankan)")


def _kunci_wajib(request: Request) -> str:
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB",
                                                     "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    return kunci


def _uuid(i):
    return i if isinstance(i, uuid.UUID) else uuid.UUID(str(i))


def _blok(code: str, message: str, status: int = 400) -> dict:
    return {"code": code, "message": message, "status": status}


# ---------------------------------------------------------------- per modul: tabel nomor, peta kode galat teks inti, inti tulis, penentu baca
async def _tulis_tagihan(conn, ctx, i):
    hasil, berkas = await BS.hapus_draf_tagihan_core(conn, ctx["tenant_id"], _uuid(i), ctx["user_id"])

    async def bersih():  # #30: objek lampiran dihapus SESUDAH commit item (jalankan_per_item)
        await hapus_objek_sesudah_commit(berkas, f"hapus draf tagihan massal {i}")
    return {**hasil, "_sesudah_commit": bersih}


async def _baca_tagihan(conn, ctx, i):
    b = await conn.fetchrow("""SELECT id, invoice_number, amount_paid, status, status_v2, accounting_status, operational_status
                               FROM bills WHERE id = $1 AND tenant_id = $2""", _uuid(i), ctx["tenant_id"])
    if not b:
        return [_blok("TAGIHAN_TAK_ADA", BS.PESAN_TAGIHAN_TAK_ADA, 404)], None
    bloks = TA.blok_hapus(b, await TA.ref_hapus(conn, b["id"]))
    notes = ([{"code": "TAGIH_PO_DIPULIHKAN", "message": "Jumlah tertagih pesanan pembelian asal dipulihkan."}]
             if await po_terkait_bill(conn, ctx["tenant_id"], b["id"]) else [])
    return bloks, {"id": str(b["id"]), "invoice_number": b["invoice_number"], "notes": notes}


async def _tulis_bayar(conn, ctx, i):
    return await RBP.hapus_draf_bayar_core(conn, ctx, str(_uuid(i)))


async def _baca_bayar(conn, ctx, i):
    r = await BKR.rencana_hapus_draf_bayar(conn, ctx["tenant_id"], _uuid(i), pratinjau=True)
    p = r["payment"]
    return r["blocks"], ({"id": str(p["id"]), "payment_number": p["payment_number"]} if p else None)


async def _tulis_umv(conn, ctx, i):
    return await RVD.hapus_draf_umv_core(conn, ctx, _uuid(i))


async def _baca_umv(conn, ctx, i):
    vd = await conn.fetchrow("SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2", _uuid(i), ctx["tenant_id"])
    if not vd:
        return [_blok("UANG_MUKA_TAK_ADA", "Uang muka vendor tidak ditemukan.", 404)], None
    return (await UMVR.rencana_hapus(conn, ctx, vd))["blocks"], {"id": str(vd["id"]), "deposit_number": vd["deposit_number"]}


async def _tulis_nd(conn, ctx, i):
    return await RVC.hapus_draf_nd_core(conn, ctx, _uuid(i))


async def _baca_nd(conn, ctx, i):
    vc = await conn.fetchrow("SELECT id, status, credit_number FROM vendor_credits WHERE id = $1 AND tenant_id = $2", _uuid(i), ctx["tenant_id"])
    if not vc:
        return [_blok("ND_TAK_ADA", "Nota debit tidak ditemukan.", 404)], None
    return RVC.rencana_hapus_nd(vc), {"id": str(vc["id"]), "vendor_credit_number": vc["credit_number"]}


# modul -> (tabel, kolom nomor, peta status->kode untuk galat TEKS inti, tulis, baca)
MODUL = {
    "bills": ("bills", "invoice_number", {}, _tulis_tagihan, _baca_tagihan),  # inti tagihan sudah melempar {code, message}
    "bill-payments": ("bill_payments_v2", "payment_number", {404: "TIDAK_DITEMUKAN", 400: "STATUS_BUKAN_DRAF"}, _tulis_bayar, _baca_bayar),
    "vendor-deposits": ("vendor_deposits", "deposit_number", {404: "UANG_MUKA_TAK_ADA", 400: "BUKAN_DRAF"}, _tulis_umv, _baca_umv),
    "vendor-credits": ("vendor_credits", "credit_number", {404: "ND_TAK_ADA", 400: "ND_BUKAN_DRAF", 409: "ND_BERUBAH"}, _tulis_nd, _baca_nd),
}
AKSI = ("delete",)
assert set(AKSI) <= bulk.AKSI_DIIZINKAN  # setiap aksi di berkas ini harus ada di daftar putih


async def _nomor(pool, tenant_id: str, tabel: str, kolom: str, ids) -> dict:
    async with pool.acquire() as conn:
        return {str(r["id"]): r["n"] for r in await conn.fetch(
            f"SELECT id, {kolom} AS n FROM {tabel} WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, ids)}  # nosec B608 (peta tetap)


def buat_router(modul: str) -> APIRouter:
    tabel, kolom, peta, tulis_inti, baca = MODUL[modul]
    router = APIRouter()

    async def tulis(conn, ctx, i):
        """app.tenant_id seperti rute tunggal, lalu inti tunggal; galat TEKS inti -> kode stabil (= kode blok pratinjau), pesan apa adanya."""
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
        try:
            return await tulis_inti(conn, ctx, i)
        except HTTPException as e:
            if isinstance(e.detail, dict):
                raise
            raise HTTPException(status_code=e.status_code, detail={"code": peta.get(e.status_code, f"HTTP_{e.status_code}"), "message": e.detail})

    async def pratinjau(conn, ctx, i):
        blok, ringkas = await baca(conn, ctx, i)
        return blok, (ringkas if not blok else None)

    @router.post("/bulk/delete/preview")
    async def bulk_delete_preview(request: Request, body: BulkHapusRequest):
        ctx = _ctx(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        return {"success": True, "data": await bulk.pratinjau_per_item(
            pool, ctx, "delete", ids, pratinjau, await _nomor(pool, ctx["tenant_id"], tabel, kolom, ids), baca_saja=True)}

    @router.post("/bulk/delete")
    async def bulk_delete(request: Request, body: BulkHapusRequest):
        ctx = _ctx(request)
        kunci = _kunci_wajib(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        return {"success": True, "data": await bulk.jalankan_per_item(
            pool, ctx, "delete", modul, ids, tulis, kunci_batch=kunci, nomor=await _nomor(pool, ctx["tenant_id"], tabel, kolom, ids))}

    return router


ROUTERS = {m: buat_router(m) for m in MODUL}
