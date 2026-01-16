"""
Documents Router
================
File attachment management with S3/MinIO storage.
"""
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from ..config import settings
from ..schemas.documents import (
    AttachDocumentRequest,
    AttachDocumentResponse,
    DeleteDocumentResponse,
    DetachDocumentRequest,
    DetachDocumentResponse,
    DocumentAttachmentData,
    DocumentData,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentWithAttachments,
    EntityDocument,
    EntityDocumentsResponse,
    RecentDocumentsResponse,
    SearchDocumentsResponse,
    StorageUsageByCategory,
    StorageUsageData,
    StorageUsageResponse,
    UpdateDocumentRequest,
    UpdateDocumentResponse,
    UploadDocumentResponse,
)

router = APIRouter()

_pool: Optional[asyncpg.Pool] = None

# File size limits
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain", "text/csv",
    "application/zip", "application/x-rar-compressed",
}


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(**db_config, min_size=2, max_size=10)
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {
        "tenant_id": request.state.user["tenant_id"],
        "user_id": request.state.user.get("user_id"),
    }


def format_file_size(size_bytes: int) -> str:
    """Format file size for display"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("", response_model=DocumentListResponse)
async def list_documents(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    category: Optional[str] = None,
    search: Optional[str] = None,
):
    """List all documents"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        where_clauses = ["d.tenant_id = $1", "d.deleted_at IS NULL"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if category:
            where_clauses.append(f"d.category = ${param_idx}")
            params.append(category)
            param_idx += 1

        if search:
            where_clauses.append(f"(d.file_name ILIKE ${param_idx} OR d.title ILIKE ${param_idx})")
            params.append(f"%{search}%")
            param_idx += 1

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM documents d WHERE {where_sql}",
            *params
        )

        rows = await conn.fetch(
            f"""
            SELECT d.*,
                   (SELECT COUNT(*) FROM document_attachments WHERE document_id = d.id) as attachment_count
            FROM documents d
            WHERE {where_sql}
            ORDER BY d.uploaded_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """,
            *params, limit, skip
        )

        data = []
        for row in rows:
            doc = dict(row)
            doc["file_size_formatted"] = format_file_size(doc["file_size"]) if doc["file_size"] else None
            data.append(DocumentData(**doc))

        return DocumentListResponse(data=data, total=total, has_more=(skip + limit) < total)


@router.get("/recent", response_model=RecentDocumentsResponse)
async def get_recent_documents(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
):
    """Get recently uploaded documents"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            """
            SELECT d.*,
                   (SELECT COUNT(*) FROM document_attachments WHERE document_id = d.id) as attachment_count
            FROM documents d
            WHERE d.tenant_id = $1 AND d.deleted_at IS NULL
            ORDER BY d.uploaded_at DESC
            LIMIT $2
            """,
            ctx["tenant_id"], limit
        )

        data = []
        for row in rows:
            doc = dict(row)
            doc["file_size_formatted"] = format_file_size(doc["file_size"]) if doc["file_size"] else None
            data.append(DocumentData(**doc))

        return RecentDocumentsResponse(data=data, total=len(data))


@router.get("/search", response_model=SearchDocumentsResponse)
async def search_documents(
    request: Request,
    query: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[List[str]] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Search documents"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM search_documents($1, $2, $3, $4, $5, $6)",
            ctx["tenant_id"], query, category, tags, limit, offset
        )

        data = []
        for row in rows:
            doc = dict(row)
            doc["file_size_formatted"] = format_file_size(doc["file_size"]) if doc.get("file_size") else None
            data.append(DocumentData(**doc))

        return SearchDocumentsResponse(
            query=query,
            category=category,
            tags=tags,
            data=data,
            total=len(data),
            has_more=len(data) >= limit
        )


@router.get("/storage-usage", response_model=StorageUsageResponse)
async def get_storage_usage(request: Request):
    """Get storage usage statistics"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            "SELECT * FROM get_tenant_storage_usage($1)",
            ctx["tenant_id"]
        )

        by_category = []
        if row["by_category"]:
            import json
            cat_data = row["by_category"] if isinstance(row["by_category"], dict) else json.loads(row["by_category"])
            for cat, info in cat_data.items():
                by_category.append(StorageUsageByCategory(
                    category=cat,
                    count=info["count"],
                    size_bytes=info["size_bytes"],
                    size_formatted=format_file_size(info["size_bytes"])
                ))

        return StorageUsageResponse(
            data=StorageUsageData(
                total_documents=row["total_documents"],
                total_size_bytes=row["total_size_bytes"],
                total_size_mb=float(row["total_size_mb"]),
                total_size_formatted=format_file_size(row["total_size_bytes"]),
                by_category=by_category
            )
        )


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(request: Request, document_id: UUID):
    """Get document details with attachments"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        row = await conn.fetchrow(
            """
            SELECT d.*,
                   (SELECT COUNT(*) FROM document_attachments WHERE document_id = d.id) as attachment_count
            FROM documents d
            WHERE d.id = $1 AND d.tenant_id = $2 AND d.deleted_at IS NULL
            """,
            document_id, ctx["tenant_id"]
        )

        if not row:
            raise HTTPException(status_code=404, detail="Document not found")

        attachments = await conn.fetch(
            "SELECT * FROM document_attachments WHERE document_id = $1",
            document_id
        )

        doc = dict(row)
        doc["file_size_formatted"] = format_file_size(doc["file_size"]) if doc["file_size"] else None

        data = DocumentWithAttachments(
            **doc,
            attachments=[DocumentAttachmentData(**dict(a)) for a in attachments]
        )

        return DocumentDetailResponse(data=data)


@router.post("/upload", response_model=UploadDocumentResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    category: Optional[str] = Form("other"),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),  # Comma-separated
):
    """Upload a new document"""
    ctx = get_user_context(request)
    pool = await get_pool()

    # Validate file
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"File type {file.content_type} not allowed")

    # Read file content
    content = await file.read()
    file_size = len(content)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB")

    # Parse tags
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Generate file path
        file_path = await conn.fetchval(
            "SELECT generate_document_key($1, $2, $3)",
            ctx["tenant_id"], category or "other", file.filename
        )

        # In production: Upload to S3/MinIO here
        # For now, just store metadata

        # Calculate checksum
        import hashlib
        md5 = hashlib.md5(content).hexdigest()

        row = await conn.fetchrow(
            """
            INSERT INTO documents (
                tenant_id, file_name, original_name, file_type, file_size,
                storage_type, file_path, category, title, description, tags,
                checksum_md5, uploaded_by
            ) VALUES ($1, $2, $3, $4, $5, 's3', $6, $7, $8, $9, $10, $11, $12)
            RETURNING *
            """,
            ctx["tenant_id"], file.filename, file.filename, file.content_type,
            file_size, file_path, category, title, description, tag_list, md5,
            ctx.get("user_id")
        )

        doc = dict(row)
        doc["file_size_formatted"] = format_file_size(file_size)
        doc["attachment_count"] = 0

        return UploadDocumentResponse(data=DocumentData(**doc))


@router.patch("/{document_id}", response_model=UpdateDocumentResponse)
async def update_document(request: Request, document_id: UUID, body: UpdateDocumentRequest):
    """Update document metadata"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            document_id, ctx["tenant_id"]
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        updates = []
        params = []
        param_idx = 1

        for field in ["category", "subcategory", "title", "description", "tags"]:
            value = getattr(body, field, None)
            if value is not None:
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

        if not updates:
            doc = dict(existing)
            doc["file_size_formatted"] = format_file_size(doc["file_size"]) if doc["file_size"] else None
            doc["attachment_count"] = 0
            return UpdateDocumentResponse(data=DocumentData(**doc))

        params.append(document_id)

        row = await conn.fetchrow(
            f"""
            UPDATE documents SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
            RETURNING *
            """,
            *params
        )

        doc = dict(row)
        doc["file_size_formatted"] = format_file_size(doc["file_size"]) if doc["file_size"] else None
        doc["attachment_count"] = await conn.fetchval(
            "SELECT COUNT(*) FROM document_attachments WHERE document_id = $1",
            document_id
        )

        return UpdateDocumentResponse(data=DocumentData(**doc))


