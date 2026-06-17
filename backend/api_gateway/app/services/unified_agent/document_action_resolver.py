"""
DocumentActionResolver — Maps OCR + match results to existing DirectActions.

Registry-driven: ACTION_MAP dict maps (doc_category, direction, has_match) → action_key.
Payload builders know how to transform OCR data + match data into DirectAction payloads.
"""
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class BankOption:
    """A bank account option for clarification."""

    id: str
    label: str  # e.g. "BCA - Kas BCA (xxx4567)"
    value: str  # sent back when user taps — the bank_account_id


@dataclass
class ResolvedAction:
    """Result of resolving a document to a DirectAction."""

    action_key: str  # e.g. "create_bill_payment"
    payload: dict  # Fields matching DirectActionConfig.fields
    warnings: List[str] = field(default_factory=list)
    needs_clarification: bool = False  # True = missing required info
    clarification_question: str = ""  # Question to ask user
    clarification_options: List[BankOption] = field(
        default_factory=list
    )  # tappable choices


# (doc_category, direction, has_match) → action_key
ACTION_MAP: dict[tuple[str, str, bool], Optional[str]] = {
    ("payment", "out", True): "create_bill_payment",
    ("payment", "in", True): "create_receive_payment",
    ("expense", "out", False): "create_expense",
    ("expense", "out", True): "create_bill_payment",
    # Phase 2
    ("payment", "out", False): None,
    ("payment", "in", False): None,
    ("new_bill", "in", False): None,  # "create_bill" when ready
    ("tax", "out", False): None,
    ("tax", "in", False): None,
}


