"""Lampiran dokumen penjualan lewat SATU pabrik rute (LAMPIRAN-NK-QUOTE, 15 Okt 2026, putusan MASTER #5).

Nota kredit dan penawaran belum punya rute lampiran (grup NK/penawaran di GET /api/lampiran berurl null). Pola SAMA dengan
lampiran SO / Surat Jalan (sales_order_lampiran.py, delivery_lampiran.py) -- tidak disalin per modul, melainkan `buat_router`:
  POST   /api/{modul}/{id}/attachments                     multipart `files` (1..n), sukses sebagian; maks 10 x 10 MB
  GET    /api/{modul}/{id}/attachments                     -> {data, kuota}
  GET    /api/{modul}/{id}/attachments/{aid}/download      stream
  DELETE /api/{modul}/{id}/attachments/{aid}               lepas TAUTAN (berkas `documents` tak dihapus) + audit
Penyimpanan: MinIO `<tenant>/uploads/lampiran/<sha256><ext>` (BUKAN /tmp kontainer), `documents` storage_type='s3',
tautan `document_attachments` entity_type = jenis (CHECK chk_da_entity sudah memuat 'credit_note' & 'quote' -> tanpa migrasi).
Kuota dihitung DI DALAM transaksi yang memegang advisory lock per dokumen. Tenant eksplisit di tiap SQL (gateway BYPASSRLS).
NOL BUKU: tak ada jurnal/stok. Nama per TAUTAN (document_attachments.display_name, V411). Izin: ROUTE_PERMISSIONS per modul.
"""
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

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

PENANDA_LAMPIRAN_INDUK = "lampiran-induk-nk-quote-w20261015"
_SUBDIR = "lampiran"


@dataclass(frozen=True)
class SpecLampiran:
    entitas: str        # document_attachments.entity_type
    modul_url: str      # segmen URL modul (/api/{modul_url}/...)
    tabel: str          # tabel induk (berkolom id, tenant_id)
    kolom_nomor: str    # nomor dokumen untuk audit/404
    label: str          # nama dokumen untuk pesan ("Nota kredit")
    entitas_audit: str  # entity_type audit_logs (catat_riwayat)
    # LAMPIRAN-SO-SJ-PABRIK (15 Okt 2026): SO + SJ pindah ke pabrik dengan kontrak HTTP LAMA persis
    pesan_404: Optional[str] = None                       # teks 404 induk (None -> "{label} tidak ditemukan.")
    pra_cek: Optional[Callable[..., Awaitable]] = None    # async (conn, tenant_id) di SETIAP rute (SJ: flag pengiriman_serah)
    event_lepas: str = "LAMPIRAN_DILEPAS"
    ringkas_lepas: Optional[str] = None                   # templat audit lepas, {nomor}
    sumber_audit: Optional[str] = None
    modul_dep: Optional[str] = None                       # modul pemilik get_pool/get_storage_service/simpan_objek_unggahan
    nama_endpoint: dict = field(default_factory=dict)     # {unggah|daftar|unduh|lepas: nama fungsi} (tabel rute / tes lama)


async def get_pool():
    from ..services.db_pool import get_db_pool
    return await get_db_pool()


