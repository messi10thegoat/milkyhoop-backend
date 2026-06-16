"""Document Intake V3 — DocumentIntakePipelineV3.

Orchestrates: classifier → handler dispatch → ResolvedAction.
Three-layer error handling:
  1. Handler match failure → clarification UX
  2. Handler resolve failure → V2 generic preview fallback
  3. Pipeline crash → re-raise _FallbackToV2PreviewSkip

Phase 1 registers 3 handlers (RECEIVE_PAYMENT, BILL_PAYMENT, EXPENSE_OPERATIONAL, BANK_FEE).
Unregistered TransferTypes raise _FallbackToV2PreviewSkip so V2 inline preview takes over.
"""

from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import re as _re
import time as _time
from dataclasses import dataclass, field
from typing import Optional

from ..document_action_resolver import ResolvedAction
from .classifier import TransferClassifier
from .handlers.base import (
    ForcedOverride,
    HandlerMatchError,
    HandlerMatchResult,
    HandlerResolveError,
)
from .registry import build_handler_registry
from .signals import ClassificationResult
from .transfer_types import TYPE_DISPLAY_NAMES, TransferType

logger = logging.getLogger(__name__)


# --- Skip exceptions (define here; Task 7 will import from this module) ---


class _FallbackToGenericChatSkip(Exception):
    """Signal to unified_chat: classifier returned UNKNOWN, treat as normal chat."""


class _FallbackToV2PreviewSkip(Exception):
    """Signal to unified_chat: V3 cannot produce action; fall through to V2 inline preview."""


# --- V2-compat mapping dicts (Task 6b) ---

_TYPE_TO_CATEGORY: dict[TransferType, str] = {
    TransferType.RECEIVE_PAYMENT: "payment",
    TransferType.BILL_PAYMENT: "payment",
    TransferType.CUSTOMER_DEPOSIT: "payment",
    TransferType.VENDOR_DEPOSIT: "payment",
    TransferType.CUSTOMER_REFUND: "payment",
    TransferType.VENDOR_REFUND: "payment",
    TransferType.EXPENSE_OPERATIONAL: "expense",
    TransferType.BANK_FEE: "expense",
    TransferType.PAYROLL: "expense",
    TransferType.TAX_PAYMENT: "tax",
    TransferType.BPJS_PAYMENT: "tax",
    TransferType.LOAN_PAYMENT: "payment",
    TransferType.OWNER_DRAWING: "payment",
    TransferType.OWNER_CAPITAL: "payment",
    TransferType.INTERNAL_TRANSFER: "payment",
}

_TYPE_TO_DIRECTION: dict[TransferType, str] = {
    TransferType.RECEIVE_PAYMENT: "in",
    TransferType.CUSTOMER_DEPOSIT: "in",
    TransferType.VENDOR_REFUND: "in",
    TransferType.OWNER_CAPITAL: "in",
    TransferType.BILL_PAYMENT: "out",
    TransferType.VENDOR_DEPOSIT: "out",
    TransferType.CUSTOMER_REFUND: "out",
    TransferType.EXPENSE_OPERATIONAL: "out",
    TransferType.BANK_FEE: "out",
    TransferType.PAYROLL: "out",
    TransferType.TAX_PAYMENT: "out",
    TransferType.BPJS_PAYMENT: "out",
    TransferType.LOAN_PAYMENT: "out",
    TransferType.OWNER_DRAWING: "out",
    TransferType.INTERNAL_TRANSFER: "ambiguous",
}


# --- Result container ---


