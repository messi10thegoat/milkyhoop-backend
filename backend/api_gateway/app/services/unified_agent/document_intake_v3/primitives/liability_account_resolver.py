"""LiabilityAccountResolver — resolve LIABILITY-type CoA accounts.

Used by TaxPaymentHandler, BpjsPaymentHandler, LoanPaymentHandler (Phase 2+).
Phase 1 primitive exists so downstream handlers can import without rework.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ...document_matcher import AccountRecommendation


# Pattern-match hints for common liability accounts. Name-first (Law 27).
TAX_CODE_TO_NAME_PATTERN: dict[str, str] = {
    "pph21": "%PPh 21%",
    "pph23": "%PPh 23%",
    "pph25": "%PPh 25%",
    "pph4(2)": "%PPh 4%",
    "ppn": "%PPN Keluaran%",
    "ppn_masukan": "%PPN Masukan%",
}


@dataclass
class _LiabilityHit:
    id: str
    code: str
    name: str


class LiabilityAccountResolver:
    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id

    async def resolve_by_tax_code(
        self, tax_code: str
    ) -> Optional[AccountRecommendation]:
        pattern = TAX_CODE_TO_NAME_PATTERN.get(tax_code.lower())
        if not pattern:
            return None
        hits = await self._search_by_name_pattern(pattern)
        if not hits:
            return None
        first = hits[0]
        return AccountRecommendation(
            account_id=first.id,
            account_name=first.name,
            account_code=first.code,
            confidence=1.0 if len(hits) == 1 else 0.5,
        )

    async def resolve_by_code(
        self, code: str, fallback_name_pattern: str = ""
    ) -> Optional[AccountRecommendation]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                row = await conn.fetchrow(
                    """
                    SELECT id, account_code, name
                    FROM chart_of_accounts
                    WHERE tenant_id = $1 AND is_active = true AND is_header = false
                      AND account_code = $2
                    LIMIT 1
                    """,
                    self.tenant_id,
                    code,
                )
                if row:
                    return AccountRecommendation(
                        account_id=str(row["id"]),
                        account_name=row["name"],
                        account_code=row["account_code"],
                        confidence=1.0,
                    )
        if fallback_name_pattern:
            hits = await self._search_by_name_pattern(fallback_name_pattern)
            if hits:
                first = hits[0]
                return AccountRecommendation(
                    account_id=first.id,
                    account_name=first.name,
                    account_code=first.code,
                    confidence=0.7,
                )
        return None

    async def resolve_by_name_pattern(
        self, pattern: str
    ) -> list[AccountRecommendation]:
        hits = await self._search_by_name_pattern(pattern)
        return [
            AccountRecommendation(
                account_id=h.id,
                account_name=h.name,
                account_code=h.code,
                confidence=1.0 if len(hits) == 1 else 0.5,
            )
            for h in hits
        ]

    async def _search_by_name_pattern(self, pattern: str) -> list[_LiabilityHit]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                rows = await conn.fetch(
                    """
                    SELECT id, account_code, name
                    FROM chart_of_accounts
                    WHERE tenant_id = $1 AND is_active = true AND is_header = false
                      AND account_type = 'LIABILITY'
                      AND name ILIKE $2
                    ORDER BY length(name) ASC
                    LIMIT 10
                    """,
                    self.tenant_id,
                    pattern,
                )
                return [
                    _LiabilityHit(
                        id=str(r["id"]), code=r["account_code"], name=r["name"]
                    )
                    for r in rows
                ]