def _ctx(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")
    user_id = user.get("user_id") or user.get("id")
    return {"tenant_id": tenant_id, "user_id": str(user_id) if user_id else None}


def _nama(f: UploadFile, i: int) -> str:
    nama = (f.filename or "").replace("\r", "").replace("\n", "").strip()
    return (nama or f"lampiran-{i + 1}")[:255]


def _kuota(terpakai: int) -> dict:
    return {"terpakai": terpakai, "maks": MAKS_LAMPIRAN_PER_DOKUMEN}


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


def sql_spec(s: SpecLampiran) -> dict:
    """SQL per spesifikasi -- nama tabel/entitas dari SpecLampiran tetap (kode), tak pernah dari masukan pengguna."""
    e, t = s.entitas, s.tabel
    return {
        "induk": f"SELECT id, {s.kolom_nomor} AS nomor FROM {t} WHERE id = $1 AND tenant_id = $2",
        "urutan": f"SELECT count(*) FROM document_attachments WHERE tenant_id = $1 AND entity_type = '{e}' AND entity_id = $2",
        # tenant eksplisit juga di sini (salinan pola SJ/SO dulu tanpa tenant -- penjaga tes alias menangkapnya, 15 Okt)
        "sudah": f"SELECT 1 FROM document_attachments WHERE document_id = $1 AND entity_type = '{e}' AND entity_id = $2 AND tenant_id = $3",
        "taut": f"""INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id,
                        attachment_type, display_order, attached_by, display_name)
                    VALUES ($1, $2, '{e}', $3, 'attachment', $4, $5::uuid, $6)""",
        "daftar": f"""SELECT d.id, COALESCE(da.display_name, d.file_name) AS file_name, d.file_size, d.file_type,
                             d.storage_type, d.deleted_at, d.uploaded_at
                      FROM document_attachments da
                      JOIN documents d ON d.id = da.document_id
                      JOIN {t} ind ON ind.id = da.entity_id
                      WHERE da.entity_type = '{e}' AND da.entity_id = $1
                        AND ind.tenant_id = $2 AND da.tenant_id = $2 AND d.tenant_id = $2 AND d.deleted_at IS NULL
                      ORDER BY da.display_order, d.uploaded_at""",
        "unduh": f"""SELECT COALESCE(da.display_name, d.file_name) AS file_name, d.file_path, d.file_type, d.storage_type
                     FROM document_attachments da
                     JOIN documents d ON d.id = da.document_id
                     JOIN {t} ind ON ind.id = da.entity_id
                     WHERE d.id = $1 AND da.entity_id = $2 AND da.entity_type = '{e}'
                       AND ind.tenant_id = $3 AND da.tenant_id = $3 AND d.tenant_id = $3 AND d.deleted_at IS NULL""",
        "lepas": f"""DELETE FROM document_attachments da USING {t} ind
                     WHERE da.document_id = $1 AND da.entity_id = $2 AND da.entity_type = '{e}'
                       AND ind.id = da.entity_id AND ind.tenant_id = $3 AND da.tenant_id = $3
                     RETURNING da.id""",
    }


def buat_router(s: SpecLampiran) -> APIRouter:
    router = APIRouter()
    q = sql_spec(s)

    def dep(nama: str):
        """Ketergantungan dibaca dari modul pemilik (modul_dep) SAAT dipanggil -> tes lama yang menambal SL/DL tetap berlaku."""
        return getattr(sys.modules[s.modul_dep or __name__], nama)

    async def _cek(conn, tenant_id) -> None:
        if s.pra_cek is not None:
            await s.pra_cek(conn, tenant_id)

    async def _induk(conn, doc_id, tenant_id) -> str:
        await _cek(conn, tenant_id)
        r = await conn.fetchrow(q["induk"], doc_id, tenant_id)
        if not r:
            raise HTTPException(status_code=404, detail=s.pesan_404 or f"{s.label} tidak ditemukan.")
        return r["nomor"]

    @router.post("/{doc_id}/attachments")
    async def unggah(request: Request, doc_id: UUID,
                     files: List[UploadFile] = File(..., description="1..n berkas, maks 10 MB per berkas")):
        ctx = _ctx(request)
        tenant_id = ctx["tenant_id"]
        if not files:
            raise HTTPException(status_code=400, detail="Tidak ada berkas")
        hasil: list = [None] * len(files)
        calon = []
        for i, f in enumerate(files):
            nama = _nama(f, i)
            if i >= MAKS_LAMPIRAN_PER_DOKUMEN:
                hasil[i] = {"nama": nama, "ok": False, "galat": "kuota_penuh"}
                continue
            isi = await f.read()
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
        storage = dep("get_storage_service")() if calon else None
        pool = await dep("get_pool")()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
                await _induk(conn, doc_id, tenant_id)
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                                   kunci_kuota_lampiran(tenant_id, s.entitas, doc_id))
                terpakai = await hitung_lampiran_tersedia(conn, tenant_id, s.entitas, doc_id)
                urutan = int(await conn.fetchval(q["urutan"], tenant_id, doc_id) or 0)
                for i, nama, ctype, ext, isi, sha, kunci in calon:
                    if terpakai >= MAKS_LAMPIRAN_PER_DOKUMEN:
                        hasil[i] = {"nama": nama, "ok": False, "galat": "kuota_penuh"}
                        continue
                    try:
                        await dep("simpan_objek_unggahan")(storage, kunci, isi)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"[Lampiran {s.entitas}] simpan objek gagal: {type(e).__name__}")
                        hasil[i] = {"nama": nama, "ok": False, "galat": "penyimpanan"}
                        continue
                    try:
                        async with conn.transaction():
                            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"LAMPIRAN_FILE:{tenant_id}:{sha}")
                            dok = await conn.fetchval(_SQL_DOK_SAMA, tenant_id, sha, kunci)
                            if dok is None:
                                dok = await conn.fetchval(_SQL_DOK_BARU, tenant_id, nama, ctype, ext, len(isi), kunci, sha,
                                                          ctx["user_id"])
                            if await conn.fetchval(q["sudah"], dok, doc_id, tenant_id):
                                hasil[i] = {"nama": nama, "ok": False, "galat": "duplikat", "id": str(dok)}
                                continue
                            await conn.execute(q["taut"], tenant_id, dok, doc_id, urutan, ctx["user_id"], nama)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"[Lampiran {s.entitas}] simpan baris gagal: {type(e).__name__}: {e}")
                        hasil[i] = {"nama": nama, "ok": False, "galat": "gagal_simpan"}
                        continue
                    urutan += 1
                    terpakai += 1
                    hasil[i] = {"nama": nama, "ok": True, "id": str(dok)}
        return {"success": True, "hasil": hasil, "kuota": _kuota(terpakai)}

    @router.get("/{doc_id}/attachments")
    async def daftar(request: Request, doc_id: UUID):
        ctx = _ctx(request)
        tenant_id = ctx["tenant_id"]
        pool = await dep("get_pool")()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
                await _induk(conn, doc_id, tenant_id)
                rows = await conn.fetch(q["daftar"], doc_id, tenant_id)
        data = [{"id": str(r["id"]), "nama": r["file_name"], "ukuran": r["file_size"], "mime": r["file_type"],
                 "url": url_unduh_lampiran(s.modul_url, doc_id, r["id"]),
                 "tersedia": lampiran_tersedia(r["storage_type"], r["deleted_at"]),
                 "diunggah": r["uploaded_at"].isoformat() if r["uploaded_at"] else None} for r in rows]
        return {"success": True, "data": data, "kuota": _kuota(sum(1 for d in data if d["tersedia"]))}

    @router.get("/{doc_id}/attachments/{attachment_id}/download")
    async def unduh(request: Request, doc_id: UUID, attachment_id: UUID):
        ctx = _ctx(request)
        tenant_id = ctx["tenant_id"]
        pool = await dep("get_pool")()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
                await _cek(conn, tenant_id)
                row = await conn.fetchrow(q["unduh"], attachment_id, doc_id, tenant_id)
        if not row:
            raise HTTPException(status_code=404, detail="Lampiran tidak ditemukan.")
        return stream_lampiran(row, dep("get_storage_service")())

    @router.delete("/{doc_id}/attachments/{attachment_id}")
    async def lepas(request: Request, doc_id: UUID, attachment_id: UUID):
        from ..services.so_riwayat import catat_riwayat
        ctx = _ctx(request)
        tenant_id = ctx["tenant_id"]
        pool = await dep("get_pool")()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
                nomor = await _induk(conn, doc_id, tenant_id)
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                                   kunci_kuota_lampiran(tenant_id, s.entitas, doc_id))
                if not await conn.fetchval(q["lepas"], attachment_id, doc_id, tenant_id):
                    raise HTTPException(status_code=404, detail="Lampiran tidak ditemukan.")
                # Law 8/12: tautan dilepas = jejak audit di transaksi yang sama (gagal mencatat = batal)
                ringkas = (s.ringkas_lepas or f"Lampiran {s.label.lower()} dilepas ({{nomor}})").format(nomor=nomor)
                await catat_riwayat(conn, tenant_id, s.entitas_audit, doc_id, nomor, s.event_lepas, ctx["user_id"],
                                    ringkas, {"document_id": str(attachment_id)},
                                    source=s.sumber_audit or f"api:{s.modul_url}.attachments")
                terpakai = await hitung_lampiran_tersedia(conn, tenant_id, s.entitas, doc_id)
        return {"success": True, "kuota": _kuota(terpakai)}

    for r in router.routes:  # nama endpoint unik per modul (tabel rute / OpenAPI); SO/SJ memakai nama lama
        dasar = r.endpoint.__name__
        r.endpoint.__name__ = s.nama_endpoint.get(dasar, f"{dasar}_lampiran_{s.entitas}")
        r.endpoint.__qualname__ = r.endpoint.__name__
        if s.modul_dep:
            r.endpoint.__module__ = s.modul_dep
        r.name = r.endpoint.__name__
    return router


SPEC_NOTA_KREDIT = SpecLampiran("credit_note", "credit-notes", "credit_notes", "credit_note_number", "Nota kredit", "credit_notes")
SPEC_PENAWARAN = SpecLampiran("quote", "quotes", "quotes", "quote_number", "Penawaran", "quotes")
router_nota_kredit = buat_router(SPEC_NOTA_KREDIT)
router_penawaran = buat_router(SPEC_PENAWARAN)
# LAMPIRAN-PEMBELIAN F2a (30 Okt 2026): PO + Nota Debit lewat pabrik yang SAMA (chk_da_entity sudah memuat purchase_order & vendor_credit)
SPEC_PESANAN_PEMBELIAN = SpecLampiran("purchase_order", "purchase-orders", "purchase_orders", "po_number", "Pesanan pembelian",
                                      "purchase_orders")
SPEC_NOTA_DEBIT = SpecLampiran("vendor_credit", "vendor-credits", "vendor_credits", "credit_number", "Nota debit", "vendor_credits")
router_pesanan_pembelian = buat_router(SPEC_PESANAN_PEMBELIAN)
router_nota_debit = buat_router(SPEC_NOTA_DEBIT)