@dataclass
class IntakeResult:
    success: bool
    classification: Optional[ClassificationResult] = None
    handler_match: Optional[HandlerMatchResult] = None
    resolved_action: Optional[ResolvedAction] = None
    needs_clarification: bool = False
    clarification_question: str = ""
    clarification_options: list[dict] = field(default_factory=list)
    log_trail: list[str] = field(default_factory=list)

    # --- V2-compat shim (Task 6b) ---
    # These @property accessors let V2-shape downstream code (unified_chat.py ~L2410-2440)
    # consume V3 IntakeResult without changes. Remove after V2 deprecation.

    @property
    def doc_category(self) -> str:
        """V2-compat: map TransferType → V2 doc_category string."""
        if self.classification is None:
            return "unknown"
        return _TYPE_TO_CATEGORY.get(self.classification.type, "unknown")

    @property
    def direction(self) -> str:
        """V2-compat: V3 TransferType → V2 direction. AMBIGUOUS/UNKNOWN → ambiguous."""
        if self.classification is None:
            return "ambiguous"
        t = self.classification.type
        if t in (TransferType.AMBIGUOUS, TransferType.UNKNOWN):
            return "ambiguous"
        return _TYPE_TO_DIRECTION.get(t, "ambiguous")

    @property
    def direction_source(self) -> str:
        """V2-compat: best-effort mapping from classification reasons.

        Phase 1 shim — may not preserve V2 semantics for edge cases. Remove post-V2.
        """
        if self.classification is None:
            return "none"
        reasons = self.classification.reasons or []
        if "forced_type" in reasons or "legacy_direction_override" in reasons:
            return "forced"
        for sig in self.classification.signals_fired or []:
            name = sig.get("name", "")
            if name.startswith("amount_matches_open_invoice_AR"):
                return "ar_match"
            if name.startswith("amount_matches_open_bill_AP"):
                return "ap_match"
            if name.startswith("caption:"):
                return "caption"
            if name.startswith("doc_type=bank_transfer"):
                return "ocr_transfer"
        return "none"

    @property
    def best_match(self):
        """V2-compat: handler_match.best (MatchCandidate or AccountRecommendation).

        Returns None if no handler match available.
        """
        if self.handler_match is None:
            return None
        return self.handler_match.best

    @property
    def ar_matches(self) -> list:
        """V2-compat: candidates list if RECEIVE_PAYMENT/CUSTOMER_DEPOSIT, else []."""
        if self.handler_match is None or self.classification is None:
            return []
        if self.classification.type in (
            TransferType.RECEIVE_PAYMENT,
            TransferType.CUSTOMER_DEPOSIT,
        ):
            return list(self.handler_match.candidates or [])
        return []

    @property
    def ap_matches(self) -> list:
        """V2-compat: candidates list if BILL_PAYMENT/VENDOR_DEPOSIT, else []."""
        if self.handler_match is None or self.classification is None:
            return []
        if self.classification.type in (
            TransferType.BILL_PAYMENT,
            TransferType.VENDOR_DEPOSIT,
        ):
            return list(self.handler_match.candidates or [])
        return []

    @property
    def bank_id(self) -> Optional[str]:
        """V2-compat: extract bank id from resolved_action.payload."""
        if self.resolved_action is None:
            return None
        payload = self.resolved_action.payload or {}
        val = payload.get("bank_account_id") or payload.get("paid_through_id")
        return val or None

    @property
    def bank_display_name(self) -> Optional[str]:
        """V2-compat: bank display name from resolved_action.payload."""
        if self.resolved_action is None:
            return None
        payload = self.resolved_action.payload or {}
        val = payload.get("bank_account_name") or payload.get("paid_through_name")
        if val in (None, "", "(pilih rekening)"):
            return None
        return val

    @property
    def bank_candidates(self) -> list:
        """V2-compat: bank clarification options (dict with id+label)."""
        if self.resolved_action and self.resolved_action.clarification_options:
            return [
                {"id": opt.id, "label": opt.label}
                for opt in self.resolved_action.clarification_options
            ]
        return [
            {"id": o.get("id", ""), "label": o.get("label", "")}
            for o in self.clarification_options
            if isinstance(o, dict)
        ]

    @property
    def needs_direction_clarification(self) -> bool:
        """V2-compat: True if AMBIGUOUS clarification pending AND question about direction."""
        if not self.needs_clarification:
            return False
        q = (self.clarification_question or "").lower()
        if "masuk atau keluar" in q:
            return True
        if self.classification and self.classification.type == TransferType.AMBIGUOUS:
            return True
        return False

    @property
    def needs_bank_clarification(self) -> bool:
        """V2-compat: True if resolved_action exists and needs_clarification for bank."""
        if self.resolved_action is None:
            return False
        return bool(self.resolved_action.needs_clarification)


# --- Pipeline ---


