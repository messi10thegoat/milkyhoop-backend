"""
Chat-to-Document Pipeline Bridge
=================================
Routes chat file uploads to the document intake pipeline.
Bridges the chat upload flow with the Intelligence Layer (Phases 3-5).

Used by: unified_chat.py (upload handler)
Depends on: document_intake.py (DocumentIntakeService), document_processor.py (DocumentProcessor)
"""
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FINANCIAL_SIGNAL_WORDS = {
    "faktur", "invoice", "nota", "kwitansi", "receipt", "bukti",
    "tagihan", "bill", "struk", "bon", "purchase order", "po",
    "faktur pajak", "tax invoice", "delivery order", "do",
    "proses", "input", "catat", "record", "posting", "post",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
DOCUMENT_EXTENSIONS = {".pdf"}
DATA_EXTENSIONS = {".csv", ".xlsx", ".xls", ".ofx"}

RECON_KEYWORDS = {"rekon", "rekonsiliasi", "reconciliation", "reconcile"}

# Allowed MIME types for pipeline (same as document_intake.py)
PIPELINE_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}

UPLOAD_BASE_DIR = os.getenv("DOCUMENT_UPLOAD_DIR", "/tmp/milkyhoop_uploads")


# ---------------------------------------------------------------------------
# Intent Detection
# ---------------------------------------------------------------------------

def detect_upload_intent(text: str, file_metas: List[Dict[str, Any]]) -> str:
    """
    Determine what to do with uploaded files.

    Args:
        text: User message text
        file_metas: [{"filename": ..., "extension": ".pdf", "content_type": ..., "size": ...}]

    Returns:
        'financial_doc'     -> route to document intake pipeline
        'bank_statement'    -> route to recon workflow (existing ReconShortcut)
        'vision_general'    -> LLM vision path (existing)
        'data_import'       -> defer (future)
    """
    text_lower = text.strip().lower()
    extensions = {fm.get("extension", "").lower() for fm in file_metas}

    # 1. ReconShortcut takes priority (EXISTING, DON'T TOUCH)
    if extensions & DATA_EXTENSIONS and any(k in text_lower for k in RECON_KEYWORDS):
        return "bank_statement"

    # 2. Check if any file is a supported pipeline type (image/PDF)
    has_pipeline_file = bool(extensions & (IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS))

    if not has_pipeline_file:
        # No image/PDF files — check for data import
        if extensions & DATA_EXTENSIONS:
            return "data_import"
        return "vision_general"  # fallback

    # 3. Explicit financial signal from text
    if any(word in text_lower for word in FINANCIAL_SIGNAL_WORDS):
        return "financial_doc"

    # 4. PDF = almost always financial in accounting context
    if extensions & DOCUMENT_EXTENSIONS:
        return "financial_doc"

    # 5. Multiple images = likely batch receipts/invoices
    image_count = sum(
        1 for fm in file_metas
        if fm.get("extension", "").lower() in IMAGE_EXTENSIONS
    )
    if image_count > 1:
        return "financial_doc"

    # 6. Single image without signal = let LLM decide via vision
    return "vision_general"


# ---------------------------------------------------------------------------
# Upload Bridge: Chat file -> uploaded_documents record
# ---------------------------------------------------------------------------

