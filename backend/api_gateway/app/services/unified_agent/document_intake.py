"""
DocumentIntakePipeline — 3-stage document intake pipeline (V2).

Stage 1: Classify — OCR → doc_category (payment/expense/tax). No caption overrides.
Stage 2: Match Both Sides — AR + AP simultaneously. Direction from match results.
Stage 3: Resolve — Bank auto-resolve + DirectAction payload.

Direction is inferred from match scores, not keywords. Caption = boost signal only.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List

from .document_matcher import DocumentMatcher, MatchCandidate, SmartMatchResult
from .document_action_resolver import DocumentActionResolver, ResolvedAction

logger = logging.getLogger("unified_agent.document_intake")

# ---------------------------------------------------------------------------
# Caption boost signals
# ---------------------------------------------------------------------------

CAPTION_IN_SIGNALS = [
    "dari pelanggan",
    "dari customer",
    "dari pembeli",
    "pembayaran masuk",
    "uang masuk",
    "terima dari",
    "pelanggan bayar",
    "diterima dari",
    "pembayaran dari",
]

CAPTION_OUT_SIGNALS = [
    "bayar ke",
    "ke vendor",
    "ke supplier",
    "ke pemasok",
    "pembayaran keluar",
    "uang keluar",
    "bayar vendor",
]

CAPTION_BOOST = 0.15
OCR_TRANSFER_BOOST = 0.10


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class IntakeResult:
    """Final result from DocumentIntakePipeline.process()."""

    doc_category: str = "unknown"  # payment / expense / tax / unknown
    ar_matches: List[MatchCandidate] = field(default_factory=list)
    ap_matches: List[MatchCandidate] = field(default_factory=list)
    best_match: Optional[MatchCandidate] = None
    direction: str = "ambiguous"  # in / out / ambiguous
    direction_source: str = (
        "none"  # "ar_match" / "ap_match" / "caption" / "ocr_transfer" / "none"
    )

    resolved_action: Optional[ResolvedAction] = None

    # Bank resolution
    bank_id: Optional[str] = None
    bank_display_name: Optional[str] = None
    bank_candidates: List[dict] = field(default_factory=list)

    needs_direction_clarification: bool = False
    needs_bank_clarification: bool = False

    log_trail: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class DocumentIntakePipeline:
    """
    3-stage pipeline:
      1. _classify  — doc_category from OCR fields, no caption override
      2. _match_both_sides — search AR + AP, pick best side, caption as boost
      3. _resolve_action — bank auto-resolve + build DirectAction payload
    """

    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id
        self.matcher = DocumentMatcher(pool, tenant_id)
        self.resolver = DocumentActionResolver(pool, tenant_id)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def process(self, ocr_data: dict, caption: str = "") -> IntakeResult:
        """Run the full 3-stage pipeline. Returns IntakeResult."""
        result = IntakeResult()
        log = result.log_trail

        # Stage 1: Classify
        doc_category = self._classify(ocr_data)
        result.doc_category = doc_category
        log.append(f"stage1:classify → {doc_category}")

        # Stage 2: Match both sides
        await self._match_both_sides(ocr_data, caption, result)
        log.append(
            f"stage2:match → direction={result.direction} "
            f"source={result.direction_source} "
            f"best={result.best_match.label if result.best_match else 'none'}"
        )

        # Stage 3: Resolve action
        await self._resolve_action(ocr_data, result)
        log.append(
            f"stage3:resolve → "
            f"action={result.resolved_action.action_key if result.resolved_action else 'none'} "
            f"bank={result.bank_display_name or 'none'}"
        )

        return result

    # ------------------------------------------------------------------
    # Stage 1 — Classify
    # ------------------------------------------------------------------

    def _classify(self, ocr: dict) -> str:
        """
        Map OCR doc_type to doc_category.
        No caption override — caption only used in stage 2 as boost.
        """
        doc_type = (ocr.get("doc_type") or "").lower()

        if doc_type in ("bank_transfer", "receipt", "payment_receipt"):
            return "payment"
        if doc_type in ("nota", "invoice", "expense_receipt", "kwitansi"):
            return "expense"
        if doc_type in ("faktur_pajak", "tax_invoice"):
            return "tax"

        # Fallback: infer from OCR fields
        if ocr.get("tax_number") or ocr.get("npwp"):
            return "tax"
        if ocr.get("total_amount") and not ocr.get("line_items"):
            return "payment"

        return "unknown"

    # ------------------------------------------------------------------
    # Stage 2 — Match both sides
    # ------------------------------------------------------------------

    async def _match_both_sides(
        self, ocr: dict, caption: str, result: IntakeResult
    ) -> None:
        """
        Search AR and AP simultaneously.
        Direction = which side yields higher confidence match.
        Caption and OCR transfer_direction act as additive boosts, not overrides.
        Supports forced_direction in ocr dict (re-trigger flow).
        """
        forced = ocr.get("forced_direction")  # "in" or "out" or None
        amount = ocr.get("total_amount")
        counterparty = (
            ocr.get("counterparty_name")
            or ocr.get("vendor_name")
            or ocr.get("customer_name")
            or ""
        )

        # Caption direction signal
        caption_lower = caption.lower() if caption else ""
        caption_direction: Optional[str] = None
        if any(sig in caption_lower for sig in CAPTION_IN_SIGNALS):
            caption_direction = "in"
        elif any(sig in caption_lower for sig in CAPTION_OUT_SIGNALS):
            caption_direction = "out"

        # OCR transfer_direction signal
        transfer_dir_raw = (ocr.get("transfer_direction") or "").lower()
        ocr_transfer_direction: Optional[str] = None
        if "masuk" in transfer_dir_raw:
            ocr_transfer_direction = "in"
        elif "keluar" in transfer_dir_raw:
            ocr_transfer_direction = "out"

        # --- Forced direction: skip dual search ---
        if forced == "in":
            amount_min, amount_max = self._amount_range(amount, ocr)
            ar_raw = await self.matcher.match_ar(amount_min, amount_max, counterparty)
            amount_float = float(amount) if amount else None
            doc_date = ocr.get("doc_date") or ocr.get("date")
            reference = ocr.get("reference") or ocr.get("invoice_number") or ""
            for c in ar_raw:
                c.confidence, c.reasons = self.matcher.score_match(
                    c, amount_float, counterparty, doc_date, reference
                )
            ar_raw.sort(key=lambda c: c.confidence, reverse=True)
            result.ar_matches = ar_raw
            result.direction = "in"
            result.direction_source = "forced"
            result.best_match = ar_raw[0] if ar_raw else None
            return

        if forced == "out":
            amount_min, amount_max = self._amount_range(amount, ocr)
            ap_raw = await self.matcher.match_ap(amount_min, amount_max, counterparty)
            amount_float = float(amount) if amount else None
            doc_date = ocr.get("doc_date") or ocr.get("date")
            reference = ocr.get("reference") or ocr.get("invoice_number") or ""
            for c in ap_raw:
                c.confidence, c.reasons = self.matcher.score_match(
                    c, amount_float, counterparty, doc_date, reference
                )
            ap_raw.sort(key=lambda c: c.confidence, reverse=True)
            result.ap_matches = ap_raw
            result.direction = "out"
            result.direction_source = "forced"
            result.best_match = ap_raw[0] if ap_raw else None
            return

        # --- Dual search ---
        amount_min, amount_max = self._amount_range(amount, ocr)
        ar_raw = await self.matcher.match_ar(amount_min, amount_max, counterparty)
        ap_raw = await self.matcher.match_ap(amount_min, amount_max, counterparty)

        # Score candidates (match_ar/match_ap return confidence=0.0)
        amount_float = float(amount) if amount else None
        doc_date = ocr.get("doc_date") or ocr.get("date")
        reference = ocr.get("reference") or ocr.get("invoice_number") or ""
        for c in ar_raw:
            c.confidence, c.reasons = self.matcher.score_match(
                c, amount_float, counterparty, doc_date, reference
            )
        for c in ap_raw:
            c.confidence, c.reasons = self.matcher.score_match(
                c, amount_float, counterparty, doc_date, reference
            )
        ar_raw.sort(key=lambda c: c.confidence, reverse=True)
        ap_raw.sort(key=lambda c: c.confidence, reverse=True)

        result.ar_matches = ar_raw
        result.ap_matches = ap_raw

        # Apply caption boost
        best_ar_conf = 0.0
        best_ap_conf = 0.0

        if ar_raw:
            best_ar_conf = ar_raw[0].confidence
            if caption_direction == "in":
                best_ar_conf = min(1.0, best_ar_conf + CAPTION_BOOST)
            if ocr_transfer_direction == "in":
                best_ar_conf = min(1.0, best_ar_conf + OCR_TRANSFER_BOOST)

        if ap_raw:
            best_ap_conf = ap_raw[0].confidence
            if caption_direction == "out":
                best_ap_conf = min(1.0, best_ap_conf + CAPTION_BOOST)
            if ocr_transfer_direction == "out":
                best_ap_conf = min(1.0, best_ap_conf + OCR_TRANSFER_BOOST)

        # Direction decision logic
        if ar_raw and best_ar_conf >= best_ap_conf:
            result.direction = "in"
            result.direction_source = "ar_match"
            result.best_match = ar_raw[0]
        elif ap_raw:
            result.direction = "out"
            result.direction_source = "ap_match"
            result.best_match = ap_raw[0]
        elif caption_direction:
            result.direction = caption_direction
            result.direction_source = "caption"
            result.best_match = None
        elif ocr_transfer_direction:
            result.direction = ocr_transfer_direction
            result.direction_source = "ocr_transfer"
            result.best_match = None
        else:
            result.direction = "ambiguous"
            result.direction_source = "none"
            result.best_match = None
            result.needs_direction_clarification = True

    # ------------------------------------------------------------------
    # Stage 2 helper — amount tolerance
    # ------------------------------------------------------------------

    def _amount_range(self, amount, ocr: dict) -> tuple:
        """Return (amount_min, amount_max) for match queries."""
        if amount is None:
            return (None, None)
        from decimal import Decimal

        amt = Decimal(str(amount))
        doc_type = (ocr.get("doc_type") or "default").lower()
        tol = {
            "bank_transfer": Decimal("0.0"),
            "receipt": Decimal("0.02"),
            "nota": Decimal("0.05"),
            "invoice": Decimal("0.01"),
        }.get(doc_type, Decimal("0.02"))
        return (amt * (1 - tol), amt * (1 + tol))

    # ------------------------------------------------------------------
    # Stage 3 — Resolve action
    # ------------------------------------------------------------------

    async def _resolve_action(self, ocr: dict, result: IntakeResult) -> None:
        """
        Auto-resolve bank account from OCR account numbers,
        then build SmartMatchResult compat object and call DocumentActionResolver.
        """
        # Bank auto-resolve
        account_hint = (
            ocr.get("account_number")
            or ocr.get("bank_account")
            or ocr.get("bank_name")
            or ""
        )
        if account_hint:
            (
                bank_id,
                bank_display,
                bank_candidates,
            ) = await self.resolver._resolve_bank_account(account_hint)
            result.bank_id = bank_id
            result.bank_display_name = bank_display
            result.bank_candidates = bank_candidates or []
            if not bank_id and bank_candidates:
                result.needs_bank_clarification = True

        # Build SmartMatchResult-compatible object for resolver
        # Pick alternatives: whichever side is the best side, return the rest
        if result.direction == "in":
            alternatives = result.ar_matches[1:] if len(result.ar_matches) > 1 else []
        elif result.direction == "out":
            alternatives = result.ap_matches[1:] if len(result.ap_matches) > 1 else []
        else:
            alternatives = []

        smart_result = SmartMatchResult(
            doc_category=result.doc_category,
            direction=result.direction,
            direction_confidence=result.best_match.confidence
            if result.best_match
            else 0.0,
            best_match=result.best_match,
            alternatives=alternatives,
            confidence_level="high"
            if (result.best_match and result.best_match.confidence >= 0.8)
            else "medium"
            if (result.best_match and result.best_match.confidence >= 0.5)
            else "low",
            needs_user_input=result.needs_direction_clarification
            or result.needs_bank_clarification,
        )

        try:
            resolved = await self.resolver.resolve(smart_result, ocr)
            result.resolved_action = resolved
        except Exception as exc:
            logger.warning("resolver.resolve failed: %s", exc)
            result.resolved_action = None
