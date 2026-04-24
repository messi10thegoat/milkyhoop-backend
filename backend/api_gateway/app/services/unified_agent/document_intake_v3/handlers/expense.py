"""ExpenseHandler — beban operasional dan biaya admin bank (no AR/AP match)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from ...document_action_resolver import DocumentActionResolver, ResolvedAction
from ...document_matcher import SmartMatchResult
from ..primitives.expense_account_resolver import ExpenseAccountResolver
from ..primitives.liability_account_resolver import LiabilityAccountResolver
from ..transfer_types import TransferType
from .base import ForcedOverride, HandlerMatchResult, HandlerResolveError


class ExpenseHandler:
    # Covers two transfer types; instantiated twice in registry.
    def __init__(
        self,
        pool,
        tenant_id: str,
        variant: Literal["operational", "bank_fee"] = "operational",
    ):
        self.pool = pool
        self.tenant_id = tenant_id
        self.variant = variant
        self._expense_accounts = ExpenseAccountResolver(pool, tenant_id)
        self._liability = LiabilityAccountResolver(pool, tenant_id)
        self._resolver = DocumentActionResolver(pool, tenant_id)

    @property
    def transfer_type(self) -> TransferType:
        return (
            TransferType.BANK_FEE
            if self.variant == "bank_fee"
            else TransferType.EXPENSE_OPERATIONAL
        )

    async def match(
        self,
        ocr: dict,
        caption: str,
        tenant_ctx: Any,
        forced: Optional[ForcedOverride] = None,
    ) -> HandlerMatchResult:
        amount = ocr.get("total_amount") or ocr.get("amount")
        if amount is None or float(amount) <= 0:
            return HandlerMatchResult(success=False, reasons=["no_amount"])

        if self.variant == "bank_fee":
            acct = await self._liability.resolve_by_code(
                "5-30100", fallback_name_pattern="%Admin Bank%"
            )
            if acct is None:
                acct = await self._expense_accounts.recommend(ocr)
        else:
            acct = await self._expense_accounts.recommend(ocr)

        return HandlerMatchResult(
            success=True,
            candidates=[],
            best=acct,
            reasons=[f"expense_variant:{self.variant}"],
        )

    async def resolve(
        self,
        match: HandlerMatchResult,
        ocr: dict,
        tenant_ctx: Any,
    ) -> ResolvedAction:
        if not match.success:
            raise HandlerResolveError("expense match failed")

        smart = SmartMatchResult(
            doc_category="expense",
            direction="out",
            direction_confidence=1.0,
            best_match=None,
            alternatives=[],
            account_recommendation=match.best,
            confidence_level="medium" if match.best else "low",
            needs_user_input=match.best is None,
        )

        resolved = await self._resolver._build_expense_payload(smart, ocr)
        resolved.payload["_meta_transfer_type"] = self.transfer_type.value
        return resolved

    def describe_match(self, match: HandlerMatchResult) -> str:
        if not match.success:
            return "Tidak dapat menentukan biaya dari dokumen ini."
        if match.best is None:
            return "Biaya terdeteksi, tapi akun CoA belum bisa dipastikan."
        acct = match.best
        return f"Biaya ke akun {acct.account_name} ({acct.account_code})."
