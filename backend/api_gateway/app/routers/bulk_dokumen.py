"""Aksi massal DOKUMEN (U1b F5, 5 Okt 2026, MASTER GO): PDF massal (ZIP per dokumen, <=25) + tautan bagikan massal (+ pratinjau).

POST /api/{modul}/bulk/pdf            body {ids}                -> ZIP application/zip: satu PDF per dokumen lewat renderer YANG ADA
   (dokumen._render + pdf_tercache = bytes sama dengan GET /documents/{kind}/{id}/pdf) + _RINGKASAN.txt; id tak ada/gagal/waktu habis
   dilewati (X-Bulk-Dilewati), bukan menggagalkan semuanya. Baca saja + 1 audit BULK_PDF. Izin = izin PDF tunggal (R modul).
POST /api/{modul}/bulk/share/preview  body {ids, channel?}      -> per item boleh/ditolak (draf/dibatalkan ditolak per item) via dokumen._bagikan
   YANG SAMA berurutan di satu transaksi lalu ROLLBACK (tautan tak tersisa).
POST /api/{modul}/bulk/share          body {ids, channel?} + X-Idempotency-Key (WAJIB) -> tiap item transaksi sendiri, idempotensi per item,
   batas 50, hasil per item. URL tautan (rahasia: token hanya di-hash di DB) dikembalikan HANYA pada respons pertama dan TIDAK disimpan di
   tabel idempotensi; replay mengembalikan share_id tanpa url. Izin = izin share tunggal (E modul). Penawaran draf ikut ditandai terkirim
   (aturan share tunggal). TANPA surel/WA dari server -- hanya tautan."""
import io
import logging
import re
import time
import uuid
import zipfile
from datetime import date
from typing import List, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import bulk
from ..services import idem_buat
from ..services.bulk_export import SPEC
from ..utils.tanggal_tenant import tanggal_dokumen
from . import dokumen as D

logger = logging.getLogger(__name__)

MODUL_KIND = {"sales-orders": "rekap", "quotes": "quotation", "proformas": "proforma", "sales-invoices": "invoice",
              "customer-deposits": "receipt", "receive-payments": "receipt", "deliveries": "delivery",
              "credit-notes": "credit_note"}
assert set(MODUL_KIND) == set(SPEC)
for _a in ("pdf", "share"):
    assert _a in bulk.AKSI_DIIZINKAN  # setiap aksi di berkas ini harus ada di daftar putih

BATAS_WAKTU_PDF = 40.0  # detik; sisa dokumen dilewati (tercatat) bila terlampaui -- tak menggantung sampai batas waktu proxy
_BATAL = {"void", "voided", "cancelled", "batal"}


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


class BulkIdsRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id dokumen terpilih (urutan dipertahankan)")


class BulkShareRequest(BulkIdsRequest):
    channel: Literal["link", "wa", "email"] = "link"


def _kunci_wajib(request: Request) -> str:
    kunci = idem_buat.kunci_dari(request)
    if not kunci:
        raise HTTPException(status_code=400, detail={"code": "BULK_KUNCI_WAJIB",
                                                     "message": "Aksi massal wajib membawa header X-Idempotency-Key."})
    return kunci


def _uuid(i):
    return i if isinstance(i, uuid.UUID) else uuid.UUID(str(i))


def _pesan(e: HTTPException) -> str:
    d = e.detail
    return d.get("message") if isinstance(d, dict) and d.get("message") else str(d)


async def _periksa_bagikan(conn, ctx: dict, kind: str, uid):
    """Penolakan PER ITEM yang tak ada di share tunggal: dokumen DRAF dan DIBATALKAN tak boleh dibagikan massal (penawaran: aturan
    penentunya sendiri di dokumen._bagikan -- draf penawaran boleh, ditandai terkirim). -> {nomor, status}."""
    try:
        dok = await D._dokumen_ada(conn, ctx["tenant_id"], kind, uid)
    except HTTPException as e:
        if e.status_code == 404 and not isinstance(e.detail, dict):
            raise HTTPException(status_code=404, detail={"code": "DOKUMEN_TAK_ADA", "message": e.detail})
        raise
    if kind == "quotation":
        return dok
    st = str(dok.get("status") or "").lower()
    label = D.LABEL_JENIS.get(kind, "Dokumen")
    if st in _BATAL:
        raise HTTPException(status_code=409, detail={"code": "DOKUMEN_BATAL",
                                                     "message": f"{label} {dok['nomor']} sudah dibatalkan; tak bisa dibagikan."})
    if st == "draft":
        raise HTTPException(status_code=409, detail={"code": "DOKUMEN_DRAF",
                                                     "message": f"{label} {dok['nomor']} masih draf; terbitkan dulu sebelum dibagikan."})
    return dok


def _fungsi_bagikan(request: Request, kind: str, channel: str):
    async def tulis(conn, ctx, i):
        uid = _uuid(i)
        await _periksa_bagikan(conn, ctx, kind, uid)
        hasil, info = await D._bagikan(conn, request, ctx, kind, uid, channel)
        r = {"share_id": hasil["id"], "number": info["number"], "channel": channel, "sent_at": hasil["sent_at"],
             "expires_at": hasil["expires_at"], "_rahasia": {"url": hasil["url"]}}
        if kind == "quotation":
            r["marked_sent"] = info.get("akan_ditandai_terkirim")
        return r

    async def pratinjau(conn, ctx, i):
        uid = _uuid(i)
        await _periksa_bagikan(conn, ctx, kind, uid)  # HTTPException = satu blok
        _, info = await D._bagikan(conn, request, ctx, kind, uid, channel)  # jalur yang SAMA; tautan hilang bersama rollback
        ring = {"number": info["number"], "status_now": info["status_now"], "channel": channel}
        if kind == "quotation":
            ring["akan_ditandai_terkirim"] = info.get("akan_ditandai_terkirim")
        return [], ring
    return tulis, pratinjau


