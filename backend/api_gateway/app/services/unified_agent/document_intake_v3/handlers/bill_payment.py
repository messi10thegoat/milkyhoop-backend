"""BillPaymentHandler — transfer uang keluar ke vendor, match ke AP bill."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar, Optional

from ...document_action_resolver import DocumentActionResolver, ResolvedAction
from ...document_matcher import DocumentMatcher, SmartMatchResult
from ..primitives.arap_matcher import ARAPMatcher
from ..signals import classify_doc_number, extract_doc_numbers, extract_party_name
from ..transfer_types import TransferType
from .base import ForcedOverride, HandlerMatchResult, HandlerResolveError


class BillPaymentHandler:
    transfer_type: ClassVar[TransferType] = TransferType.BILL_PAYMENT

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
        counterparty = ocr.get("counterparty_name") or ocr.get("vendor_name") or ""
        if amount is None:
            return HandlerMatchResult(success=False, reasons=["no_amount"])

        try:
            amt = Decimal(str(amount))
        except Exception:
            return HandlerMatchResult(success=False, reasons=["bad_amount"])

        # FIX_DOCNUM_MATCH: explicit bill number in caption → match THAT bill
        # directly (partial amount allowed). Falls back to amount-based matching.
        for _ref in extract_doc_numbers(caption or ""):
            if classify_doc_number(_ref) != "ap":
                continue
            by_num = await self._arap.match_ap_by_number(_ref)
            if by_num:
                best = by_num[0]
                best.confidence = 0.9
                best.reasons = ["explicit_bill_reference", _ref]
                return HandlerMatchResult(
                    success=True,
                    candidates=by_num,
                    best=best,
                    reasons=["ap_match_by_number", _ref],
                )

        # FIX_CAPTION_PARTY_PRIORITY (2026-06-18): caption names the payee
        # ("ke vendor NONENG") -> match THAT vendor's open bill FIRST, before amount
        # (a coincidental +/-2% amount hit must not override an explicit vendor name).
        _vend_pri = extract_party_name(caption or "", "out")
        if _vend_pri:
            _by_vend_pri = await self._arap.match_ap_by_vendor(_vend_pri)
            if _by_vend_pri:
                _best = _by_vend_pri[0]
                _best.confidence = 0.88
                _best.reasons = ["caption_vendor_priority", _vend_pri]
                return HandlerMatchResult(
                    success=True,
                    candidates=_by_vend_pri,
                    best=_best,
                    reasons=["ap_match_by_vendor", _vend_pri],
                )

        amt_min = amt * Decimal("0.98")
        amt_max = amt * Decimal("1.02")
        candidates = await self._arap.match_ap(amt_min, amt_max, counterparty)

        if not candidates:
            # FIX_DIR_PARTYNAME: caption names a vendor → match THEIR open bill
            # (oldest first), partial payment allowed.
            _vend = extract_party_name(caption or "", "out")
            if _vend:
                by_vend = await self._arap.match_ap_by_vendor(_vend)
                if by_vend:
                    best = by_vend[0]
                    best.confidence = 0.85
                    best.reasons = ["caption_vendor_match", _vend]
                    return HandlerMatchResult(
                        success=True,
                        candidates=by_vend,
                        best=best,
                        reasons=["ap_match_by_vendor", _vend],
                    )
            return HandlerMatchResult(
                success=False,
                reasons=["no_ap_match"],
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
            reasons=["ap_match"],
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
            direction="out",
            direction_confidence=1.0,
            best_match=match.best,
            alternatives=match.candidates,
            confidence_level=(
                "high" if conf >= 0.85 else "medium" if conf >= 0.60 else "low"
            ),
            needs_user_input=False,
        )

        resolved = await self._resolver._build_bill_payment_payload(smart, ocr)
        resolved.payload["_meta_transfer_type"] = TransferType.BILL_PAYMENT.value
        return resolved

    def describe_match(self, match: HandlerMatchResult) -> str:
        if not match.success or match.best is None:
            return "Tidak ada tagihan yang cocok."
        bm = match.best
        return (
            f"Pembayaran Rp {float(bm.amount):,.0f} ke {bm.counterparty} "
            f"untuk {bm.label}."
        )
