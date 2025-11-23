"""
Text processing and normalization utilities
"""

import re


def normalize_amount(amount_str: str, unit: str = "") -> int:
    """
    Normalize amount string to integer rupiah.

    Handles various formats:
    - "15.000" → 15000
    - "15rb" → 15000
    - "15jt" → 15000000

    Args:
        amount_str: Amount string (may contain dots, commas)
        unit: Unit suffix ("rb", "ribu", "jt", "juta", "k")

    Returns:
        Normalized amount in rupiah

    Example:
        >>> normalize_amount("15.000", "rb")
        15000
        >>> normalize_amount("2", "jt")
        2000000
    """
    # Remove dots and commas
    clean_amount = amount_str.replace('.', '').replace(',', '')
    amount_num = float(clean_amount)

    # Convert based on unit
    unit_lower = unit.lower()
    if 'jt' in unit_lower or 'juta' in unit_lower:
        return int(amount_num * 1000000)
    elif 'rb' in unit_lower or 'ribu' in unit_lower or 'k' in unit_lower:
        return int(amount_num * 1000)
    else:
        # No unit, assume raw number
        return int(amount_num)


def clean_product_name(product_name: str, stop_words: list = None) -> str:
    """
    Clean product name by removing stop words and extra whitespace.

    Args:
        product_name: Raw product name
        stop_words: List of words to remove (default: common Indonesian stop words)

    Returns:
        Cleaned product name

    Example:
        >>> clean_product_name("sweater jumlah", ["jumlah"])
        "sweater"
    """
    if stop_words is None:
        stop_words = [
            "yang", "dari", "ke", "untuk", "dengan", "adalah",
            "ini", "itu", "sebanyak", "sejumlah", "jumlah",
            "qty", "quantity"
        ]

    # Split and filter
    words = [w for w in product_name.split() if w.lower() not in stop_words]

    return " ".join(words).strip()


def strip_transaction_keywords(text: str) -> str:
    """
    Remove transaction keywords from beginning of text.

    Args:
        text: Input text

    Returns:
        Text with transaction keywords stripped

    Example:
        >>> strip_transaction_keywords("jual kopi")
        "kopi"
    """
    return re.sub(r'^(jual|beli|bayar)\s+', '', text, flags=re.IGNORECASE).strip()