"""
Document Matcher — Bridge between Vision OCR and Financial Intelligence.

Takes OCR extraction results and matches them against open AR/AP documents.
Code-driven: DB queries, fuzzy matching, weighted scoring.
Zero LLM calls.

CRITICAL: customers table uses Bahasa Indonesia columns (nama, saldo_hutang, etc.)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import date, datetime
from decimal import Decimal

logger = logging.getLogger("unified_agent.document_matcher")


# --- Dataclasses ---


@dataclass
class MatchCandidate:
    """A single candidate match from AR/AP."""

    source_type: str  # "sales_invoice" or "bill"
    source_id: str
    label: str  # e.g. "INV-0042"
    counterparty: str  # customer or vendor name
    amount: Decimal
    outstanding: Decimal
    due_date: Optional[date]
    confidence: float = 0.0
    reasons: list = field(default_factory=list)


@dataclass
class AccountRecommendation:
    """Recommended CoA account for posting."""

    account_id: str
    account_name: str
    account_code: str
    confidence: float = 0.0


@dataclass
class SmartMatchResult:
    """Final result from document matching."""

    doc_category: str  # "payment", "expense", "tax", "unknown"
    direction: str  # "in" or "out"
    direction_confidence: float
    best_match: Optional[MatchCandidate] = None
    alternatives: List[MatchCandidate] = field(default_factory=list)
    account_recommendation: Optional[AccountRecommendation] = None
    confidence_level: str = "low"  # "high", "medium", "low"
    needs_user_input: bool = True


# --- Constants ---

AMOUNT_TOLERANCE = {
    "bank_transfer": 0.0,
    "receipt": 0.02,
    "nota": 0.05,
    "invoice": 0.01,
    "default": 0.02,
}

# Direction keywords — ONLY unambiguous terms
# "pembayaran" REMOVED: ambiguous (masuk or keluar depending on context)
# "bayar" REMOVED: same ambiguity ("pelanggan bayar" = in, "bayar vendor" = out)
DIRECTION_OUT_KEYWORDS = [
    "bayar ke",
    "transfer keluar",
    "kirim ke",
    "debit",
    "pembelian dari",
    "pengeluaran",
    "biaya",
    "ke vendor",
    "ke supplier",
    "ke pemasok",
    "pembayaran keluar",
    "uang keluar",
]

DIRECTION_IN_KEYWORDS = [
    "penerimaan",
    "terima dari",
    "transfer masuk",
    "kredit",
    "penjualan",
    "pendapatan",
    "dari pelanggan",
    "dari customer",
    "dari pembeli",
    "pelanggan bayar",
    "pelanggan transfer",
    "customer bayar",
    "pembayaran masuk",
    "uang masuk",
    "diterima dari",
    "masuk dari",
]

EXPENSE_KEYWORDS = {
    "listrik": ("5-20100", "Beban Listrik"),
    "pln": ("5-20100", "Beban Listrik"),
    "token": ("5-20100", "Beban Listrik"),
    "air": ("5-20200", "Beban Air"),
    "pdam": ("5-20200", "Beban Air"),
    "telepon": ("5-20300", "Beban Telepon"),
    "internet": ("5-20300", "Beban Telepon & Internet"),
    "sewa": ("5-20400", "Beban Sewa"),
    "bensin": ("5-20500", "Beban Transportasi"),
    "solar": ("5-20500", "Beban Transportasi"),
    "parkir": ("5-20500", "Beban Transportasi"),
    "tol": ("5-20500", "Beban Transportasi"),
    "transport": ("5-20500", "Beban Transportasi"),
    "grab": ("5-20500", "Beban Transportasi"),
    "gojek": ("5-20500", "Beban Transportasi"),
    "atk": ("5-20600", "Beban Perlengkapan Kantor"),
    "makan": ("5-20700", "Beban Konsumsi"),
    "catering": ("5-20700", "Beban Konsumsi"),
    # Maintenance & repair
    "perbaikan": ("5-20700", "Beban Pemeliharaan"),
    "reparasi": ("5-20700", "Beban Pemeliharaan"),
    "servis": ("5-20700", "Beban Pemeliharaan"),
    "service": ("5-20700", "Beban Pemeliharaan"),
    "maintenance": ("5-20700", "Beban Pemeliharaan"),
    "jasa": ("5-20700", "Beban Pemeliharaan"),
    "perawatan": ("5-20700", "Beban Pemeliharaan"),
    # Cleaning
    "laundry": ("5-20700", "Beban Pemeliharaan"),
    "cuci": ("5-20700", "Beban Pemeliharaan"),
    # Printing & copies
    "fotokopi": ("5-20600", "Beban Perlengkapan Kantor"),
    "print": ("5-20600", "Beban Perlengkapan Kantor"),
    "cetak": ("5-20600", "Beban Perlengkapan Kantor"),
    # Shipping
    "kirim": ("5-20500", "Beban Transportasi"),
    "ongkir": ("5-20500", "Beban Transportasi"),
    "ekspedisi": ("5-20500", "Beban Transportasi"),
    "kurir": ("5-20500", "Beban Transportasi"),
    # Other common
    "asuransi": ("5-20900", "Beban Lain-lain"),
    "iuran": ("5-20900", "Beban Lain-lain"),
    "donasi": ("5-20900", "Beban Lain-lain"),
    "sumbangan": ("5-20900", "Beban Lain-lain"),
}

TAX_KEYWORDS = {
    "ppn": ("2-10600", "PPN Keluaran"),
    "ppn masukan": ("1-10800", "PPN Masukan"),
    "ppn keluaran": ("2-10600", "PPN Keluaran"),
    "pph": ("2-10300", "Utang Pajak"),
    "pph 21": ("2-10300", "Utang Pajak PPh 21"),
    "pph 23": ("2-10300", "Utang Pajak PPh 23"),
    "pajak": ("2-10300", "Utang Pajak"),
}

# Category classification map: doc_type → category
DOC_TYPE_CATEGORY = {
    "bank_transfer": "payment",
    "receipt": "expense",
    "invoice": "payment",
    "nota": "expense",
    "struk": "expense",
    "kwitansi": "expense",
    "expense": "expense",
    "utility": "expense",
    "pln": "expense",
    "faktur_pajak": "tax",
    "spt": "tax",
    "bukti_potong": "tax",
    # QRIS / merchant payments = expense (not bank transfer)
    "qris": "expense",
    "qris_payment": "expense",
    "merchant_payment": "expense",
    "e_wallet": "expense",
    "ewallet": "expense",
    "payment_receipt": "expense",
}


class DocumentMatcher:
    """Matches OCR results against open AR/AP documents."""

    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id

    async def match(self, ocr_result: dict) -> SmartMatchResult:
        """
        Main entry point. Takes an OCR extraction result and returns match.

        ocr_result expected keys:
            doc_type, amount, counterparty, date, reference,
            items, tax_amount, raw_text
        """
        # Normalize OCR field names to matcher expected names
        ocr_result = dict(ocr_result)  # shallow copy
        if "total_amount" in ocr_result and "amount" not in ocr_result:
            ocr_result["amount"] = ocr_result["total_amount"]
        if "counterparty" not in ocr_result:
            ocr_result["counterparty"] = (
                ocr_result.get("counterparty_name")
                or ocr_result.get("vendor_name")
                or ocr_result.get("customer_name")
                or ""
            )
        if "document_date" in ocr_result and "date" not in ocr_result:
            ocr_result["date"] = ocr_result["document_date"]
        if "document_number" in ocr_result and "reference" not in ocr_result:
            ocr_result["reference"] = ocr_result["document_number"]

        doc_category = self._classify_category(ocr_result)
        direction, dir_confidence = self._detect_direction(ocr_result, doc_category)

        best_match = None
        alternatives = []
        account_rec = None

        if doc_category == "payment":
            if direction == "ambiguous":
                # Search both AR and AP — let match confidence decide direction
                best_in, alts_in = await self._match_payment(ocr_result, doc_category, "in")
                best_out, alts_out = await self._match_payment(ocr_result, doc_category, "out")

                if best_in and best_out:
                    if best_in.confidence >= best_out.confidence:
                        best_match, alternatives = best_in, alts_in
                        direction = "in"
                    else:
                        best_match, alternatives = best_out, alts_out
                        direction = "out"
                elif best_in:
                    best_match, alternatives = best_in, alts_in
                    direction = "in"
                elif best_out:
                    best_match, alternatives = best_out, alts_out
                    direction = "out"
                else:
                    # No match on either side — direction stays ambiguous
                    best_match, alternatives = None, []
            else:
                best_match, alternatives = await self._match_payment(
                    ocr_result, doc_category, direction
                )
        elif doc_category == "expense":
            account_rec = await self._recommend_expense_account(ocr_result)
        elif doc_category == "tax":
            account_rec = await self._recommend_tax_account(ocr_result)

        # Determine confidence level
        if best_match and best_match.confidence >= 0.85:
            confidence_level = "high"
            needs_user_input = False
        elif best_match and best_match.confidence >= 0.60:
            confidence_level = "medium"
            needs_user_input = True
        else:
            confidence_level = "low"
            needs_user_input = True

        return SmartMatchResult(
            doc_category=doc_category,
            direction=direction,
            direction_confidence=dir_confidence,
            best_match=best_match,
            alternatives=alternatives,
            account_recommendation=account_rec,
            confidence_level=confidence_level,
            needs_user_input=needs_user_input,
        )

    def _classify_category(self, ocr: dict) -> str:
        """Classify OCR doc_type into a category."""
        doc_type = (ocr.get("doc_type") or "").lower().strip()
        return DOC_TYPE_CATEGORY.get(doc_type, "unknown")

    def _detect_direction(self, ocr: dict, doc_category: str) -> Tuple[str, float]:
        """
        6-layer direction detection:
        0. Explicit override (user answered direction clarification)
        1. Explicit from doc_type
        1.5. OCR transfer_direction field
        1.5b. Caption intent (user caption keywords)
        2. Keyword scan in raw_text
        3. Default by category
        """
        # Layer 0: Explicit override from direction clarification
        forced = (ocr.get("forced_direction") or "").lower()
        if forced in ("in", "masuk"):
            return ("in", 1.0)
        if forced in ("out", "keluar"):
            return ("out", 1.0)

        doc_type = (ocr.get("doc_type") or "").lower()

        # Layer 1: doc_type implies direction
        if doc_type in ("bank_transfer",):
            # Ambiguous — fall through
            pass
        elif doc_type in ("invoice",):
            return ("in", 0.8)
        elif doc_type in ("nota", "struk", "kwitansi"):
            return ("out", 0.8)
        elif doc_type in ("faktur_pajak", "bukti_potong"):
            return ("out", 0.7)

        # Layer 1.5: transfer_direction from OCR (if present)
        transfer_dir = (ocr.get("transfer_direction") or "").lower()
        if transfer_dir == "keluar":
            return ("out", 0.85)
        elif transfer_dir == "masuk":
            return ("in", 0.85)

        # Layer 1.5b: Caption intent (user explicitly states direction)
        # Caption is strongest human signal — confidence 0.90
        caption = (ocr.get("user_caption") or "").lower()
        if caption:
            _in_signals = [
                "dari pelanggan", "dari customer", "dari pembeli",
                "pembayaran masuk", "uang masuk", "terima dari",
                "pelanggan bayar", "pelanggan transfer", "customer bayar",
                "diterima dari", "masuk dari", "pembayaran dari",
            ]
            _out_signals = [
                "bayar ke", "ke vendor", "ke supplier", "ke pemasok",
                "pembayaran keluar", "uang keluar", "transfer ke",
                "kirim ke", "bayar vendor", "bayar supplier",
            ]
            if any(sig in caption for sig in _in_signals):
                return ("in", 0.90)
            if any(sig in caption for sig in _out_signals):
                return ("out", 0.90)

        # Layer 2: keyword scan (compound keywords — threshold 1 sufficient)
        raw_text = (
            ocr.get("raw_text") or ocr.get("berita") or ocr.get("reference_note") or ""
        ).lower()
        out_score = sum(1 for kw in DIRECTION_OUT_KEYWORDS if kw in raw_text)
        in_score = sum(1 for kw in DIRECTION_IN_KEYWORDS if kw in raw_text)

        if out_score > in_score and out_score >= 1:
            return ("out", min(0.6 + out_score * 0.1, 0.85))
        if in_score > out_score and in_score >= 1:
            return ("in", min(0.6 + in_score * 0.1, 0.85))

        # Layer 3: default by category
        if doc_category == "expense":
            return ("out", 0.5)
        if doc_category == "tax":
            return ("out", 0.5)
        # Payment direction unknown — downstream will ask user
        return ("ambiguous", 0.2)

    async def _match_payment(
        self, ocr: dict, doc_category: str, direction: str
    ) -> Tuple[Optional[MatchCandidate], List[MatchCandidate]]:
        """Match OCR against open AR (in) or AP (out) documents."""
        amount = ocr.get("amount")
        counterparty = ocr.get("counterparty") or ""
        doc_date = ocr.get("date")
        reference = ocr.get("reference") or ""

        if amount is None and not counterparty:
            return (None, [])

        # Determine tolerance
        doc_type = (ocr.get("doc_type") or "").lower()
        tolerance = AMOUNT_TOLERANCE.get(doc_type, AMOUNT_TOLERANCE["default"])

        candidates = []

        if amount is not None:
            amt = Decimal(str(amount))
            amount_min = amt * Decimal(str(1 - tolerance))
            amount_max = amt * Decimal(str(1 + tolerance))

            if direction == "in":
                candidates = await self._find_open_receivables(
                    amount_min, amount_max, counterparty
                )
            else:
                candidates = await self._find_open_payables(
                    amount_min, amount_max, counterparty
                )

        # Fallback: name-only search if no amount matches
        if not candidates and counterparty:
            if direction == "in":
                candidates = await self._find_open_receivables(None, None, counterparty)
            else:
                candidates = await self._find_open_payables(None, None, counterparty)

        if not candidates:
            return (None, [])

        # Score all candidates
        for c in candidates:
            c.confidence, c.reasons = self._score_match(
                c, amount, counterparty, doc_date, reference
            )

        # Sort by confidence descending
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        best = candidates[0]
        alts = candidates[1:5]  # top 4 alternatives

        return (best, alts)

    async def _find_open_receivables(
        self,
        amount_min: Optional[Decimal],
        amount_max: Optional[Decimal],
        counterparty: str,
    ) -> List[MatchCandidate]:
        """Query compute_ar_outstanding for matching open invoices."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")

                if amount_min is not None and amount_max is not None:
                    rows = await conn.fetch(
                        """
                        SELECT invoice_id, invoice_number, customer_name,
                               invoice_total, outstanding, due_date
                        FROM compute_ar_outstanding($1)
                        WHERE outstanding > 0
                          AND outstanding >= $2 AND outstanding <= $3
                        ORDER BY outstanding DESC
                        LIMIT 10
                        """,
                        self.tenant_id,
                        float(amount_min),
                        float(amount_max),
                    )
                elif counterparty:
                    rows = await conn.fetch(
                        """
                        SELECT invoice_id, invoice_number, customer_name,
                               invoice_total, outstanding, due_date
                        FROM compute_ar_outstanding($1)
                        WHERE outstanding > 0
                          AND customer_name ILIKE $2
                        ORDER BY outstanding DESC
                        LIMIT 5
                        """,
                        self.tenant_id,
                        f"%{counterparty}%",
                    )
                else:
                    return []

        return [
            MatchCandidate(
                source_type="sales_invoice",
                source_id=str(row["invoice_id"]),
                label=row["invoice_number"] or "",
                counterparty=row["customer_name"] or "",
                amount=Decimal(str(row["invoice_total"] or 0)),
                outstanding=Decimal(str(row["outstanding"] or 0)),
                due_date=row["due_date"],
            )
            for row in rows
        ]

    async def _find_open_payables(
        self,
        amount_min: Optional[Decimal],
        amount_max: Optional[Decimal],
        counterparty: str,
    ) -> List[MatchCandidate]:
        """Query compute_ap_outstanding for matching open bills."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")

                if amount_min is not None and amount_max is not None:
                    rows = await conn.fetch(
                        """
                        SELECT bill_id, bill_number, vendor_name,
                               bill_total, outstanding, due_date
                        FROM compute_ap_outstanding($1)
                        WHERE outstanding > 0
                          AND outstanding >= $2 AND outstanding <= $3
                        ORDER BY outstanding DESC
                        LIMIT 10
                        """,
                        self.tenant_id,
                        float(amount_min),
                        float(amount_max),
                    )
                elif counterparty:
                    rows = await conn.fetch(
                        """
                        SELECT bill_id, bill_number, vendor_name,
                               bill_total, outstanding, due_date
                        FROM compute_ap_outstanding($1)
                        WHERE outstanding > 0
                          AND vendor_name ILIKE $2
                        ORDER BY outstanding DESC
                        LIMIT 5
                        """,
                        self.tenant_id,
                        f"%{counterparty}%",
                    )
                else:
                    return []

        return [
            MatchCandidate(
                source_type="bill",
                source_id=str(row["bill_id"]),
                label=row["bill_number"] or "",
                counterparty=row["vendor_name"] or "",
                amount=Decimal(str(row["bill_total"] or 0)),
                outstanding=Decimal(str(row["outstanding"] or 0)),
                due_date=row["due_date"],
            )
            for row in rows
        ]

    def _score_match(
        self,
        candidate: MatchCandidate,
        amount: Optional[float],
        counterparty: str,
        doc_date: Optional[str],
        reference: str,
    ) -> Tuple[float, list]:
        """
        Weighted scoring: amount 35%, name 30%, date 20%, reference 15%.
        Returns (score, reasons).
        """
        score = 0.0
        reasons = []

        # --- Amount (35%) ---
        if amount is not None:
            amt = Decimal(str(amount))
            outstanding = candidate.outstanding
            if outstanding > 0:
                ratio = float(min(amt, outstanding) / max(amt, outstanding))
                if ratio >= 0.99:
                    score += 0.35
                    reasons.append("exact amount match")
                elif ratio >= 0.95:
                    score += 0.30
                    reasons.append("close amount match")
                elif ratio >= 0.80:
                    score += 0.20
                    reasons.append("approximate amount match")
                else:
                    score += ratio * 0.15
        else:
            # No amount — partial weight
            score += 0.05

        # --- Counterparty name (30%) ---
        if counterparty:
            cand_name = (candidate.counterparty or "").lower()
            query_name = counterparty.lower()
            if query_name == cand_name:
                score += 0.30
                reasons.append("exact name match")
            elif query_name in cand_name or cand_name in query_name:
                score += 0.22
                reasons.append("partial name match")
            else:
                # Simple token overlap
                query_tokens = set(query_name.split())
                cand_tokens = set(cand_name.split())
                overlap = query_tokens & cand_tokens
                if overlap:
                    overlap_ratio = len(overlap) / max(
                        len(query_tokens), len(cand_tokens), 1
                    )
                    score += 0.15 * overlap_ratio
                    reasons.append(f"token overlap ({len(overlap)} words)")

        # --- Date (20%) ---
        if doc_date and candidate.due_date:
            try:
                if isinstance(doc_date, str):
                    parsed = datetime.strptime(doc_date, "%Y-%m-%d").date()
                else:
                    parsed = doc_date
                delta = abs((parsed - candidate.due_date).days)
                if delta == 0:
                    score += 0.20
                    reasons.append("exact date match")
                elif delta <= 3:
                    score += 0.15
                    reasons.append("close date match")
                elif delta <= 7:
                    score += 0.10
                    reasons.append("week-range date match")
                elif delta <= 30:
                    score += 0.05
                    reasons.append("month-range date match")
            except (ValueError, TypeError):
                pass

        # --- Reference (15%) ---
        if reference:
            ref_lower = reference.lower()
            label_lower = (candidate.label or "").lower()
            if ref_lower == label_lower:
                score += 0.15
                reasons.append("exact reference match")
            elif ref_lower in label_lower or label_lower in ref_lower:
                score += 0.10
                reasons.append("partial reference match")

        return (round(score, 4), reasons)

    async def _recommend_expense_account(
        self, ocr: dict
    ) -> Optional[AccountRecommendation]:
        """Match OCR text keywords to expense CoA accounts."""
        items_text = " ".join(
            (item.get("description") or "").lower() for item in (ocr.get("items") or [])
        )
        search_text = " ".join(
            [
                str(ocr.get("raw_text") or ""),
                str(ocr.get("vendor_name") or ""),
                str(ocr.get("customer_name") or ""),
                str(ocr.get("notes") or ""),
                str(ocr.get("reference_note") or ""),
                str(ocr.get("doc_type") or ""),
                items_text,
            ]
        ).lower()

        for keyword in sorted(EXPENSE_KEYWORDS.keys(), key=len, reverse=True):
            if keyword in search_text:
                code, name = EXPENSE_KEYWORDS[keyword]
                return await self._resolve_account(code, name)

        # Fallback: generic expense account if user context suggests expense
        return await self._resolve_account("5-20900", "Beban Lain-lain")

    async def _recommend_tax_account(
        self, ocr: dict
    ) -> Optional[AccountRecommendation]:
        """Match OCR to tax-related CoA accounts."""
        raw_text = (ocr.get("raw_text") or "").lower()
        doc_type = (ocr.get("doc_type") or "").lower()
        search_text = f"{raw_text} {doc_type}"

        # Check longer keywords first (e.g. "ppn masukan" before "ppn")
        for keyword in sorted(TAX_KEYWORDS.keys(), key=len, reverse=True):
            if keyword in search_text:
                code, name = TAX_KEYWORDS[keyword]
                return await self._resolve_account(code, name)

        return None

    async def _resolve_account(
        self, code: str, fallback_name: str
    ) -> Optional[AccountRecommendation]:
        """Resolve account_id from account_code via DB."""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                    # Strategy 1: Search by canonical name (tenant-agnostic)
                    row = await conn.fetchrow(
                        """
                        SELECT id, account_code, name AS account_name
                        FROM chart_of_accounts
                        WHERE tenant_id = $1 AND is_active = true AND is_header = false
                          AND name ILIKE $2
                        ORDER BY length(name) ASC
                        LIMIT 1
                        """,
                        self.tenant_id,
                        f"%{fallback_name}%",
                    )
                    if not row:
                        # Strategy 2: fallback to specific code
                        row = await conn.fetchrow(
                            """
                            SELECT id, account_code, name AS account_name
                            FROM chart_of_accounts
                                WHERE tenant_id = $1 AND account_code = $2
                            """,
                            self.tenant_id,
                            code,
                        )
            if row:
                return AccountRecommendation(
                    account_id=str(row["id"]),
                    account_name=row["account_name"],
                    account_code=row["account_code"],
                    confidence=0.75,
                )
        except Exception as e:
            logger.warning("Failed to resolve account %s: %s", code, e)

        return None
