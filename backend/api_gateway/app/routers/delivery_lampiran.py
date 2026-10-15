"""Lampiran Surat Jalan (11 Okt 2026, kontrak pengiriman serah): foto resi / tanda terima.

Cermin sales_order_lampiran.py (Unit L1): maks 10 berkas x 10 MB, unggah banyak, sukses sebagian.
  POST   /api/deliveries/{id}/attachments                     multipart, medan `files`
  GET    /api/deliveries/{id}/attachments                     -> {data, kuota}
  GET    /api/deliveries/{id}/attachments/{aid}/download      stream
  DELETE /api/deliveries/{id}/attachments/{aid}               lepas TAUTAN (berkas `documents` tak dihapus) + audit

Penyimpanan: MinIO `<tenant>/uploads/lampiran/<sha256><ext>` (BUKAN /tmp kontainer), `documents` storage_type='s3',
tautan `document_attachments` entity_type 'delivery' (CHECK sudah memuatnya). Induk = invoice_fulfillments (Surat Jalan;
tenant_id di tiap SQL). NOL BUKU: tak ada jurnal/stok. Di belakang flag `pengiriman_serah` (409 SERAH_NONAKTIF).
Izin: modul `sales_invoice` (rute /api/deliveries memakai modul itu di PermissionMiddleware).

NAMA per TAUTAN (12 Okt 2026, temuan WORKSPACE uji nyata kaos; pola NetSuite/SAP): berkas dideduplikasi per isi (sha256)
di `documents`, tetapi nama yang dilihat pengguna = nama UNGGAHAN tautan ini (`document_attachments.display_name`, V411).
Dulu PNG baru "tes-e2e-resi-....png" tampil sebagai dokumen lama "uji-r139-sah.png". Tautan lama (display_name NULL)
memakai nama dokumen.
"""
import hashlib
import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ..services import pengiriman_serah as _serah
from ..services.so_riwayat import catat_riwayat
from ..attachment_limits import (
    MAKS_LAMPIRAN_PER_DOKUMEN,
    hitung_lampiran_tersedia,
    kunci_kuota_lampiran,
    lampiran_tersedia,
    tentukan_tipe_lampiran,
)
from ..services.storage_service import get_storage_service
from ..utils.chat_file_path import kunci_unggahan, simpan_objek_unggahan
from ..utils.lampiran_unduh import stream_lampiran, url_unduh_lampiran

logger = logging.getLogger(__name__)

# Penanda unik (window_item.sh: harus ada di kode BERJALAN).
PENANDA_LAMPIRAN_SJ = "lampiran-sj-serah-kuota-dalam-txn"

_MODUL_URL = "deliveries"
_ENTITAS = "delivery"
_SUBDIR = "lampiran"


async def get_pool():
    """Singleton pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    user_id = user.get("user_id") or user.get("id")
    return {"tenant_id": tenant_id, "user_id": str(user_id) if user_id else None}


# LAMPIRAN-SO-SJ-PABRIK (15 Okt 2026, putusan MASTER): unggah/daftar/unduh/lepas lampiran SJ kini lewat SATU pabrik
# routers/lampiran_induk.buat_router (tenant di tiap SQL -- cek "sudah tertaut" dulu tanpa tenant). Kontrak HTTP LAMA persis:
# flag pengiriman_serah di SETIAP rute (409 SERAH_NONAKTIF), 404 "Surat Jalan tidak ditemukan", audit lepas SJ_LAMPIRAN_DILEPAS
# berentitas invoice_fulfillments, nama fungsi lama. _ENTITAS = "delivery" (entity_type document_attachments).
from . import lampiran_induk as _LI  # noqa: E402

_SQL_SO = "SELECT id, fulfillment_number FROM invoice_fulfillments WHERE id = $1 AND tenant_id = $2"


async def _pra_cek(conn, tenant_id: str) -> None:
    await _serah.pastikan_aktif(conn, tenant_id)


SPEC_LAMPIRAN_SJ = _LI.SpecLampiran(
    _ENTITAS, _MODUL_URL, "invoice_fulfillments", "fulfillment_number", "Surat Jalan", "invoice_fulfillments",
    pesan_404="Surat Jalan tidak ditemukan", pra_cek=_pra_cek, event_lepas="SJ_LAMPIRAN_DILEPAS",
    ringkas_lepas="Lampiran Surat Jalan dilepas ({nomor})", sumber_audit="api:deliveries.attachments",
    modul_dep=__name__,
    nama_endpoint={"unggah": "unggah_lampiran_sj", "daftar": "daftar_lampiran_sj", "unduh": "unduh_lampiran_sj",
                   "lepas": "lepas_lampiran_sj"})
router = _LI.buat_router(SPEC_LAMPIRAN_SJ)
_EP = {r.endpoint.__name__: r.endpoint for r in router.routes}
unggah_lampiran_sj = _EP["unggah_lampiran_sj"]
daftar_lampiran_sj = _EP["daftar_lampiran_sj"]
unduh_lampiran_sj = _EP["unduh_lampiran_sj"]
lepas_lampiran_sj = _EP["lepas_lampiran_sj"]
_SQL = _LI.sql_spec(SPEC_LAMPIRAN_SJ)
_SQL_TAUT, _SQL_DAFTAR, _SQL_UNDUH, _SQL_LEPAS = _SQL["taut"], _SQL["daftar"], _SQL["unduh"], _SQL["lepas"]
