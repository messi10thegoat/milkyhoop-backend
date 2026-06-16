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

    async def match_ar_by_number(self, number: str) -> list[MatchCandidate]:
        # FIX_DOCNUM_MATCH: open AR invoice by exact number (partial amount ok)
        return await self._inner.match_ar_by_number(number)

    async def match_ap_by_number(self, number: str) -> list[MatchCandidate]:
        # FIX_DOCNUM_MATCH: open AP bill by exact number (partial amount ok)
        return await self._inner.match_ap_by_number(number)

    async def match_ar_by_customer(self, customer_name: str) -> list[MatchCandidate]:
        # FIX_DIR_PARTYNAME: open AR invoices for named customer, oldest first
        return await self._inner.match_ar_by_customer(customer_name)

    async def match_ap_by_vendor(self, vendor_name: str) -> list[MatchCandidate]:
        # FIX_DIR_PARTYNAME: open AP bills for named vendor, oldest first
        return await self._inner.match_ap_by_vendor(vendor_name)
