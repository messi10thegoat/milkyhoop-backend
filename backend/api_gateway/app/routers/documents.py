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

from ..services.policy_engine_client import anggota_aktif

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
from ..utils.lampiran_unduh import (  # noqa: E402
    content_disposition_lampiran,
    sajian_lampiran,
    stream_lampiran,
)

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
    eng, c = await _konteks_izin(request)  # READ_OPEN: anggota AKTIF wajib (audit 26 Sep)
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

        if c.business_role_code == "OWNER":
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
        else:
            # Non-OWNER: saring per dokumen dengan aturan /download, LALU paginasi
            # (total & has_more jujur untuk yang boleh dilihat). Batas 2000 baris.
            semua = await conn.fetch(
                f"""
                SELECT d.*,
                       (SELECT COUNT(*) FROM document_attachments WHERE document_id = d.id) as attachment_count
                FROM documents d
                WHERE {where_sql}
                ORDER BY d.uploaded_at DESC
                LIMIT 2000
                """,
                *params,
            )
            boleh = await _saring_dokumen(conn, request, eng, c, ctx, semua)
            total = len(boleh)
            rows = boleh[skip:skip + limit]

        data = []
        for row in rows:
            doc = _url_unduh(dict(row))
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
    eng, c = await _konteks_izin(request)
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
            limit if c.business_role_code == "OWNER" else 2000,
        )
        if c.business_role_code != "OWNER":
            rows = (await _saring_dokumen(conn, request, eng, c, ctx, rows))[:limit]

        data = []
        for row in rows:
            doc = _url_unduh(dict(row))
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
    eng, c = await _konteks_izin(request)
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
        if c.business_role_code != "OWNER":
            # Catatan: saring SESUDAH halaman fungsi DB -> halaman bisa kurang dari
            # `limit`; has_more tetap dari jumlah baris mentah (tak menyiratkan jumlah).
            mentah = len(rows)
            rows = await _saring_dokumen(conn, request, eng, c, ctx, rows)
        else:
            mentah = len(rows)

        data = []
        for row in rows:
            doc = _url_unduh(dict(row))
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
            has_more=mentah >= limit,
        )


@router.get("/storage-usage", response_model=StorageUsageResponse)
async def get_storage_usage(request: Request):
    """Get storage usage statistics"""
    ctx = get_user_context(request)
    await _konteks_izin(request)  # agregat saja; anggota AKTIF wajib
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
    eng, c = await _konteks_izin(request)
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

        if not row or not await _boleh_baca_dokumen(
            conn, request, eng, c, row["id"], row.get("uploaded_by"), ctx["tenant_id"], ctx.get("user_id")
        ):
            # tak boleh = sama dengan tak ada (tanpa oracle keberadaan)
            raise HTTPException(status_code=404, detail="Document not found")

        attachments = await conn.fetch(
            "SELECT * FROM document_attachments WHERE document_id = $1 AND tenant_id = $2",
            document_id, ctx["tenant_id"],
        )

        doc = _url_unduh(dict(row))
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
    if not anggota_aktif(c):
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


# hub-authz (25 Sep 2026): SATU penentu izin hub untuk attach/detach (C|U),
# daftar per entitas & unduh by-id (R). Entitas `employee` WAJIB lolos
# pay-group (RULE: setiap endpoint ber-employee_id memfilter pay-group) --
# dulu attach employee cukup izin modul, dan unduh by-id tanpa izin apa pun.
PENANDA_HUB_AUTHZ = "hub-authz-izin-bersama"

# Peta BACA = peta attach + entitas yang lampirannya ditulis rute modulnya
# sendiri (DP) tetapi dokumennya bisa diunduh lewat hub.
_ENTITAS_BACA = {
    **_ENTITAS_HUB,
    "customer_deposit": (("customer_deposits", "customer-deposits"),),
}


async def _konteks_izin(request: Request):
    """(engine, konteks) anggota AKTIF; tak aktif -> 403."""
    from ..services.policy_engine_client import get_policy_engine

    u = getattr(request.state, "user", {}) or {}
    eng = get_policy_engine()
    c = await eng.get_user_context(str(u.get("user_id")), u.get("tenant_id"), u.get("role", "USER"))
    if not anggota_aktif(c):
        raise HTTPException(status_code=403, detail="Keanggotaan tenant tidak aktif")
    return eng, c


async def _boleh_entitas(conn, request: Request, eng, c, entity_type: str,
                         segmen: str, entity_id, aksi: tuple) -> bool:
    """OWNER lolos; selain itu salah satu `aksi` pada modul entitas (dari
    ROUTE_PERMISSIONS PATCH rute modulnya) DAN -- untuk employee -- pay-group."""
    if c.business_role_code == "OWNER":
        return True
    izin = _izin_modul(segmen, entity_id)
    if izin is None:
        return False
    ok = False
    for a in aksi:
        if await eng.can(c, a, izin[0]):
            ok = True
            break
    if ok and entity_type == "employee":
        from ..services.pay_group_access import employee_in_scope

        u = getattr(request.state, "user", {}) or {}
        ok = await employee_in_scope(conn, u.get("tenant_id"), u.get("user_id"), entity_id)
    return ok


