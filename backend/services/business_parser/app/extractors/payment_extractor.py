"""
Payment method extraction with fuzzy matching support
"""

import logging
from typing import Optional
from app.utils.fuzzy_match import fuzzy_match_payment_method

logger = logging.getLogger(__name__)

# Payment method keywords mapping
PAYMENT_KEYWORDS = {
    "via transfer": "transfer",  # Check "via" prefix first
    "via tunai": "tunai",
    "tunai": "tunai",
    "cash": "tunai",
    "kas": "tunai",
    "uang tunai": "tunai",
    "transfer": "transfer",
    "tf": "transfer",
    "bank": "transfer",
    "bca": "transfer",
    "mandiri": "transfer",
    "bni": "transfer",
    "tempo": "tempo",
    "hutang": "tempo",
    "utang": "tempo",
    "credit": "tempo"
}


def extract_payment_method(text: str, use_fuzzy: bool = True) -> Optional[str]:
    """
    Extract payment method from text with fuzzy matching support.

    Supports exact matching and fuzzy matching (typo tolerance).

    Args:
        text: Input text (should be lowercased)
        use_fuzzy: Whether to use fuzzy matching for typos (default: True)

    Returns:
        Payment method: "tunai", "transfer", or "tempo", or None if not found

    Example:
        >>> extract_payment_method("jual kopi via transfer")
        "transfer"
        >>> extract_payment_method("jual kopi via tranffer")  # typo
        "transfer"  # fuzzy matched
    """
    text_lower = text.lower()

    # Try fuzzy matching first (handles typos like "tranffer" → "transfer")
    if use_fuzzy:
        fuzzy_method = fuzzy_match_payment_method(text_lower, PAYMENT_KEYWORDS, threshold=2)
        if fuzzy_method:
            logger.info(f"[PAYMENT] Extracted via fuzzy match: {fuzzy_method}")
            return fuzzy_method

    # Fallback to exact matching
    for keyword, method in PAYMENT_KEYWORDS.items():
        if keyword in text_lower:
            logger.info(f"[PAYMENT] Extracted: {method} from keyword '{keyword}'")
            return method

    return None