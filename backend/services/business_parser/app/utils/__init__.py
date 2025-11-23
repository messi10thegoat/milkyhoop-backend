"""
Utility modules for business parser

Contains:
- fuzzy_match: Fuzzy string matching utilities
- text_utils: Text processing and normalization
"""

from .fuzzy_match import levenshtein_distance, fuzzy_match_payment_method

__all__ = [
    "levenshtein_distance",
    "fuzzy_match_payment_method",
]