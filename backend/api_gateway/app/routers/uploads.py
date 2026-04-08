"""
Generic file upload endpoint for forms (expense, bill, invoice, etc).
Stores file on disk + creates documents row + returns document_id.
Frontend includes document_id in entity create payload (attachment_ids[]).
"""
import os
import hashlib
import logging
from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi import Request as _Req
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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/uploads", tags=["uploads"])

UPLOAD_BASE_DIR = "/tmp/milkyhoop_uploads"
ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "application/pdf",
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

    # SHA-256 dedup
    file_hash = hashlib.sha256(content).hexdigest()
    ext = os.path.splitext(file.filename or "")[1].lower()
    store_dir = os.path.join(UPLOAD_BASE_DIR, tenant_id, "forms")
    store_path = os.path.join(store_dir, f"{file_hash}{ext}")

    # Save file (idempotent)
    if not os.path.exists(store_path):
        os.makedirs(store_dir, exist_ok=True)
        with open(store_path, "wb") as fh:
            fh.write(content)
        logger.info(f"[FormUpload] Stored: {file.filename} -> {file_hash[:12]}")
    else:
        logger.info(f"[FormUpload] Dedup hit: {file.filename} -> {file_hash[:12]}")

    pool = await get_session_db_pool()
    file_url = f"/api/v3/chat/files/{tenant_id}/forms/{file_hash}{ext}"
    relative_path = store_path[len(UPLOAD_BASE_DIR):].lstrip("/") if store_path.startswith(UPLOAD_BASE_DIR) else store_path

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
            # Dedup by checksum
            existing = await conn.fetchrow(
                "SELECT id, file_url FROM documents WHERE tenant_id = $1 AND checksum_sha256 = $2 AND deleted_at IS NULL LIMIT 1",
                tenant_id, file_hash,
            )
            if existing:
                return {
                    "success": True,
                    "data": {
                        "id": str(existing["id"]),
                        "file_name": file.filename,
                        "file_size": len(content),
                        "mime_type": file.content_type,
                        "url": existing["file_url"],
                    },
                }
            doc_id = await conn.fetchval(
                """INSERT INTO documents (
                    tenant_id, file_name, original_name, file_type, file_extension,
                    file_size, storage_type, file_path, file_url, category, checksum_sha256, source, uploaded_by
                ) VALUES ($1, $2, $3, $4, $5, $6, 'local', $7, $8, 'receipt', $9, 'form', $10::uuid)
                RETURNING id""",
                tenant_id, file.filename, file.filename, file.content_type, ext,
                len(content), relative_path, file_url, file_hash, user_id,
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
