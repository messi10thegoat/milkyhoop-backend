"""Shared domain token vocabulary for intent regex classification.

Single source of truth for `\b`-anchored domain patterns used across
classify_query_intent() in entity_extractor.py. Adding a new domain is
1 dict entry; adding a new ranking direction is 1 constant.

Refs: docs/plans/2026-04-22-chat-regex-slot-fix-plan.md (P1 systemic)
"""

from __future__ import annotations

import re
from typing import Dict

# Domain noun tokens, always `\b`-anchored so they don't match substrings
# like "barang"/"bayar"/"besar"/"apa" (root cause of the P1 regression).
# Add new domain -> 1 line here.
DOMAIN_TOKENS: Dict[str, str] = {
    "ar": r"(?:\bpiutang\b|\bar\b(?!\w))",
    "ap": r"(?:\bhutang\b|\butang\b|\bap\b(?!\w))",
    "stock": r"(?:\bstok\b|\bstock\b|\bpersediaan\b|\bbarang\b|\bitem\b)",
    "expense": r"(?:\bbeban\b|\bbiaya\b|\bexpense\b|\bpengeluaran\b)",
    "sales": r"(?:\bpenjualan\b|\bsales\b|\brevenue\b|\bomzet\b)",
    "vendor": r"(?:\bvendor\b|\bpemasok\b|\bsupplier\b)",
    "customer": r"(?:\bpelanggan\b|\bcustomer\b|\bklien\b)",
}

# Superlative phrases. Ranking-direction is an open set — currently "high"
# (biggest/most) and "low" (smallest/least).
RANK_SUPERLATIVES = (
    r"(?:paling\s+besar|terbesar|paling\s+banyak|terbanyak|"
    r"paling\s+tinggi|tertinggi|top)"
)
LOW_SUPERLATIVES = (
    r"(?:paling\s+kecil|terkecil|paling\s+sedikit|tersedikit|" r"terendah|minim)"
)


def domain_token(domain: str) -> str:
    """Return the `\b`-anchored regex fragment for a domain. Raises ValueError
    if domain unknown — fail loud rather than silently miss."""
    if domain not in DOMAIN_TOKENS:
        raise ValueError(f"unknown domain: {domain!r}. known: {sorted(DOMAIN_TOKENS)}")
    return DOMAIN_TOKENS[domain]


def rank_pattern(domain: str, *, direction: str = "high") -> re.Pattern:
    """Build a compiled regex matching `<domain_token>` + `<superlative>` in
    either order. Case-insensitive. `direction` is "high" (default) or "low".

    Example:
        >>> rank_pattern("ar").search("piutang terbesar")       # match
        >>> rank_pattern("ar").search("barang paling banyak")   # no match (\\b)
    """
    if direction == "high":
        super_pat = RANK_SUPERLATIVES
    elif direction == "low":
        super_pat = LOW_SUPERLATIVES
    else:
        raise ValueError(f"unknown direction: {direction!r}")
    token = domain_token(domain)
    return re.compile(
        rf"(?:{token}.*{super_pat})|(?:{super_pat}.*{token})",
        re.IGNORECASE,
    )
