"""Document Intake V3 — Signal and ClassificationResult dataclasses.

Signal carries one piece of evidence (from OCR, caption, or DB) with positive
and negative contributions to TransferType scores. Classifier aggregates Signals
to pick a winning type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .transfer_types import TransferType


SignalSource = Literal["ocr", "caption", "db", "forced"]


# FIX_DOCNUM_MATCH: explicit document numbers a user may type in a caption.
# INV-YYMM-NNNN = sales invoice (AR), PB-YYMM-NNNN = bill (AP). Used to resolve
# transfer type/direction deterministically AND to match the specific document
# even when the paid amount is partial (e.g. a down payment / "uang panjar").
_DOC_NUM_RE = re.compile(r"\b(INV|PB)-\d{3,6}-\d{2,6}\b", re.IGNORECASE)


def extract_doc_numbers(text: str) -> list[str]:
    """Return unique INV-/PB- document numbers found in free text (upper-cased)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _DOC_NUM_RE.finditer(text):
        n = m.group(0).upper()
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def classify_doc_number(number: str) -> str:
    """'ar' for sales-invoice numbers, 'ap' for bill numbers, else 'unknown'."""
    u = (number or "").upper()
    if u.startswith("INV-"):
        return "ar"
    if u.startswith("PB-"):
        return "ap"
    return "unknown"


# FIX_DIR_PARTYNAME (2026-06-16): pull the customer/vendor name the user typed in
# the caption ("dari pelanggan Britney Ticoalu", "ke vendor LILING"). Lets the
# handler match that party's open invoices by NAME (partial payments never equal
# the invoice amount, so amount-match alone fails). Stop at a separator / amount /
# transfer word so we don't swallow the rest of the sentence.
_PARTY_STOP = (
    # fix/docintake-caption-party: angka / "Rp" juga kata henti
    # ("bayar ke PT Grosir Kaos 100rb" → "PT Grosir Kaos").
    r"(?=$|[,.;]|\s+(?:\d|rp\b|(?:untuk|sebesar|sejumlah|senilai|via|lewat|pakai|pake|"
    r"transfer|tf|tgl|tanggal|no\b|nomor|faktur|invoice|tagihan|yang)\b))"
)
_PARTY_IN_RE = re.compile(
    # FIX_PARTY_KEYWORD_OPTIONAL (2026-06-18): the party-type word
    # (pelanggan/customer) is OPTIONAL — users write "dari Marwa Pahude"
    # as often as "dari pelanggan Marwa". The downstream customer lookup
    # (ILIKE on an OPEN invoice) self-validates, so a bare "dari X" that is
    # not a real customer simply yields no match.
    r"\bdari\s+(?:pelanggan|pelangan|customer|cust|pembeli)?\s*(.+?)" + _PARTY_STOP,
    re.IGNORECASE,
)
_PARTY_OUT_RE = re.compile(
    # vendor/supplier word OPTIONAL — "ke NONENG" == "ke vendor NONENG".
    r"\b(?:ke|kepada|bayar\s+ke|bayar|untuk)\s+(?:vendor|supplier|pemasok|toko)?\s*(.+?)"
    + _PARTY_STOP,
    re.IGNORECASE,
)


def extract_party_name(caption: str, direction: str) -> str | None:
    """direction 'in' → customer name after 'dari pelanggan'; 'out' → vendor name
    after 'ke/bayar vendor'. Returns None if not present."""
    if not caption:
        return None
    rx = _PARTY_IN_RE if direction == "in" else _PARTY_OUT_RE
    m = rx.search(caption)
    if not m:
        return None
    name = m.group(1).strip(" .,:-")
    return name or None


@dataclass
class Signal:
    """One piece of evidence contributing to transfer type classification."""

    source: SignalSource
    name: str
    strength: float
    targets: dict[TransferType, float] = field(default_factory=dict)
    excludes: dict[TransferType, float] = field(default_factory=dict)

    def to_telemetry(self) -> dict:
        """PII-safe compact representation for telemetry logging."""
        return {
            "src": self.source,
            "name": self.name,
            "strength": round(self.strength, 3),
            "targets": {t.value: round(w, 3) for t, w in self.targets.items()},
            "excludes": {t.value: round(w, 3) for t, w in self.excludes.items()},
        }


@dataclass
class ClassificationResult:
    """Output of TransferClassifier.classify()."""

    type: TransferType
    confidence: float
    alternatives: list[tuple[TransferType, float]] = field(default_factory=list)
    signals_fired: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
