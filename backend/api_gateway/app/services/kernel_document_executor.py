"""
Kernel Document Executor — Tahap 1 Refactor

Routes confirmed documents to existing REST endpoints instead of direct INSERT.
REST endpoints handle ALL Iron Law compliance (advisory locks, hash chain,
double-entry, inventory WAC, AP/AR tracking).

Flow:
  1. Peek at action_type to determine routing
  2. If REST route exists: transform → HTTP call → finalize
  3. If unknown action_type: fall back to legacy direct-INSERT path

Iron Laws enforced:
  Law 0:  Kernel executes (not LLM)
  Law 13: Advisory lock DOCUMENT_INTAKE:{doc_id}
  Law 14: Idempotency via already-posted check
  Law 23: Atomic transaction per phase
  Law 24: RLS context set per connection
  Law 25: Decimal precision (int amounts in payloads)
"""

import json
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from .resolve_account import resolve_accounts_by_codes

logger = logging.getLogger(__name__)

SOURCE_TYPE = "DOCUMENT_INTAKE"
LOCK_PREFIX = "DOCUMENT_INTAKE"
KERNEL_BASE_URL = "http://localhost:8000"
REST_TIMEOUT = 15.0


class ExecutionResult:
    """Result of a document execution."""

    def __init__(
        self,
        success: bool,
        document_id: str,
        journal_id: Optional[str] = None,
        journal_number: Optional[str] = None,
        bank_transaction_id: Optional[str] = None,
        inventory_ledger_ids: Optional[List[str]] = None,
        error: Optional[str] = None,
        is_duplicate: bool = False,
        transaction_type: Optional[str] = None,
        transaction_id: Optional[str] = None,
    ):
        self.success = success
        self.document_id = document_id
        self.journal_id = journal_id
        self.journal_number = journal_number
        self.bank_transaction_id = bank_transaction_id
        self.inventory_ledger_ids = inventory_ledger_ids or []
        self.error = error
        self.is_duplicate = is_duplicate
        self.transaction_type = transaction_type
        self.transaction_id = transaction_id

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "document_id": self.document_id,
            "journal_id": self.journal_id,
            "journal_number": self.journal_number,
            "bank_transaction_id": self.bank_transaction_id,
            "inventory_ledger_ids": self.inventory_ledger_ids,
            "error": self.error,
            "is_duplicate": self.is_duplicate,
            "transaction_type": self.transaction_type,
            "transaction_id": self.transaction_id,
        }


