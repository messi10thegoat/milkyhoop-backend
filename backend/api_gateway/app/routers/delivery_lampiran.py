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
router = APIRouter()

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


_SQL_SO = "SELECT id, fulfillment_number FROM invoice_fulfillments WHERE id = $1 AND tenant_id = $2"

_SQL_URUTAN = """
    SELECT count(*) FROM document_attachments
    WHERE tenant_id = $1 AND entity_type = 'delivery' AND entity_id = $2
"""

_SQL_DOK_SAMA = """
    SELECT id FROM documents
    WHERE tenant_id = $1 AND checksum_sha256 = $2 AND storage_type = 's3'
      AND file_path = $3 AND deleted_at IS NULL
    LIMIT 1
"""

_SQL_DOK_BARU = """
    INSERT INTO documents (
        tenant_id, file_name, original_name, file_type, file_extension,
        file_size, storage_type, file_path, file_url, category,
        checksum_sha256, source, uploaded_by
    ) VALUES ($1, $2, $2, $3, $4, $5, 's3', $6, NULL, 'other', $7, 'upload', $8::uuid)
    RETURNING id
"""

_SQL_SUDAH_TERTAUT = """
    SELECT 1 FROM document_attachments
    WHERE document_id = $1 AND entity_type = 'delivery' AND entity_id = $2
"""

_SQL_TAUT = """
    INSERT INTO document_attachments (
        tenant_id, document_id, entity_type, entity_id,
        attachment_type, display_order, attached_by
    ) VALUES ($1, $2, 'delivery', $3, 'attachment', $4, $5::uuid)
"""

_SQL_DAFTAR = """
    SELECT d.id, d.file_name, d.file_size, d.file_type, d.storage_type,
           d.deleted_at, d.uploaded_at
    FROM document_attachments da
    JOIN documents d ON d.id = da.document_id
    JOIN invoice_fulfillments so ON so.id = da.entity_id
    WHERE da.entity_type = 'delivery' AND da.entity_id = $1
      AND so.tenant_id = $2 AND da.tenant_id = $2 AND d.tenant_id = $2
      AND d.deleted_at IS NULL
    ORDER BY da.display_order, d.uploaded_at
"""

_SQL_UNDUH = """
    SELECT d.file_name, d.file_path, d.file_type, d.storage_type
    FROM document_attachments da
    JOIN documents d ON d.id = da.document_id
    JOIN invoice_fulfillments so ON so.id = da.entity_id
    WHERE d.id = $1 AND da.entity_id = $2
      AND da.entity_type = 'delivery'
      AND so.tenant_id = $3 AND da.tenant_id = $3 AND d.tenant_id = $3
      AND d.deleted_at IS NULL
"""

_SQL_LEPAS = """
    DELETE FROM document_attachments da
    USING invoice_fulfillments so
    WHERE da.document_id = $1 AND da.entity_id = $2
      AND da.entity_type = 'delivery'
      AND so.id = da.entity_id AND so.tenant_id = $3 AND da.tenant_id = $3
    RETURNING da.id
"""


def _kuota(terpakai: int) -> dict:
    return {"terpakai": terpakai, "maks": MAKS_LAMPIRAN_PER_DOKUMEN}


def _nama(f: UploadFile, i: int) -> str:
    nama = (f.filename or "").replace("\r", "").replace("\n", "").strip()
    return (nama or f"lampiran-{i + 1}")[:255]


async def _pastikan_so(conn, delivery_id: UUID, tenant_id: str):
    """Flag dulu (tenant tanpa fitur -> 409), lalu Surat Jalan MILIK tenant (lain/tak ada -> 404). -> nomor SJ."""
    await _serah.pastikan_aktif(conn, tenant_id)
    r = await conn.fetchrow(_SQL_SO, delivery_id, tenant_id)
    if not r:
        raise HTTPException(status_code=404, detail="Surat Jalan tidak ditemukan")
    return r["fulfillment_number"]


