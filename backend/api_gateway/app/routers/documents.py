"""
Documents Router
================
File attachment management with S3/MinIO storage.
"""
from typing import List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

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

from ..services.storage_service import get_storage_service  # noqa: E402


# File size limits — SATU SUMBER: app/attachment_limits.py (10 MB + 14 tipe).
from ..attachment_limits import (  # noqa: E402
    ATTACHMENT_ALLOWED_TYPES,
    ATTACHMENT_MAX_BYTES,
    enforce_attachment_limits,
    MAKS_LAMPIRAN_PER_DOKUMEN,
    baca_lampiran_atau_400,
    hitung_lampiran_tersedia,
    http_400_lampiran,
    kunci_kuota_lampiran,
)
from ..utils.lampiran_unduh import content_disposition_lampiran, sajian_lampiran  # noqa: E402

MAX_FILE_SIZE = ATTACHMENT_MAX_BYTES  # alias untuk referensi lain (storage-usage dsb.)
ALLOWED_CONTENT_TYPES = ATTACHMENT_ALLOWED_TYPES


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


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
    entity_type: Optional[str] = None,
    entity_id: Optional[UUID] = None,
):
    """List all documents. entity_type/entity_id filter to docs attached to that entity
    (via document_attachments); dulu diam-diam diabaikan -> hasil salah."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        where_clauses = ["d.tenant_id = $1", "d.deleted_at IS NULL"]
        params = [ctx["tenant_id"]]
        param_idx = 2

        if category:
            where_clauses.append(f"d.category = ${param_idx}")
            params.append(category)
            param_idx += 1

        if search:
            where_clauses.append(
                f"(d.file_name ILIKE ${param_idx} OR d.title ILIKE ${param_idx})"
            )
            params.append(f"%{search}%")
            param_idx += 1

        if entity_type or entity_id:
            _att = ["da.document_id = d.id"]
            if entity_type:
                _att.append(f"da.entity_type = ${param_idx}")
                params.append(entity_type)
                param_idx += 1
            if entity_id:
                _att.append(f"da.entity_id = ${param_idx}")
                params.append(entity_id)
                param_idx += 1
            where_clauses.append(
                "EXISTS (SELECT 1 FROM document_attachments da WHERE "
                + " AND ".join(_att) + ")"
            )

        where_sql = " AND ".join(where_clauses)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM documents d WHERE {where_sql}", *params
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
            *params,
            limit,
            skip,
        )

        data = []
        for row in rows:
            doc = dict(row)
            doc["file_size_formatted"] = (
                format_file_size(doc["file_size"]) if doc["file_size"] else None
            )
            data.append(DocumentData(**doc))

        return DocumentListResponse(
            data=data, total=total, has_more=(skip + limit) < total
        )


@router.get("/recent", response_model=RecentDocumentsResponse)
async def get_recent_documents(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
):
    """Get recently uploaded documents"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        rows = await conn.fetch(
            """
            SELECT d.*,
                   (SELECT COUNT(*) FROM document_attachments WHERE document_id = d.id) as attachment_count
            FROM documents d
            WHERE d.tenant_id = $1 AND d.deleted_at IS NULL
            ORDER BY d.uploaded_at DESC
            LIMIT $2
            """,
            ctx["tenant_id"],
            limit,
        )

        data = []
        for row in rows:
            doc = dict(row)
            doc["file_size_formatted"] = (
                format_file_size(doc["file_size"]) if doc["file_size"] else None
            )
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
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        rows = await conn.fetch(
            "SELECT * FROM search_documents($1, $2, $3, $4, $5, $6)",
            ctx["tenant_id"],
            query,
            category,
            tags,
            limit,
            offset,
        )

        data = []
        for row in rows:
            doc = dict(row)
            doc["file_size_formatted"] = (
                format_file_size(doc["file_size"]) if doc.get("file_size") else None
            )
            data.append(DocumentData(**doc))

        return SearchDocumentsResponse(
            query=query,
            category=category,
            tags=tags,
            data=data,
            total=len(data),
            has_more=len(data) >= limit,
        )


