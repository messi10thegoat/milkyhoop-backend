"""ReceivePaymentHandler — transfer uang masuk dari pelanggan, match ke AR invoice."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar, Optional

from ...document_action_resolver import DocumentActionResolver, ResolvedAction
from ...document_matcher import DocumentMatcher, SmartMatchResult
from ..primitives.arap_matcher import ARAPMatcher
from ..signals import classify_doc_number, extract_doc_numbers, extract_party_name
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

        # FIX_DOCNUM_MATCH: explicit invoice number in caption → match THAT invoice
        # directly (partial amount allowed, e.g. a down payment / "uang panjar").
        # Falls back to amount-based matching below when no usable reference.
        for _ref in extract_doc_numbers(caption or ""):
            if classify_doc_number(_ref) != "ar":
                continue
            by_num = await self._arap.match_ar_by_number(_ref)
            if by_num:
                best = by_num[0]
                best.confidence = 0.9
                best.reasons = ["explicit_invoice_reference", _ref]
                return HandlerMatchResult(
                    success=True,
                    candidates=by_num,
                    best=best,
                    reasons=["ar_match_by_number", _ref],
                )

        # FIX_CAPTION_PARTY_PRIORITY (2026-06-18): if the caption explicitly names
        # the payer ("dari Marwa Pahude"), match THAT customer's open invoice FIRST,
        # before amount matching. A partial payment never equals an invoice total, so
        # the +/-2% amount window can coincidentally hit a DIFFERENT customer's invoice
        # and book under the wrong customer. The user told us who paid; honour it.
        _cust_pri = extract_party_name(caption or "", "in")
        if _cust_pri:
            _by_cust_pri = await self._arap.match_ar_by_customer(_cust_pri)
            if _by_cust_pri:
                _best = _by_cust_pri[0]
                _best.confidence = 0.88
                _best.reasons = ["caption_customer_priority", _cust_pri]
                return HandlerMatchResult(
                    success=True,
                    candidates=_by_cust_pri,
                    best=_best,
                    reasons=["ar_match_by_customer", _cust_pri],
                )

        amt_min = amt * Decimal("0.98")
        amt_max = amt * Decimal("1.02")
        candidates = await self._arap.match_ar(amt_min, amt_max, counterparty)

        if not candidates:
            # FIX_DIR_PARTYNAME: caption names a customer → match THEIR open invoice
            # (oldest first), partial payment allowed. A "pembayaran sebagian" never
            # equals the invoice amount, so amount-match above misses it; the user
            # told us who paid, so honour that instead of failing/flipping direction.
            _cust = extract_party_name(caption or "", "in")
            if _cust:
                by_cust = await self._arap.match_ar_by_customer(_cust)
                if by_cust:
                    best = by_cust[0]
                    best.confidence = 0.85
                    best.reasons = ["caption_customer_match", _cust]
                    return HandlerMatchResult(
                        success=True,
                        candidates=by_cust,
                        best=best,
                        reasons=["ar_match_by_customer", _cust],
                    )
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
