"""
Bank Text Normalizer for Indonesian Bank Statements.

Handles noise removal, abbreviation expansion, reference extraction,
and token-based similarity scoring for bank reconciliation matching.

Supports: BCA, BNI, Mandiri, BRI, CIMB, and generic Indonesian bank formats.
"""

import re
from dataclasses import dataclass

# ============ NOISE WORDS ============
# Common prefixes/suffixes in Indonesian bank statements that add no matching value
NOISE_WORDS = frozenset({
    # Transaction type markers
    "trf", "cr", "db", "trx", "txn", "overbooking", "ob",
    "switching", "swt", "kliring", "klrg", "rtgs", "sknbi",
    "ib", "atm", "edc", "pos", "ach",
    # Bank names (when used as prefixes)
    "bca", "bni", "mandiri", "bri", "cimb", "niaga",
    "permata", "danamon", "mega", "btn", "bsi",
    "ocbc", "nisp", "hsbc", "uob", "dbs",
    # Common filler words
    "ke", "dari", "via", "melalui", "untuk", "pembayaran",
    "transfer", "setoran", "penarikan", "biaya", "admin",
    # Date/time markers that appear inline
    "tgl", "jam", "pukul",
})

# ============ ABBREVIATION EXPANSIONS ============
ABBREVIATIONS = {
    "adm": "admin",
    "pymnt": "payment",
    "pymt": "payment",
    "inv": "invoice",
    "rcv": "receive",
    "sls": "sales",
    "pch": "purchase",
    "int": "interest",
    "chrg": "charge",
    "comm": "commission",
    "tfr": "transfer",
    "dp": "down payment",
    "rek": "rekening",
    "bln": "bulan",
    "thn": "tahun",
}

# ============ COMPILED PATTERNS ============
_PUNCT_PATTERN = re.compile(r"[.\-/,;:'\"\(\)]+")
_MULTI_SPACE = re.compile(r"\s+")
_REF_PATTERNS = [
    re.compile(r"\b\d{10,20}\b"),           # Long numeric (10-20 digits)
    re.compile(r"\b[A-Z]{2,4}[-/]?\d{6,}\b"),  # Prefix + digits (INV-123456)
    re.compile(r"\b\d{3,4}[-/]\d{3,4}[-/]\d{3,8}\b"),  # Segmented (123/456/789)
]
_DATE_INLINE = re.compile(r"\b\d{2}[/-]\d{2}[/-](?:\d{2}|\d{4})\b")
_TIME_INLINE = re.compile(r"\b\d{2}:\d{2}(?::\d{2})?\b")


# ============ CORE FUNCTIONS ============

def normalize_bank_text(text: str) -> str:
    """
    Normalize Indonesian bank statement description text.

    Pipeline:
    1. Lowercase
    2. Remove inline dates/times
    3. Remove punctuation
    4. Expand abbreviations
    5. Remove noise words
    6. Collapse whitespace
    7. Strip

    Args:
        text: Raw bank statement description

    Returns:
        Normalized text ready for similarity comparison
    """
    if not text:
        return ""

    # Step 1: Lowercase
    text = text.lower().strip()

    # Step 2: Remove inline dates and times
    text = _DATE_INLINE.sub(" ", text)
    text = _TIME_INLINE.sub(" ", text)

    # Step 3: Remove punctuation (preserve spaces)
    text = _PUNCT_PATTERN.sub(" ", text)

    # Step 4: Tokenize, expand abbreviations, remove noise
    tokens = text.split()
    cleaned_tokens = []
    for token in tokens:
        # Expand known abbreviations
        expanded = ABBREVIATIONS.get(token, token)
        # Skip noise words
        if expanded not in NOISE_WORDS:
            cleaned_tokens.append(expanded)

    # Step 5: Rejoin and collapse whitespace
    text = " ".join(cleaned_tokens)
    text = _MULTI_SPACE.sub(" ", text).strip()

    return text


def extract_reference_numbers(text: str) -> list[str]:
    """
    Extract potential reference numbers from bank statement text.

    Looks for:
    - Long numeric sequences (10-20 digits)
    - Alphanumeric codes with prefixes (INV-123456, PO/2024/001)
    - Segmented numbers (123/456/789)

    Args:
        text: Raw bank statement description

    Returns:
        List of extracted reference strings
    """
    if not text:
        return []

    refs = []
    for pattern in _REF_PATTERNS:
        matches = pattern.findall(text.upper())
        refs.extend(matches)

    # Deduplicate while preserving order
    seen = set()
    unique_refs = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)

    return unique_refs


def extract_contact_hint(text: str) -> str | None:
    """
    Extract a potential contact name hint from bank statement text.

    After removing noise words and references, the remaining text
    often contains the counterparty name.

    Args:
        text: Raw bank statement description

    Returns:
        Contact name hint or None if nothing meaningful remains
    """
    if not text:
        return None

    normalized = normalize_bank_text(text)

    # Remove any remaining pure-digit tokens (reference fragments)
    tokens = [t for t in normalized.split() if not t.isdigit()]

    if not tokens:
        return None

    hint = " ".join(tokens).strip()
    # Only return if we have something meaningful (at least 2 chars)
    return hint if len(hint) >= 2 else None


def token_set_ratio(text_a: str, text_b: str) -> float:
    """
    Compute token-set similarity ratio between two texts.

    Uses Jaccard-like similarity on token sets, plus containment bonus.
    This handles word reordering and partial matches well.

    Score = max(jaccard_similarity, containment_ratio)

    Where:
    - jaccard = |intersection| / |union|
    - containment = |intersection| / min(|set_a|, |set_b|)

    Args:
        text_a: First text (pre-normalized recommended)
        text_b: Second text (pre-normalized recommended)

    Returns:
        Similarity score 0.0 to 1.0
    """
    if not text_a or not text_b:
        return 0.0

    set_a = set(text_a.lower().split())
    set_b = set(text_b.lower().split())

    if not set_a or not set_b:
        return 0.0

    intersection = set_a & set_b
    union = set_a | set_b

    jaccard = len(intersection) / len(union) if union else 0.0
    containment = len(intersection) / min(len(set_a), len(set_b))

    return max(jaccard, containment)
