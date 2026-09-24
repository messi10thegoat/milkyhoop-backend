"""
Generic file upload endpoint for forms (expense, bill, invoice, etc).
Stores file in MinIO (persisten) + creates documents row + returns document_id.
Frontend includes document_id in entity create payload (attachment_ids[]).

Unit U1 (24 Sep 2026): dulu ditulis ke /tmp/milkyhoop_uploads di kontainer
(hilang tiap recreate). Kini objek MinIO berkunci deterministik
`<tenant>/uploads/forms/<sha256><ext>` (lihat app/utils/chat_file_path.py);
baris documents storage_type='s3'. Tak ada tulisan ke disk.
"""
import hashlib
import logging
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from uuid import UUID as _UUID

def get_user_context(request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": _UUID(user_id) if user_id else None}
from ..services.db_pool import get_db_pool as get_session_db_pool
from ..services.storage_service import get_storage_service
from ..utils.chat_file_path import kunci_unggahan, simpan_objek_unggahan, url_berkas

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Tipe yang diterima -> ext kunci objek (ditentukan server, BUKAN dari nama
# berkas mentah: "nota.html" bertipe image/png tetap .png).
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
}
MAX_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/document")
async def upload_document_for_form(
    request: Request,
    file: UploadFile = File(...),
):
    """
    Upload file as a document. Returns document_id for use in entity creation forms.
    Used by: expense create form, bill create form, invoice create form, etc.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    user_id = ctx.get("user_id")

    # Validate
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"File type {file.content_type} not allowed")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Max {MAX_SIZE // (1024*1024)}MB")

    file_hash = hashlib.sha256(content).hexdigest()
    ext = ALLOWED_TYPES[file.content_type]
    try:
        kunci = kunci_unggahan(tenant_id, "forms", file_hash, ext)
    except ValueError:
        raise HTTPException(status_code=400, detail="Konteks tenant tidak sah")
    file_url = url_berkas(kunci)

    # Objek dulu, baris sesudahnya: baris s3 hanya ada bila objeknya ada.
    # Kunci deterministik -> PUT ulang isi yang sama idempoten.
    try:
        await simpan_objek_unggahan(get_storage_service(), kunci, content)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[FormUpload] Gagal menyimpan objek: {type(e).__name__}")
        raise HTTPException(status_code=503, detail="Penyimpanan berkas tidak tersedia")
    logger.info(f"[FormUpload] Stored -> {file_hash[:12]}{ext}")

    pool = await get_session_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"FORM_FILE:{tenant_id}:{file_hash}",
            )
            # Dedup HANYA ke baris s3 berkunci sama. Baris 'local' lama
            # (berkasnya sudah hilang) diabaikan -> unggahan ulang membuat baris
            # s3 BARU; baris s3 lain (mis. /api/documents, file_url NULL) juga
            # tak dipakai supaya url tak pernah NULL.
            existing = await conn.fetchrow(
                "SELECT id FROM documents WHERE tenant_id = $1 AND checksum_sha256 = $2"
                " AND storage_type = 's3' AND file_path = $3 AND deleted_at IS NULL LIMIT 1",
                tenant_id, file_hash, kunci,
            )
            if existing:
                doc_id = existing["id"]
                logger.info(f"[FormUpload] Dedup hit: {file_hash[:12]} -> {doc_id}")
            else:
                doc_id = await conn.fetchval(
                    """INSERT INTO documents (
                        tenant_id, file_name, original_name, file_type, file_extension,
                        file_size, storage_type, file_path, file_url, category, checksum_sha256, source, uploaded_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, 's3', $7, $8, 'receipt', $9, 'form', $10::uuid)
                    RETURNING id""",
                    tenant_id, file.filename or f"upload{ext}", file.filename, file.content_type, ext,
                    len(content), kunci, file_url, file_hash, user_id,
                )

    return {
        "success": True,
        "data": {
            "id": str(doc_id),
            "file_name": file.filename,
            "file_size": len(content),
            "mime_type": file.content_type,
            "url": file_url,
        },
    }