@router.post("/{delivery_id}/attachments")
async def unggah_lampiran_sj(
    request: Request,
    delivery_id: UUID,
    files: List[UploadFile] = File(..., description="1..n berkas, maks 10 MB per berkas"),
):
    ctx = _ctx(request)
    tenant_id = ctx["tenant_id"]
    if not files:
        raise HTTPException(status_code=400, detail="Tidak ada berkas")

    # 1) Baca + golongkan per berkas (tanpa DB). Berkas ke-(MAKS+1) dst. tak
    #    dibaca: kuota per dokumen tak mungkin memuatnya.
    hasil: list = [None] * len(files)
    calon = []
    for i, f in enumerate(files):
        nama = _nama(f, i)
        if i >= MAKS_LAMPIRAN_PER_DOKUMEN:
            hasil[i] = {"nama": nama, "ok": False, "galat": "kuota_penuh"}
            continue
        isi = await f.read()
        # Tipe dari EKSTENSI + tanda tangan byte, bukan content_type klien saja.
        ctype, ext, galat = tentukan_tipe_lampiran(f.filename, f.content_type, isi)
        if galat:
            hasil[i] = {"nama": nama, "ok": False, "galat": galat}
            continue
        sha = hashlib.sha256(isi).hexdigest()
        try:
            kunci = kunci_unggahan(tenant_id, _SUBDIR, sha, ext)
        except ValueError:
            raise HTTPException(status_code=400, detail="Konteks tenant tidak sah")
        calon.append((i, nama, ctype, ext, isi, sha, kunci))

    storage = get_storage_service() if calon else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await _pastikan_so(conn, delivery_id, tenant_id)
            # Kuota: lock DULU, hitung SESUDAHNYA, semua di transaksi yang sama.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                kunci_kuota_lampiran(tenant_id, _ENTITAS, delivery_id),
            )
            terpakai = await hitung_lampiran_tersedia(conn, tenant_id, _ENTITAS, delivery_id)
            urutan = int(await conn.fetchval(_SQL_URUTAN, tenant_id, delivery_id) or 0)

            for i, nama, ctype, ext, isi, sha, kunci in calon:
                if terpakai >= MAKS_LAMPIRAN_PER_DOKUMEN:
                    hasil[i] = {"nama": nama, "ok": False, "galat": "kuota_penuh"}
                    continue
                # Objek dulu, baris sesudahnya: baris s3 hanya ada bila objeknya
                # ada. Kunci deterministik -> PUT ulang isi sama idempoten.
                try:
                    await simpan_objek_unggahan(storage, kunci, isi)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"[LampiranSJ] simpan objek gagal: {type(e).__name__}")
                    hasil[i] = {"nama": nama, "ok": False, "galat": "penyimpanan"}
                    continue
                try:
                    # Savepoint per berkas: galat DB satu berkas tak membatalkan
                    # berkas lain yang sudah tersimpan.
                    async with conn.transaction():
                        await conn.execute(
                            "SELECT pg_advisory_xact_lock(hashtext($1))",
                            f"LAMPIRAN_FILE:{tenant_id}:{sha}",
                        )
                        doc_id = await conn.fetchval(_SQL_DOK_SAMA, tenant_id, sha, kunci)
                        if doc_id is None:
                            doc_id = await conn.fetchval(
                                _SQL_DOK_BARU, tenant_id, nama, ctype, ext, len(isi),
                                kunci, sha, ctx["user_id"],
                            )
                        if await conn.fetchval(_SQL_SUDAH_TERTAUT, doc_id, delivery_id):
                            hasil[i] = {"nama": nama, "ok": False, "galat": "duplikat", "id": str(doc_id)}
                            continue
                        await conn.execute(
                            _SQL_TAUT, tenant_id, doc_id, delivery_id, urutan, ctx["user_id"]
                        )
                except Exception as e:  # noqa: BLE001
                    logger.error(f"[LampiranSJ] simpan baris gagal: {type(e).__name__}: {e}")
                    hasil[i] = {"nama": nama, "ok": False, "galat": "gagal_simpan"}
                    continue
                urutan += 1
                terpakai += 1
                hasil[i] = {"nama": nama, "ok": True, "id": str(doc_id)}

    return {"success": True, "hasil": hasil, "kuota": _kuota(terpakai)}


@router.get("/{delivery_id}/attachments")
async def daftar_lampiran_sj(request: Request, delivery_id: UUID):
    ctx = _ctx(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await _pastikan_so(conn, delivery_id, tenant_id)
            rows = await conn.fetch(_SQL_DAFTAR, delivery_id, tenant_id)
    data = []
    for r in rows:
        data.append(
            {
                "id": str(r["id"]),
                "nama": r["file_name"],
                "ukuran": r["file_size"],
                "mime": r["file_type"],
                "url": url_unduh_lampiran(_MODUL_URL, delivery_id, r["id"]),
                "tersedia": lampiran_tersedia(r["storage_type"], r["deleted_at"]),
                "diunggah": r["uploaded_at"].isoformat() if r["uploaded_at"] else None,
            }
        )
    terpakai = sum(1 for d in data if d["tersedia"])
    return {"success": True, "data": data, "kuota": _kuota(terpakai)}


@router.get("/{delivery_id}/attachments/{attachment_id}/download")
async def unduh_lampiran_sj(request: Request, delivery_id: UUID, attachment_id: UUID):
    ctx = _ctx(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await _serah.pastikan_aktif(conn, tenant_id)
            row = await conn.fetchrow(_SQL_UNDUH, attachment_id, delivery_id, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lampiran tidak ditemukan.")
    return stream_lampiran(row, get_storage_service())


@router.delete("/{delivery_id}/attachments/{attachment_id}")
async def lepas_lampiran_sj(request: Request, delivery_id: UUID, attachment_id: UUID):
    ctx = _ctx(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            nomor = await _pastikan_so(conn, delivery_id, tenant_id)
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                kunci_kuota_lampiran(tenant_id, _ENTITAS, delivery_id),
            )
            lepas = await conn.fetchval(_SQL_LEPAS, attachment_id, delivery_id, tenant_id)
            if not lepas:
                raise HTTPException(status_code=404, detail="Lampiran tidak ditemukan.")
            # Law 8/12: tautan dilepas = jejak audit di transaksi yang sama (gagal mencatat = batal)
            await catat_riwayat(conn, tenant_id, "invoice_fulfillments", delivery_id, nomor, "SJ_LAMPIRAN_DILEPAS",
                                ctx["user_id"], f"Lampiran Surat Jalan dilepas ({nomor})",
                                {"document_id": str(attachment_id)}, source="api:deliveries.attachments")
            terpakai = await hitung_lampiran_tersedia(conn, tenant_id, _ENTITAS, delivery_id)
    return {"success": True, "kuota": _kuota(terpakai)}
