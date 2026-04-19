"""
Document Intake Router
======================
REST endpoints for the Financial Intelligence pipeline.
Phase 2: Upload, batch status, document detail, review queue.
"""
import json
import logging
from typing import List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form, Query

from ..schemas.document_intake import (
    BatchSummary,
    ConfirmDraftRequest,
    ConfirmDraftResponse,
    DocumentIntakeData,
    DocumentIntakeDetail,
    DocumentIntakeDetailResponse,
    BatchStatusResponse,
    RejectDocumentRequest,
    RejectDocumentResponse,
    ReviewQueueResponse,
    UploadDocumentIntakeResponse,
)
from ..services.document_intake import DocumentIntakeService

logger = logging.getLogger(__name__)

router = APIRouter()

# Connection pool (initialized on first request)


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


async def _get_service() -> DocumentIntakeService:
    """Create service instance with connection pool."""
    pool = await get_pool()
    return DocumentIntakeService(pool=pool)


def _parse_jsonb(value):
    """Parse JSONB value from asyncpg (returned as string)."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value


def _batch_summary(row: dict) -> BatchSummary:
    return BatchSummary(
        id=row["id"],
        total_documents=row["total_documents"],
        processed_count=row["processed_count"],
        failed_count=row["failed_count"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _doc_data(row: dict) -> DocumentIntakeData:
    return DocumentIntakeData(
        id=row["id"],
        batch_id=row.get("batch_id"),
        original_filename=row["original_filename"],
        file_hash=row["file_hash"],
        file_size_bytes=row.get("file_size_bytes"),
        mime_type=row.get("mime_type"),
        status=row["status"],
        status_detail=row.get("status_detail"),
        retry_count=row.get("retry_count", 0),
        ocr_confidence=float(row["ocr_confidence"])
        if row.get("ocr_confidence")
        else None,
        ocr_model_used=row.get("ocr_model_used"),
        doc_type=row.get("doc_type"),
        classification_confidence=float(row["classification_confidence"])
        if row.get("classification_confidence")
        else None,
        draft_plan=_parse_jsonb(row.get("draft_plan")),
        journal_entry_id=row.get("journal_entry_id"),
        bank_transaction_id=row.get("bank_transaction_id"),
        confirmed_by=row.get("confirmed_by"),
        confirmed_at=row.get("confirmed_at"),
        posted_at=row.get("posted_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _doc_detail(row: dict) -> DocumentIntakeDetail:
    base = _doc_data(row)
    return DocumentIntakeDetail(
        **base.model_dump(),
        ocr_result=_parse_jsonb(row.get("ocr_result")),
        analysis_result=_parse_jsonb(row.get("analysis_result")),
        inventory_ledger_ids=row.get("inventory_ledger_ids"),
    )


# ======================================================================
# ENDPOINTS
# ======================================================================


@router.post("/upload", response_model=UploadDocumentIntakeResponse)
async def upload_documents(
    request: Request,
    files: List[UploadFile] = File(..., description="One or more financial documents"),
    doc_type_hint: Optional[str] = Form(None),
    batch_id: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    """
    Upload one or more financial documents for AI processing.

    Accepts images (JPEG, PNG, WebP, HEIC) and PDFs.
    Files are deduplicated by SHA-256 hash.
    Returns batch ID for tracking processing status.
    """
    ctx = get_user_context(request)
    svc = await _get_service()

    try:
        parsed_batch_id = UUID(batch_id) if batch_id else None
        batch_row, doc_rows = await svc.upload_documents(
            ctx["tenant_id"],
            ctx["user_id"],
            files,
            batch_id=parsed_batch_id,
            doc_type_hint=doc_type_hint,
            notes=notes,
        )
        return UploadDocumentIntakeResponse(
            batch=_batch_summary(batch_row),
            documents=[_doc_data(r) for r in doc_rows],
            message=f"{len(doc_rows)} document(s) queued for processing",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[DocIntake] Upload failed: {e}")
        raise HTTPException(status_code=500, detail="Upload failed")


@router.get("/batch/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    request: Request,
    batch_id: UUID,
):
    """Get batch processing status with all documents."""
    ctx = get_user_context(request)
    svc = await _get_service()

    batch, docs = await svc.get_batch_status(ctx["tenant_id"], batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    return BatchStatusResponse(
        batch=_batch_summary(batch),
        documents=[_doc_data(d) for d in docs],
    )


@router.get("/document/{doc_id}", response_model=DocumentIntakeDetailResponse)
async def get_document_detail(
    request: Request,
    doc_id: UUID,
):
    """Get full document detail including OCR results and draft plan."""
    ctx = get_user_context(request)
    svc = await _get_service()

    doc = await svc.get_document_detail(ctx["tenant_id"], doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentIntakeDetailResponse(data=_doc_detail(doc))


@router.get("/review", response_model=ReviewQueueResponse)
async def get_review_queue(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get documents pending review.
    Default: shows draft_ready + reviewing.
    """
    ctx = get_user_context(request)
    svc = await _get_service()

    docs, total = await svc.get_review_queue(
        ctx["tenant_id"],
        status_filter=status,
        limit=limit,
        offset=offset,
    )

    return ReviewQueueResponse(
        data=[_doc_data(d) for d in docs],
        total=total,
        has_more=(offset + limit) < total,
    )


