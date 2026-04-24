"""TaxPeriodResolver — extract tax period (year, month, tax_code) from OCR/caption.

Handles Indonesian patterns like:
  - "Masa PPh 21 Maret 2026"
  - "PPN Masa Februari 26"
  - "PPh 25 Jan 2026"
  - "SPT Masa April 2026"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_MONTHS = {
    "januari": 1,
    "jan": 1,
    "februari": 2,
    "feb": 2,
    "maret": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "agustus": 8,
    "agu": 8,
    "ags": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "desember": 12,
    "des": 12,
    "dec": 12,
}

_MONTH_KEYS_SORTED = sorted(_MONTHS.keys(), key=len, reverse=True)
_MONTHS_ALT = "|".join(re.escape(k) for k in _MONTH_KEYS_SORTED)

# Match "<month> <year>" where year is 4-digit or 2-digit (20-99)
_MONTH_YEAR_RE = re.compile(
    rf"\b({_MONTHS_ALT})\s+(20\d{{2}}|\d{{2}})\b",
    re.IGNORECASE,
)

_TAX_CODE_PATTERNS = [
    (re.compile(r"\bpph\s*21\b", re.I), "pph21"),
    (re.compile(r"\bpph\s*23\b", re.I), "pph23"),
    (re.compile(r"\bpph\s*25\b", re.I), "pph25"),
    (re.compile(r"\bpph\s*4\s*\(?2\)?\b", re.I), "pph4(2)"),
    (re.compile(r"\bppn\s*keluaran\b", re.I), "ppn"),
    (re.compile(r"\bppn\s*masukan\b", re.I), "ppn_masukan"),
    (re.compile(r"\bppn\b", re.I), "ppn"),
]


@dataclass
class TaxPeriod:
    year: int
    month: int
    tax_code: Optional[str] = None


class TaxPeriodResolver:
    def extract_period(self, ocr: dict, caption: str = "") -> Optional[TaxPeriod]:
        text_parts = [
            caption or "",
            str(ocr.get("raw_text") or ""),
            str(ocr.get("notes") or ""),
            str(ocr.get("reference_note") or ""),
            str(ocr.get("doc_type") or ""),
        ]
        text = " ".join(text_parts).strip()
        if not text:
            return None

        month, year = self._find_month_year(text)
        code = self._find_tax_code(text)

        if month is None or year is None:
            return None
        return TaxPeriod(year=year, month=month, tax_code=code)

    @staticmethod
    def _find_month_year(text: str) -> tuple[Optional[int], Optional[int]]:
        """Find a month+year pair. Requires month and year to co-occur adjacently
        so stray numbers (e.g. "PPh 23" or "PPh 21 2026" with no month) won't
        be misinterpreted as year-only matches.
        """
        m = _MONTH_YEAR_RE.search(text)
        if not m:
            return None, None
        month = _MONTHS[m.group(1).lower()]
        year_str = m.group(2)
        if len(year_str) == 4:
            year = int(year_str)
        else:
            yy = int(year_str)
            if not (20 <= yy <= 99):
                return None, None
            year = 2000 + yy
        return month, year

    @staticmethod
    def _find_tax_code(text: str) -> Optional[str]:
        for pattern, code in _TAX_CODE_PATTERNS:
            if pattern.search(text):
                return code
        return None