class DocumentIntakePipelineV3:
    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id
        self.classifier = TransferClassifier(pool, tenant_id)
        self.registry = build_handler_registry(pool, tenant_id)

    async def process(
        self,
        ocr_data: dict,
        caption: str = "",
        forced: Optional[ForcedOverride] = None,
    ) -> IntakeResult:
        _t_start = _time.perf_counter()
        _t_after_classify: Optional[float] = None
        _t_after_handler: Optional[float] = None
        result: Optional[IntakeResult] = None
        error_stage: Optional[str] = None
        error_message: Optional[str] = None
        try:
            try:
                classification = await self._classify_or_force(
                    ocr_data, caption, forced
                )
                _t_after_classify = _time.perf_counter()
            except Exception as e:
                error_stage = "classify"
                error_message = str(e)
                raise

            # UNKNOWN → fall through to generic chat
            if classification.type == TransferType.UNKNOWN:
                result = IntakeResult(
                    success=False,
                    classification=classification,
                    log_trail=["unknown_fallback_generic"],
                )
                raise _FallbackToGenericChatSkip()

            # AMBIGUOUS → clarification UX
            if classification.type == TransferType.AMBIGUOUS:
                result = self._build_clarification_result(classification)
                return result

            # Dispatch to handler
            handler = self.registry.get(classification.type)
            if handler is None:
                # Phase 1 does not register this type; fall through to V2 preview
                logger.info(
                    "[IntakeV3] type %s not registered in Phase 1 — falling back to V2",
                    classification.type.value,
                )
                result = IntakeResult(
                    success=False,
                    classification=classification,
                    log_trail=[f"unregistered_type:{classification.type.value}"],
                )
                raise _FallbackToV2PreviewSkip()

            tenant_ctx = None  # reserved for future context object
            try:
                match = await handler.match(
                    ocr_data, caption, tenant_ctx, forced=forced
                )
                _t_after_handler = _time.perf_counter()
            except HandlerMatchError as e:
                error_stage = "match"
                error_message = str(e)
                result = IntakeResult(
                    success=False,
                    classification=classification,
                    log_trail=[f"handler_match_error:{classification.type.value}"],
                )
                logger.warning("[IntakeV3] HandlerMatchError: %s", e)
                raise _FallbackToV2PreviewSkip() from e

            if not match.success:
                if match.needs_clarification:
                    result = IntakeResult(
                        success=False,
                        classification=classification,
                        handler_match=match,
                        needs_clarification=True,
                        clarification_question=match.clarification_question,
                        clarification_options=match.clarification_options,
                        log_trail=[
                            f"handler_needs_clarification:{classification.type.value}"
                        ],
                    )
                    return result
                # Hard fail. FIX_DIR_NOFLIP: for a CONFIDENTLY-classified payment
                # direction, do NOT fall to the V2 preview — V2 re-classifies purely
                # by amount and can FLIP the side (a customer payment whose amount
                # coincidentally equals an open bill became a vendor payment). The
                # user's direction is authoritative; capture gracefully instead so
                # the framing stays correct (no opposite-side posting).
                result = IntakeResult(
                    success=False,
                    classification=classification,
                    handler_match=match,
                    log_trail=[f"handler_match_fail:{classification.type.value}"],
                )
                if classification.type in (
                    TransferType.RECEIVE_PAYMENT,
                    TransferType.BILL_PAYMENT,
                ):
                    logger.info(
                        "[IntakeV3] handler match failed for %s (%s) — graceful "
                        "capture (no V2 flip)",
                        classification.type.value,
                        ",".join(match.reasons),
                    )
                    raise _FallbackToGenericChatSkip()
                logger.info(
                    "[IntakeV3] handler match failed for %s (%s) — fallback to V2",
                    classification.type.value,
                    ",".join(match.reasons),
                )
                raise _FallbackToV2PreviewSkip()

            try:
                action = await handler.resolve(match, ocr_data, tenant_ctx)
            except HandlerResolveError as e:
                error_stage = "resolve"
                error_message = str(e)
                result = IntakeResult(
                    success=False,
                    classification=classification,
                    handler_match=match,
                    log_trail=[f"handler_resolve_error:{classification.type.value}"],
                )
                logger.warning("[IntakeV3] HandlerResolveError: %s", e)
                raise _FallbackToV2PreviewSkip() from e

            result = IntakeResult(
                success=True,
                classification=classification,
                handler_match=match,
                resolved_action=action,
                needs_clarification=action.needs_clarification,
                clarification_question=action.clarification_question,
                clarification_options=[
                    {"id": opt.id, "label": opt.label, "value": opt.value}
                    for opt in action.clarification_options
                ],
                log_trail=[f"resolved:{action.action_key}"],
            )
            return result

        except (_FallbackToGenericChatSkip, _FallbackToV2PreviewSkip):
            # Propagate — handled by unified_chat
            raise
        except HandlerMatchError as e:
            if error_stage is None:
                error_stage = "match"
                error_message = str(e)
            logger.warning("[IntakeV3] HandlerMatchError: %s", e)
            raise _FallbackToV2PreviewSkip() from e
        except HandlerResolveError as e:
            if error_stage is None:
                error_stage = "resolve"
                error_message = str(e)
            logger.warning("[IntakeV3] HandlerResolveError: %s", e)
            raise _FallbackToV2PreviewSkip() from e
        except Exception as e:
            if error_stage is None:
                error_stage = "crash"
                error_message = str(e)
            logger.exception("[IntakeV3] pipeline crashed: %s", e)
            raise _FallbackToV2PreviewSkip() from e
        finally:
            try:
                classify_ms = (
                    int((_t_after_classify - _t_start) * 1000)
                    if _t_after_classify is not None
                    else None
                )
                handler_ms = (
                    int((_t_after_handler - _t_after_classify) * 1000)
                    if (_t_after_handler is not None and _t_after_classify is not None)
                    else None
                )
                total_ms = int((_time.perf_counter() - _t_start) * 1000)
                self._fire_telemetry(
                    ocr_data,
                    caption,
                    result,
                    error_stage=error_stage,
                    error_message=error_message,
                    classify_latency_ms=classify_ms,
                    handler_latency_ms=handler_ms,
                    total_latency_ms=total_ms,
                )
            except Exception as _tel_err:
                logger.warning("[IntakeV3Telemetry] finally hook failed: %s", _tel_err)

    # ---------------- Telemetry helpers (Task 8) ----------------

    def _fire_telemetry(
        self,
        ocr_data: dict,
        caption: str,
        result: Optional[IntakeResult],
        error_stage: Optional[str] = None,
        error_message: Optional[str] = None,
        classify_latency_ms: Optional[int] = None,
        handler_latency_ms: Optional[int] = None,
        total_latency_ms: Optional[int] = None,
    ) -> None:
        """Fire-and-forget telemetry log. NEVER blocks, NEVER raises."""
        try:
            asyncio.create_task(
                self._log_intake_decision(
                    ocr_data,
                    caption,
                    result,
                    error_stage=error_stage,
                    error_message=error_message,
                    classify_latency_ms=classify_latency_ms,
                    handler_latency_ms=handler_latency_ms,
                    total_latency_ms=total_latency_ms,
                )
            )
        except Exception as e:
            logger.warning("[IntakeV3Telemetry] schedule failed: %s", e)

    async def _log_intake_decision(
        self,
        ocr_data: dict,
        caption: str,
        result: Optional[IntakeResult],
        error_stage: Optional[str] = None,
        error_message: Optional[str] = None,
        classify_latency_ms: Optional[int] = None,
        handler_latency_ms: Optional[int] = None,
        total_latency_ms: Optional[int] = None,
    ) -> None:
        """Insert 1 row into document_intake_log. PII-safe. Never raises."""
        try:
            ocr_hash = self._canonical_ocr_hash(ocr_data)
            doc_type = str(ocr_data.get("doc_type") or "")[:64]
            amount = ocr_data.get("total_amount") or ocr_data.get("amount")
            amount_bucket = self._bucket_amount(amount)
            has_counterparty = bool(
                ocr_data.get("counterparty_name")
                or ocr_data.get("vendor_name")
                or ocr_data.get("customer_name")
            )
            has_caption = bool(caption and caption.strip())

            classified_type: Optional[str] = None
            classified_confidence: Optional[float] = None
            alternatives_json: Optional[str] = None
            signals_fired_json: Optional[str] = None
            ambiguity_reason: Optional[str] = None
            if result and result.classification:
                cls = result.classification
                classified_type = cls.type.value
                try:
                    classified_confidence = round(float(cls.confidence), 3)
                except (TypeError, ValueError):
                    classified_confidence = None
                alternatives_json = _json.dumps(
                    [
                        {"type": t.value, "score": round(float(s), 3)}
                        for t, s in (cls.alternatives or [])
                    ]
                )
                signals_fired_json = _json.dumps(cls.signals_fired or [])
                if cls.type == TransferType.AMBIGUOUS:
                    reasons = cls.reasons or []
                    for kw in ("low_conf", "gap", "conflict"):
                        if any(kw in r for r in reasons):
                            ambiguity_reason = kw
                            break

            handler_selected = classified_type
            handler_match_success: Optional[bool] = None
            handler_needed_clarification: Optional[bool] = None
            if result and result.handler_match:
                handler_match_success = result.handler_match.success
                handler_needed_clarification = result.handler_match.needs_clarification

            action_key: Optional[str] = None
            action_resolved = False
            if result and result.resolved_action:
                action_key = result.resolved_action.action_key
                action_resolved = True

            err_msg: Optional[str] = None
            if error_message:
                err_msg = _re.sub(r"\d+", "*", str(error_message))[:500]

            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true)",
                        self.tenant_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO document_intake_log (
                            tenant_id, ocr_hash, doc_type, amount_bucket,
                            has_counterparty, has_caption,
                            classified_type, classified_confidence,
                            alternatives, signals_fired, ambiguity_reason,
                            handler_selected, handler_match_success,
                            handler_needed_clarification,
                            action_key, action_resolved,
                            classify_latency_ms, handler_latency_ms, total_latency_ms,
                            error_stage, error_message
                        ) VALUES (
                            $1, $2, $3, $4,
                            $5, $6,
                            $7, $8,
                            $9::jsonb, $10::jsonb, $11,
                            $12, $13,
                            $14,
                            $15, $16,
                            $17, $18, $19,
                            $20, $21
                        )
                        """,
                        self.tenant_id,
                        ocr_hash,
                        doc_type,
                        amount_bucket,
                        has_counterparty,
                        has_caption,
                        classified_type,
                        classified_confidence,
                        alternatives_json,
                        signals_fired_json,
                        ambiguity_reason,
                        handler_selected,
                        handler_match_success,
                        handler_needed_clarification,
                        action_key,
                        action_resolved,
                        classify_latency_ms,
                        handler_latency_ms,
                        total_latency_ms,
                        error_stage,
                        err_msg,
                    )
        except Exception as e:
            logger.warning("[IntakeV3Telemetry] insert failed: %s", e)

    @staticmethod
    def _canonical_ocr_hash(ocr: dict) -> str:
        """Canonical SHA-256 hash of OCR payload (dedup + time-series grouping)."""
        try:
            canonical = _json.dumps(ocr or {}, sort_keys=True, default=str)
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        except Exception:
            return ""

    @staticmethod
    def _bucket_amount(amount) -> str:
        """Coarse amount bucket. PII-safe."""
        if amount is None:
            return "none"
        try:
            a = float(amount)
        except (TypeError, ValueError):
            return "none"
        if a < 100_000:
            return "<100K"
        if a < 1_000_000:
            return "100K-1M"
        if a < 10_000_000:
            return "1M-10M"
        return ">10M"

    async def _classify_or_force(
        self,
        ocr_data: dict,
        caption: str,
        forced: Optional[ForcedOverride],
    ) -> ClassificationResult:
        # V3 explicit type override
        if forced and forced.type:
            return ClassificationResult(
                type=forced.type,
                confidence=1.0,
                alternatives=[],
                signals_fired=[],
                reasons=["forced_type"],
            )

        # V2 legacy direction override → infer type from direction
        if forced and forced.direction and not forced.type:
            inferred = {
                "in": TransferType.RECEIVE_PAYMENT,
                "out": TransferType.BILL_PAYMENT,
            }.get(forced.direction)
            if inferred:
                forced.type = inferred
                return ClassificationResult(
                    type=inferred,
                    confidence=1.0,
                    alternatives=[],
                    signals_fired=[],
                    reasons=["legacy_direction_override"],
                )

        # Normal path
        return await self.classifier.classify(ocr_data, caption, None)

    def _build_clarification_result(
        self, classification: ClassificationResult
    ) -> IntakeResult:
        top = classification.alternatives[:3]

        # Tier 1: AR/AP only → narrow 2-pill question
        ar_ap_only = len(top) >= 2 and all(
            t in {TransferType.RECEIVE_PAYMENT, TransferType.BILL_PAYMENT}
            for t, _ in top[:2]
        )
        if ar_ap_only:
            question = "Ini pembayaran masuk atau keluar?"
            options = [
                {"label": "Pembayaran Masuk", "value": "type:receive_payment"},
                {"label": "Pembayaran Keluar", "value": "type:bill_payment"},
            ]
        else:
            question = "Ini transfer jenis apa?"
            options = [
                {
                    "label": TYPE_DISPLAY_NAMES.get(t, t.value),
                    "value": f"type:{t.value}",
                }
                for t, _ in top
                if t in TYPE_DISPLAY_NAMES
            ]
            options.append({"label": "Jenis lain", "value": "type:other"})

        return IntakeResult(
            success=False,
            classification=classification,
            needs_clarification=True,
            clarification_question=question,
            clarification_options=options,
            log_trail=["ambiguous:" + ",".join(classification.reasons)],
        )
