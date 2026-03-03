"""
Document Intake Service
=======================
Handles file upload, SHA-256 dedup, batch creation, status management.
Phase 2: Upload + storage pipeline. OCR/classification deferred to Phase 3.
"""
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import asyncpg
from fastapi import UploadFile

logger = logging.getLogger(__name__)

# Storage configuration
UPLOAD_BASE_DIR = os.getenv("DOCUMENT_UPLOAD_DIR", "/tmp/milkyhoop_uploads")

# Allowed MIME types for financial documents
ALLOWED_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}

MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB per file
MAX_FILES_PER_BATCH = 20


class DocumentIntakeService:
    """Service for the document intake pipeline."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    # ------------------------------------------------------------------
    # UPLOAD
    # ------------------------------------------------------------------

    async def upload_documents(
        self,
        tenant_id: str,
        user_id: UUID,
        files: List[UploadFile],
        *,
        batch_id: Optional[UUID] = None,
        doc_type_hint: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Upload one or more files. Returns (batch_row, document_rows).

        Steps:
        1. Validate files (type, size)
        2. Create or reuse batch
        3. For each file: hash, dedup, store on disk, insert DB row
        4. Update batch counters
        """
        if len(files) > MAX_FILES_PER_BATCH:
            raise ValueError(f"Maximum {MAX_FILES_PER_BATCH} files per upload")

        # Pre-validate all files before touching DB
        file_data_list: List[Tuple[UploadFile, bytes, str, str]] = []
        for f in files:
            content = await f.read()
            await f.seek(0)

            if len(content) > MAX_FILE_SIZE:
                raise ValueError(
                    f"File {f.filename} exceeds {MAX_FILE_SIZE // (1024*1024)}MB limit"
                )

            mime = f.content_type or "application/octet-stream"
            if mime not in ALLOWED_MIME_TYPES:
                allowed = ", ".join(ALLOWED_MIME_TYPES.keys())
                raise ValueError(
                    f"File type {mime} not allowed. Accepted: {allowed}"
                )

            file_hash = hashlib.sha256(content).hexdigest()
            ext = ALLOWED_MIME_TYPES[mime]
            file_data_list.append((f, content, file_hash, ext))

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Create or fetch batch
                if batch_id:
                    batch_row = await conn.fetchrow(
                        """
                        SELECT * FROM document_batches
                        WHERE id = $1 AND tenant_id = $2
                        """,
                        batch_id, tenant_id,
                    )
                    if not batch_row:
                        raise ValueError("Batch not found")
                else:
                    batch_id = uuid4()
                    batch_row = await conn.fetchrow(
                        """
                        INSERT INTO document_batches (id, tenant_id, user_id, total_documents, status)
                        VALUES ($1, $2, $3, $4, 'processing')
                        RETURNING *
                        """,
                        batch_id, tenant_id, user_id, len(file_data_list),
                    )

                doc_rows = []
                for f, content, file_hash, ext in file_data_list:
                    doc_row = await self._store_and_insert(
                        conn, tenant_id, user_id, batch_id,
                        f, content, file_hash, ext,
                        doc_type_hint=doc_type_hint,
                        notes=notes,
                    )
                    doc_rows.append(doc_row)

                # Sync batch counters with actual document count
                actual_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM uploaded_documents
                    WHERE batch_id = $1 AND tenant_id = $2
                    """,
                    batch_id, tenant_id,
                )
                if actual_count != batch_row["total_documents"]:
                    batch_row = await conn.fetchrow(
                        """
                        UPDATE document_batches
                        SET total_documents = $1
                        WHERE id = $2 AND tenant_id = $3
                        RETURNING *
                        """,
                        actual_count, batch_id, tenant_id,
                    )

        return dict(batch_row), [dict(r) for r in doc_rows]

    async def _store_and_insert(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        user_id: UUID,
        batch_id: UUID,
        file: UploadFile,
        content: bytes,
        file_hash: str,
        ext: str,
        *,
        doc_type_hint: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> asyncpg.Record:
        """Store file on disk + insert uploaded_documents row."""

        store_dir = os.path.join(UPLOAD_BASE_DIR, tenant_id, "documents")
        store_path = os.path.join(store_dir, f"{file_hash}{ext}")

        # Idempotency key = hash (same content = same document)
        idempotency_key = f"doc:{file_hash}"

        # Check dedup via idempotency index
        existing = await conn.fetchrow(
            """
            SELECT * FROM uploaded_documents
            WHERE tenant_id = $1 AND idempotency_key = $2
              AND status NOT IN ('rejected', 'cancelled')
            """,
            tenant_id, idempotency_key,
        )
        if existing:
            logger.info(
                f"[DocIntake] Dedup hit: {file.filename} -> {file_hash[:12]}"
            )
            # Re-assign to current batch so response batch_id is consistent
            if existing["batch_id"] != batch_id:
                existing = await conn.fetchrow(
                    """
                    UPDATE uploaded_documents
                    SET batch_id = $3
                    WHERE id = $1 AND tenant_id = $2
                    RETURNING *
                    """,
                    existing["id"], tenant_id, batch_id,
                )
            return existing

        # Advisory lock to prevent race on same file hash
        lock_key = f"DOC_UPLOAD:{tenant_id}:{file_hash}"
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))", lock_key
        )

        # Double-check after lock
        existing = await conn.fetchrow(
            """
            SELECT * FROM uploaded_documents
            WHERE tenant_id = $1 AND idempotency_key = $2
              AND status NOT IN ('rejected', 'cancelled')
            """,
            tenant_id, idempotency_key,
        )
        if existing:
            # Re-assign to current batch so response batch_id is consistent
            if existing["batch_id"] != batch_id:
                existing = await conn.fetchrow(
                    """
                    UPDATE uploaded_documents
                    SET batch_id = $3
                    WHERE id = $1 AND tenant_id = $2
                    RETURNING *
                    """,
                    existing["id"], tenant_id, batch_id,
                )
            return existing

        # Write file to disk
        os.makedirs(store_dir, exist_ok=True)
        with open(store_path, "wb") as fh:
            fh.write(content)

        logger.info(
            f"[DocIntake] Stored: {file.filename} ({len(content)} bytes) -> {store_path}"
        )

        # Insert DB row
        doc_id = uuid4()
        row = await conn.fetchrow(
            """
            INSERT INTO uploaded_documents (
                id, tenant_id, batch_id, user_id,
                original_filename, file_path, file_hash,
                file_size_bytes, mime_type,
                idempotency_key, status, status_detail,
                doc_type
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9,
                $10, 'queued', $11,
                $12
            )
            RETURNING *
            """,
            doc_id, tenant_id, batch_id, user_id,
            file.filename or "unnamed", store_path, file_hash,
            len(content), file.content_type,
            idempotency_key, notes,
            doc_type_hint,
        )
        return row

    # ------------------------------------------------------------------
    # BATCH STATUS
    # ------------------------------------------------------------------

    async def get_batch_status(
        self, tenant_id: str, batch_id: UUID
    ) -> Tuple[Optional[Dict], List[Dict]]:
        """Get batch + all its documents."""
        async with self.pool.acquire() as conn:
            batch = await conn.fetchrow(
                """
                SELECT * FROM document_batches
                WHERE id = $1 AND tenant_id = $2
                """,
                batch_id, tenant_id,
            )
            if not batch:
                return None, []

            docs = await conn.fetch(
                """
                SELECT * FROM uploaded_documents
                WHERE batch_id = $1 AND tenant_id = $2
                ORDER BY created_at ASC
                """,
                batch_id, tenant_id,
            )
            return dict(batch), [dict(d) for d in docs]

    # ------------------------------------------------------------------
    # DOCUMENT DETAIL
    # ------------------------------------------------------------------

    async def get_document_detail(
        self, tenant_id: str, doc_id: UUID
    ) -> Optional[Dict]:
        """Get single document with full detail (OCR result, analysis, draft)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM uploaded_documents
                WHERE id = $1 AND tenant_id = $2
                """,
                doc_id, tenant_id,
            )
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # REVIEW QUEUE
    # ------------------------------------------------------------------

    async def get_review_queue(
        self,
        tenant_id: str,
        *,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict], int]:
        """
        Get documents pending review (draft_ready, reviewing).
        Returns (documents, total_count).
        """
        statuses = ["draft_ready", "reviewing"]
        if status_filter and status_filter in (
            "draft_ready", "reviewing", "confirmed", "posted", "failed", "rejected"
        ):
            statuses = [status_filter]

        async with self.pool.acquire() as conn:
            total = await conn.fetchval(
                """
                SELECT COUNT(*) FROM uploaded_documents
                WHERE tenant_id = $1 AND status = ANY($2::text[])
                """,
                tenant_id, statuses,
            )

            docs = await conn.fetch(
                """
                SELECT id, batch_id, original_filename, file_hash,
                       file_size_bytes, mime_type, status, status_detail,
                       retry_count, ocr_confidence, ocr_model_used,
                       doc_type, classification_confidence,
                       draft_plan, journal_entry_id, bank_transaction_id,
                       confirmed_by, confirmed_at, posted_at,
                       created_at, updated_at
                FROM uploaded_documents
                WHERE tenant_id = $1 AND status = ANY($2::text[])
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                tenant_id, statuses, limit, offset,
            )
            return [dict(d) for d in docs], total

    # ------------------------------------------------------------------
    # CONFIRM / REJECT
    # ------------------------------------------------------------------

    async def confirm_document(
        self,
        tenant_id: str,
        doc_id: UUID,
        user_id: UUID,
        *,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict]:
        """Confirm a draft_ready document for posting."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT * FROM uploaded_documents
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                    """,
                    doc_id, tenant_id,
                )
                if not row:
                    return None
                if row["status"] not in ("draft_ready", "reviewing"):
                    raise ValueError(
                        f"Cannot confirm document in status '{row['status']}'"
                    )

                # Apply overrides to draft_plan if provided
                draft_plan = row["draft_plan"] or {}
                if isinstance(draft_plan, str):
                    draft_plan = json.loads(draft_plan)
                if overrides:
                    draft_plan.update(overrides)

                updated = await conn.fetchrow(
                    """
                    UPDATE uploaded_documents
                    SET status = 'confirmed',
                        confirmed_by = $3,
                        confirmed_at = NOW(),
                        draft_plan = $4::jsonb
                    WHERE id = $1 AND tenant_id = $2
                    RETURNING *
                    """,
                    doc_id, tenant_id, user_id,
                    json.dumps(draft_plan),
                )
                return dict(updated) if updated else None

    async def reject_document(
        self,
        tenant_id: str,
        doc_id: UUID,
        *,
        reason: Optional[str] = None,
    ) -> bool:
        """Reject a document."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE uploaded_documents
                SET status = 'rejected',
                    status_detail = COALESCE($3, status_detail)
                WHERE id = $1 AND tenant_id = $2
                  AND status NOT IN ('posted', 'cancelled')
                """,
                doc_id, tenant_id, reason,
            )
            return result != "UPDATE 0"
