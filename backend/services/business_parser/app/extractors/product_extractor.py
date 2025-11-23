"""
Product name extraction from inventory queries
"""

import re
from typing import Optional


def extract_product_name(text: str) -> Optional[str]:
    """
    Extract product name from inventory query.

    Handles patterns:
    - "stok ballpoint berapa?"
    - "cek stok pensil"
    - "berapa stok kopi?"

    Args:
        text: Query text (lowercased)

    Returns:
        Product name, or empty string if not found

    Example:
        >>> extract_product_name("stok ballpoint berapa?")
        "ballpoint"
    """
    text_lower = text.lower()

    patterns = [
        r'(?:stok|stock)\s+([a-z0-9\s]+?)(?:\s+(?:berapa|ada|di)|$|\?)',
        r'(?:cek|berapa)\s+stok\s+([a-z0-9\s]+?)(?:\?|$)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            product = match.group(1).strip()
            # Remove common words
            product = re.sub(r'\b(di|ke|dari|yang|ini|itu)\b', '', product).strip()
            if product and len(product) > 2:
                return product

    return ""