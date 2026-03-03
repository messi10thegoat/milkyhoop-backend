"""
Robust balance parser for Indonesian number formats.

Handles: 24.793.500 | 24.793.500,00 | 24793500 | Rp 500.000 | saldo 0
Avoids: misidentifying account numbers as balances.
Law 25: Returns Decimal, never float.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple, List


def parse_balance(text: str, known_account_numbers: List[str] = None) -> Tuple[Optional[Decimal], bool]:
    """
    Parse saldo akhir from user text.
    Returns (Decimal, True) if found, (None, False) if not.

    Priority:
    1. Explicit "saldo (akhir) <number>" — including "saldo 0"
    2. "Rp/IDR <number>"
    3. Last large number >= 1,000,000 (excluding known account numbers)
    """
    # Remove known account numbers to avoid false positives
    clean = text
    for acc in (known_account_numbers or []):
        acc_str = str(acc).strip()
        if acc_str:
            clean = clean.replace(acc_str, " __ACC__ ")

    # Strategy 1: "saldo (akhir) X" — including "saldo 0"
    m = re.search(
        r'saldo\s+(?:akhir\s+)?(?:Rp\.?\s*)?(\d[\d.,]*)',
        clean, re.IGNORECASE
    )
    if m:
        val = _clean_indonesian_number(m.group(1))
        if val is not None:
            return val, True

    # Strategy 2: "Rp X" or "IDR X"
    m = re.search(r'(?:Rp\.?|IDR)\s*(\d[\d.,]*)', clean, re.IGNORECASE)
    if m:
        val = _clean_indonesian_number(m.group(1))
        if val is not None:
            return val, True

    # Strategy 3: last large number (fallback) — skip account-number-like sequences
    numbers = re.findall(r'\d[\d.,]*\d|\d', clean)
    for n in reversed(numbers):
        # Skip pure-digit sequences 8+ chars with no separator (likely account number)
        raw = n.replace('.', '').replace(',', '')
        if len(raw) >= 8 and '.' not in n and ',' not in n:
            continue
        # Skip placeholder
        if '__ACC__' in n:
            continue
        val = _clean_indonesian_number(n)
        if val is not None and val >= 1_000_000:
            return val, True

    return None, False


def _clean_indonesian_number(s: str) -> Optional[Decimal]:
    """
    Parse Indonesian number format to Decimal.

    24.793.500     → 24793500       (dots as thousands)
    24.793.500,00  → 24793500.00    (dots thousands, comma decimal)
    24793500       → 24793500
    24793500,00    → 24793500.00
    24,793,500     → 24793500       (US format)
    24,793,500.00  → 24793500.00
    0              → 0
    500.000        → 500000         (single dot as thousands if 3 digits after)
    """
    s = s.strip()
    if not s:
        return None

    try:
        # Pure integer (including "0")
        if s.isdigit():
            return Decimal(s)

        has_dots = '.' in s
        has_commas = ',' in s

        if has_dots and has_commas:
            # Determine which is thousands, which is decimal
            last_dot = s.rfind('.')
            last_comma = s.rfind(',')
            if last_comma > last_dot:
                # Indonesian: 24.793.500,00 → dots=thousands, comma=decimal
                s = s.replace('.', '').replace(',', '.')
            else:
                # US: 24,793,500.00 → commas=thousands, dot=decimal
                s = s.replace(',', '')
        elif has_dots and s.count('.') > 1:
            # Multiple dots = thousands separator: 24.793.500
            s = s.replace('.', '')
        elif has_commas and s.count(',') > 1:
            # Multiple commas = thousands separator: 24,793,500
            s = s.replace(',', '')
        elif has_dots and s.count('.') == 1:
            # Single dot — check if 3 digits after → thousands separator
            parts = s.split('.')
            if len(parts[1]) == 3:
                # 500.000 → thousands separator
                s = s.replace('.', '')
            # else: genuine decimal like 123.45
        elif has_commas and s.count(',') == 1:
            # Single comma — Indonesian decimal: 24793500,00
            s = s.replace(',', '.')

        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None