async def _wajib_izin_entitas(request: Request, conn, entity_type: str, segmen: str, entity_id) -> None:
    """Attach/detach: C ATAU U pada modul entitas (FE melampirkan TEPAT SESUDAH
    membuat dokumen, jadi staf yang boleh membuat harus boleh melampirkan)."""
    eng, c = await _konteks_izin(request)
    if not await _boleh_entitas(conn, request, eng, c, entity_type, segmen, entity_id, ("U", "C")):
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
            await _wajib_izin_entitas(request, conn, body.entity_type, segmen, body.entity_id)

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
        await _wajib_izin_entitas(request, conn, body.entity_type, segmen, body.entity_id)

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


# #25 (25 Sep 2026): daftar dokumen per entitas.
# Dulu: memanggil fungsi DB get_entity_documents yang TIDAK mengembalikan
# display_order (EntityDocument mewajibkannya) -> 500 untuk SETIAP entitas
# berlampiran; respons entity_type ber-Literal tanpa customer_deposit -> 500
# walau kosong; file_url = nilai mentah DB (baris local = path berkas-chat
# mati); dan TANPA izin per-doctype (READ /api/documents default-open) --
# memperbaiki 500 saja = membuka dokumen modul mana pun ke semua anggota.
# Kini: SQL langsung (tanpa migrasi), entitas harus didukung + ada + milik
# tenant (_cari_entitas_hub), izin BACA modul entitas, url = rute unduh hub.
PENANDA_T25_DAFTAR = "t25-hub-daftar-dokumen-entitas"

_SQL_DOKUMEN_ENTITAS = """
    SELECT d.id AS document_id, d.file_name, d.file_type, d.file_size,
           d.category, d.title, d.uploaded_at, d.storage_type,
           da.attachment_type, da.display_order
    FROM document_attachments da
    JOIN documents d ON d.id = da.document_id
    WHERE da.tenant_id = $1 AND d.tenant_id = $1
      AND da.entity_type = $2 AND da.entity_id = $3
      AND d.deleted_at IS NULL
    ORDER BY da.display_order ASC, d.uploaded_at DESC
"""

async def _wajib_izin_baca_entitas(request: Request, conn, entity_type: str, segmen: str, entity_id) -> None:
    """Anggota aktif + R modul entitas (OWNER lolos; employee + pay-group)."""
    eng, c = await _konteks_izin(request)
    if not await _boleh_entitas(conn, request, eng, c, entity_type, segmen, entity_id, ("R",)):
        raise HTTPException(
            status_code=403, detail="Anda tidak punya izin melihat dokumen ini."
        )


