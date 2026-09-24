"""Unggahan PERSISTEN (Unit U1, 24 Sep 2026): kunci objek MinIO untuk berkas
unggahan form & chat + penyajiannya lewat GET /api/v3/chat/files/{kunci}.

LATAR: unggahan form (routers/uploads.py) dan chat (unified_chat
_store_upload_file) dulu ditulis ke /tmp/milkyhoop_uploads di dalam kontainer
(tak di-mount) -> hilang tiap kontainer dibuat ulang; baris `documents` /
`chat_attachments` jadi referensi mati. Kini isi berkas disimpan di MinIO
(bucket `milkyhoop-documents`, persisten) dengan kunci DETERMINISTIK:

    <tenant_id>/uploads/<forms|chat|documents|lampiran>/<sha256 64 hex><ext>

tenant dari JWT, sha256 dihitung server, ext dari allowlist server (bukan nama
berkas mentah). Modul ini = SATU-SATUNYA sumber bentuk kunci: penulis
membangunnya lewat `kunci_unggahan`, rute penyaji memvalidasinya lewat
`kunci_sah_milik_tenant`.

Rute penyaji TIDAK PERNAH membaca disk. Kunci bentuk lama
(`<tenant>/<chat|documents|forms>/<sha><ext>`, berkas lokal yang sudah hilang)
-> 404 bersih. Semua penolakan = 404 (bukan 403) supaya tak jadi oracle.
"""

import asyncio
import logging
import re
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Penanda unik (window_item.sh memastikan kode ini terpasang).
PENANDA_UNGGAHAN_U1 = "unggahan-persisten-u1-minio"

PENANDA_UNGGAHAN_U2 = "unggahan-persisten-u2-pembaca-minio"

URL_BERKAS_PREFIX = "/api/v3/chat/files/"
# U2: `documents` = unggahan /api/document-intake (dulu disk <t>/documents/).
# L1: `lampiran` = unggahan rute lampiran dokumen (mis. /api/sales-orders/{id}/attachments).
SUBDIR_UNGGAHAN = frozenset({"forms", "chat", "documents", "lampiran"})

# Ext -> Content-Type objek. Gabungan allowlist penulis form (uploads.py,
# dari content-type) dan chat (UPLOAD_ALLOWED_EXTENSIONS). Ext di luar ini tak
# pernah menjadi bagian kunci.
TIPE_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".ofx": "application/x-ofx",
    # L1 lampiran SO: sisa ekstensi resmi (attachment_limits.EKSTENSI_LAMPIRAN)
    # supaya kuncinya sah. Tak satu pun masuk TIPE_INLINE -> selalu diunduh.
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}

# Hanya tipe ini yang boleh tampil inline; selain itu diunduh sebagai lampiran
# (kebijakan 888a9058 dipertahankan).
TIPE_INLINE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}

_TENANT_SAH = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_NAMA_SAH = re.compile(r"^([0-9a-f]{64})(\.[a-z0-9]{1,10})$")


def kunci_unggahan(tenant_id: str, subdir: str, sha256_hex: str, ext: str) -> str:
    """Kunci objek MinIO untuk satu unggahan. Gagal-keras bila bagian tak sah
    (penulis tak boleh diam-diam menulis kunci yang tak bisa disajikan)."""
    kunci = f"{tenant_id}/uploads/{subdir}/{sha256_hex}{ext}"
    if not kunci_sah_milik_tenant(tenant_id, kunci):
        raise ValueError("kunci unggahan tidak sah")
    return kunci


def url_berkas(kunci: str) -> str:
    """URL relatif gateway (bukan presign :9000) untuk kunci unggahan."""
    return URL_BERKAS_PREFIX + kunci


def kunci_sah_milik_tenant(tenant_id: str, kunci: str) -> bool:
    """True hanya untuk `<tenant_id>/uploads/<forms|chat|documents|lampiran>/<sha256><ext>` persis,
    tenant = tenant pemanggil, ext di TIPE_EXT. Menolak `..`, segmen kosong,
    backslash, `%`, huruf besar, dan bentuk lama tanpa `uploads`."""
    if not isinstance(tenant_id, str) or not _TENANT_SAH.match(tenant_id):
        return False
    if not isinstance(kunci, str) or "\\" in kunci or "%" in kunci:
        return False
    bagian = kunci.split("/")
    if len(bagian) != 4:
        return False
    tenant_bagian, akar, subdir, nama = bagian
    if tenant_bagian != tenant_id or akar != "uploads" or subdir not in SUBDIR_UNGGAHAN:
        return False
    m = _NAMA_SAH.match(nama)
    if not m or m.group(2) not in TIPE_EXT:
        return False
    return True