@router.delete("/{document_id}", response_model=DeleteDocumentResponse)
async def delete_document(request: Request, document_id: UUID):
    """Soft delete a document"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        existing = await conn.fetchrow(
            "SELECT * FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            document_id, ctx["tenant_id"]
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        await conn.execute(
            "UPDATE documents SET deleted_at = NOW() WHERE id = $1",
            document_id
        )

        # In production: Delete from S3 or schedule cleanup

        return DeleteDocumentResponse()


@router.post("/{document_id}/attach", response_model=AttachDocumentResponse)
async def attach_document(request: Request, document_id: UUID, body: AttachDocumentRequest):
    """Attach document to an entity"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        # Verify document exists
        doc = await conn.fetchval(
            "SELECT id FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            document_id, ctx["tenant_id"]
        )

        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        # Check for existing attachment
        existing = await conn.fetchval(
            """
            SELECT id FROM document_attachments
            WHERE document_id = $1 AND entity_type = $2 AND entity_id = $3
            """,
            document_id, body.entity_type, body.entity_id
        )

        if existing:
            raise HTTPException(status_code=400, detail="Document already attached to this entity")

        row = await conn.fetchrow(
            """
            INSERT INTO document_attachments (
                tenant_id, document_id, entity_type, entity_id, attachment_type, display_order, attached_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            ctx["tenant_id"], document_id, body.entity_type, body.entity_id,
            body.attachment_type, body.display_order, ctx.get("user_id")
        )

        return AttachDocumentResponse(attachment=DocumentAttachmentData(**dict(row)))


@router.delete("/{document_id}/detach", response_model=DetachDocumentResponse)
async def detach_document(request: Request, document_id: UUID, body: DetachDocumentRequest):
    """Detach document from an entity"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        deleted = await conn.fetchval(
            """
            DELETE FROM document_attachments
            WHERE document_id = $1 AND entity_type = $2 AND entity_id = $3 AND tenant_id = $4
            RETURNING id
            """,
            document_id, body.entity_type, body.entity_id, ctx["tenant_id"]
        )

        if not deleted:
            raise HTTPException(status_code=404, detail="Attachment not found")

        return DetachDocumentResponse()


@router.get("/{entity_type}/{entity_id}/documents", response_model=EntityDocumentsResponse)
async def get_entity_documents(
    request: Request,
    entity_type: str,
    entity_id: UUID,
):
    """Get all documents attached to an entity"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])

        rows = await conn.fetch(
            "SELECT * FROM get_entity_documents($1, $2, $3)",
            ctx["tenant_id"], entity_type, entity_id
        )

        return EntityDocumentsResponse(
            entity_type=entity_type,
            entity_id=entity_id,
            data=[EntityDocument(**dict(row)) for row in rows],
            total=len(rows)
        )
