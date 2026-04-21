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

import logging
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
        try:
            # Resolve classification (or forced override)
            classification = await self._classify_or_force(ocr_data, caption, forced)

            # UNKNOWN → fall through to generic chat
            if classification.type == TransferType.UNKNOWN:
                raise _FallbackToGenericChatSkip()

            # AMBIGUOUS → clarification UX
            if classification.type == TransferType.AMBIGUOUS:
                return self._build_clarification_result(classification)

            # Dispatch to handler
            handler = self.registry.get(classification.type)
            if handler is None:
                # Phase 1 does not register this type; fall through to V2 preview
                logger.info(
                    "[IntakeV3] type %s not registered in Phase 1 — falling back to V2",
                    classification.type.value,
                )
                raise _FallbackToV2PreviewSkip()

            tenant_ctx = None  # reserved for future context object
            match = await handler.match(ocr_data, caption, tenant_ctx, forced=forced)

            if not match.success:
                if match.needs_clarification:
                    return IntakeResult(
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
                # Hard fail — fall through to V2 inline preview
                logger.info(
                    "[IntakeV3] handler match failed for %s (%s) — fallback to V2",
                    classification.type.value,
                    ",".join(match.reasons),
                )
                raise _FallbackToV2PreviewSkip()

            action = await handler.resolve(match, ocr_data, tenant_ctx)

            return IntakeResult(
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

        except (_FallbackToGenericChatSkip, _FallbackToV2PreviewSkip):
            # Propagate — handled by unified_chat
            raise
        except HandlerMatchError as e:
            logger.warning("[IntakeV3] HandlerMatchError: %s", e)
            raise _FallbackToV2PreviewSkip() from e
        except HandlerResolveError as e:
            logger.warning("[IntakeV3] HandlerResolveError: %s", e)
            raise _FallbackToV2PreviewSkip() from e
        except Exception as e:
            logger.exception("[IntakeV3] pipeline crashed: %s", e)
            raise _FallbackToV2PreviewSkip() from e

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