class KernelDocumentExecutor:
    """
    Posts confirmed documents by routing to REST endpoints.

    REST path (action_type in ACTION_ROUTES):
      - Transform draft_plan → endpoint payload
      - Call POST /api/bills/v2, /api/sales-invoices, etc.
      - Endpoint creates: transaction record + journal + inventory + AP/AR
      - Link uploaded_document → transaction_id

    Legacy path (unknown action_type):
      - Direct INSERT journal_entries/lines (Phase 8 behavior)
      - Used as fallback only
    """

    def __init__(self, pool, auth_token: str = None):
        self.pool = pool
        self.auth_token = auth_token

    # ═══════════════════════════════════════════════════════════════════
    # MAIN EXECUTE — ROUTER
    # ═══════════════════════════════════════════════════════════════════

    async def execute(
        self,
        document_id: str,
        tenant_id: str,
        user_id: str,
    ) -> ExecutionResult:
        """
        Execute a confirmed document. Routes to REST endpoint if possible,
        falls back to legacy direct-INSERT for unknown action_types.
        """
        from .payload_transformers import get_route

        # Quick peek at action_type (read-only, no lock needed)
        action_type = await self._peek_action_type(document_id, tenant_id)
        route = get_route(action_type)

        if route:
            logger.info(
                f"[KDE] REST path: doc={document_id}, action={action_type}, "
                f"endpoint={route[0]}"
            )
            return await self._execute_via_rest(
                document_id, tenant_id, user_id, route
            )
        else:
            logger.info(
                f"[KDE] Legacy path: doc={document_id}, action={action_type}"
            )
            return await self._execute_legacy(document_id, tenant_id, user_id)

    # ═══════════════════════════════════════════════════════════════════
    # REST EXECUTION PATH
    # ═══════════════════════════════════════════════════════════════════

    async def _execute_via_rest(
        self,
        document_id: str,
        tenant_id: str,
        user_id: str,
        route: tuple,
    ) -> ExecutionResult:
        """
        Execute via REST endpoint.

        Phase 1: Lock + validate + snapshot data (DB transaction)
        Phase 2: Transform payload (no DB)
        Phase 3: Call REST endpoint (HTTP, no DB transaction)
        Phase 4: Finalize — link document to transaction (DB transaction)
        """
        doc_uuid = uuid.UUID(document_id)
        endpoint, method, transformer, transaction_type = route

        # ── Phase 1: Lock + Validate ──────────────────────────────
        draft_plan = None
        doc_snapshot = None
        batch_id = None

        async with self.pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            async with conn.transaction():
                # Advisory lock (Law 13)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"{LOCK_PREFIX}:{document_id}",
                )

                # Idempotency check (Law 14)
                existing = await conn.fetchrow(
                    """SELECT status, journal_entry_id, transaction_id, transaction_type
                       FROM uploaded_documents
                       WHERE id = $1 AND tenant_id = $2""",
                    doc_uuid, tenant_id,
                )
                if existing and existing["status"] == "posted":
                    return ExecutionResult(
                        success=True,
                        document_id=document_id,
                        journal_id=str(existing["journal_entry_id"]) if existing["journal_entry_id"] else None,
                        transaction_type=existing["transaction_type"],
                        transaction_id=str(existing["transaction_id"]) if existing["transaction_id"] else None,
                        is_duplicate=True,
                    )

                # Fetch document FOR UPDATE
                doc = await conn.fetchrow(
                    """SELECT id, tenant_id, status, doc_type, draft_plan,
                              analysis_result, original_filename, batch_id
                       FROM uploaded_documents
                       WHERE id = $1 AND tenant_id = $2
                       FOR UPDATE""",
                    doc_uuid, tenant_id,
                )
                if not doc:
                    return ExecutionResult(
                        success=False, document_id=document_id,
                        error=f"Document {document_id} not found",
                    )
                if doc["status"] != "confirmed":
                    return ExecutionResult(
                        success=False, document_id=document_id,
                        error=f"Status is '{doc['status']}', expected 'confirmed'",
                    )

                # Parse draft_plan
                draft_plan = doc["draft_plan"]
                if isinstance(draft_plan, str):
                    draft_plan = json.loads(draft_plan)
                if not draft_plan:
                    return await self._mark_failed(
                        conn, doc_uuid, tenant_id, "No draft_plan found"
                    )

                # Snapshot document data for Phase 2
                ar = doc["analysis_result"]
                if isinstance(ar, str):
                    ar = json.loads(ar)
                doc_snapshot = {
                    "analysis_result": ar,
                    "doc_type": doc["doc_type"],
                    "original_filename": doc["original_filename"],
                }
                batch_id = doc["batch_id"]

                # Mark as posting (prevents concurrent re-execution)
                await conn.execute(
                    """UPDATE uploaded_documents
                       SET status = 'posting', updated_at = NOW()
                       WHERE id = $1 AND tenant_id = $2""",
                    doc_uuid, tenant_id,
                )

        # ── Phase 2: Transform ────────────────────────────────────
        try:
            payload = transformer(draft_plan, doc_snapshot)
            logger.info(f"[KDE] Payload transformed for doc {document_id}")
        except Exception as e:
            logger.error(f"[KDE] Transformer error for doc {document_id}: {e}")
            await self._update_status(
                doc_uuid, tenant_id, "posting_failed",
                f"Transformer error: {str(e)[:500]}"
            )
            return ExecutionResult(
                success=False, document_id=document_id,
                error=f"Transformer: {e}",
            )

        # ── Phase 2b: Pre-resolve vendor for bill/expense ─────
        if transaction_type in ("bill", "expense"):
            vendor_name_val = payload.get("vendor_name")
            if vendor_name_val and not payload.get("vendor_id"):
                async with self.pool.acquire() as conn2:
                    resolved_vendor_id = await self._find_or_create_vendor(
                        conn2, tenant_id, vendor_name_val
                    )
                    if resolved_vendor_id:
                        payload["vendor_id"] = resolved_vendor_id
                        logger.info(
                            f"[KDE] Pre-resolved vendor for doc {document_id}: "
                            f"{vendor_name_val} -> {resolved_vendor_id}"
                        )

        # ── Phase 3: Call REST endpoint ───────────────────────────
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-ID": tenant_id,
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            async with httpx.AsyncClient(timeout=REST_TIMEOUT) as client:
                url = f"{KERNEL_BASE_URL}{endpoint}"
                logger.info(f"[KDE] REST call: {method} {url}")

                resp = await client.request(method, url, json=payload, headers=headers)

                if resp.status_code >= 400:
                    error_body = resp.text
                    try:
                        ej = resp.json()
                        error_body = str(ej.get("detail", ej))
                    except Exception:
                        pass

                    logger.error(
                        f"[KDE] REST {resp.status_code} for doc {document_id}: "
                        f"{error_body[:300]}"
                    )
                    await self._update_status(
                        doc_uuid, tenant_id, "posting_failed",
                        f"REST {resp.status_code}: {str(error_body)[:500]}"
                    )
                    return ExecutionResult(
                        success=False, document_id=document_id,
                        error=f"REST {resp.status_code}: {error_body}",
                    )

                result = resp.json()
                logger.info(
                    f"[KDE] REST success for doc {document_id}: "
                    f"status={resp.status_code}"
                )

        except httpx.TimeoutException:
            logger.error(f"[KDE] Timeout calling {endpoint} for doc {document_id}")
            await self._update_status(
                doc_uuid, tenant_id, "posting_failed", "REST timeout"
            )
            return ExecutionResult(
                success=False, document_id=document_id, error="REST timeout"
            )
        except Exception as e:
            logger.error(f"[KDE] HTTP error for doc {document_id}: {e}")
            await self._update_status(
                doc_uuid, tenant_id, "posting_failed",
                f"HTTP error: {str(e)[:500]}"
            )
            return ExecutionResult(
                success=False, document_id=document_id,
                error=f"HTTP error: {e}",
            )

        # ── Phase 4: Finalize ─────────────────────────────────────
        # Extract IDs from response (bills v2 wraps in "data")
        data = result.get("data") or result
        tx_id_str = (
            data.get("id")
            or data.get("bill_id")
            or data.get("invoice_id")
            or data.get("expense_id")
        )
        journal_id_str = data.get("journal_id") or data.get("journal_entry_id")
        journal_number = data.get("journal_number")

        tx_id_uuid = uuid.UUID(tx_id_str) if tx_id_str else None
        journal_id_uuid = uuid.UUID(journal_id_str) if journal_id_str else None

        async with self.pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            async with conn.transaction():
                # If journal_id not in REST response, look it up
                if not journal_id_uuid and tx_id_uuid:
                    journal_id_uuid = await self._lookup_journal_id(
                        conn, transaction_type, tx_id_uuid, tenant_id
                    )

                # Link document to transaction + mark posted
                await conn.execute(
                    """UPDATE uploaded_documents
                       SET status = 'posted',
                           transaction_type = $1,
                           transaction_id = $2,
                           journal_entry_id = $3,
                           posted_at = NOW(),
                           updated_at = NOW()
                       WHERE id = $4 AND tenant_id = $5""",
                    transaction_type,
                    tx_id_uuid,
                    journal_id_uuid,
                    doc_uuid,
                    tenant_id,
                )

                # Sync batch counters
                if batch_id:
                    await self._sync_batch(conn, batch_id, tenant_id)

        logger.info(
            f"[KDE] Posted via REST: doc={document_id}, "
            f"type={transaction_type}, tx={tx_id_str}, "
            f"journal={journal_id_uuid}"
        )

        return ExecutionResult(
            success=True,
            document_id=document_id,
            journal_id=str(journal_id_uuid) if journal_id_uuid else None,
            journal_number=journal_number,
            transaction_type=transaction_type,
            transaction_id=tx_id_str,
        )

    # ═══════════════════════════════════════════════════════════════════
    # LEGACY EXECUTION PATH (Phase 8 — direct INSERT)
    # ═══════════════════════════════════════════════════════════════════

    async def _execute_legacy(
        self,
        document_id: str,
        tenant_id: str,
        user_id: str,
    ) -> ExecutionResult:
        """
        Legacy execution — direct INSERT journal_entries/lines.
        Kept as fallback for unknown action_types (e.g. 'unknown').
        """
        doc_uuid = uuid.UUID(document_id)
        user_uuid = uuid.UUID(user_id) if user_id and user_id != "system" else None
        logger.info(f"[KDE-legacy] Starting: doc={document_id}")

        async with self.pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

            async with conn.transaction():
                # Advisory lock (Law 13)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"{LOCK_PREFIX}:{document_id}",
                )

                # Idempotency check
                existing = await conn.fetchrow(
                    """SELECT journal_entry_id, status
                       FROM uploaded_documents
                       WHERE id = $1 AND tenant_id = $2""",
                    doc_uuid, tenant_id,
                )
                if existing and existing["status"] == "posted" and existing["journal_entry_id"]:
                    return ExecutionResult(
                        success=True, document_id=document_id,
                        journal_id=str(existing["journal_entry_id"]),
                        is_duplicate=True,
                    )

                # Fetch document FOR UPDATE
                doc = await conn.fetchrow(
                    """SELECT * FROM uploaded_documents
                       WHERE id = $1 AND tenant_id = $2
                       FOR UPDATE""",
                    doc_uuid, tenant_id,
                )
                if not doc:
                    return ExecutionResult(
                        success=False, document_id=document_id,
                        error=f"Document {document_id} not found",
                    )
                if doc["status"] != "confirmed":
                    return ExecutionResult(
                        success=False, document_id=document_id,
                        error=f"Document status is '{doc['status']}', expected 'confirmed'",
                    )

                # Mark as posting
                await conn.execute(
                    """UPDATE uploaded_documents SET status = 'posting'
                       WHERE id = $1 AND tenant_id = $2""",
                    doc_uuid, tenant_id,
                )

                # Parse draft_plan
                draft_plan = doc["draft_plan"]
                if isinstance(draft_plan, str):
                    draft_plan = json.loads(draft_plan)
                if not draft_plan:
                    return await self._mark_failed(
                        conn, doc_uuid, tenant_id, "No draft_plan found"
                    )

                journal_draft = draft_plan.get("journal_draft", {})
                lines_raw = journal_draft.get("lines", [])
                if not lines_raw:
                    return await self._mark_failed(
                        conn, doc_uuid, tenant_id, "No journal lines in draft_plan"
                    )

                # Validate balance (Law 4)
                total_debit = Decimal("0")
                total_credit = Decimal("0")
                for line in lines_raw:
                    total_debit += Decimal(str(line.get("debit", "0")))
                    total_credit += Decimal(str(line.get("credit", "0")))

                if abs(total_debit - total_credit) >= Decimal("0.01"):
                    return await self._mark_failed(
                        conn, doc_uuid, tenant_id,
                        f"Journal not balanced: debit={total_debit}, credit={total_credit}",
                    )

                # Period lock check (Law 5)
                journal_date = self._parse_date(draft_plan)
                period_ok = await self._check_period(conn, tenant_id, journal_date)
                if not period_ok:
                    return await self._mark_failed(
                        conn, doc_uuid, tenant_id,
                        f"Period is closed for date {journal_date}",
                    )

                # Resolve account codes (Law 27)
                account_codes = list(set(
                    line["account_code"] for line in lines_raw
                    if line.get("account_code")
                ))
                try:
                    code_to_id = await resolve_accounts_by_codes(
                        conn, tenant_id, account_codes
                    )
                except ValueError as e:
                    return await self._mark_failed(
                        conn, doc_uuid, tenant_id,
                        f"Account resolution failed: {e}",
                    )

                # Get next journal number
                journal_number = await self._next_journal_number(
                    conn, tenant_id, "DI", journal_date
                )

                # INSERT journal_entries as DRAFT (Law 20)
                journal_id = uuid.uuid4()
                description = (
                    journal_draft.get("description", "")
                    or doc["original_filename"]
                )

                await conn.execute(
                    """INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id,
                        status, total_debit, total_credit,
                        created_by, created_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        'DRAFT', $8, $9, $10, NOW()
                    )""",
                    journal_id, tenant_id, journal_number, journal_date,
                    description, SOURCE_TYPE, doc_uuid,
                    total_debit, total_credit, user_uuid,
                )

                # INSERT journal_lines
                for idx, line in enumerate(lines_raw, 1):
                    line_id = uuid.uuid4()
                    account_id = uuid.UUID(code_to_id[line["account_code"]])
                    debit = Decimal(str(line.get("debit", "0")))
                    credit = Decimal(str(line.get("credit", "0")))
                    memo = line.get("account_name", "")

                    await conn.execute(
                        """INSERT INTO journal_lines (
                            id, journal_id, line_number,
                            account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                        line_id, journal_id, idx,
                        account_id, debit, credit, memo,
                    )

                # UPDATE to POSTED (Law 20 — triggers hash chain)
                await conn.execute(
                    """UPDATE journal_entries
                       SET status = 'POSTED'
                       WHERE id = $1 AND tenant_id = $2""",
                    journal_id, tenant_id,
                )

                # Bank transaction (if applicable)
                bank_tx_id = None
                bank_account_id = draft_plan.get("bank_account_id")
                if bank_account_id:
                    bank_tx_id = await self._create_bank_transaction(
                        conn, tenant_id, user_uuid, doc,
                        draft_plan, journal_id, journal_number,
                    )

                # Inventory movements (if applicable)
                inv_ids = []
                inventory_movements = draft_plan.get("inventory_movements", [])
                if inventory_movements:
                    inv_ids = await self._create_inventory_movements(
                        conn, tenant_id, user_uuid, doc_uuid,
                        journal_id, inventory_movements,
                    )

                # Mark document as posted
                await conn.execute(
                    """UPDATE uploaded_documents
                       SET status = 'posted',
                           journal_entry_id = $3,
                           bank_transaction_id = $4,
                           inventory_ledger_ids = $5,
                           posted_at = NOW()
                       WHERE id = $1 AND tenant_id = $2""",
                    doc_uuid, tenant_id,
                    journal_id,
                    uuid.UUID(bank_tx_id) if bank_tx_id else None,
                    [uuid.UUID(i) for i in inv_ids] if inv_ids else None,
                )

                # Sync batch counters
                if doc["batch_id"]:
                    await self._sync_batch(conn, doc["batch_id"], tenant_id)

                logger.info(
                    f"[KDE-legacy] Posted: doc={document_id}, "
                    f"journal={journal_id} ({journal_number})"
                )

                return ExecutionResult(
                    success=True,
                    document_id=document_id,
                    journal_id=str(journal_id),
                    journal_number=journal_number,
                    bank_transaction_id=bank_tx_id,
                    inventory_ledger_ids=inv_ids,
                )

    # ═══════════════════════════════════════════════════════════════════
    # SHARED HELPERS
    # ═══════════════════════════════════════════════════════════════════

    async def _peek_action_type(
        self, document_id: str, tenant_id: str
    ) -> Optional[str]:
        """Quick read of action_type from draft_plan (no lock)."""
        doc_uuid = uuid.UUID(document_id)
        async with self.pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await conn.fetchrow(
                """SELECT draft_plan->>'action_type' as action_type
                   FROM uploaded_documents
                   WHERE id = $1 AND tenant_id = $2""",
                doc_uuid, tenant_id,
            )
            if row and row["action_type"]:
                return row["action_type"]
        return None

    async def _lookup_journal_id(
        self, conn, transaction_type: str, tx_id: uuid.UUID, tenant_id: str
    ) -> Optional[uuid.UUID]:
        """Look up journal_id from the transaction table."""
        table_map = {
            "bill": ("bills", "journal_id"),
            "sales_invoice": ("sales_invoices", "journal_id"),
            "expense": ("expenses", "journal_id"),
            "bill_payment": ("bill_payments_v2", "journal_id"),
            "receive_payment": ("receive_payments", "journal_id"),
            "journal_entry": ("journal_entries", "id"),
        }
        mapping = table_map.get(transaction_type)
        if not mapping:
            return None

        table, col = mapping
        try:
            val = await conn.fetchval(
                f"SELECT {col} FROM {table} WHERE id = $1 AND tenant_id = $2",
                tx_id, tenant_id,
            )
            return val
        except Exception as e:
            logger.warning(f"[KDE] journal lookup failed for {table}: {e}")
            return None

    async def _update_status(
        self,
        doc_uuid: uuid.UUID,
        tenant_id: str,
        status: str,
        detail: str = None,
    ):
        """Update document status (standalone, new connection)."""
        async with self.pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await conn.execute(
                """UPDATE uploaded_documents
                   SET status = $1, status_detail = $2, updated_at = NOW()
                   WHERE id = $3 AND tenant_id = $4""",
                status, detail, doc_uuid, tenant_id,
            )

    async def _sync_batch(self, conn, batch_id, tenant_id: str):
        """Sync batch counters after document status change."""
        batch_stats = await conn.fetchrow(
            """SELECT
                  COUNT(*) as total,
                  COUNT(*) FILTER (
                      WHERE status IN ('draft_ready','confirmed','posted','posting_failed','rejected')
                  ) as done,
                  COUNT(*) FILTER (
                      WHERE status IN ('posting_failed','rejected')
                  ) as failed
               FROM uploaded_documents
               WHERE batch_id = $1 AND tenant_id = $2""",
            batch_id, tenant_id,
        )
        new_status = (
            "completed"
            if batch_stats["done"] == batch_stats["total"]
            else "processing"
        )
        await conn.execute(
            """UPDATE document_batches
               SET processed_count = $3,
                   failed_count = $4,
                   total_documents = $5,
                   status = $6,
                   updated_at = NOW()
               WHERE id = $1 AND tenant_id = $2""",
            batch_id, tenant_id,
            batch_stats["done"], batch_stats["failed"],
            batch_stats["total"], new_status,
        )

    def _parse_date(self, draft_plan: dict) -> date:
        """Extract journal date from draft_plan or OCR result."""
        date_str = draft_plan.get("date") or draft_plan.get("journal_date")
        if not date_str:
            journal_draft = draft_plan.get("journal_draft", {})
            date_str = journal_draft.get("date") or journal_draft.get("journal_date")
        if not date_str:
            ocr = draft_plan.get("ocr_result") or {}
            date_str = ocr.get("date")
        if date_str:
            try:
                return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
        return date.today()

    async def _check_period(self, conn, tenant_id: str, journal_date: date) -> bool:
        """Check if accounting period is open (Law 5)."""
        row = await conn.fetchrow(
            """SELECT status FROM fiscal_periods
               WHERE tenant_id = $1
                 AND start_date <= $2 AND end_date >= $2
               LIMIT 1""",
            tenant_id, journal_date,
        )
        if not row:
            return True
        return row["status"] == "OPEN"

    async def _next_journal_number(
        self, conn, tenant_id: str, prefix: str = "DI", p_date=None
    ) -> str:
        """Generate next journal number via the canonical self-healing DB fn.

        Delegates to get_next_journal_number(tenant, prefix, p_date) (V176):
        bumps the prefix's own counter AND self-heals against the actual emitted
        max (drift-proof, concurrency-safe). p_date defaults to today; callers
        should pass journal_date so the YYMM segment tracks the document date.
        """
        if p_date is None:
            p_date = date.today()
        return await conn.fetchval(
            "SELECT get_next_journal_number($1, $2, $3)",
            tenant_id, prefix, p_date,
        )

    async def _mark_failed(
        self, conn, doc_uuid, tenant_id: str, error: str
    ) -> ExecutionResult:
        """Mark document as failed within a transaction."""
        logger.error(f"[KDE] Failed: doc={doc_uuid}, error={error}")
        await conn.execute(
            """UPDATE uploaded_documents
               SET status = 'posting_failed'
               WHERE id = $1 AND tenant_id = $2""",
            doc_uuid, tenant_id,
        )
        return ExecutionResult(
            success=False, document_id=str(doc_uuid), error=error
        )

    # ═══════════════════════════════════════════════════════════════════
    # LEGACY HELPERS (used by _execute_legacy only)
    # ═══════════════════════════════════════════════════════════════════

    async def _create_bank_transaction(
        self, conn, tenant_id, user_uuid, doc, draft_plan, journal_id, journal_number
    ):
        """Create bank transaction linked to journal (legacy path)."""
        bank_account_id = draft_plan.get("bank_account_id")
        if not bank_account_id:
            return None

        journal_draft = draft_plan.get("journal_draft", {})
        total_debit = Decimal(str(journal_draft.get("total_debit", "0")))
        total_credit = Decimal(str(journal_draft.get("total_credit", "0")))

        doc_type = doc["doc_type"] if doc["doc_type"] else ""
        if doc_type in ("sales_invoice", "receipt", "sales_receipt"):
            amount = total_debit
            tx_type = "DEPOSIT"
        else:
            amount = total_credit
            tx_type = "WITHDRAWAL"

        if amount <= Decimal("0"):
            return None

        # DOCUMENT_INTAKE guard: Check balance before withdrawal
        if tx_type in ("WITHDRAWAL", "CREDIT"):
            current_bal = await conn.fetchval("""
                SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                WHERE jl.account_id = (
                    SELECT coa_id FROM bank_accounts WHERE id = $1::uuid
                )
                  AND je.status = 'POSTED'
                  AND je.tenant_id = $2
            """, uuid.UUID(bank_account_id), tenant_id)
            if current_bal is not None and Decimal(str(current_bal)) < abs(amount):
                raise ValueError(
                    f"DOCUMENT_INTAKE guard: Insufficient balance. "
                    f"Account balance: {current_bal}, Required: {abs(amount)}. "
                    f"Route to AP/bill instead of direct payment."
                )

        tx_id = uuid.uuid4()
        description = (
            journal_draft.get("description", "")
            or str(doc["original_filename"] or "")
        )
        journal_date = self._parse_date(draft_plan)
        tx_number = f"DI-{journal_number}"

        await conn.execute(
            """INSERT INTO bank_transactions (
                id, tenant_id, bank_account_id, transaction_date,
                transaction_type, amount,
                reference_type, reference_id,
                description, journal_id,
                status, origin_type, source_module, transaction_number,
                created_by
            ) VALUES (
                $1, $2, $3::uuid, $4, $5, $6, $7, $8,
                $9, $10, 'POSTED', 'DOCUMENT_INTAKE', 'document_intake', $11, $12
            )""",
            tx_id, tenant_id, bank_account_id, journal_date,
            tx_type, amount, SOURCE_TYPE, doc["id"],
            description, journal_id, tx_number, user_uuid,
        )

        # Law 21: current_balance cache deprecated (v3.5). Balance derived from journal.
        # balance_delta = amount if tx_type == "DEPOSIT" else -amount
        # await conn.execute(
        #     """UPDATE bank_accounts
        #        SET current_balance = current_balance + $2, updated_at = NOW()
        #        WHERE id = $1::uuid AND tenant_id = $3""",
        #     uuid.UUID(bank_account_id), balance_delta, tenant_id,
        # )
        return str(tx_id)

    async def _create_inventory_movements(
        self, conn, tenant_id, user_uuid, doc_uuid, journal_id, movements
    ):
        """Create inventory ledger entries (legacy path)."""
        ledger_ids = []
        for mv in movements:
            product_id = mv.get("product_id")
            if not product_id:
                product_id = await self._find_or_create_product(conn, tenant_id, mv)
            if not product_id:
                logger.warning(f"[KDE] Skipping inventory: no product_id for {mv}")
                continue

            product_uuid = uuid.UUID(product_id)
            prod = await conn.fetchrow(
                "SELECT kode_produk, nama_produk FROM products WHERE id = $1 AND tenant_id = $2",
                product_uuid, tenant_id,
            )
            product_code = prod["kode_produk"] if prod else ""
            product_name = prod["nama_produk"] if prod else mv.get("product_name", "")

            quantity = Decimal(str(mv.get("quantity", "0")))
            direction = mv.get("direction", "IN").upper()
            unit_cost = Decimal(str(mv.get("unit_price", "0")))

            quantity_in = quantity if direction == "IN" else Decimal("0")
            quantity_out = quantity if direction == "OUT" else Decimal("0")

            balance_row = await conn.fetchrow(
                """SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) AS balance
                   FROM inventory_ledger
                   WHERE tenant_id = $1 AND product_id = $2""",
                tenant_id, product_uuid,
            )
            current_balance = Decimal(str(balance_row["balance"]))
            new_balance = current_balance + quantity_in - quantity_out

            movement_type = "PURCHASE" if direction == "IN" else "SALE"
            movement_date = self._parse_date({"date": mv.get("date")})

            row = await conn.fetchrow(
                """INSERT INTO inventory_ledger (
                    tenant_id, product_id, product_code, product_name,
                    movement_type, movement_date, source_type, source_id,
                    quantity_in, quantity_out, quantity_balance,
                    unit_cost, total_cost, average_cost,
                    warehouse_id, journal_id, created_by, notes
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $12,
                    $14, $15, $16, $17
                ) RETURNING id""",
                tenant_id, product_uuid, product_code, product_name,
                movement_type, movement_date, SOURCE_TYPE, doc_uuid,
                quantity_in, quantity_out, new_balance,
                unit_cost, unit_cost * quantity,
                uuid.UUID(mv["warehouse_id"]) if mv.get("warehouse_id") else None,
                journal_id, user_uuid,
                mv.get("notes", f"Document Intake: {product_name}"),
            )
            ledger_ids.append(str(row["id"]))
        return ledger_ids


    async def _find_or_create_vendor(self, conn, tenant_id: str, vendor_name: str) -> str:
        """Find vendor by name, create if not found. Returns vendor_id or None."""
        if not vendor_name or not vendor_name.strip():
            return None

        vendor_name = vendor_name.strip()
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)

        vendor = await conn.fetchrow(
            "SELECT id FROM vendors WHERE tenant_id = $1 AND LOWER(name) = LOWER($2) AND is_active = true",
            tenant_id, vendor_name,
        )
        if vendor:
            return str(vendor["id"])

        # Auto-create minimal vendor
        try:
            vendor_id = uuid.uuid4()
            await conn.execute(
                """INSERT INTO vendors (id, tenant_id, name, is_active, created_at)
                   VALUES ($1, $2, $3, true, NOW())""",
                vendor_id, tenant_id, vendor_name,
            )
            logger.info(f"[KDE] Auto-created vendor: {vendor_name} -> {vendor_id}")
            return str(vendor_id)
        except Exception as e:
            logger.warning(f"[KDE] Failed to auto-create vendor {vendor_name}: {e}")
            return None

    async def _find_or_create_product(self, conn, tenant_id, mv):
        """Find or create product from movement data (legacy path)."""
        product_name = mv.get("product_name", "").strip()
        if not product_name:
            return None

        existing = await conn.fetchrow(
            """SELECT id FROM products
               WHERE tenant_id = $1 AND nama_produk = $2 AND is_active = true
               LIMIT 1""",
            tenant_id, product_name,
        )
        if existing:
            return str(existing["id"])

        product_id = uuid.uuid4()
        unit = mv.get("unit", "pcs")
        buy_price = Decimal(str(mv.get("unit_price", "0")))
        sell_price = Decimal(str(mv.get("sell_price", "0")))

        await conn.execute(
            """INSERT INTO products (
                id, tenant_id, nama_produk, satuan, base_unit,
                item_type, track_inventory, status, is_active,
                sales_price, purchase_price, harga_jual, harga_beli,
                created_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                'PRODUCT', $6, 'ACTIVE', true,
                $7, $8, $9, $10, NOW()
            )""",
            product_id, tenant_id, product_name, unit, unit,
            mv.get("track_inventory", True),
            sell_price, buy_price, sell_price, buy_price,
        )
        logger.info(f"[KDE] Product created: {product_id} ({product_name})")
        return str(product_id)

    # ═══════════════════════════════════════════════════════════════════
    # BATCH EXECUTOR
    # ═══════════════════════════════════════════════════════════════════

    async def execute_batch(
        self,
        document_ids: List[str],
        tenant_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Execute multiple documents sequentially (deadlock prevention)."""
        results = []
        succeeded = 0
        failed = 0

        for doc_id in document_ids:
            try:
                result = await self.execute(doc_id, tenant_id, user_id)
                results.append(result.to_dict())
                if result.success:
                    succeeded += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"[KDE] Batch error for {doc_id}: {e}")
                results.append({
                    "success": False,
                    "document_id": doc_id,
                    "error": str(e),
                })
                failed += 1

        return {
            "total": len(document_ids),
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }
