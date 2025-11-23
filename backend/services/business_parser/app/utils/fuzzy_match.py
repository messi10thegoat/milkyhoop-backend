"""
Fuzzy string matching utilities

Provides Levenshtein distance calculation and fuzzy matching
for payment method keywords with typo tolerance.
"""

import logging

logger = logging.getLogger(__name__)


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Calculate Levenshtein distance between two strings.

    The Levenshtein distance is the minimum number of single-character edits
    (insertions, deletions, or substitutions) required to change one string
    into another.

    Args:
        s1: First string
        s2: Second string

    Returns:
        Minimum edit distance between s1 and s2

    Example:
        >>> levenshtein_distance("transfer", "tranffer")
        1
        >>> levenshtein_distance("tunai", "tunal")
        1
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def fuzzy_match_payment_method(text: str, keywords: dict, threshold: int = 2) -> str:
    """
    Fuzzy match payment method keywords with typo tolerance.

    This function matches payment keywords in text even with typos,
    using Levenshtein distance to measure similarity.

    Args:
        text: Text to search in (should be lowercased)
        keywords: Dict mapping keywords to payment methods
                  e.g., {"transfer": "transfer", "tunai": "tunai"}
        threshold: Maximum edit distance allowed (default 2 = tolerates 2 typos)

    Returns:
        Matched payment method name, or None if no match found

    Example:
        >>> keywords = {"transfer": "transfer", "tunai": "tunai"}
        >>> fuzzy_match_payment_method("via tranffer", keywords)
        "transfer"  # matched "tranffer" → "transfer" with distance 1
    """
    words = text.split()

    for keyword, method in keywords.items():
        keyword_words = keyword.split()

        # Exact match first (fast path)
        if keyword in text:
            return method

        # Fuzzy match each word in keyword
        if len(keyword_words) == 1:
            # Single word keyword: check each word in text
            for word in words:
                if len(word) >= 3:  # Only fuzzy match words >= 3 chars
                    distance = levenshtein_distance(word, keyword)
                    if distance <= threshold:
                        logger.info(f"[FUZZY] Matched '{word}' → '{keyword}' (distance={distance})")
                        return method
        else:
            # Multi-word keyword: check consecutive word pairs
            for i in range(len(words) - len(keyword_words) + 1):
                phrase = " ".join(words[i:i+len(keyword_words)])
                distance = levenshtein_distance(phrase, keyword)
                if distance <= threshold:
                    logger.info(f"[FUZZY] Matched '{phrase}' → '{keyword}' (distance={distance})")
                    return method

    return None