async def upload_chat_file_to_pipeline(
    conn: asyncpg.Connection,
    file_content: bytes,
    filename: str,
    content_type: str,
    tenant_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Create an uploaded_documents record from a chat-uploaded file.
    Stores file in document intake path and creates DB record.

    Returns dict with document info or None if file type not supported.
    """
    # Determine extension from content type
    mime_ext_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "application/pdf": ".pdf",
    }
    ext = mime_ext_map.get(content_type)
    if not ext:
        # Try from filename
        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf"):
            logger.warning(f"[ChatBridge] Unsupported file type: {content_type} / {ext}")
            return None

    file_hash = hashlib.sha256(file_content).hexdigest()
    idempotency_key = f"chat:{file_hash}"

    # Check dedup
    existing = await conn.fetchrow(
        """
        SELECT id, status, draft_plan, doc_type, original_filename
        FROM uploaded_documents
        WHERE tenant_id = $1 AND idempotency_key = $2
          AND status NOT IN ('rejected', 'cancelled')
        """,
        tenant_id, idempotency_key,
    )
    if existing:
        logger.info(f"[ChatBridge] Dedup hit: {filename} -> {file_hash[:12]}")
        return {
            "id": str(existing["id"]),
            "filename": existing["original_filename"],
            "status": existing["status"],
            "is_duplicate": True,
            "draft_plan": (
                json.loads(existing["draft_plan"])
                if existing["draft_plan"] else None
            ),
            "doc_type": existing["doc_type"],
        }

    # Advisory lock
    lock_key = f"DOC_UPLOAD:{tenant_id}:{file_hash}"
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", lock_key)

    # Double-check after lock
    existing = await conn.fetchrow(
        """
        SELECT id, status, draft_plan, doc_type, original_filename
        FROM uploaded_documents
        WHERE tenant_id = $1 AND idempotency_key = $2
          AND status NOT IN ('rejected', 'cancelled')
        """,
        tenant_id, idempotency_key,
    )
    if existing:
        return {
            "id": str(existing["id"]),
            "filename": existing["original_filename"],
            "status": existing["status"],
            "is_duplicate": True,
            "draft_plan": (
                json.loads(existing["draft_plan"])
                if existing["draft_plan"] else None
            ),
            "doc_type": existing["doc_type"],
        }

    # Store file in document intake path (not chat path)
    store_dir = os.path.join(UPLOAD_BASE_DIR, tenant_id, "documents")
    os.makedirs(store_dir, exist_ok=True)
    store_path = os.path.join(store_dir, f"{file_hash}{ext}")

    if not os.path.exists(store_path):
        with open(store_path, "wb") as fh:
            fh.write(file_content)
        logger.info(f"[ChatBridge] Stored: {filename} ({len(file_content)} bytes) -> {store_path}")

    # Create batch for this chat upload
    batch_id = uuid4()
    await conn.execute(
        """
        INSERT INTO document_batches (id, tenant_id, user_id, total_documents, status)
        VALUES ($1, $2, $3::uuid, 1, 'processing')
        """,
        batch_id, tenant_id, user_id,
    )

    # Insert document record
    doc_id = uuid4()
    await conn.fetchrow(
        """
        INSERT INTO uploaded_documents (
            id, tenant_id, batch_id, user_id,
            original_filename, file_path, file_hash,
            file_size_bytes, mime_type,
            idempotency_key, status, status_detail
        ) VALUES (
            $1, $2, $3, $4::uuid,
            $5, $6, $7,
            $8, $9,
            $10, 'queued', 'Uploaded via chat'
        )
        """,
        doc_id, tenant_id, batch_id, user_id,
        filename or "unnamed", store_path, file_hash,
        len(file_content), content_type,
        idempotency_key,
    )

    logger.info(f"[ChatBridge] Created doc record: {doc_id} for {filename}")

    return {
        "id": str(doc_id),
        "filename": filename,
        "status": "queued",
        "is_duplicate": False,
        "draft_plan": None,
        "doc_type": None,
    }


# ---------------------------------------------------------------------------
# Sync Processing: Process single document and wait for result
# ---------------------------------------------------------------------------

async def process_document_sync(
    pool: asyncpg.Pool,
    doc_id: str,
    tenant_id: str,
) -> Dict[str, Any]:
    """
    Process a single document through the full pipeline synchronously.
    Called from chat flow -- waits until draft_ready or failure.

    Returns:
        {"status": "draft_ready", "draft_plan": {...}, "document": {...}}
        {"status": "<error_status>", "error": "..."}
    """
    from .document_processor import DocumentProcessor

    processor = DocumentProcessor(pool)

    # Fetch the document record
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)", tenant_id
        )
        doc = await conn.fetchrow(
            """
            SELECT id, tenant_id, batch_id, file_path, mime_type,
                   file_hash, original_filename, retry_count, max_retries,
                   status, ocr_result, doc_type, analysis_result
            FROM uploaded_documents
            WHERE id = $1::uuid AND tenant_id = $2
            """,
            doc_id, tenant_id,
        )

    if not doc:
        return {"status": "error", "error": "Document not found"}

    doc_dict = dict(doc)

    # Process through full pipeline (synchronous -- returns when done)
    try:
        result = await processor.process_single_document(doc_dict)
    except Exception as e:
        logger.error(f"[ChatBridge] Pipeline failed for {doc_id}: {e}", exc_info=True)
        return {
            "status": "pipeline_error",
            "error": f"Pipeline error: {str(e)[:200]}",
        }

    status = result.get("status", "unknown")

    if status == "draft_ready":
        # Fetch the updated document with draft_plan
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            updated = await conn.fetchrow(
                """
                SELECT id, status, draft_plan, doc_type, original_filename,
                       classification_confidence, ocr_confidence,
                       analysis_result
                FROM uploaded_documents
                WHERE id = $1::uuid AND tenant_id = $2
                """,
                doc_id, tenant_id,
            )

        if not updated or not updated["draft_plan"]:
            return {"status": "draft_failed", "error": "Draft plan not generated"}

        draft_plan = json.loads(updated["draft_plan"]) if isinstance(updated["draft_plan"], str) else dict(updated["draft_plan"])
        analysis_result = None
        if updated["analysis_result"]:
            analysis_result = (
                json.loads(updated["analysis_result"])
                if isinstance(updated["analysis_result"], str)
                else dict(updated["analysis_result"])
            )

        return {
            "status": "draft_ready",
            "draft_plan": draft_plan,
            "analysis_result": analysis_result,
            "document": {
                "id": str(updated["id"]),
                "filename": updated["original_filename"],
                "classification": updated["doc_type"],
                "ocr_confidence": float(updated["ocr_confidence"] or 0),
                "classification_confidence": float(updated["classification_confidence"] or 0),
            },
        }

    # Pipeline didn't reach draft_ready
    return {
        "status": status,
        "error": result.get("error") or f"Pipeline ended at status: {status}",
    }


# ---------------------------------------------------------------------------
# Draft-to-DirectAction: Convert pipeline draft to chat proposal
# ---------------------------------------------------------------------------

def build_document_narration(
    draft_plan: Dict[str, Any],
    document: Dict[str, Any],
    analysis_result: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build natural narration text for a document draft.
    """
    action_type = draft_plan.get("action_type", "unknown")
    classification = document.get("classification", "")

    # Extract counterparty name
    counterparty_name = "Unknown"
    if analysis_result:
        anomalies = analysis_result.get("anomalies", [])
        for a in anomalies:
            details = a.get("details", {})
            if details.get("counterparty_name"):
                counterparty_name = details["counterparty_name"]
                break
    # Fallback to draft_plan counterparty
    if counterparty_name == "Unknown":
        cp = draft_plan.get("counterparty", {})
        if isinstance(cp, dict):
            counterparty_name = cp.get("name", "Unknown")

    # Extract total from journal draft
    journal_draft = draft_plan.get("journal_draft", {})
    total = journal_draft.get("total_debit", 0)
    if not total:
        lines = journal_draft.get("lines", [])
        total = sum(float(l.get("debit", 0)) for l in lines)

    # Format amount
    try:
        total_formatted = f"Rp {int(float(total)):,}".replace(",", ".")
    except (ValueError, TypeError):
        total_formatted = f"Rp {total}"

    # Build narration
    parts = []

    # Classification description
    classification_map = {
        "invoice_purchase": "faktur pembelian",
        "purchase_invoice": "faktur pembelian",
        "invoice_sales": "faktur penjualan",
        "sales_invoice": "faktur penjualan",
        "receipt": "kwitansi/bukti bayar",
        "expense_receipt": "bukti pengeluaran",
        "bank_statement": "rekening koran",
    }
    doc_type_text = classification_map.get(classification, classification or "dokumen")

    parts.append(f"Saya baca {doc_type_text} dari **{counterparty_name}**.")

    # Items summary
    inv_movements = draft_plan.get("inventory_movements", [])
    journal_lines = journal_draft.get("lines", [])
    if inv_movements:
        item_count = len(inv_movements)
        parts.append(f"{item_count} item, total {total_formatted}.")
    elif journal_lines:
        parts.append(f"Total {total_formatted}.")

    # Warnings
    warnings = draft_plan.get("warnings", [])
    if warnings:
        for w in warnings:
            if "vendor" in str(w).lower() or "new" in str(w).lower():
                parts.append(f"**Catatan:** {w}")

    # Confidence
    ocr_conf = document.get("ocr_confidence", 0)
    if ocr_conf and ocr_conf < 0.7:
        parts.append("**Perhatian:** Kualitas baca dokumen agak rendah, mohon dicek ulang.")

    return "\n\n".join(parts)


def build_draft_proposal(
    draft_plan: Dict[str, Any],
    document: Dict[str, Any],
    analysis_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convert pipeline draft to DirectAction proposal data.
    Uses existing confirm_document_draft DirectAction.

    Returns dict ready for propose_direct_action flow.
    """
    doc_id = document.get("id", "")
    narration = build_document_narration(draft_plan, document, analysis_result)

    action_type = draft_plan.get("action_type", "unknown")

    # Build payload for confirm_document_draft DirectAction
    payload = {
        "document_id": doc_id,
    }

    return {
        "narration": narration,
        "action_key": "confirm_document_draft",
        "payload": payload,
        "document_id": doc_id,
        "action_type": action_type,
        "draft_plan": draft_plan,
    }