def _nama_berkas(nomor: str, dipakai: set) -> str:
    dasar = re.sub(r"[^A-Za-z0-9._-]+", "_", str(nomor or "dokumen")).strip("._") or "dokumen"
    nama, n = dasar, 1
    while nama.lower() in dipakai:
        n += 1
        nama = f"{dasar}-{n}"
    dipakai.add(nama.lower())
    return f"{nama}.pdf"


def buat_zip(berkas: list, ringkasan: str) -> bytes:
    """ZIP dalam memori: tiap (nomor, bytes pdf) satu berkas + _RINGKASAN.txt. Nama aman (tanpa jalur), unik."""
    out = io.BytesIO()
    dipakai: set = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for nomor, pdf in berkas:
            z.writestr(_nama_berkas(nomor, dipakai), pdf)
        z.writestr("_RINGKASAN.txt", ringkasan.encode("utf-8"))
    return out.getvalue()


def buat_router(modul: str) -> APIRouter:
    spec, kind = SPEC[modul], MODUL_KIND[modul]
    router = APIRouter()

    @router.post("/bulk/pdf")
    async def bulk_pdf(request: Request, body: BulkIdsRequest):
        ctx = D._ctx(request)
        ids = bulk.validasi_ids(body.ids, "pdf")
        pool = await get_pool()
        berkas, lewat, mulai = [], [], time.monotonic()
        for i in ids:
            if time.monotonic() - mulai > BATAS_WAKTU_PDF:
                lewat.append((str(i), "waktu habis; ulangi untuk sisanya"))
                continue
            try:
                async with pool.acquire() as conn:
                    r, nomor = await D._render(conn, ctx, request, kind, str(i))
                berkas.append((nomor, await D.pdf_tercache(ctx["tenant_id"], r)))
            except HTTPException as e:
                lewat.append((str(i), _pesan(e)))
            except Exception:
                logger.error("bulk pdf %s item %s gagal", modul, i, exc_info=True)
                lewat.append((str(i), "gagal dirender"))
        if not berkas:
            raise HTTPException(status_code=404, detail={"code": "BULK_TAK_ADA", "message": "Dokumen yang dipilih tidak ditemukan atau gagal dirender."})
        ring = f"{len(berkas)} dari {len(ids)} dokumen masuk ZIP.\r\n" + "".join(f"DILEWATI {i}: {a}\r\n" for i, a in lewat)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await bulk.catat_audit(conn, ctx, "pdf", modul, ids, {"ditemukan": len(berkas), "dilewati": len(lewat)})
            hari = await tanggal_dokumen(conn, ctx["tenant_id"])
        isi = buat_zip(berkas, ring)
        return Response(content=isi, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{spec.berkas}-{hari.isoformat()}.zip"', "Cache-Control": "no-store",
            "X-Bulk-Total": str(len(ids)), "X-Bulk-Ditemukan": str(len(berkas)), "X-Bulk-Dilewati": str(len(lewat))})

    @router.post("/bulk/share/preview")
    async def bulk_share_preview(request: Request, body: BulkShareRequest):
        ctx = D._ctx(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        _, pratinjau = _fungsi_bagikan(request, kind, body.channel)
        return {"success": True, "data": await bulk.pratinjau_per_item(pool, ctx, "share", ids, pratinjau, await _nomor(pool, modul, ctx["tenant_id"], ids))}

    @router.post("/bulk/share")
    async def bulk_share(request: Request, body: BulkShareRequest):
        ctx = D._ctx(request)
        kunci = _kunci_wajib(request)
        ids = bulk.validasi_ids(body.ids, "tulis")
        pool = await get_pool()
        tulis, _ = _fungsi_bagikan(request, kind, body.channel)
        return {"success": True, "data": await bulk.jalankan_per_item(
            pool, ctx, "share", modul, ids, tulis, kunci_batch=kunci, payload={"channel": body.channel},
            nomor=await _nomor(pool, modul, ctx["tenant_id"], ids))}

    return router


_NOMOR_SQL = {
    "sales-orders": "SELECT id, order_number AS nomor FROM sales_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "quotes": "SELECT id, quote_number AS nomor FROM quotes WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "proformas": "SELECT id, proforma_number AS nomor FROM proformas WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "sales-invoices": "SELECT id, invoice_number AS nomor FROM sales_invoices WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "customer-deposits": "SELECT id, deposit_number AS nomor FROM customer_deposits WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "receive-payments": "SELECT id, payment_number AS nomor FROM receive_payments WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "deliveries": "SELECT id, fulfillment_number AS nomor FROM invoice_fulfillments WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
    "credit-notes": "SELECT id, credit_note_number AS nomor FROM credit_notes WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
}
assert set(_NOMOR_SQL) == set(SPEC)


async def _nomor(pool, modul: str, tenant_id: str, ids) -> dict:
    async with pool.acquire() as conn:
        return {str(r["id"]): r["nomor"] for r in await conn.fetch(_NOMOR_SQL[modul], tenant_id, ids)}


ROUTERS = {modul: buat_router(modul) for modul in SPEC}
