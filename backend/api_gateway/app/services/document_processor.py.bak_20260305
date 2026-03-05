"""
Document Processor
==================
Background worker for OCR extraction, classification, analysis, and draft generation.

Pipeline per document:
  queued → extracting → classifying → classified → analyzing → analyzed → draft_ready

Phase 3: OCR + Classification (queued → classified)
Phase 4: Financial Intelligence (classified → analyzed)
Phase 5: Draft Plan Generation (analyzed → draft_ready)

ZERO accounting writes. ZERO journal entries. READ + ANALYZE + PLAN only.
"""
import io
import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from .ocr_providers import get_ocr_provider, OCRProviderResult
from .document_classifier import classify_document
from .financial_intelligence import FinancialIntelligence
from .draft_plan_generator import DraftPlanGenerator, validate_draft_balance

logger = logging.getLogger(__name__)

# Structured file MIME types — parsed without LLM (Tier 1)
STRUCTURED_MIME_TYPES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}

# Confidence threshold for auto-escalation from Tier 2 → Tier 3
ESCALATION_THRESHOLD = Decimal("0.6")

# OCR extraction prompt (sent to provider)
OCR_EXTRACTION_PROMPT = """You are an OCR extraction engine for Indonesian financial documents. Extract ALL data from this document to JSON format.

RULES:
1. Extract ALL numbers as strings (not numbers). Example: "150000" not 150000
2. Dates in ISO format: "2026-02-25"
3. If you cannot read a field, set null
4. Do NOT assume or infer data that is not visible
5. For line_items, extract every visible line item

Output JSON (strictly this schema, no extra fields):
{
  "doc_type_hint": "invoice" | "receipt" | "bank_transfer" | "bank_statement" | "credit_note" | "debit_note" | "tax_document" | "unknown",
  "confidence": "0.95",
  "document_number": "INV-001" or null,
  "document_date": "2026-02-25" or null,
  "due_date": "2026-03-25" or null,
  "counterparty_name": "PT Maju Jaya" or null,
  "counterparty_tax_id": "01.234.567.8-901.000" or null,
  "currency": "IDR",
  "subtotal": "1000000" or null,
  "tax_amount": "110000" or null,
  "total_amount": "1110000" or null,
  "line_items": [
    {
      "description": "Kabel NYM 2x1.5",
      "quantity": "10",
      "unit": "roll",
      "unit_price": "185000",
      "total_price": "1850000",
      "item_code": null
    }
  ],
  "bank_name": "BCA" or null,
  "bank_account_number": "1234567890" or null,
  "reference_number": "REF-001" or null
}

Respond with ONLY the JSON. No explanation, no markdown."""