@router.post("/document/{doc_id}/confirm", response_model=ConfirmDraftResponse)
async def confirm_document(
    request: Request,
    doc_id: UUID,
    body: Optional[ConfirmDraftRequest] = None,
):
    """
    Confirm a draft document for posting.
    Optionally apply field-level overrides to the draft plan.
    Phase 8: Immediately executes (creates journal + bank + inventory).
    """
    ctx = get_user_context(request)
    svc = await _get_service()

    try:
        doc = await svc.confirm_document(
            ctx["tenant_id"],
            doc_id,
            ctx["user_id"],
            overrides=body.overrides if body else None,
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        # Phase 8: Immediate execution after confirm (Option A)
        execution = None
        execution_error = None
        try:
            from ..services.kernel_document_executor import KernelDocumentExecutor

            pool = await get_pool()
            auth_token = (
                request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            )
            executor = KernelDocumentExecutor(pool, auth_token=auth_token)
            exec_result = await executor.execute(
                str(doc_id), ctx["tenant_id"], str(ctx["user_id"])
            )
            if exec_result.success:
                execution = exec_result.to_dict()
                # Re-fetch doc to reflect posted status
                pool2 = await get_pool()
                async with pool2.acquire() as conn:
                    updated = await conn.fetchrow(
                        "SELECT * FROM uploaded_documents WHERE id = $1 AND tenant_id = $2",
                        doc_id,
                        ctx["tenant_id"],
                    )
                    if updated:
                        doc = dict(updated)
            else:
                execution_error = exec_result.error
                logger.error(
                    f"[confirm] Execution failed after confirm: {exec_result.error}"
                )
        except Exception as e:
            execution_error = str(e)
            logger.exception(f"[confirm] Execution exception after confirm: {e}")

        response = ConfirmDraftResponse(
            data=_doc_data(doc),
            message=(
                f"Document confirmed and posted (journal {execution.get('journal_number', '')})"
                if execution
                else "Document confirmed but execution pending"
            ),
        )
        # Attach execution info as extra fields in the response dict
        resp_dict = response.model_dump()
        if execution:
            resp_dict["execution"] = execution
        if execution_error:
            resp_dict["execution_error"] = execution_error
        return resp_dict
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/document/{doc_id}/reject", response_model=RejectDocumentResponse)
async def reject_document(
    request: Request,
    doc_id: UUID,
    body: Optional[RejectDocumentRequest] = None,
):
    """Reject a document with optional reason."""
    ctx = get_user_context(request)
    svc = await _get_service()

    success = await svc.reject_document(
        ctx["tenant_id"],
        doc_id,
        reason=body.reason if body else None,
    )
    if not success:
        raise HTTPException(
            status_code=404, detail="Document not found or already posted"
        )

    return RejectDocumentResponse()


# ======================================================================
# PROCESSING TRIGGER (Phase 3)
# ======================================================================


@router.post("/process")
async def trigger_processing(
    request: Request,
    body: Optional[dict] = None,
):
    """
    Trigger OCR extraction + classification for queued documents.
    Optionally filter by batch_id. Returns processing summary.
    """
    from ..schemas.document_intake import ProcessResponse, ProcessResultItem
    from ..services.document_processor import DocumentProcessor

    ctx = get_user_context(request)
    pool = await get_pool()
    processor = DocumentProcessor(pool=pool)

    batch_id = None
    limit = 10
    if body:
        batch_id = body.get("batch_id")
        limit = min(body.get("limit", 10), 50)

    try:
        result = await processor.process_next_batch(
            tenant_id=ctx["tenant_id"],
            batch_id=batch_id,
            limit=limit,
        )

        details = [ProcessResultItem(**d) for d in result.get("details", [])]

        return ProcessResponse(
            processed=result["processed"],
            failed=result["failed"],
            remaining=result["remaining"],
            details=details,
        )
    except Exception as e:
        logger.exception(f"[DocIntake] Processing failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Processing failed: {str(e)[:200]}"
        )


# ── Phase 8: Kernel Document Executor endpoints ──


@router.post("/document/{doc_id}/execute")
async def execute_document(
    doc_id: str,
    request: Request,
):
    """Execute a confirmed document — create journal + bank + inventory atomically."""
    ctx = get_user_context(request)
    pool = await get_pool()

    from ..services.kernel_document_executor import KernelDocumentExecutor

    auth_token = (
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    executor = KernelDocumentExecutor(pool, auth_token=auth_token)
    result = await executor.execute(doc_id, ctx["tenant_id"], str(ctx["user_id"]))

    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return {
        "success": True,
        "data": result.to_dict(),
    }


@router.post("/execute-batch")
async def execute_batch(
    request: Request,
):
    """Execute multiple confirmed documents sequentially (deadlock-safe)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    body = await request.json()
    document_ids = body.get("document_ids", [])

    if not document_ids:
        raise HTTPException(status_code=400, detail="document_ids required")
    if len(document_ids) > 50:
        raise HTTPException(status_code=400, detail="Max 50 documents per batch")

    from ..services.kernel_document_executor import KernelDocumentExecutor

    auth_token = (
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    executor = KernelDocumentExecutor(pool, auth_token=auth_token)
    result = await executor.execute_batch(
        document_ids, ctx["tenant_id"], str(ctx["user_id"])
    )

    return {
        "success": True,
        "data": result,
    }


# ── Phase 9: Progress, Retry, Stats, Cleanup endpoints ──


@router.get("/batch/{batch_id}/progress")
async def get_batch_progress(
    batch_id: str,
    request: Request,
):
    """Real-time progress for a batch upload. Poll every 3s while processing."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        batch_uuid = UUID(batch_id)
        batch = await conn.fetchrow(
            """SELECT id, total_documents, processed_count, failed_count, status
               FROM document_batches
               WHERE id = $1 AND tenant_id = $2""",
            batch_uuid,
            ctx["tenant_id"],
        )
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        documents = await conn.fetch(
            """SELECT id, original_filename, status, doc_type,
                      (draft_plan->>'overall_confidence') as confidence
               FROM uploaded_documents
               WHERE batch_id = $1 AND tenant_id = $2
               ORDER BY created_at""",
            batch_uuid,
            ctx["tenant_id"],
        )

        # Count by status
        progress = {}
        for doc in documents:
            s = doc["status"]
            progress[s] = progress.get(s, 0) + 1

        total = len(documents)
        terminal = {
            "draft_ready",
            "confirmed",
            "posted",
            "posting_failed",
            "rejected",
            "duplicate",
        }
        done_count = sum(1 for d in documents if d["status"] in terminal)
        is_complete = done_count == total and total > 0

        # Sync batch status if processing is complete
        if is_complete and batch["status"] == "processing":
            failed_count = sum(
                1 for d in documents if d["status"] in ("posting_failed", "rejected")
            )
            await conn.execute(
                """UPDATE document_batches
                   SET status = 'completed',
                       processed_count = $3,
                       failed_count = $4,
                       total_documents = $5,
                       updated_at = NOW()
                   WHERE id = $1 AND tenant_id = $2""",
                batch_uuid,
                ctx["tenant_id"],
                done_count,
                failed_count,
                total,
            )

        return {
            "batch_id": batch_id,
            "total_documents": total,
            "progress": progress,
            "documents": [
                {
                    "id": str(d["id"]),
                    "filename": d["original_filename"],
                    "status": d["status"],
                    "doc_type": d["doc_type"],
                    "confidence": d["confidence"],
                }
                for d in documents
            ],
            "is_complete": is_complete,
            "percent_complete": round((done_count / total) * 100) if total > 0 else 0,
        }


@router.post("/document/{doc_id}/retry")
async def retry_document(
    doc_id: str,
    request: Request,
):
    """Retry a posting_failed document. Resets to confirmed and re-executes."""
    ctx = get_user_context(request)
    pool = await get_pool()

    doc_uuid = UUID(doc_id)
    async with pool.acquire() as conn:
        doc = await conn.fetchrow(
            """SELECT id, status FROM uploaded_documents
               WHERE id = $1 AND tenant_id = $2""",
            doc_uuid,
            ctx["tenant_id"],
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc["status"] != "posting_failed":
            raise HTTPException(
                status_code=400,
                detail=f"Can only retry posting_failed documents, got '{doc['status']}'",
            )

        # Reset status to confirmed
        await conn.execute(
            """UPDATE uploaded_documents
               SET status = 'confirmed', updated_at = NOW()
               WHERE id = $1 AND tenant_id = $2""",
            doc_uuid,
            ctx["tenant_id"],
        )

    # Re-execute via Phase 8 kernel
    from ..services.kernel_document_executor import KernelDocumentExecutor

    auth_token = (
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    executor = KernelDocumentExecutor(pool, auth_token=auth_token)
    result = await executor.execute(doc_id, ctx["tenant_id"], str(ctx["user_id"]))

    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return {"success": True, "data": result.to_dict()}


@router.post("/retry-all-failed")
async def retry_all_failed(
    request: Request,
):
    """Retry all posting_failed documents for this tenant. Sequential execution."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        failed_docs = await conn.fetch(
            """SELECT id FROM uploaded_documents
               WHERE tenant_id = $1 AND status = 'posting_failed'
               ORDER BY updated_at ASC""",
            ctx["tenant_id"],
        )

    if not failed_docs:
        return {
            "success": True,
            "message": "No failed documents to retry",
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "results": [],
        }

    # Reset all to confirmed first
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE uploaded_documents
               SET status = 'confirmed', updated_at = NOW()
               WHERE tenant_id = $1 AND status = 'posting_failed'""",
            ctx["tenant_id"],
        )

    from ..services.kernel_document_executor import KernelDocumentExecutor

    auth_token = (
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    executor = KernelDocumentExecutor(pool, auth_token=auth_token)
    result = await executor.execute_batch(
        [str(d["id"]) for d in failed_docs],
        ctx["tenant_id"],
        str(ctx["user_id"]),
    )

    return {"success": True, "data": result}


@router.get("/stats")
async def get_document_stats(
    request: Request,
):
    """Dashboard stats for document intelligence. Used by banner + bell badge."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        stats = await conn.fetchrow(
            """SELECT
                COUNT(*) FILTER (WHERE status = 'draft_ready') as pending_review,
                COUNT(*) FILTER (WHERE status = 'posting_failed') as posting_failed,
                COUNT(*) FILTER (WHERE status IN ('extracting', 'classifying', 'analyzing', 'draft_generating')) as processing,
                COUNT(*) FILTER (WHERE status = 'posted' AND updated_at::date = CURRENT_DATE) as posted_today,
                COUNT(*) FILTER (WHERE status = 'posted') as total_processed
               FROM uploaded_documents
               WHERE tenant_id = $1""",
            ctx["tenant_id"],
        )

    return {
        "pending_review": stats["pending_review"],
        "posting_failed": stats["posting_failed"],
        "processing": stats["processing"],
        "posted_today": stats["posted_today"],
        "total_processed": stats["total_processed"],
    }


@router.post("/cleanup")
async def cleanup_stale_documents(
    request: Request,
):
    """Handle stuck documents: reset stale processing, mark stuck posting as failed."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Reset stuck extracting/classifying/analyzing (> 10 min)
        r1 = await conn.execute(
            """UPDATE uploaded_documents
               SET status = 'queued', updated_at = NOW()
               WHERE tenant_id = $1
                 AND status IN ('extracting', 'classifying', 'analyzing')
                 AND updated_at < NOW() - INTERVAL '10 minutes'""",
            ctx["tenant_id"],
        )

        # Reset stuck draft generation (> 5 min)
        r2 = await conn.execute(
            """UPDATE uploaded_documents
               SET status = 'analyzed', updated_at = NOW()
               WHERE tenant_id = $1
                 AND status = 'draft_generating'
                 AND updated_at < NOW() - INTERVAL '5 minutes'""",
            ctx["tenant_id"],
        )

        # Mark stuck posting as failed (> 5 min)
        r3 = await conn.execute(
            """UPDATE uploaded_documents
               SET status = 'posting_failed', updated_at = NOW()
               WHERE tenant_id = $1
                 AND status = 'posting'
                 AND updated_at < NOW() - INTERVAL '5 minutes'""",
            ctx["tenant_id"],
        )

    return {
        "success": True,
        "reset_processing": int(r1.split()[-1]) if isinstance(r1, str) else 0,
        "reset_draft_gen": int(r2.split()[-1]) if isinstance(r2, str) else 0,
        "marked_failed": int(r3.split()[-1]) if isinstance(r3, str) else 0,
    }
