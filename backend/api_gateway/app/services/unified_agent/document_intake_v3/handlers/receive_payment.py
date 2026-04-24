"""ReceivePaymentHandler — transfer uang masuk dari pelanggan, match ke AR invoice."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar, Optional

from ...document_action_resolver import DocumentActionResolver, ResolvedAction
from ...document_matcher import DocumentMatcher, SmartMatchResult
from ..primitives.arap_matcher import ARAPMatcher
from ..transfer_types import TransferType
from .base import ForcedOverride, HandlerMatchResult, HandlerResolveError


class ReceivePaymentHandler:
    transfer_type: ClassVar[TransferType] = TransferType.RECEIVE_PAYMENT

    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id
        self._arap = ARAPMatcher(pool, tenant_id)
        self._resolver = DocumentActionResolver(pool, tenant_id)
        self._dm = DocumentMatcher(pool, tenant_id)

    async def match(
        self,
        ocr: dict,
        caption: str,
        tenant_ctx: Any,
        forced: Optional[ForcedOverride] = None,
    ) -> HandlerMatchResult:
        amount = ocr.get("total_amount") or ocr.get("amount")
        counterparty = ocr.get("counterparty_name") or ocr.get("customer_name") or ""
        if amount is None:
            return HandlerMatchResult(success=False, reasons=["no_amount"])

        try:
            amt = Decimal(str(amount))
        except Exception:
            return HandlerMatchResult(success=False, reasons=["bad_amount"])

        amt_min = amt * Decimal("0.98")
        amt_max = amt * Decimal("1.02")
        candidates = await self._arap.match_ar(amt_min, amt_max, counterparty)

        if not candidates:
            return HandlerMatchResult(
                success=False,
                reasons=["no_ar_match"],
                needs_clarification=False,
            )

        doc_date = ocr.get("document_date") or ocr.get("date")
        reference = ocr.get("reference_number") or ocr.get("document_number") or ""
        scored = []
        for c in candidates:
            conf, reasons = self._dm.score_match(
                c, amt, counterparty, doc_date, reference
            )
            scored.append((conf, c, reasons))
        scored.sort(key=lambda x: x[0], reverse=True)
        best_conf, best_cand, best_reasons = scored[0]
        best_cand.confidence = best_conf
        best_cand.reasons = best_reasons

        return HandlerMatchResult(
            success=True,
            candidates=[c for _, c, _ in scored],
            best=best_cand,
            reasons=["ar_match"],
        )

    async def resolve(
        self,
        match: HandlerMatchResult,
        ocr: dict,
        tenant_ctx: Any,
    ) -> ResolvedAction:
        if not match.success or match.best is None:
            raise HandlerResolveError("no match to resolve")

        conf = match.best.confidence
        smart = SmartMatchResult(
            doc_category="payment",
            direction="in",
            direction_confidence=1.0,
            best_match=match.best,
            alternatives=match.candidates,
            confidence_level=(
                "high" if conf >= 0.85 else "medium" if conf >= 0.60 else "low"
            ),
            needs_user_input=False,
        )

        resolved = await self._resolver._build_receive_payment_payload(smart, ocr)
        resolved.payload["_meta_transfer_type"] = TransferType.RECEIVE_PAYMENT.value
        return resolved

    def describe_match(self, match: HandlerMatchResult) -> str:
        if not match.success or match.best is None:
            return "Tidak ada faktur penjualan yang cocok."
        bm = match.best
        return (
            f"Pembayaran Rp {float(bm.amount):,.0f} dari {bm.counterparty} "
            f"cocok dengan {bm.label}."
        )