@router.get("/storage-usage", response_model=StorageUsageResponse)
async def get_storage_usage(request: Request):
    """Get storage usage statistics"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            "SELECT * FROM get_tenant_storage_usage($1)", ctx["tenant_id"]
        )

        by_category = []
        if row["by_category"]:
            import json

            cat_data = (
                row["by_category"]
                if isinstance(row["by_category"], dict)
                else json.loads(row["by_category"])
            )
            for cat, info in cat_data.items():
                by_category.append(
                    StorageUsageByCategory(
                        category=cat,
                        count=info["count"],
                        size_bytes=info["size_bytes"],
                        size_formatted=format_file_size(info["size_bytes"]),
                    )
                )

        return StorageUsageResponse(
            data=StorageUsageData(
                total_documents=row["total_documents"],
                total_size_bytes=row["total_size_bytes"],
                total_size_mb=float(row["total_size_mb"]),
                total_size_formatted=format_file_size(row["total_size_bytes"]),
                by_category=by_category,
            )
        )


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(request: Request, document_id: UUID):
    """Get document details with attachments"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        row = await conn.fetchrow(
            """
            SELECT d.*,
                   (SELECT COUNT(*) FROM document_attachments WHERE document_id = d.id) as attachment_count
            FROM documents d
            WHERE d.id = $1 AND d.tenant_id = $2 AND d.deleted_at IS NULL
            """,
            document_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Document not found")

        attachments = await conn.fetch(
            "SELECT * FROM document_attachments WHERE document_id = $1", document_id
        )

        doc = dict(row)
        doc["file_size_formatted"] = (
            format_file_size(doc["file_size"]) if doc["file_size"] else None
        )

        data = DocumentWithAttachments(
            **doc, attachments=[DocumentAttachmentData(**dict(a)) for a in attachments]
        )

        return DocumentDetailResponse(data=data)


async def _require_active_member_docs(request):
    """Anggota AKTIF tenant (bukan sekadar token valid)."""
    from ..services.policy_engine_client import get_policy_engine
    u = getattr(request.state, "user", {}) or {}
    eng = get_policy_engine()
    c = await eng.get_user_context(str(u.get("user_id")), u.get("tenant_id"), u.get("role", "USER"))
    if not c.membership_active:
        raise HTTPException(status_code=403, detail="Keanggotaan tenant tidak aktif")


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
    await _require_active_member_docs(request)
    pool = await get_pool()

    # L2: satu sumber aturan lampiran (ekstensi + byte awal + 10 MB).
    tipe, content = await baca_lampiran_atau_400(file)
    file_size = len(content)

    # Parse tags
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        # Generate file path
        file_path = await conn.fetchval(
            "SELECT generate_document_key($1, $2, $3)",
            ctx["tenant_id"],
            category or "other",
            file.filename,
        )

        # Upload bytes to storage (S3/MinIO) — was a stub (metadata only) before
        storage = get_storage_service()
        await file.seek(0)
        upload_result = await storage.upload_file(
            file=file,
            tenant_id=ctx["tenant_id"],
            category=category or "other",
        )
        file_path = upload_result.file_path

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
            ctx["tenant_id"],
            file.filename,
            file.filename,
            tipe,
            file_size,
            file_path,
            category,
            title,
            description,
            tag_list,
            md5,
            ctx.get("user_id"),
        )

        doc = dict(row)
        doc["file_size_formatted"] = format_file_size(file_size)
        doc["attachment_count"] = 0

        return UploadDocumentResponse(data=DocumentData(**doc))


@router.patch("/{document_id}", response_model=UpdateDocumentResponse)
async def update_document(
    request: Request, document_id: UUID, body: UpdateDocumentRequest
):
    """Update document metadata"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        existing = await conn.fetchrow(
            "SELECT * FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            document_id,
            ctx["tenant_id"],
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
            doc["file_size_formatted"] = (
                format_file_size(doc["file_size"]) if doc["file_size"] else None
            )
            doc["attachment_count"] = 0
            return UpdateDocumentResponse(data=DocumentData(**doc))

        params.append(document_id)

        row = await conn.fetchrow(
            f"""
            UPDATE documents SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${param_idx}
            RETURNING *
            """,
            *params,
        )

        doc = dict(row)
        doc["file_size_formatted"] = (
            format_file_size(doc["file_size"]) if doc["file_size"] else None
        )
        doc["attachment_count"] = await conn.fetchval(
            "SELECT COUNT(*) FROM document_attachments WHERE document_id = $1",
            document_id,
        )

        return UpdateDocumentResponse(data=DocumentData(**doc))


@router.delete("/{document_id}", response_model=DeleteDocumentResponse)
async def delete_document(request: Request, document_id: UUID):
    """Soft delete a document"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        existing = await conn.fetchrow(
            "SELECT * FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            document_id,
            ctx["tenant_id"],
        )

        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        await conn.execute(
            "UPDATE documents SET deleted_at = NOW() WHERE id = $1", document_id
        )

        # In production: Delete from S3 or schedule cleanup

        return DeleteDocumentResponse()