def ext_dari_kunci(kunci: str) -> str:
    m = _NAMA_SAH.match(kunci.rsplit("/", 1)[-1])
    return m.group(2) if m else ""


def tipe_sajian(kunci: str):
    """(media_type, inline?) -- tipe di luar TIPE_INLINE diunduh, bukan dirender."""
    ext = ext_dari_kunci(kunci)
    if ext in TIPE_INLINE:
        return TIPE_INLINE[ext], True
    return "application/octet-stream", False


async def simpan_objek_unggahan(storage, kunci: str, isi: bytes) -> None:
    """PUT isi ke MinIO di threadpool (boto3 sinkron tak boleh memblokir event
    loop). Metadata SENGAJA kosong: nama berkas asli (bisa non-ASCII) tidak
    dibawa ke objek -- tinggal di baris `documents`/`chat_attachments`.
    Kunci deterministik -> PUT ulang isi yang sama = idempoten."""
    await asyncio.to_thread(
        storage.client.put_object,
        Bucket=storage.config.bucket,
        Key=kunci,
        Body=isi,
        ContentType=TIPE_EXT.get(ext_dari_kunci(kunci), "application/octet-stream"),
    )


def _kode_galat(e: Exception) -> str:
    try:
        return e.response["Error"]["Code"]  # botocore ClientError
    except Exception:  # noqa: BLE001
        return ""


async def sajikan_objek_unggahan(
    storage, tenant_id: str, kunci: str
) -> StreamingResponse:
    """Stream objek unggahan milik tenant. Kunci tak sah / objek tak ada -> 404
    (tanpa menyentuh storage untuk kunci tak sah, tanpa membaca disk)."""
    if not kunci_sah_milik_tenant(tenant_id, kunci):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        obj = await asyncio.to_thread(
            storage.client.get_object, Bucket=storage.config.bucket, Key=kunci
        )
    except Exception as e:  # noqa: BLE001
        if _kode_galat(e) in ("NoSuchKey", "404", "NotFound"):
            raise HTTPException(status_code=404, detail="File not found")
        logger.error(f"[BerkasUnggahan] get_object gagal: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Gagal mengambil berkas")

    body = obj["Body"]

    def iter_body():
        try:
            while chunk := body.read(65536):
                yield chunk
        finally:
            body.close()

    content_type, inline = tipe_sajian(kunci)
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600",
    }
    if not inline:
        nama = kunci.rsplit("/", 1)[-1]  # <sha><ext>: ASCII, aman untuk header
        headers["Content-Disposition"] = f'attachment; filename="{nama}"'
    return StreamingResponse(iter_body(), media_type=content_type, headers=headers)


_KODE_TAK_ADA = ("NoSuchKey", "404", "NotFound")


async def ambil_objek_unggahan(storage, tenant_id: str, kunci: str) -> Optional[bytes]:
    """Unit U2: ISI objek unggahan milik tenant untuk pembaca TERTUNDA (impor
    rekening via file_ref, antrean multidoc, document-intake). Kunci tak sah /
    milik tenant lain -> None TANPA menyentuh storage. Objek tak ada -> None.
    Galat storage lain DILEMPAR (bukan disamarkan jadi "tak ada" -> pembaca
    tak menghapus referensi karena MinIO sesaat mati). Tanpa disk; boto3
    sinkron (get + read) di threadpool."""
    if not kunci_sah_milik_tenant(tenant_id, kunci):
        return None

    def _ambil() -> bytes:
        obj = storage.client.get_object(Bucket=storage.config.bucket, Key=kunci)
        body = obj["Body"]
        try:
            return body.read()
        finally:
            body.close()

    try:
        return await asyncio.to_thread(_ambil)
    except Exception as e:  # noqa: BLE001
        if _kode_galat(e) in _KODE_TAK_ADA:
            return None
        raise


async def objek_unggahan_ada(storage, tenant_id: str, kunci: str) -> bool:
    """Unit U2: cek keberadaan (head_object di threadpool) untuk gerbang
    workflow. Kunci tak sah -> False tanpa menyentuh storage; tak ada ->
    False; galat storage lain DILEMPAR."""
    if not kunci_sah_milik_tenant(tenant_id, kunci):
        return False
    try:
        await asyncio.to_thread(
            storage.client.head_object, Bucket=storage.config.bucket, Key=kunci
        )
        return True
    except Exception as e:  # noqa: BLE001
        if _kode_galat(e) in _KODE_TAK_ADA:
            return False
        raise


def isi_atau_none(fm: dict) -> Optional[bytes]:
    """Isi berkas unggahan dari file_meta request yang SAMA (di memori)."""
    isi = fm.get("_isi") if isinstance(fm, dict) else None
    return isi if isinstance(isi, (bytes, bytearray)) and isi else None
