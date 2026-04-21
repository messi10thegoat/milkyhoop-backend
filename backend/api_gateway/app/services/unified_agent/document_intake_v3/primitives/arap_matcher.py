"""ARAPMatcher — thin wrapper over legacy DocumentMatcher.match_ar/match_ap.

Exists so V3 handlers import from primitives/, not legacy document_matcher.
Phase 1 delegates directly; Phase 2+ may add V3-specific logic without
touching handler call sites.
"""

from __future__ import annotations

from ...document_matcher import DocumentMatcher, MatchCandidate


class ARAPMatcher:
    def __init__(self, pool, tenant_id: str):
        self._inner = DocumentMatcher(pool, tenant_id)

    async def match_ar(
        self, amount_min, amount_max, counterparty: str
    ) -> list[MatchCandidate]:
        return await self._inner.match_ar(amount_min, amount_max, counterparty)

    async def match_ap(
        self, amount_min, amount_max, counterparty: str
    ) -> list[MatchCandidate]:
        return await self._inner.match_ap(amount_min, amount_max, counterparty)