class DocumentProcessor:
    """
    Background worker for OCR extraction, classification, analysis, and draft generation.

    Processing pipeline per document:
    Phase 3: queued → extracting → classifying → classified
    Phase 4: classified → analyzing → analyzed
    Phase 5: analyzed → draft_ready

    Error handling:
    - Retry up to max_retries (default 3)
    - Status → extraction_failed / classification_failed / analysis_failed / draft_failed
    - Batch counters updated on every final status
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._ocr_provider = None

    @property
    def ocr_provider(self):
        if self._ocr_provider is None:
            self._ocr_provider = get_ocr_provider()
        return self._ocr_provider

    # ------------------------------------------------------------------
    # MAIN ENTRY POINTS
    # ------------------------------------------------------------------

    async def process_next_batch(
        self,
        tenant_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Pick up queued, classified, and analyzed documents and process them.
        - queued → full pipeline (OCR + classify + analyze + draft)
        - classified → analysis + draft
        - analyzed → draft generation only
        Returns summary: { processed, failed, remaining, details }
        """
        async with self.pool.acquire() as conn:
            # Build query — pick up queued, classified, and analyzed
            conditions = ["status IN ('queued', 'classified', 'analyzed')"]
            params: list = []
            idx = 1

            if tenant_id:
                conditions.append(f"tenant_id = ${idx}")
                params.append(tenant_id)
                idx += 1

            if batch_id:
                conditions.append(f"batch_id = ${idx}::uuid")
                params.append(batch_id)
                idx += 1

            params.append(limit)
            where = " AND ".join(conditions)

            docs = await conn.fetch(
                f"""
                SELECT id, tenant_id, batch_id, file_path, mime_type,
                       file_hash, original_filename, retry_count, max_retries,
                       status, ocr_result, doc_type, analysis_result
                FROM uploaded_documents
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT ${idx}
                """,
                *params,
            )

        processed = 0
        failed = 0
        details = []

        for doc in docs:
            try:
                doc_dict = dict(doc)
                # Parse JSONB if returned as string
                if isinstance(doc_dict.get("ocr_result"), str):
                    try:
                        doc_dict["ocr_result"] = json.loads(doc_dict["ocr_result"])
                    except (json.JSONDecodeError, TypeError):
                        doc_dict["ocr_result"] = None
                if isinstance(doc_dict.get("analysis_result"), str):
                    try:
                        doc_dict["analysis_result"] = json.loads(doc_dict["analysis_result"])
                    except (json.JSONDecodeError, TypeError):
                        doc_dict["analysis_result"] = None

                current_status = doc_dict.get("status", "queued")

                if current_status == "queued":
                    result = await self.process_single_document(doc_dict)
                elif current_status == "classified":
                    result = await self.analyze_single_document(doc_dict)
                elif current_status == "analyzed":
                    result = await self.draft_single_document(doc_dict)
                else:
                    continue

                if result.get("status") in ("draft_ready", "analyzed", "classified"):
                    processed += 1
                else:
                    failed += 1
                details.append(result)
            except Exception as e:
                failed += 1
                details.append({
                    "document_id": str(doc["id"]),
                    "status": "error",
                    "error": str(e),
                })
                logger.exception(f"[Processor] Unexpected error for doc {doc['id']}: {e}")

        # Count remaining
        async with self.pool.acquire() as conn:
            remaining_conditions = ["status IN ('queued', 'classified', 'analyzed')"]
            remaining_params: list = []
            r_idx = 1
            if tenant_id:
                remaining_conditions.append(f"tenant_id = ${r_idx}")
                remaining_params.append(tenant_id)
                r_idx += 1
            remaining = await conn.fetchval(
                f"SELECT COUNT(*) FROM uploaded_documents WHERE {' AND '.join(remaining_conditions)}",
                *remaining_params,
            )

        return {
            "processed": processed,
            "failed": failed,
            "remaining": remaining or 0,
            "details": details,
        }

    async def process_single_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Full pipeline for one document (from queued):
        1. OCR extraction (Phase 3)
        2. Classification (Phase 3)
        3. Financial analysis (Phase 4)
        """
        doc_id = str(doc["id"])
        tenant_id = doc["tenant_id"]
        file_path = doc["file_path"]
        mime_type = doc.get("mime_type") or "application/octet-stream"
        retry_count = doc.get("retry_count", 0)
        max_retries = doc.get("max_retries", 3)
        batch_id = doc.get("batch_id")

        logger.info(f"[Processor] Starting: {doc_id} ({doc.get('original_filename')})")

        # === Phase 3: OCR Extraction ===
        await self._update_status(doc_id, tenant_id, "extracting")

        tier = self._determine_tier(mime_type)

        try:
            if tier == 1:
                ocr_dict = await self._parse_structured(file_path, mime_type)
                model_used = "parser"
                ocr_confidence = Decimal(str(ocr_dict.get("confidence", "0.8")))
            else:
                provider_result = await self.ocr_provider.extract(
                    file_path=file_path,
                    mime_type=mime_type,
                    tier=tier,
                    prompt=OCR_EXTRACTION_PROMPT,
                )
                ocr_dict = provider_result.raw_json
                model_used = provider_result.model_used
                ocr_confidence = provider_result.confidence

                # Auto-escalate if low confidence
                if tier == 2 and ocr_confidence < ESCALATION_THRESHOLD:
                    logger.info(
                        f"[Processor] Escalating {doc_id} from Tier 2 to Tier 3 "
                        f"(confidence={ocr_confidence})"
                    )
                    provider_result = await self.ocr_provider.extract(
                        file_path=file_path,
                        mime_type=mime_type,
                        tier=3,
                        prompt=OCR_EXTRACTION_PROMPT,
                    )
                    ocr_dict = provider_result.raw_json
                    model_used = provider_result.model_used
                    ocr_confidence = provider_result.confidence

        except Exception as e:
            logger.error(f"[Processor] OCR failed for {doc_id}: {e}")
            if retry_count + 1 < max_retries:
                await self._update_status(
                    doc_id, tenant_id, "queued",
                    extra={"retry_count": retry_count + 1},
                    detail=f"OCR failed (attempt {retry_count + 1}): {str(e)[:200]}",
                )
                return {"document_id": doc_id, "status": "retry", "error": str(e)[:200]}
            else:
                await self._update_status(
                    doc_id, tenant_id, "extraction_failed",
                    detail=f"OCR failed after {max_retries} attempts: {str(e)[:200]}",
                )
                await self._update_batch_failed(batch_id, tenant_id)
                return {"document_id": doc_id, "status": "extraction_failed", "error": str(e)[:200]}

        # Sanitize for JSONB (Law 25)
        sanitized_ocr = self._sanitize_for_json(ocr_dict)

        await self._update_status(
            doc_id, tenant_id, "classifying",
            extra={
                "ocr_result": json.dumps(sanitized_ocr),
                "ocr_model_used": model_used,
                "ocr_confidence": ocr_confidence,
            },
        )

        # === Phase 3: Classification ===
        try:
            doc_type, classification_confidence = classify_document(sanitized_ocr)
        except Exception as e:
            logger.error(f"[Processor] Classification failed for {doc_id}: {e}")
            await self._update_status(
                doc_id, tenant_id, "classification_failed",
                detail=f"Classification error: {str(e)[:200]}",
            )
            await self._update_batch_failed(batch_id, tenant_id)
            return {"document_id": doc_id, "status": "classification_failed", "error": str(e)[:200]}

        await self._update_status(
            doc_id, tenant_id, "classified",
            extra={
                "doc_type": doc_type,
                "classification_confidence": classification_confidence,
            },
        )

        logger.info(
            f"[Processor] Classified: {doc_id} -> {doc_type} "
            f"(ocr_conf={ocr_confidence}, class_conf={classification_confidence})"
        )

        # === Phase 4: Financial Analysis ===
        analysis_result = await self._run_analysis(
            doc_id, tenant_id, sanitized_ocr, doc_type, batch_id
        )

        # === Phase 5: Draft Plan Generation ===
        draft_plan = None
        if analysis_result:
            draft_plan = await self._run_draft_generation(
                doc_id, tenant_id, sanitized_ocr, doc_type, analysis_result, batch_id
            )

        final_status = "draft_ready" if draft_plan else (
            "analyzed" if analysis_result else "classified"
        )

        return {
            "document_id": doc_id,
            "status": final_status,
            "doc_type": doc_type,
            "ocr_confidence": str(ocr_confidence),
            "classification_confidence": str(classification_confidence),
            "model_used": model_used,
            "has_analysis": analysis_result is not None,
            "has_draft": draft_plan is not None,
        }

    async def analyze_single_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analysis + draft pipeline for already-classified documents.
        classified → analyzing → analyzed → draft_ready
        """
        doc_id = str(doc["id"])
        tenant_id = doc["tenant_id"]
        batch_id = doc.get("batch_id")
        ocr_result = doc.get("ocr_result") or {}
        doc_type = doc.get("doc_type") or "unknown"

        logger.info(f"[Processor] Analyzing: {doc_id} (type={doc_type})")

        analysis_result = await self._run_analysis(
            doc_id, tenant_id, ocr_result, doc_type, batch_id
        )

        # Chain into draft generation
        draft_plan = None
        if analysis_result:
            draft_plan = await self._run_draft_generation(
                doc_id, tenant_id, ocr_result, doc_type, analysis_result, batch_id
            )

        final_status = "draft_ready" if draft_plan else (
            "analyzed" if analysis_result else "analysis_failed"
        )

        return {
            "document_id": doc_id,
            "status": final_status,
            "doc_type": doc_type,
            "has_analysis": analysis_result is not None,
            "has_draft": draft_plan is not None,
        }

    async def draft_single_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Draft-only pipeline for already-analyzed documents.
        analyzed → draft_generating → draft_ready
        """
        doc_id = str(doc["id"])
        tenant_id = doc["tenant_id"]
        batch_id = doc.get("batch_id")
        ocr_result = doc.get("ocr_result") or {}
        doc_type = doc.get("doc_type") or "unknown"

        # Parse analysis_result if string
        analysis_result = doc.get("analysis_result")
        if isinstance(analysis_result, str):
            try:
                analysis_result = json.loads(analysis_result)
            except (json.JSONDecodeError, TypeError):
                analysis_result = {}
        analysis_result = analysis_result or {}

        logger.info(f"[Processor] Drafting: {doc_id} (type={doc_type})")

        draft_plan = await self._run_draft_generation(
            doc_id, tenant_id, ocr_result, doc_type, analysis_result, batch_id
        )

        return {
            "document_id": doc_id,
            "status": "draft_ready" if draft_plan else "draft_failed",
            "doc_type": doc_type,
            "has_draft": draft_plan is not None,
        }

    async def _run_analysis(
        self,
        doc_id: str,
        tenant_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        batch_id: Optional[Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Run financial intelligence analysis.
        Returns analysis_result dict or None on failure.
        """
        await self._update_status(doc_id, tenant_id, "analyzing")

        try:
            async with self.pool.acquire() as conn:
                intelligence = FinancialIntelligence(conn)
                analysis = await intelligence.analyze_document(
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    ocr_result=ocr_result,
                    doc_type=doc_type,
                )

            # Sanitize analysis for JSONB
            sanitized = self._sanitize_for_json(analysis)

            await self._update_status(
                doc_id, tenant_id, "analyzed",
                extra={
                    "analysis_result": json.dumps(sanitized, default=str),
                },
            )
            await self._update_batch_processed(batch_id, tenant_id)

            logger.info(f"[Processor] Analyzed: {doc_id}")
            return sanitized

        except Exception as e:
            logger.error(f"[Processor] Analysis failed for {doc_id}: {e}")
            await self._update_status(
                doc_id, tenant_id, "analysis_failed",
                detail=f"Analysis error: {str(e)[:200]}",
            )
            await self._update_batch_failed(batch_id, tenant_id)
            return None

    async def _run_draft_generation(
        self,
        doc_id: str,
        tenant_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
        batch_id: Optional[Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Run draft plan generation (Phase 5).
        Returns draft_plan dict or None on failure.
        """
        await self._update_status(doc_id, tenant_id, "draft_generating")

        try:
            async with self.pool.acquire() as conn:
                generator = DraftPlanGenerator(conn)
                draft = await generator.generate_plan(
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    ocr_result=ocr_result,
                    doc_type=doc_type,
                    analysis_result=analysis_result,
                )

            # Validate balance (Law 4)
            is_balanced, balance_error = validate_draft_balance(draft)
            if not is_balanced:
                logger.error(
                    f"[Processor] Draft imbalanced for {doc_id}: {balance_error}"
                )
                draft["warnings"] = draft.get("warnings", [])
                draft["warnings"].append(f"BALANCE_ERROR: {balance_error}")

            # Sanitize for JSONB
            sanitized = self._sanitize_for_json(draft)

            await self._update_status(
                doc_id, tenant_id, "draft_ready",
                extra={
                    "draft_plan": json.dumps(sanitized, default=str),
                },
            )
            await self._update_batch_processed(batch_id, tenant_id)

            logger.info(f"[Processor] Draft ready: {doc_id}")
            return sanitized

        except Exception as e:
            logger.error(f"[Processor] Draft generation failed for {doc_id}: {e}")
            await self._update_status(
                doc_id, tenant_id, "draft_failed",
                detail=f"Draft generation error: {str(e)[:200]}",
            )
            await self._update_batch_failed(batch_id, tenant_id)
            return None

    # ------------------------------------------------------------------
    # TIER DETERMINATION
    # ------------------------------------------------------------------

    def _determine_tier(self, mime_type: str) -> int:
        """
        Tier 1: Structured (CSV/XLSX) — parser only
        Tier 2: Standard images/PDFs — cheap LLM
        Tier 3: Only via auto-escalation (not initial)
        """
        if mime_type in STRUCTURED_MIME_TYPES:
            return 1
        return 2

    # ------------------------------------------------------------------
    # STRUCTURED FILE PARSER (Tier 1)
    # ------------------------------------------------------------------

    async def _parse_structured(self, file_path: str, mime_type: str) -> Dict[str, Any]:
        """Parse CSV/XLSX without LLM. Detect document type via heuristics."""
        try:
            import pandas as pd

            if mime_type == "text/csv":
                df = pd.read_csv(file_path, nrows=100)
            else:
                df = pd.read_excel(file_path, nrows=100)

            columns_lower = {c.lower() for c in df.columns}

            # Detect bank statement
            bank_signals = {"debit", "credit", "mutasi", "saldo", "balance"}
            if bank_signals & columns_lower:
                return {
                    "doc_type_hint": "bank_statement",
                    "confidence": "0.85",
                    "document_number": None,
                    "document_date": None,
                    "due_date": None,
                    "counterparty_name": None,
                    "counterparty_tax_id": None,
                    "currency": "IDR",
                    "subtotal": None,
                    "tax_amount": None,
                    "total_amount": None,
                    "line_items": [],
                    "bank_name": None,
                    "bank_account_number": None,
                    "reference_number": None,
                    "raw_text": ", ".join(df.columns.tolist()),
                }

            # Detect invoice/purchase order
            invoice_signals = {"qty", "quantity", "jumlah", "harga", "price", "amount"}
            if invoice_signals & columns_lower:
                items = []
                for _, row in df.head(20).iterrows():
                    item = {"description": str(row.iloc[0]) if len(row) > 0 else ""}
                    for col in df.columns:
                        cl = col.lower()
                        val = row[col]
                        if cl in ("qty", "quantity", "jumlah"):
                            item["quantity"] = str(val) if pd.notna(val) else None
                        elif cl in ("harga", "price", "unit_price", "harga_satuan"):
                            item["unit_price"] = str(val) if pd.notna(val) else None
                        elif cl in ("total", "amount", "subtotal"):
                            item["total_price"] = str(val) if pd.notna(val) else None
                    items.append(item)

                return {
                    "doc_type_hint": "invoice",
                    "confidence": "0.75",
                    "document_number": None,
                    "document_date": None,
                    "due_date": None,
                    "counterparty_name": None,
                    "counterparty_tax_id": None,
                    "currency": "IDR",
                    "subtotal": None,
                    "tax_amount": None,
                    "total_amount": None,
                    "line_items": items,
                    "bank_name": None,
                    "bank_account_number": None,
                    "reference_number": None,
                    "raw_text": ", ".join(df.columns.tolist()),
                }

            # Unknown structured file
            return {
                "doc_type_hint": "unknown",
                "confidence": "0.30",
                "document_number": None,
                "document_date": None,
                "due_date": None,
                "counterparty_name": None,
                "counterparty_tax_id": None,
                "currency": "IDR",
                "subtotal": None,
                "tax_amount": None,
                "total_amount": None,
                "line_items": [],
                "bank_name": None,
                "bank_account_number": None,
                "reference_number": None,
                "raw_text": ", ".join(df.columns.tolist()),
            }

        except Exception as e:
            logger.error(f"[Processor] Structured parse failed: {e}")
            raise ValueError(f"Cannot parse structured file: {e}")

    # ------------------------------------------------------------------
    # DB HELPERS
    # ------------------------------------------------------------------

    async def _update_status(
        self,
        doc_id: str,
        tenant_id: str,
        new_status: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
        detail: Optional[str] = None,
    ):
        """Update document status + optional extra fields."""
        sets = ["status = $3"]
        params: list = [doc_id, tenant_id, new_status]
        idx = 4

        if detail:
            sets.append(f"status_detail = ${idx}")
            params.append(detail)
            idx += 1

        if extra:
            for key, value in extra.items():
                if key in ("ocr_result", "analysis_result", "draft_plan"):
                    sets.append(f"{key} = ${idx}::jsonb")
                    params.append(value)
                elif key in ("ocr_confidence", "classification_confidence"):
                    sets.append(f"{key} = ${idx}")
                    params.append(value)
                elif key == "retry_count":
                    sets.append(f"retry_count = ${idx}")
                    params.append(value)
                else:
                    sets.append(f"{key} = ${idx}")
                    params.append(value)
                idx += 1

        set_clause = ", ".join(sets)
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE uploaded_documents
                SET {set_clause}, updated_at = NOW()
                WHERE id = $1::uuid AND tenant_id = $2
                """,
                *params,
            )

    async def _update_batch_processed(
        self, batch_id: Optional[Any], tenant_id: str
    ):
        """Increment batch processed_count."""
        if not batch_id:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE document_batches
                SET processed_count = processed_count + 1
                WHERE id = $1 AND tenant_id = $2
                """,
                batch_id, tenant_id,
            )

    async def _update_batch_failed(
        self, batch_id: Optional[Any], tenant_id: str
    ):
        """Increment batch failed_count."""
        if not batch_id:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE document_batches
                SET failed_count = failed_count + 1
                WHERE id = $1 AND tenant_id = $2
                """,
                batch_id, tenant_id,
            )

    # ------------------------------------------------------------------
    # SANITIZATION
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_for_json(data: Any) -> Any:
        """
        Recursively convert Decimal and other non-JSON-safe types to strings.
        Law 25: All amounts stored as string representation.
        """
        if isinstance(data, dict):
            return {k: DocumentProcessor._sanitize_for_json(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [DocumentProcessor._sanitize_for_json(v) for v in data]
        elif isinstance(data, Decimal):
            return str(data)
        elif isinstance(data, (int, float)):
            return data
        return data