# ---------------------------------------------------------------------------
# L2 (24 Sep 2026): hub attach/detach berpagar ENTITAS + izin PER-DOCTYPE.
# Dulu hanya "anggota aktif": entity_id tak diverifikasi (tautan ke entitas
# tenant lain / tak ada), dan staf tanpa izin modul bisa melampirkan ke dokumen
# modul itu. Sekarang: entitas HARUS ada & milik tenant JWT (predikat eksplisit,
# gateway = BYPASSRLS), dan izin diambil dari ROUTE_PERMISSIONS rute PATCH
# entitas itu sendiri (satu sumber kebenaran; tak dikodekan ulang di sini).
# ---------------------------------------------------------------------------
PENANDA_L2_HUB = "l2-hub-attach-pagar-entitas"

# entity_type -> daftar (tabel, segmen URL modul). `payment` DIPAKAI BERSAMA
# penerimaan & pembayaran-keluar; tabel pertama yang memuat id itu menang.
_ENTITAS_HUB = {
    "sales_invoice": (("sales_invoices", "sales-invoices"),),
    "bill": (("bills", "bills"),),
    "expense": (("expenses", "expenses"),),
    "customer": (("customers", "customers"),),
    "vendor": (("vendors", "vendors"),),
    "item": (("products", "items"),),
    "journal": (("journal_entries", "journals"),),
    "quote": (("quotes", "quotes"),),
    "purchase_order": (("purchase_orders", "purchase-orders"),),
    "sales_order": (("sales_orders", "sales-orders"),),
    "sales_receipt": (("sales_receipts", "sales-receipts"),),
    "payment": (("receive_payments", "receive-payments"), ("bill_payments_v2", "bill-payments")),
    "credit_note": (("credit_notes", "credit-notes"),),
    "vendor_credit": (("vendor_credits", "vendor-credits"),),
    "stock_adjustment": (("stock_adjustments", "stock-adjustments"),),
    "stock_transfer": (("stock_transfers", "stock-transfers"),),
    "employee": (("employees", "employees"),),
    "asset": (("fixed_assets", "fixed-assets"),),
    "proforma": (("proformas", "proformas"),),
}
# Tak didukung (tanpa tabel entitas yang bisa diverifikasi): project, contract,
# other, delivery -> 422.

_RESOLVER_IZIN = None


def _izin_modul(segmen: str, entity_id) -> Optional[tuple]:
    """(modul, aksi) dari ROUTE_PERMISSIONS untuk PATCH /api/<segmen>/<id>."""
    global _RESOLVER_IZIN
    if _RESOLVER_IZIN is None:
        from ..middleware.permission_middleware import PermissionMiddleware

        _RESOLVER_IZIN = PermissionMiddleware(app=None)
    return _RESOLVER_IZIN._find_permission(f"/api/{segmen}/{entity_id}", "PATCH")


async def _cari_entitas_hub(conn, entity_type: str, entity_id, tenant_id: str) -> str:
    """Segmen URL modul entitas yang ADA & milik tenant; 422/404 bila tidak."""
    calon = _ENTITAS_HUB.get(entity_type)
    if not calon:
        raise HTTPException(
            status_code=422, detail=f"Jenis entitas '{entity_type}' tidak didukung untuk lampiran"
        )
    for tabel, segmen in calon:
        ada = await conn.fetchval(
            f"SELECT 1 FROM {tabel} WHERE id = $1 AND tenant_id = $2",  # nosec B608 - tabel dari peta tetap
            entity_id,
            tenant_id,
        )
        if ada:
            return segmen
    raise HTTPException(status_code=404, detail="Entitas tujuan tidak ditemukan")


async def _wajib_izin_entitas(request: Request, segmen: str, entity_id) -> None:
    """Anggota aktif + izin modul entitas (OWNER lolos; tak terpetakan ->
    hanya OWNER, sama dengan default-tertutup middleware)."""
    from ..services.policy_engine_client import get_policy_engine

    u = getattr(request.state, "user", {}) or {}
    eng = get_policy_engine()
    c = await eng.get_user_context(str(u.get("user_id")), u.get("tenant_id"), u.get("role", "USER"))
    if not c.membership_active:
        raise HTTPException(status_code=403, detail="Keanggotaan tenant tidak aktif")
    if c.business_role_code == "OWNER":
        return
    izin = _izin_modul(segmen, entity_id)
    # C ATAU U pada modul entitas: FE melampirkan TEPAT SESUDAH membuat dokumen
    # (quote, penerimaan), jadi staf yang boleh membuat harus boleh melampirkan.
    if izin is None or not (
        await eng.can(c, "U", izin[0]) or await eng.can(c, "C", izin[0])
    ):
        raise HTTPException(
            status_code=403, detail="Anda tidak punya izin mengubah dokumen ini."
        )


@router.post("/{document_id}/attach", response_model=AttachDocumentResponse)
async def attach_document(
    request: Request, document_id: UUID, body: AttachDocumentRequest
):
    """Attach document to an entity (L2: entitas + izin + kuota 10)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
            )
            segmen = await _cari_entitas_hub(
                conn, body.entity_type, body.entity_id, ctx["tenant_id"]
            )
            await _wajib_izin_entitas(request, segmen, body.entity_id)

            doc = await conn.fetchval(
                "SELECT id FROM documents WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
                document_id,
                ctx["tenant_id"],
            )
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")

            # Kuota: lock per entitas DULU, hitung SESUDAHNYA, satu transaksi.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                kunci_kuota_lampiran(ctx["tenant_id"], body.entity_type, body.entity_id),
            )
            existing = await conn.fetchval(
                """
                SELECT id FROM document_attachments
                WHERE document_id = $1 AND entity_type = $2 AND entity_id = $3
                  AND tenant_id = $4
                """,
                document_id,
                body.entity_type,
                body.entity_id,
                ctx["tenant_id"],
            )
            if existing:
                raise HTTPException(
                    status_code=400, detail="Document already attached to this entity"
                )
            terpakai = await hitung_lampiran_tersedia(
                conn, ctx["tenant_id"], body.entity_type, body.entity_id
            )
            if terpakai >= MAKS_LAMPIRAN_PER_DOKUMEN:
                raise http_400_lampiran("kuota_penuh")

            row = await conn.fetchrow(
                """
                INSERT INTO document_attachments (
                    tenant_id, document_id, entity_type, entity_id, attachment_type, display_order, attached_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING *
                """,
                ctx["tenant_id"],
                document_id,
                body.entity_type,
                body.entity_id,
                body.attachment_type,
                body.display_order,
                ctx.get("user_id"),
            )

        return AttachDocumentResponse(attachment=DocumentAttachmentData(**dict(row)))


@router.delete("/{document_id}/detach", response_model=DetachDocumentResponse)
async def detach_document(
    request: Request, document_id: UUID, body: DetachDocumentRequest
):
    """Detach document from an entity (L2: anggota aktif + izin modul entitas)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )
        segmen = await _cari_entitas_hub(
            conn, body.entity_type, body.entity_id, ctx["tenant_id"]
        )
        await _wajib_izin_entitas(request, segmen, body.entity_id)

        deleted = await conn.fetchval(
            """
            DELETE FROM document_attachments
            WHERE document_id = $1 AND entity_type = $2 AND entity_id = $3 AND tenant_id = $4
            RETURNING id
            """,
            document_id,
            body.entity_type,
            body.entity_id,
            ctx["tenant_id"],
        )

        if not deleted:
            raise HTTPException(status_code=404, detail="Attachment not found")

        return DetachDocumentResponse()


@router.get(
    "/{entity_type}/{entity_id}/documents", response_model=EntityDocumentsResponse
)
async def get_entity_documents(
    request: Request,
    entity_type: str,
    entity_id: UUID,
):
    """Get all documents attached to an entity"""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )

        rows = await conn.fetch(
            "SELECT * FROM get_entity_documents($1, $2, $3)",
            ctx["tenant_id"],
            entity_type,
            entity_id,
        )

        return EntityDocumentsResponse(
            entity_type=entity_type,
            entity_id=entity_id,
            data=[EntityDocument(**dict(row)) for row in rows],
            total=len(rows),
        )


@router.get("/{document_id}/download")
async def download_document(request: Request, document_id: UUID):
    """Proxy-stream a document file from storage (S3/MinIO)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
        )
        row = await conn.fetchrow(
            "SELECT file_name, file_path, file_type FROM documents "
            "WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
            document_id,
            ctx["tenant_id"],
        )
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    storage = get_storage_service()
    obj = storage.client.get_object(Bucket=storage.config.bucket, Key=row["file_path"])
    body = obj["Body"]

    def iter_body():
        while chunk := body.read(65536):
            yield chunk
        body.close()

    # L2: sajian aman sama dengan rute /download modul (lampiran_unduh):
    # inline hanya jpeg/png/webp/gif/pdf; lainnya octet-stream + attachment;
    # nosniff; nama berkas lewat content_disposition_lampiran (tak bisa
    # memutus header -- dulu nama mentah di f-string).
    media_type, inline = sajian_lampiran(row["file_type"])
    return StreamingResponse(
        iter_body(),
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition_lampiran(row["file_name"], inline),
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
