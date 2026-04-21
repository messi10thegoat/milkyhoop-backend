"""ExpenseAccountResolver — keyword→CoA account recommendation.

Reuses EXPENSE_KEYWORDS and _resolve_account from legacy DocumentMatcher
so behavior is byte-identical to V2 _recommend_expense_account.
"""

from __future__ import annotations

from typing import Optional

from ...document_matcher import AccountRecommendation, DocumentMatcher


class ExpenseAccountResolver:
    def __init__(self, pool, tenant_id: str):
        self._inner = DocumentMatcher(pool, tenant_id)

    async def recommend(self, ocr: dict) -> Optional[AccountRecommendation]:
        """Keyword-match OCR text to expense CoA; fallback to 5-20900 Beban Lain-lain."""
        return await self._inner._recommend_expense_account(ocr)