@router.get(
    "/{entity_type}/{entity_id}/documents", response_model=EntityDocumentsResponse
)
async def get_entity_documents(
    request: Request,
    entity_type: str,
    entity_id: UUID,
):
    """Dokumen yang tertaut ke satu entitas (#25: entitas + izin baca)."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
            )
            segmen = await _cari_entitas_hub(
                conn, entity_type, entity_id, ctx["tenant_id"]
            )
            await _wajib_izin_baca_entitas(request, conn, entity_type, segmen, entity_id)
            rows = await conn.fetch(
                _SQL_DOKUMEN_ENTITAS, ctx["tenant_id"], entity_type, entity_id
            )

    data = []
    for r in rows:
        d = dict(r)
        storage_type = d.pop("storage_type", None)
        d["file_url"] = f"/api/documents/{d['document_id']}/download"
        d["tersedia"] = (storage_type or "").lower() == "s3"
        d["display_order"] = d["display_order"] or 0
        d["attachment_type"] = d["attachment_type"] or "attachment"
        data.append(EntityDocument(**d))
    return EntityDocumentsResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        data=data,
        total=len(data),
    )


@router.get("/{document_id}/download")
async def download_document(request: Request, document_id: UUID):
    """Proxy-stream satu dokumen dari storage (S3/MinIO).

    hub-authz: dulu cukup satu tenant (READ /api/documents default-open) ->
    staf mana pun bisa mengunduh dokumen modul apa pun, termasuk karyawan,
    asal tahu id-nya. Kini: OWNER; atau izin R pada SALAH SATU entitas yang
    ditautkan dokumen ini (employee + pay-group); dokumen tanpa tautan hanya
    untuk pengunggahnya.
    """
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"]
            )
            row = await conn.fetchrow(
                "SELECT file_name, file_path, file_type, storage_type, uploaded_by FROM documents "
                "WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL",
                document_id,
                ctx["tenant_id"],
            )
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            eng, c = await _konteks_izin(request)
            boleh = await _boleh_baca_dokumen(
                conn, request, eng, c, document_id, row.get("uploaded_by"), ctx["tenant_id"], ctx.get("user_id")
            )
            if not boleh:
                raise HTTPException(
                    status_code=403, detail="Anda tidak punya izin melihat dokumen ini."
                )
    # #25: satu jalur sajian dengan rute /download modul -- baris local / tanpa
    # file_path / objek hilang -> 404 "Berkas tidak tersedia" (dulu 500:
    # get_object dengan kunci baris local). Sajian aman (L2) tetap sama.
    return stream_lampiran(row, get_storage_service())


# Audit READ_OPEN R1 (26 Sep 2026): SATU aturan baca dokumen untuk unduh, detail,
# daftar, terbaru, cari, DAN berkas /api/v3/chat/files (R3). Dulu daftar/detail
# = tenant saja + `file_url` lama (/api/v3/chat/files/<kunci>) -> anggota tanpa
# izin modul entitas bisa mengambil kunci lalu mengunduh isinya.
async def _boleh_baca_dokumen(conn, request, eng, c, doc_id, uploaded_by, tenant_id, user_id) -> bool:
    """OWNER; atau izin R pada SALAH SATU entitas tertaut (employee + pay-group);
    dokumen tanpa tautan hanya untuk pengunggahnya."""
    if c.business_role_code == "OWNER":
        return True
    tautan = await conn.fetch(
        "SELECT entity_type, entity_id FROM document_attachments "
        "WHERE document_id = $1 AND tenant_id = $2",
        doc_id,
        tenant_id,
    )
    if not tautan:
        return uploaded_by is not None and str(uploaded_by) == str(user_id)
    for t in tautan:
        segmen = await _segmen_entitas(conn, t["entity_type"], t["entity_id"], tenant_id)
        if segmen and await _boleh_entitas(
            conn, request, eng, c, t["entity_type"], segmen, t["entity_id"], ("R",)
        ):
            return True
    return False


async def _saring_dokumen(conn, request, eng, c, ctx, rows) -> list:
    return [
        r for r in rows
        if await _boleh_baca_dokumen(
            conn, request, eng, c, r["id"], r.get("uploaded_by") if hasattr(r, "get") else r["uploaded_by"],
            ctx["tenant_id"], ctx.get("user_id"),
        )
    ]


def _url_unduh(doc: dict) -> dict:
    """file_url SELALU jalur /download ber-otorisasi -- tak pernah kunci objek
    atau URL lama (/api/v3/chat/files/<kunci>, presigned) yang melewati otorisasi."""
    if doc.get("id") is not None:
        doc["file_url"] = f"/api/documents/{doc['id']}/download"
    return doc


async def _segmen_entitas(conn, entity_type: str, entity_id, tenant_id: str):
    """Segmen URL modul entitas yang ADA & milik tenant, atau None (tanpa
    raise: satu tautan basi/tak didukung tak boleh menggagalkan unduhan yang
    sah lewat tautan lain)."""
    for tabel, segmen in _ENTITAS_BACA.get(entity_type, ()):
        ada = await conn.fetchval(
            f"SELECT 1 FROM {tabel} WHERE id = $1 AND tenant_id = $2",  # nosec B608 - tabel dari peta tetap
            entity_id,
            tenant_id,
        )
        if ada:
            return segmen
    return None


async def boleh_baca_berkas(request, conn, tenant_id: str, user_id, storage_key: str) -> bool:
    """Audit READ_OPEN R3 (26 Sep 2026): GET /api/v3/chat/files/{kunci} dulu cukup
    tenant + anggota -> anggota mana pun yang memegang kunci (bocor lewat
    `file_url` lama daftar dokumen) mengunduh berkas modul yang tak boleh ia lihat.

    Pemilik rekaman dicari lewat nama akhir kunci (`<sha256><ext>`, unik per isi):
      - OWNER -> ya;
      - lampiran chat -> hanya pemilik sesi chat-nya;
      - dokumen (forms/lampiran/documents) -> _boleh_baca_dokumen (aturan /download);
      - unggahan intake -> hanya pengunggahnya;
      - tak ditemukan di rekaman mana pun -> TIDAK (gagal tertutup).
    """
    eng, c = await _konteks_izin(request)
    if c.business_role_code == "OWNER":
        return True
    nama = (storage_key or "").rsplit("/", 1)[-1]
    if not nama or "%" in nama or "_" in nama.replace(".", ""):
        return False
    pola = "%/" + nama
    if await conn.fetchval(
        """SELECT 1 FROM chat_attachments ca
             JOIN chat_messages m ON m.id = ca.message_id::text
             JOIN chat_sessions s ON s.id = m.session_id
            WHERE ca.tenant_id = $1 AND ca.storage_key LIKE $2
              AND s.tenant_id = $1 AND s.user_id::text = $3
            LIMIT 1""",
        tenant_id, pola, str(user_id),
    ):
        return True
    docs = await conn.fetch(
        """SELECT id, uploaded_by FROM documents
            WHERE tenant_id = $1 AND deleted_at IS NULL
              AND (file_path LIKE $2 OR file_url LIKE $2)""",
        tenant_id, pola,
    )
    for d in docs:
        if await _boleh_baca_dokumen(conn, request, eng, c, d["id"], d["uploaded_by"], tenant_id, user_id):
            return True
    return bool(await conn.fetchval(
        """SELECT 1 FROM uploaded_documents
            WHERE tenant_id = $1 AND file_path LIKE $2 AND user_id::text = $3 LIMIT 1""",
        tenant_id, pola, str(user_id),
    ))
