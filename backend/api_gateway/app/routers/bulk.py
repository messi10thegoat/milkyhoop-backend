"""Aksi massal daftar (U1b F1, 5 Okt 2026): POST /api/{modul}/bulk/export -- CSV dari baris terpilih. Satu router per modul (prefiks
dipasang main.py; 8 modul Penjualan + 6 Pembelian B1a 28 Okt). Izin = R modul (middleware); baca saja + satu baris audit BULK_EXPORT. F2+ menambah aksi tulis lewat
services/bulk.jalankan_per_item; AKSI uang TIDAK PERNAH punya rute di berkas ini (daftar putih bulk.AKSI_DIIZINKAN + tes AST)."""
import logging
from typing import List, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import bulk
from ..services import bulk_export_beli as BB
from ..services.bulk_export import SPEC, kolom_tambahan, susun_baris
from ..services.kode_order import muat_setelan
from ..utils.tanggal_tenant import tanggal_dokumen

logger = logging.getLogger(__name__)

AKSI_EXPORT = "export"
assert AKSI_EXPORT in bulk.AKSI_DIIZINKAN  # rute hanya untuk aksi di daftar putih


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    return {"tenant_id": user["tenant_id"], "user_id": user.get("user_id") or user.get("id")}


class BulkExportRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=2000, description="id dokumen terpilih (urutan dipertahankan)")
    format: Literal["csv"] = "csv"  # pdf = F5


def buat_router(modul: str) -> APIRouter:
    spec = SPEC[modul]
    router = APIRouter()

    @router.post(f"/bulk/{AKSI_EXPORT}")
    async def bulk_export(request: Request, body: BulkExportRequest):
        ctx = _ctx(request)
        ids = bulk.validasi_ids(body.ids, "csv")
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                baris, ada, lewat = await susun_baris(conn, ctx["tenant_id"], spec, ids)
                if not ada:
                    raise HTTPException(status_code=404, detail={"code": "BULK_TAK_ADA",
                                                                 "message": "Dokumen yang dipilih tidak ditemukan."})
                await bulk.catat_audit(conn, ctx, AKSI_EXPORT, modul, ids, {"format": "csv", "ditemukan": ada, "dilewati": lewat})
            hari = await tanggal_dokumen(conn, ctx["tenant_id"])
            label_kode = (await muat_setelan(conn, ctx["tenant_id"]))["label"]
        isi = bulk.buat_csv(list(spec.kolom) + list(kolom_tambahan(label_kode)), baris)
        return _csv(spec.berkas, hari, isi, len(ids), ada, lewat)

    return router


def _csv(berkas: str, hari, isi: bytes, total: int, ada: int, lewat: int) -> Response:
    return Response(content=isi, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{berkas}-{hari.isoformat()}.csv"',
        "Cache-Control": "no-store", "X-Bulk-Total": str(total), "X-Bulk-Ditemukan": str(ada), "X-Bulk-Dilewati": str(lewat)})


def buat_router_beli(modul: str) -> APIRouter:
    """B1a (28 Okt 2026): ekspor CSV daftar PEMBELIAN -- bentuk, batas, audit, dan header SAMA dengan Penjualan; baris dari
    services/bulk_export_beli (pembantu turunan daftar). Kolom tambahan = Status (kode) · Referensi · ID (tanpa kode order)."""
    spec = BB.SPEC_BELI[modul]
    router = APIRouter()

    @router.post(f"/bulk/{AKSI_EXPORT}")
    async def bulk_export(request: Request, body: BulkExportRequest):
        ctx = _ctx(request)
        ids = bulk.validasi_ids(body.ids, "csv")
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                baris, ada, lewat = await BB.susun_baris(conn, ctx["tenant_id"], spec, ids)
                if not ada:
                    raise HTTPException(status_code=404, detail={"code": "BULK_TAK_ADA",
                                                                 "message": "Dokumen yang dipilih tidak ditemukan."})
                await bulk.catat_audit(conn, ctx, AKSI_EXPORT, modul, ids, {"format": "csv", "ditemukan": ada, "dilewati": lewat})
            hari = await tanggal_dokumen(conn, ctx["tenant_id"])
        return _csv(spec.berkas, hari, bulk.buat_csv(list(spec.kolom) + list(BB.KOLOM_TAMBAHAN), baris), len(ids), ada, lewat)

    return router


assert not set(SPEC) & set(BB.SPEC_BELI)
ROUTERS = {**{modul: buat_router(modul) for modul in SPEC}, **{modul: buat_router_beli(modul) for modul in BB.SPEC_BELI}}