class DocumentActionResolver:
    """Maps SmartMatchResult + OCR data → DirectAction payload."""

    def __init__(self, pool, tenant_id: str):
        self.pool = pool
        self.tenant_id = tenant_id

    async def resolve(self, match_result, ocr_data: dict) -> Optional[ResolvedAction]:
        """
        Main entry. Returns ResolvedAction or None if no action possible.

        Args:
            match_result: SmartMatchResult from DocumentMatcher
            ocr_data: Raw OCR extraction dict (total_amount, vendor_name, etc.)
        """
        has_match = match_result.best_match is not None
        lookup_key = (match_result.doc_category, match_result.direction, has_match)

        # Direction still ambiguous after matcher tried both AR/AP → ask user
        if match_result.direction == "ambiguous":
            return ResolvedAction(
                action_key="",  # no action yet — need direction first
                payload={},
                warnings=["Direction unclear from document"],
                needs_clarification=True,
                clarification_question="Ini pembayaran masuk (dari pelanggan) atau keluar (ke vendor)?",
                clarification_options=[
                    BankOption(
                        id="dir_in",
                        label="Pembayaran Masuk (dari pelanggan)",
                        value="direction:in",
                    ),
                    BankOption(
                        id="dir_out",
                        label="Pembayaran Keluar (ke vendor)",
                        value="direction:out",
                    ),
                ],
            )

        action_key = ACTION_MAP.get(lookup_key)
        if action_key is None:
            # Also try without direction specificity
            for dir_option in ["out", "in"]:
                fallback_key = (match_result.doc_category, dir_option, has_match)
                action_key = ACTION_MAP.get(fallback_key)
                if action_key:
                    break

        if action_key is None:
            logger.info("[DocResolver] No action mapped for %s", lookup_key)
            return None

        # Dispatch to payload builder
        builder = PAYLOAD_BUILDERS.get(action_key)
        if not builder:
            logger.warning("[DocResolver] No payload builder for %s", action_key)
            return None

        return await builder(self, match_result, ocr_data)

    # ─── Payload Builders ───────────────────────────────────────────────

    async def _resolve_bank_fee_account(self):
        """Resolve the 'Biaya Admin Bank' expense CoA (id, name) for transfer admin fees."""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        f"SET LOCAL app.tenant_id = '{self.tenant_id}'"
                    )
                    row = await conn.fetchrow(
                        "SELECT id, name FROM chart_of_accounts "
                        "WHERE tenant_id = $1 AND ("
                        "  account_code = '5-20800' OR name ILIKE '%admin bank%' "
                        "  OR name ILIKE '%administrasi bank%')"
                        " ORDER BY (account_code = '5-20800') DESC, account_code LIMIT 1",
                        self.tenant_id,
                    )
                    if row:
                        return str(row["id"]), row["name"]
        except Exception:
            pass
        return None, None

    async def _build_bill_payment_payload(
        self, match_result, ocr_data: dict
    ) -> ResolvedAction:
        """Build payload for create_bill_payment from matched bill."""
        bm = match_result.best_match
        amount = float(
            ocr_data.get("total_amount") or ocr_data.get("amount") or bm.outstanding
        )
        payment_date = ocr_data.get("document_date") or ocr_data.get("date")

        # Resolve vendor_id from bill record (source_id is pure UUID)
        vendor_id = await self._get_vendor_id_from_bill(bm.source_id) or ""

        # For outgoing payments: match source_account_number (our account that sends money)
        bank_name = (
            ocr_data.get("source_account_number")  # OCR: "Dari rek 1234567890"
            or ocr_data.get("bank_hint")
            or ocr_data.get("bank_source")
            or ocr_data.get("bank_destination")
            or ""
        )
        bank_id, bank_display, bank_candidates = await self._resolve_bank_account(
            bank_name
        )

        warnings = []
        needs_clarification = False
        clarification = ""
        clarification_options = []

        if not bank_id:
            needs_clarification = True
            if bank_candidates:
                clarification = "Transfer ini dari rekening yang mana?"
                clarification_options = [
                    BankOption(id=c["id"], label=c["label"], value=c["id"])
                    for c in bank_candidates
                ]
            else:
                clarification = "Pembayaran ini dari rekening bank mana?"
            warnings.append("Bank account belum teridentifikasi")

        payload = {
            # Hidden required
            "vendor_id": vendor_id,
            "bill_id": bm.source_id,
            "bank_account_id": bank_id or "",
            # Display-only
            "vendor_name": bm.counterparty,
            "bill_number": bm.label,
            "bank_account_name": bank_display or "(pilih rekening)",
            # Hidden non-required (useful context)
            "bill_amount": float(bm.amount),
            "amount_due": float(bm.outstanding),
            # Regular required
            "total_amount": amount,
            "payment_date": payment_date or "",
            "payment_method": "bank_transfer",
        }

        # FIX_TRANSFER_ADMIN_FEE (2026-06-18): outgoing transfer admin fee is OUR
        # cost (money left our bank = nominal + fee). Settle AP by nominal (total_amount
        # above) and book the fee via bank_fee_amount -> the bill-payment journal posts
        # Dr Biaya Admin Bank + Cr Bank = nominal + fee (banksync: bank = actual mutation).
        _adm_fee = float(ocr_data.get("admin_fee") or 0)
        if _adm_fee > 0:
            _fee_id, _fee_name = await self._resolve_bank_fee_account()
            if _fee_id:
                payload["bank_fee_amount"] = int(_adm_fee)
                payload["bank_fee_account_id"] = _fee_id
                payload["bank_fee_account_name"] = _fee_name or "Biaya Admin Bank"

        return ResolvedAction(
            action_key="create_bill_payment",
            payload=payload,
            warnings=warnings,
            needs_clarification=needs_clarification,
            clarification_question=clarification,
            clarification_options=clarification_options,
        )

    async def _build_receive_payment_payload(
        self, match_result, ocr_data: dict
    ) -> ResolvedAction:
        """Build payload for create_receive_payment from matched invoice."""
        bm = match_result.best_match
        amount = float(
            ocr_data.get("total_amount") or ocr_data.get("amount") or bm.outstanding
        )
        payment_date = ocr_data.get("document_date") or ocr_data.get("date")

        # For incoming payments: match destination_account_number (our account that receives money)
        bank_name = (
            ocr_data.get("destination_account_number")  # OCR: "Ke 8295032185"
            or ocr_data.get("bank_hint")
            or ocr_data.get("bank_destination")
            or ocr_data.get("bank_source")
            or ""
        )
        bank_id, bank_display, bank_candidates = await self._resolve_bank_account(
            bank_name
        )

        warnings = []
        needs_clarification = not bank_id
        clarification_options = []
        if needs_clarification and bank_candidates:
            clarification = "Pembayaran ini masuk ke rekening yang mana?"
            clarification_options = [
                BankOption(id=c["id"], label=c["label"], value=c["id"])
                for c in bank_candidates
            ]
        elif needs_clarification:
            clarification = "Pembayaran ini masuk ke rekening mana?"
        else:
            clarification = ""

        payload = {
            "customer_id": "",  # resolved from invoice below
            "bank_account_id": bank_id or "",
            "customer_name": bm.counterparty,
            "invoice_numbers": bm.label,
            "bank_account_name": bank_display or "(pilih rekening)",
            "total_amount": amount,
            "payment_date": payment_date or "",
            "payment_method": "bank_transfer",
            "allocations": [
                {
                    "invoice_id": bm.source_id,
                    "amount_applied": amount,
                }
            ],
        }

        # Resolve customer_id from invoice
        cid = await self._get_customer_id_from_invoice(bm.source_id)
        if cid:
            payload["customer_id"] = cid

        return ResolvedAction(
            action_key="create_receive_payment",
            payload=payload,
            warnings=warnings if needs_clarification else [],
            needs_clarification=needs_clarification,
            clarification_question=clarification,
            clarification_options=clarification_options if needs_clarification else [],
        )

    async def _build_expense_payload(
        self, match_result, ocr_data: dict
    ) -> ResolvedAction:
        """Build payload for create_expense from OCR data + CoA recommendation."""
        amount = float(ocr_data.get("total_amount") or ocr_data.get("amount") or 0)
        expense_date = ocr_data.get("document_date") or ocr_data.get("date") or ""
        # Prefer user caption for description ("servis motor kantor" > OCR notes)
        # Strip common command prefixes so description is clean
        _raw_caption = ocr_data.get("user_caption") or ""
        import re as _re

        _clean_caption = _re.sub(
            r"^(tolong|mohon|bisa|mau|coba)?\s*(dicatat|catat|input|masukkan|record)\s*(dong|ya|in)?[,.]?\s*",
            "",
            _raw_caption,
            flags=_re.IGNORECASE,
        ).strip()
        description = (
            _clean_caption
            or ocr_data.get("notes")
            or ocr_data.get("reference_note")
            or ocr_data.get("document_number")
            or ""
        )

        # Bank/cash account (paid through) — multi-source hint
        bank_name = (
            ocr_data.get("bank_hint")
            or ocr_data.get("source_account_number")
            or ocr_data.get("bank_source")
            or ""
        )
        bank_id, bank_display, bank_candidates = await self._resolve_bank_account(
            bank_name
        )

        # CoA from matcher recommendation
        acct = match_result.account_recommendation
        account_id = acct.account_id if acct else ""
        account_name = f"{acct.account_name} ({acct.account_code})" if acct else ""

        # Default expense_date to today if OCR didn't extract
        if not expense_date or expense_date in ("-", "null", "None", "none"):
            from datetime import date as _date_today

            expense_date = _date_today.today().isoformat()

        needs_clarification = not bank_id or not account_id
        clarification_options = []
        clarification = ""
        if needs_clarification:
            if not bank_id and bank_candidates:
                clarification = "Pembayaran ini dari rekening yang mana?"
                clarification_options = [
                    BankOption(id=c["id"], label=c["label"], value=c["id"])
                    for c in bank_candidates
                ]
            elif not bank_id and not account_id:
                clarification = "Mohon tentukan: rekening pembayaran dan akun biaya"
            elif not bank_id:
                clarification = "Pembayaran ini dari rekening bank mana?"
            else:
                clarification = "Mohon tentukan akun biaya yang sesuai"

        payload = {
            "paid_through_id": bank_id or "",
            "paid_through_name": bank_display or "(pilih rekening)",
            "account_id": account_id,
            "account_name": account_name or "(pilih akun biaya)",
            "amount": amount,
            "expense_date": expense_date,
            "description": description,
            "vendor_name": ocr_data.get("vendor_name")
            or ocr_data.get("counterparty_name")
            or "",
            "reference": ocr_data.get("reference_number")
            or ocr_data.get("document_number")
            or "",
            "notes": _raw_caption if _raw_caption else "",
        }

        # Resolve vendor if name present
        vname = payload["vendor_name"]
        if vname:
            vid = await self._resolve_vendor_by_name(vname)
            if vid:
                payload["vendor_id"] = vid

        return ResolvedAction(
            action_key="create_expense",
            payload=payload,
            warnings=[],
            needs_clarification=needs_clarification,
            clarification_question=clarification,
            clarification_options=clarification_options,
        )

    # ─── Helper Methods ─────────────────────────────────────────────────

    async def _resolve_bank_account(
        self, name_fragment: str
    ) -> tuple[Optional[str], Optional[str], List[dict]]:
        print(f"\n[ResolveBank] CALLED with={name_fragment!r}\n", flush=True)
        """
        Fuzzy match bank account. Returns (id, display_name, candidates) or (None, None, candidates).

        Resolution order:
        1. If name_fragment provided → fuzzy ILIKE match on account_name/bank_name/account_number
           - If exactly 1 match → auto-select
           - If multiple matches → return all as candidates for clarification
        2. If no name → try is_default=true account
        3. If no default → try single active account (auto-select if only 1)
        4. If multiple or zero → return all active as candidates for clarification
        """
        all_candidates = []

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")

                if name_fragment:
                    # Strategy 1: fuzzy match — try name first, then numeric
                    name_frag_clean = name_fragment.strip()
                    # FIX_BANK_MASKED_ACCT (2026-06-18): masked/prefixed receipt
                    # account numbers ("BCA ****2185", "0821****5368", "Ke
                    # 8295032185") are NOT all-digits, so the old gate sent them to
                    # the name-ILIKE branch (which fails) -> "list all" -> a needless
                    # "rekening mana?" pill. Route to the digit matcher whenever >=4
                    # digits are present; it already does substring + ratio + last-4
                    # and only auto-picks on a UNIQUE match (>1 -> candidates, safe).
                    is_numeric = (
                        len("".join(c for c in name_frag_clean if c.isdigit())) >= 4
                    )

                    if is_numeric:
                        # Numeric: bidirectional substring match (handles OCR digit errors)
                        all_active = await conn.fetch(
                            "SELECT id, account_name, bank_name, account_number FROM bank_accounts "
                            "WHERE tenant_id = $1 AND is_active = true AND deleted_at IS NULL "
                            "AND account_number IS NOT NULL "
                            "ORDER BY is_default DESC NULLS LAST, account_name",
                            self.tenant_id,
                        )
                        from difflib import SequenceMatcher as _SM

                        ocr_digits = "".join(c for c in name_frag_clean if c.isdigit())
                        matched_rows = []
                        for r in all_active:
                            db_num = (r["account_number"] or "").strip()
                            db_digits = "".join(c for c in db_num if c.isdigit())
                            if not db_digits:
                                continue
                            # Strategy a: substring match (perfect or contains)
                            if db_digits in ocr_digits or ocr_digits in db_digits:
                                matched_rows.append(r)
                                continue
                            # Strategy b: similarity ratio >= 0.85 (handles OCR digit errors)
                            ratio = _SM(None, db_digits, ocr_digits).ratio()
                            if ratio >= 0.75:
                                matched_rows.append(r)
                                continue
                            # Strategy c: last-4-digits match
                            if len(db_digits) >= 4 and len(ocr_digits) >= 4:
                                if db_digits[-4:] == ocr_digits[-4:]:
                                    matched_rows.append(r)
                        rows = matched_rows
                        print(
                            f"\n[ResolveBank] Numeric match: ocr={ocr_digits!r}, candidates={len(rows)}\n",
                            flush=True,
                        )
                    else:
                        # Text: ILIKE on name fields only
                        rows = await conn.fetch(
                            "SELECT id, account_name, bank_name, account_number FROM bank_accounts "
                            "WHERE tenant_id = $1 AND is_active = true AND deleted_at IS NULL "
                            "AND (account_name ILIKE $2 OR bank_name ILIKE $2) "
                            "ORDER BY is_default DESC NULLS LAST, account_name LIMIT 5",
                            self.tenant_id,
                            f"%{name_frag_clean}%",
                        )

                    if len(rows) == 1:
                        r = rows[0]
                        display = self._format_bank_display(r)
                        print(f"\n[ResolveBank] Matched: {display}\n", flush=True)
                        return str(r["id"]), display, []
                    elif len(rows) > 1:
                        # Multiple matches — return as candidates
                        all_candidates = [self._to_bank_candidate(r) for r in rows]
                        return None, None, all_candidates
                    # else: 0 matches → fall through to all-active list

                # Strategy 2: list all active accounts
                # If exactly 1 → auto-select. If >1 → return as candidates (NO blind default pick)
                rows = await conn.fetch(
                    "SELECT id, account_name, bank_name, account_number FROM bank_accounts "
                    "WHERE tenant_id = $1 AND is_active = true AND deleted_at IS NULL "
                    "ORDER BY is_default DESC NULLS LAST, account_name LIMIT 5",
                    self.tenant_id,
                )
                if len(rows) == 1:
                    r = rows[0]
                    display = self._format_bank_display(r)
                    return str(r["id"]), display, []
                elif len(rows) > 1:
                    all_candidates = [self._to_bank_candidate(r) for r in rows]

        return None, None, all_candidates

    @staticmethod
    def _format_bank_display(row) -> str:
        """Format bank account for display."""
        parts = []
        if row["bank_name"]:
            parts.append(row["bank_name"])
        parts.append(row["account_name"])
        if row.get("account_number"):
            # Mask account number: show last 4 digits
            num = row["account_number"]
            masked = "xxx" + num[-4:] if len(num) > 4 else num
            parts[-1] += f" ({masked})"
        return " - ".join(parts)

    @staticmethod
    def _to_bank_candidate(row) -> dict:
        """Convert a DB row to a bank candidate dict."""
        num = row.get("account_number") or ""
        masked = "xxx" + num[-4:] if len(num) > 4 else num
        label = row["bank_name"] or ""
        if row["account_name"]:
            label += f" - {row['account_name']}" if label else row["account_name"]
        if masked:
            label += f" ({masked})"
        return {
            "id": str(row["id"]),
            "label": label,
        }

    async def _get_vendor_id_from_bill(self, bill_id: str) -> Optional[str]:
        """Get vendor_id from bill record. source_id is always pure UUID."""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                    row = await conn.fetchrow(
                        "SELECT vendor_id FROM bills WHERE id = $1 AND tenant_id = $2",
                        _uuid.UUID(bill_id),
                        self.tenant_id,
                    )
            return str(row["vendor_id"]) if row else None
        except Exception:
            return None

    async def _get_customer_id_from_invoice(self, invoice_id: str) -> Optional[str]:
        """Get customer_id from sales_invoices record."""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                    row = await conn.fetchrow(
                        "SELECT customer_id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                        _uuid.UUID(invoice_id),
                        self.tenant_id,
                    )
            return str(row["customer_id"]) if row else None
        except Exception:
            return None

    async def _resolve_vendor_by_name(self, name: str) -> Optional[str]:
        """Fuzzy match vendor by name."""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL app.tenant_id = '{self.tenant_id}'")
                    row = await conn.fetchrow(
                        "SELECT id FROM vendors WHERE tenant_id = $1 AND name ILIKE $2 LIMIT 1",
                        self.tenant_id,
                        f"%{name}%",
                    )
            return str(row["id"]) if row else None
        except Exception:
            return None


# Builder dispatch table
PAYLOAD_BUILDERS = {
    "create_bill_payment": DocumentActionResolver._build_bill_payment_payload,
    "create_receive_payment": DocumentActionResolver._build_receive_payment_payload,
    "create_expense": DocumentActionResolver._build_expense_payload,
}
