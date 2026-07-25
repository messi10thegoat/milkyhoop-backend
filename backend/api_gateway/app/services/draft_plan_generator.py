"""
Draft Plan Generator
====================
Phase 5: Generate balanced journal drafts from analysis_result.

Input:  analysis_result (Phase 4) + ocr_result + doc_type
Output: draft_plan (DocumentActionPlan dict)

This is STILL read-only from ledger perspective.
draft_plan is saved to uploaded_documents.draft_plan JSONB.
Actual posting happens in Phase 8 (Kernel Execution).

Every draft_plan MUST have:
  1. journal_draft with balanced lines (total_debit == total_credit)
  2. action_type
  3. overall_confidence
  4. requires_user_input list
  5. warnings list

ZERO writes to journal_entries, journal_lines, inventory_ledger, bank_transactions.
"""
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS
# ============================================================================

# Well-known account codes — resolved at runtime via CoA query (Law 27)
# These are CODE patterns, NOT hardcoded UUIDs.
ACCOUNT_CODES = {
    "AP": "2-10100",           # Hutang Usaha (LIABILITY)
    "AR": "1-10400",           # Piutang Usaha (ASSET)
    "INVENTORY": "1-10600",    # Persediaan Barang Dagangan (ASSET)
    "PPN_IN": "1-10800",       # PPN Masukan (ASSET)
    "PPN_OUT": "2-10600",      # PPN Keluaran (LIABILITY)
    "REVENUE": "4-10100",      # Penjualan (REVENUE)
    "COGS": "5-10100",         # HPP - Pembelian Barang (COGS)
    "ADVANCE_VENDOR": "1-10700",   # Biaya Dibayar Dimuka / Uang Muka Vendor
    "ADVANCE_CUSTOMER": "2-10500", # Uang Muka Pelanggan (LIABILITY)
    "DEFAULT_EXPENSE": "5-20900",  # Beban Lain-lain (EXPENSE)
}


def _safe_decimal(value) -> Decimal:
    """Safely convert to Decimal. Returns 0 on failure."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _str_amount(value) -> str:
    """Convert to string for JSONB (Law 25). Quantize to 2 decimal places."""
    if value is None:
        return "0"
    d = _safe_decimal(value)
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ============================================================================
# BALANCE VALIDATION (Law 4)
# ============================================================================


def validate_draft_balance(plan: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Law 4: total_debit MUST equal total_credit.
    Call BEFORE saving draft_plan to DB.
    Returns (is_balanced, error_message).
    """
    journal_draft = plan.get("journal_draft", {})
    lines = journal_draft.get("lines", [])
    if not lines:
        return (False, "Journal draft has no lines.")

    total_debit = sum(_safe_decimal(line.get("debit", "0")) for line in lines)
    total_credit = sum(_safe_decimal(line.get("credit", "0")) for line in lines)

    if total_debit != total_credit:
        return (False, f"Unbalanced: debit={total_debit} credit={total_credit} gap={total_debit - total_credit}")

    if total_debit == Decimal("0"):
        return (False, "Journal draft has zero total.")

    return (True, None)


# ============================================================================
# CONFIDENCE CALCULATION
# ============================================================================


def calculate_overall_confidence(
    analysis_result: Dict[str, Any],
    journal_draft: Optional[Dict[str, Any]],
    requires_user_input: List[str],
    warnings: List[str],
) -> str:
    """
    Weighted average of sub-confidences.

    >=90%: 1-click confirm
    70-89%: confirm or edit
    50-69%: must review
    <50%: manual input required
    """
    scores: List[Tuple[Decimal, Decimal]] = []  # (score, weight)

    # AR/AP match confidence
    ar_ap = analysis_result.get("ar_ap_match", {})
    if ar_ap.get("matched"):
        scores.append((_safe_decimal(ar_ap.get("match_confidence", "0")), Decimal("0.3")))
    else:
        scores.append((Decimal("0.1"), Decimal("0.3")))

    # Inventory match average confidence
    inv_matches = analysis_result.get("inventory_matches", [])
    if inv_matches:
        inv_avg = sum(
            _safe_decimal(m.get("confidence", "0")) for m in inv_matches
        ) / len(inv_matches)
        scores.append((inv_avg, Decimal("0.2")))

    # Account recommendation confidence
    acc_rec = analysis_result.get("account_recommendation", {})
    if acc_rec.get("account_id"):
        scores.append((_safe_decimal(acc_rec.get("confidence", "0")), Decimal("0.3")))
    else:
        scores.append((Decimal("0.2"), Decimal("0.3")))

    # Base journal confidence (if we could generate balanced lines)
    if journal_draft and journal_draft.get("is_balanced"):
        scores.append((Decimal("0.8"), Decimal("0.2")))
    else:
        scores.append((Decimal("0"), Decimal("0.2")))

    # Weighted average
    if scores:
        total_weight = sum(w for _, w in scores)
        if total_weight > 0:
            weighted = sum(s * w for s, w in scores) / total_weight
        else:
            weighted = Decimal("0")
    else:
        weighted = Decimal("0")

    # Penalties
    penalty = Decimal("0")
    penalty += Decimal("0.10") * len(requires_user_input)
    penalty += Decimal("0.05") * len(warnings)

    final = max(Decimal("0"), min(Decimal("1"), weighted - penalty))
    return str(final.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ============================================================================
# MAIN CLASS
# ============================================================================


class DraftPlanGenerator:
    """
    Generates balanced journal drafts from analysis_result.

    Read-only from ledger perspective. All account_ids resolved
    at runtime from chart_of_accounts (Law 27).
    """

    def __init__(self, conn):
        self.conn = conn
        self._account_cache: Dict[str, Optional[Dict]] = {}

    # ==================================================================
    # MAIN ENTRY POINT
    # ==================================================================

    async def generate_plan(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Generate draft_plan based on doc_type.

        Returns: dict to store in uploaded_documents.draft_plan
        """
        self._account_cache.clear()

        generators = {
            "invoice_purchase": self._gen_purchase_invoice,
            "invoice_sales": self._gen_sales_invoice,
            "receipt": self._gen_receipt,
            "bank_transfer_out": self._gen_payment_made,
            "bank_transfer_in": self._gen_payment_received,
            "credit_note": self._gen_credit_debit_note,
            "debit_note": self._gen_credit_debit_note,
            "bank_statement": self._gen_unknown,
            "tax_document": self._gen_unknown,
            "unknown": self._gen_unknown,
        }

        generator = generators.get(doc_type, self._gen_unknown)

        try:
            plan = await generator(
                tenant_id, document_id, ocr_result, doc_type, analysis_result
            )
        except Exception as e:
            logger.error(f"[DraftGen] Generator failed for {document_id}: {e}")
            plan = self._make_fallback_plan(document_id, doc_type, str(e))

        return plan

    # ==================================================================
    # PURCHASE INVOICE GENERATOR
    # ==================================================================

    async def _gen_purchase_invoice(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Dr. Persediaan/Expense (per line)
        Dr. PPN Masukan (if tax)
        Cr. Hutang Usaha (total)
        """
        lines: List[Dict] = []
        inventory_movements: List[Dict] = []
        requires_user_input: List[str] = []
        warnings: List[str] = []
        reasoning: List[str] = []

        # For purchase docs, vendor = document issuer (the seller), not recipient (us)
        counterparty = ocr_result.get("document_issuer") or ocr_result.get("counterparty_name") or "Unknown Vendor"
        doc_number = ocr_result.get("document_number") or ""
        doc_date = ocr_result.get("document_date") or ""
        total_amount = _safe_decimal(ocr_result.get("total_amount"))
        subtotal = _safe_decimal(ocr_result.get("subtotal"))
        tax_amount = _safe_decimal(ocr_result.get("tax_amount"))
        line_items = ocr_result.get("line_items") or []
        inv_matches = analysis_result.get("inventory_matches") or []

        reasoning.append(f"Invoice pembelian dari {counterparty} terdeteksi.")

        # --- Debit lines per item ---
        debit_total = Decimal("0")

        for idx, item in enumerate(line_items):
            item_amount = _safe_decimal(item.get("total_price"))
            unit_price = _safe_decimal(item.get("unit_price"))
            quantity = _safe_decimal(item.get("quantity"))
            calculated = unit_price * quantity
            if item_amount == 0:
                item_amount = calculated
            elif calculated > 0 and item_amount == unit_price and quantity > 1:
                # OCR confused total_price with unit_price — use calculated
                item_amount = calculated
            if item_amount == 0:
                continue

            item_desc = item.get("description") or f"Item {idx + 1}"

            # Check if this item has an inventory match
            match = next(
                (m for m in inv_matches if m.get("line_index") == idx),
                None,
            )

            if match and match.get("match_type") in ("exact", "fuzzy"):
                # Inventory item → Dr. Persediaan
                inv_account = await self._resolve_account(
                    tenant_id, code=ACCOUNT_CODES["INVENTORY"]
                )
                if inv_account:
                    lines.append(self._make_line(
                        inv_account, debit=item_amount, credit=Decimal("0"),
                        memo=f"{item_desc} (inventory)",
                    ))
                else:
                    lines.append(self._make_line(
                        None, debit=item_amount, credit=Decimal("0"),
                        memo=f"{item_desc} (inventory - akun tidak ditemukan)",
                    ))
                    requires_user_input.append(
                        f"Akun Persediaan ({ACCOUNT_CODES['INVENTORY']}) tidak ditemukan."
                    )

                # Inventory movement
                inventory_movements.append({
                    "product_id": match.get("product_id"),
                    "product_name": match.get("product_name") or item_desc,
                    "quantity": str(item.get("quantity") or "1"),
                    "unit_cost": _str_amount(item.get("unit_price")),
                    "direction": "in",
                    "warehouse_id": None,
                    "is_new_product": False,
                    "new_product_suggestion": None,
                })

            elif match and match.get("match_type") == "none" and match.get("suggestion"):
                # New product → Dr. Persediaan + flag new product
                inv_account = await self._resolve_account(
                    tenant_id, code=ACCOUNT_CODES["INVENTORY"]
                )
                if inv_account:
                    lines.append(self._make_line(
                        inv_account, debit=item_amount, credit=Decimal("0"),
                        memo=f"{item_desc} (produk baru)",
                    ))
                else:
                    lines.append(self._make_line(
                        None, debit=item_amount, credit=Decimal("0"),
                        memo=f"{item_desc} (produk baru - akun tidak ditemukan)",
                    ))

                inventory_movements.append({
                    "product_id": None,
                    "product_name": item_desc,
                    "quantity": str(item.get("quantity") or "1"),
                    "unit_cost": _str_amount(item.get("unit_price")),
                    "direction": "in",
                    "warehouse_id": None,
                    "is_new_product": True,
                    "new_product_suggestion": match.get("suggestion"),
                })
                requires_user_input.append(
                    f"Produk '{item_desc}' belum ada — akan dibuat saat posting."
                )
            else:
                # No match or non-inventory → Dr. Expense
                acc_rec = analysis_result.get("account_recommendation", {})
                expense_account = None

                if acc_rec.get("account_id"):
                    expense_account = {
                        "account_id": acc_rec["account_id"],
                        "account_code": acc_rec.get("account_code"),
                        "account_name": acc_rec.get("account_name"),
                    }
                else:
                    expense_account = await self._resolve_account(
                        tenant_id, code=ACCOUNT_CODES["DEFAULT_EXPENSE"]
                    )

                if expense_account:
                    lines.append(self._make_line(
                        expense_account, debit=item_amount, credit=Decimal("0"),
                        memo=item_desc,
                    ))
                else:
                    lines.append(self._make_line(
                        None, debit=item_amount, credit=Decimal("0"),
                        memo=f"{item_desc} (akun belum dipilih)",
                    ))
                    requires_user_input.append(
                        f"Akun beban untuk '{item_desc}' perlu dipilih manual."
                    )

            debit_total += item_amount

        # --- PPN Masukan ---
        if tax_amount > 0:
            ppn_account = await self._resolve_account(
                tenant_id, code=ACCOUNT_CODES["PPN_IN"]
            )
            if ppn_account:
                lines.append(self._make_line(
                    ppn_account, debit=tax_amount, credit=Decimal("0"),
                    memo="PPN Masukan",
                ))
            else:
                lines.append(self._make_line(
                    None, debit=tax_amount, credit=Decimal("0"),
                    memo="PPN Masukan (akun tidak ditemukan)",
                ))
                requires_user_input.append(
                    f"Akun PPN Masukan ({ACCOUNT_CODES['PPN_IN']}) tidak ditemukan."
                )
            debit_total += tax_amount

        # --- Credit: Hutang Usaha ---
        # Use total_amount from OCR if available, otherwise sum debits
        credit_amount = total_amount if total_amount > 0 else debit_total

        # Reconcile: if debit_total != credit_amount, adjust
        # This handles rounding differences between line_items sum and total
        if debit_total != credit_amount and debit_total > 0:
            # Trust total_amount from invoice, adjust last debit line
            diff = credit_amount - debit_total
            if abs(diff) <= Decimal("1000"):
                # Small rounding difference — adjust last item line
                if lines:
                    last_debit = _safe_decimal(lines[-1]["debit"])
                    lines[-1]["debit"] = _str_amount(last_debit + diff)
                    debit_total = credit_amount
            else:
                # Significant difference — add adjustment line
                adj_account = await self._resolve_account(
                    tenant_id, code=ACCOUNT_CODES["DEFAULT_EXPENSE"]
                )
                lines.append(self._make_line(
                    adj_account, debit=diff, credit=Decimal("0"),
                    memo="Penyesuaian selisih",
                ))
                debit_total = credit_amount
                warnings.append(
                    f"Selisih Rp {_str_amount(diff)} antara total item dan total invoice."
                )

        ap_account = await self._resolve_account(
            tenant_id, code=ACCOUNT_CODES["AP"]
        )
        if ap_account:
            lines.append(self._make_line(
                ap_account, debit=Decimal("0"), credit=credit_amount,
                memo=f"AP - {counterparty}",
            ))
        else:
            lines.append(self._make_line(
                None, debit=Decimal("0"), credit=credit_amount,
                memo=f"AP - {counterparty} (akun tidak ditemukan)",
            ))
            requires_user_input.append(
                f"Akun Hutang Usaha ({ACCOUNT_CODES['AP']}) tidak ditemukan."
            )

        if inventory_movements:
            new_count = sum(1 for m in inventory_movements if m.get("is_new_product"))
            if new_count:
                reasoning.append(f"{new_count} produk baru akan dibuat saat posting.")
            reasoning.append(f"{len(inventory_movements)} item inventory terdeteksi.")

        # --- Anomaly warnings ---
        for a in analysis_result.get("anomalies", []):
            warnings.append(a.get("message", ""))

        # --- Build journal_draft ---
        journal_draft = self._build_journal_draft(
            lines=lines,
            description=f"Invoice #{doc_number} - {counterparty}" if doc_number else f"Invoice - {counterparty}",
            journal_date=doc_date,
        )

        # --- Confidence ---
        confidence = calculate_overall_confidence(
            analysis_result, journal_draft, requires_user_input, warnings
        )

        # --- AP matching ---
        matched_to = None
        ar_ap = analysis_result.get("ar_ap_match", {})
        if ar_ap.get("matched"):
            matched_to = {
                "source_id": ar_ap.get("matched_source_id"),
                "source_type": ar_ap.get("matched_source_type"),
                "description": ar_ap.get("matched_description"),
                "outstanding": ar_ap.get("outstanding_amount"),
                "is_partial": ar_ap.get("is_partial_payment", False),
            }

        return {
            "document_id": document_id,
            "action_type": "create_purchase_invoice",
            "overall_confidence": confidence,
            "reasoning": reasoning,
            "journal_draft": journal_draft,
            "inventory_movements": inventory_movements,
            "bank_draft": None,
            "matched_to": matched_to,
            "requires_user_input": requires_user_input,
            "warnings": warnings,
        }

    # ==================================================================
    # SALES INVOICE GENERATOR
    # ==================================================================

    async def _gen_sales_invoice(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Dr. Piutang Usaha (total)
        Cr. Penjualan (subtotal)
        Cr. PPN Keluaran (if tax)
        """
        lines: List[Dict] = []
        inventory_movements: List[Dict] = []
        requires_user_input: List[str] = []
        warnings: List[str] = []
        reasoning: List[str] = []

        counterparty = ocr_result.get("counterparty_name") or "Unknown Customer"
        doc_number = ocr_result.get("document_number") or ""
        doc_date = ocr_result.get("document_date") or ""
        total_amount = _safe_decimal(ocr_result.get("total_amount"))
        subtotal = _safe_decimal(ocr_result.get("subtotal"))
        tax_amount = _safe_decimal(ocr_result.get("tax_amount"))
        line_items = ocr_result.get("line_items") or []
        inv_matches = analysis_result.get("inventory_matches") or []

        reasoning.append(f"Invoice penjualan untuk {counterparty} terdeteksi.")

        # If no subtotal, derive from total - tax
        if subtotal == 0 and total_amount > 0:
            subtotal = total_amount - tax_amount

        # Dr. Piutang Usaha
        ar_account = await self._resolve_account(
            tenant_id, code=ACCOUNT_CODES["AR"]
        )
        if ar_account:
            lines.append(self._make_line(
                ar_account, debit=total_amount, credit=Decimal("0"),
                memo=f"AR - {counterparty}",
            ))
        else:
            lines.append(self._make_line(
                None, debit=total_amount, credit=Decimal("0"),
                memo=f"AR - {counterparty} (akun tidak ditemukan)",
            ))
            requires_user_input.append(
                f"Akun Piutang Usaha ({ACCOUNT_CODES['AR']}) tidak ditemukan."
            )

        # Cr. Penjualan
        rev_account = await self._resolve_account(
            tenant_id, code=ACCOUNT_CODES["REVENUE"]
        )
        if rev_account:
            lines.append(self._make_line(
                rev_account, debit=Decimal("0"), credit=subtotal,
                memo="Penjualan",
            ))
        else:
            lines.append(self._make_line(
                None, debit=Decimal("0"), credit=subtotal,
                memo="Penjualan (akun tidak ditemukan)",
            ))

        # Cr. PPN Keluaran
        if tax_amount > 0:
            ppn_out = await self._resolve_account(
                tenant_id, code=ACCOUNT_CODES["PPN_OUT"]
            )
            if ppn_out:
                lines.append(self._make_line(
                    ppn_out, debit=Decimal("0"), credit=tax_amount,
                    memo="PPN Keluaran",
                ))
            else:
                lines.append(self._make_line(
                    None, debit=Decimal("0"), credit=tax_amount,
                    memo="PPN Keluaran (akun tidak ditemukan)",
                ))

        # Inventory movements (outbound) — COGS handled by Kernel in Phase 8
        for idx, item in enumerate(line_items):
            match = next(
                (m for m in inv_matches if m.get("line_index") == idx),
                None,
            )
            if match and match.get("match_type") in ("exact", "fuzzy") and match.get("product_id"):
                inventory_movements.append({
                    "product_id": match["product_id"],
                    "product_name": match.get("product_name") or item.get("description", ""),
                    "quantity": str(item.get("quantity") or "1"),
                    "unit_cost": _str_amount(match.get("current_avg_cost")),
                    "direction": "out",
                    "warehouse_id": None,
                    "is_new_product": False,
                    "new_product_suggestion": None,
                })

        if inventory_movements:
            reasoning.append(f"{len(inventory_movements)} item inventory keluar.")

        for a in analysis_result.get("anomalies", []):
            warnings.append(a.get("message", ""))

        journal_draft = self._build_journal_draft(
            lines=lines,
            description=f"Invoice #{doc_number} - {counterparty}" if doc_number else f"Invoice - {counterparty}",
            journal_date=doc_date,
        )

        confidence = calculate_overall_confidence(
            analysis_result, journal_draft, requires_user_input, warnings
        )

        return {
            "document_id": document_id,
            "action_type": "create_sales_invoice",
            "overall_confidence": confidence,
            "reasoning": reasoning,
            "journal_draft": journal_draft,
            "inventory_movements": inventory_movements,
            "bank_draft": None,
            "matched_to": None,
            "requires_user_input": requires_user_input,
            "warnings": warnings,
        }

    # ==================================================================
    # RECEIPT / EXPENSE GENERATOR
    # ==================================================================

    async def _gen_receipt(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Dr. Beban (subtotal)
        Dr. PPN Masukan (if tax)
        Cr. Kas/Bank (total)
        """
        lines: List[Dict] = []
        requires_user_input: List[str] = []
        warnings: List[str] = []
        reasoning: List[str] = []

        counterparty = ocr_result.get("counterparty_name") or ""
        doc_number = ocr_result.get("document_number") or ""
        doc_date = ocr_result.get("document_date") or ""
        total_amount = _safe_decimal(ocr_result.get("total_amount"))
        subtotal = _safe_decimal(ocr_result.get("subtotal"))
        tax_amount = _safe_decimal(ocr_result.get("tax_amount"))

        if subtotal == 0 and total_amount > 0:
            subtotal = total_amount - tax_amount

        reasoning.append("Bukti pengeluaran/kwitansi terdeteksi.")

        # Dr. Expense account
        acc_rec = analysis_result.get("account_recommendation", {})
        expense_account = None
        if acc_rec.get("account_id"):
            expense_account = {
                "account_id": acc_rec["account_id"],
                "account_code": acc_rec.get("account_code"),
                "account_name": acc_rec.get("account_name"),
            }
            reasoning.append(
                f"Akun beban: {acc_rec.get('account_code')} "
                f"({acc_rec.get('source', 'auto')})."
            )
        else:
            expense_account = await self._resolve_account(
                tenant_id, code=ACCOUNT_CODES["DEFAULT_EXPENSE"]
            )
            requires_user_input.append(
                "Akun beban tidak bisa ditentukan otomatis — pilih manual."
            )

        if expense_account:
            lines.append(self._make_line(
                expense_account, debit=subtotal, credit=Decimal("0"),
                memo=counterparty or "Beban",
            ))
        else:
            lines.append(self._make_line(
                None, debit=subtotal, credit=Decimal("0"),
                memo=f"Beban (akun belum dipilih)",
            ))

        # Dr. PPN Masukan
        if tax_amount > 0:
            ppn = await self._resolve_account(
                tenant_id, code=ACCOUNT_CODES["PPN_IN"]
            )
            lines.append(self._make_line(
                ppn, debit=tax_amount, credit=Decimal("0"),
                memo="PPN Masukan",
            ))

        # Cr. Kas (default) — user may change to bank
        cash_account = await self._resolve_account(
            tenant_id, code="1-10100"  # Kas
        )
        bank_draft = None
        if cash_account:
            lines.append(self._make_line(
                cash_account, debit=Decimal("0"), credit=total_amount,
                memo=f"Pembayaran - {counterparty}" if counterparty else "Pembayaran",
            ))
        else:
            # Try first bank account
            bank_acc = await self._resolve_first_bank(tenant_id)
            if bank_acc:
                lines.append(self._make_line(
                    bank_acc["coa"], debit=Decimal("0"), credit=total_amount,
                    memo=f"Pembayaran - {counterparty}" if counterparty else "Pembayaran",
                ))
                bank_draft = {
                    "bank_account_id": bank_acc["bank_id"],
                    "amount": _str_amount(total_amount),
                    "transaction_type": "DEBIT",
                    "description": f"Pembayaran {doc_number} - {counterparty}",
                }
            else:
                lines.append(self._make_line(
                    None, debit=Decimal("0"), credit=total_amount,
                    memo="Pembayaran (akun kas/bank tidak ditemukan)",
                ))
                requires_user_input.append("Pilih akun Kas atau Bank untuk pembayaran.")

        # DOCUMENT_INTAKE guard: Add balance warning to draft plan
        credit_account_id = None
        if cash_account:
            credit_account_id = cash_account.get("account_id")
        elif bank_draft:
            # bank_draft exists means bank_acc was resolved; get coa_id from DB
            try:
                credit_account_id = await self.conn.fetchval(
                    "SELECT coa_id::text FROM bank_accounts WHERE id = $1::uuid AND tenant_id = $2",
                    bank_draft["bank_account_id"], tenant_id,
                )
            except Exception:
                pass

        if credit_account_id and total_amount > 0:
            try:
                kas_balance = await self.conn.fetchval("""
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    WHERE jl.account_id = $1::uuid
                      AND je.status = 'POSTED'
                      AND je.tenant_id = $2
                """, credit_account_id, tenant_id)
                if kas_balance is not None and Decimal(str(kas_balance)) < total_amount:
                    warnings.append(
                        f"Saldo Kas (Rp {kas_balance:,.0f}) tidak cukup untuk pembayaran Rp {total_amount:,.0f}. "
                        "Pertimbangkan membuat sebagai tagihan (AP) agar pembayaran dilakukan nanti."
                    )
            except Exception:
                pass  # Non-blocking - draft can still be generated

        for a in analysis_result.get("anomalies", []):
            warnings.append(a.get("message", ""))

        journal_draft = self._build_journal_draft(
            lines=lines,
            description=f"Beban {doc_number} - {counterparty}" if doc_number else f"Beban - {counterparty or 'Umum'}",
            journal_date=doc_date,
        )

        confidence = calculate_overall_confidence(
            analysis_result, journal_draft, requires_user_input, warnings
        )

        return {
            "document_id": document_id,
            "action_type": "record_expense",
            "overall_confidence": confidence,
            "reasoning": reasoning,
            "journal_draft": journal_draft,
            "inventory_movements": [],
            "bank_draft": bank_draft,
            "matched_to": None,
            "requires_user_input": requires_user_input,
            "warnings": warnings,
        }

    # ==================================================================
    # PAYMENT MADE (Bank Transfer Out → AP Settlement)
    # ==================================================================

    async def _gen_payment_made(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Dr. Hutang Usaha (matched AP amount)
        Cr. Bank (transfer amount)
        """
        lines: List[Dict] = []
        requires_user_input: List[str] = []
        warnings: List[str] = []
        reasoning: List[str] = []

        counterparty = ocr_result.get("counterparty_name") or ""
        doc_number = ocr_result.get("document_number") or ocr_result.get("reference_number") or ""
        doc_date = ocr_result.get("document_date") or ""
        total_amount = _safe_decimal(ocr_result.get("total_amount"))
        bank_name_ocr = ocr_result.get("bank_name") or ""

        reasoning.append(f"Transfer keluar ke {counterparty} terdeteksi.")

        ar_ap = analysis_result.get("ar_ap_match", {})
        matched_to = None

        if ar_ap.get("matched"):
            outstanding = _safe_decimal(ar_ap.get("outstanding_amount", "0"))
            pay_amount = min(total_amount, outstanding) if outstanding > 0 else total_amount

            matched_to = {
                "source_id": ar_ap.get("matched_source_id"),
                "source_type": ar_ap.get("matched_source_type"),
                "description": ar_ap.get("matched_description"),
                "outstanding": _str_amount(outstanding),
                "is_partial": total_amount < outstanding,
            }

            # Dr. AP
            ap_account = await self._resolve_account(
                tenant_id, code=ACCOUNT_CODES["AP"]
            )
            lines.append(self._make_line(
                ap_account, debit=pay_amount, credit=Decimal("0"),
                memo=f"Pelunasan AP - {counterparty}",
            ))

            # Handle overpayment
            if total_amount > outstanding and outstanding > 0:
                overpay = total_amount - outstanding
                adv_account = await self._resolve_account(
                    tenant_id, code=ACCOUNT_CODES["ADVANCE_VENDOR"]
                )
                lines.append(self._make_line(
                    adv_account, debit=overpay, credit=Decimal("0"),
                    memo=f"Kelebihan bayar - {counterparty}",
                ))
                requires_user_input.append(
                    f"Kelebihan bayar Rp {_str_amount(overpay)}. "
                    f"Dicatat sebagai uang muka vendor?"
                )
                reasoning.append(f"Kelebihan bayar Rp {_str_amount(overpay)} terdeteksi.")

        else:
            # No AP match — generic payment
            ap_account = await self._resolve_account(
                tenant_id, code=ACCOUNT_CODES["AP"]
            )
            lines.append(self._make_line(
                ap_account, debit=total_amount, credit=Decimal("0"),
                memo=f"Pembayaran - {counterparty}",
            ))
            requires_user_input.append(
                "Tidak ada hutang yang cocok. Pilih tagihan yang dibayar."
            )

        # Cr. Bank — try to match bank from OCR
        bank_acc = await self._resolve_bank_by_name(tenant_id, bank_name_ocr)
        if not bank_acc:
            bank_acc = await self._resolve_first_bank(tenant_id)

        bank_draft = None
        if bank_acc:
            lines.append(self._make_line(
                bank_acc["coa"], debit=Decimal("0"), credit=total_amount,
                memo=f"Transfer ke {counterparty}",
            ))
            bank_draft = {
                "bank_account_id": bank_acc["bank_id"],
                "amount": _str_amount(total_amount),
                "transaction_type": "DEBIT",
                "description": f"Pembayaran {doc_number} - {counterparty}",
            }
        else:
            lines.append(self._make_line(
                None, debit=Decimal("0"), credit=total_amount,
                memo="Transfer (akun bank tidak ditemukan)",
            ))
            requires_user_input.append("Pilih akun bank untuk transfer.")

        for a in analysis_result.get("anomalies", []):
            warnings.append(a.get("message", ""))

        journal_draft = self._build_journal_draft(
            lines=lines,
            description=f"Pembayaran {doc_number} - {counterparty}" if doc_number else f"Pembayaran - {counterparty}",
            journal_date=doc_date,
        )

        confidence = calculate_overall_confidence(
            analysis_result, journal_draft, requires_user_input, warnings
        )

        return {
            "document_id": document_id,
            "action_type": "record_payment_made",
            "overall_confidence": confidence,
            "reasoning": reasoning,
            "journal_draft": journal_draft,
            "inventory_movements": [],
            "bank_draft": bank_draft,
            "matched_to": matched_to,
            "requires_user_input": requires_user_input,
            "warnings": warnings,
        }

    # ==================================================================
    # PAYMENT RECEIVED (Bank Transfer In → AR Settlement)
    # ==================================================================

    async def _gen_payment_received(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Dr. Bank (transfer amount)
        Cr. Piutang Usaha (matched AR amount)
        """
        lines: List[Dict] = []
        requires_user_input: List[str] = []
        warnings: List[str] = []
        reasoning: List[str] = []

        counterparty = ocr_result.get("counterparty_name") or ""
        doc_number = ocr_result.get("document_number") or ocr_result.get("reference_number") or ""
        doc_date = ocr_result.get("document_date") or ""
        total_amount = _safe_decimal(ocr_result.get("total_amount"))
        bank_name_ocr = ocr_result.get("bank_name") or ""

        reasoning.append(f"Penerimaan dari {counterparty} terdeteksi.")

        # Dr. Bank
        bank_acc = await self._resolve_bank_by_name(tenant_id, bank_name_ocr)
        if not bank_acc:
            bank_acc = await self._resolve_first_bank(tenant_id)

        bank_draft = None
        if bank_acc:
            lines.append(self._make_line(
                bank_acc["coa"], debit=total_amount, credit=Decimal("0"),
                memo=f"Penerimaan dari {counterparty}",
            ))
            bank_draft = {
                "bank_account_id": bank_acc["bank_id"],
                "amount": _str_amount(total_amount),
                "transaction_type": "CREDIT",
                "description": f"Penerimaan {doc_number} - {counterparty}",
            }
        else:
            lines.append(self._make_line(
                None, debit=total_amount, credit=Decimal("0"),
                memo="Penerimaan (akun bank tidak ditemukan)",
            ))
            requires_user_input.append("Pilih akun bank untuk penerimaan.")

        # Cr. Piutang Usaha
        ar_ap = analysis_result.get("ar_ap_match", {})
        matched_to = None

        if ar_ap.get("matched"):
            outstanding = _safe_decimal(ar_ap.get("outstanding_amount", "0"))
            matched_to = {
                "source_id": ar_ap.get("matched_source_id"),
                "source_type": ar_ap.get("matched_source_type"),
                "description": ar_ap.get("matched_description"),
                "outstanding": _str_amount(outstanding),
                "is_partial": total_amount < outstanding,
            }

        ar_account = await self._resolve_account(
            tenant_id, code=ACCOUNT_CODES["AR"]
        )
        lines.append(self._make_line(
            ar_account, debit=Decimal("0"), credit=total_amount,
            memo=f"Pelunasan AR - {counterparty}",
        ))

        if not ar_ap.get("matched"):
            requires_user_input.append(
                "Tidak ada piutang yang cocok. Pilih invoice yang dilunasi."
            )

        for a in analysis_result.get("anomalies", []):
            warnings.append(a.get("message", ""))

        journal_draft = self._build_journal_draft(
            lines=lines,
            description=f"Penerimaan {doc_number} - {counterparty}" if doc_number else f"Penerimaan - {counterparty}",
            journal_date=doc_date,
        )

        confidence = calculate_overall_confidence(
            analysis_result, journal_draft, requires_user_input, warnings
        )

        return {
            "document_id": document_id,
            "action_type": "record_payment_received",
            "overall_confidence": confidence,
            "reasoning": reasoning,
            "journal_draft": journal_draft,
            "inventory_movements": [],
            "bank_draft": bank_draft,
            "matched_to": matched_to,
            "requires_user_input": requires_user_input,
            "warnings": warnings,
        }

    # ==================================================================
    # CREDIT NOTE / DEBIT NOTE
    # ==================================================================

    async def _gen_credit_debit_note(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Placeholder — requires manual review."""
        return {
            "document_id": document_id,
            "action_type": "unknown",
            "overall_confidence": "0",
            "reasoning": [
                f"{doc_type.replace('_', ' ').title()} terdeteksi.",
                "Nota kredit/debit perlu di-review manual.",
            ],
            "journal_draft": None,
            "inventory_movements": [],
            "bank_draft": None,
            "matched_to": None,
            "requires_user_input": [
                f"{doc_type.replace('_', ' ').title()} perlu di-review manual. "
                "Pilih transaksi asli yang terkait."
            ],
            "warnings": [a.get("message", "") for a in analysis_result.get("anomalies", [])],
        }

    # ==================================================================
    # UNKNOWN / FALLBACK
    # ==================================================================

    async def _gen_unknown(
        self,
        tenant_id: str,
        document_id: str,
        ocr_result: Dict[str, Any],
        doc_type: str,
        analysis_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """No journal draft for unknown documents."""
        return {
            "document_id": document_id,
            "action_type": "unknown",
            "overall_confidence": "0",
            "reasoning": [
                "Tipe dokumen tidak terdeteksi atau tidak didukung.",
                "Pilih tipe dokumen secara manual.",
            ],
            "journal_draft": None,
            "inventory_movements": [],
            "bank_draft": None,
            "matched_to": None,
            "requires_user_input": [
                "Tipe dokumen tidak terdeteksi. Pilih tipe dokumen secara manual."
            ],
            "warnings": [a.get("message", "") for a in analysis_result.get("anomalies", [])],
        }

    def _make_fallback_plan(
        self, document_id: str, doc_type: str, error: str
    ) -> Dict[str, Any]:
        """Emergency fallback when generator throws."""
        return {
            "document_id": document_id,
            "action_type": "unknown",
            "overall_confidence": "0",
            "reasoning": [f"Draft generation gagal: {error[:200]}"],
            "journal_draft": None,
            "inventory_movements": [],
            "bank_draft": None,
            "matched_to": None,
            "requires_user_input": [
                "Draft tidak bisa dibuat otomatis. Input manual diperlukan."
            ],
            "warnings": [f"Error: {error[:200]}"],
        }

    # ==================================================================
    # HELPERS
    # ==================================================================

    def _make_line(
        self,
        account: Optional[Dict],
        debit: Decimal,
        credit: Decimal,
        memo: str = "",
    ) -> Dict[str, Any]:
        """Create a journal line dict."""
        return {
            "account_id": account["account_id"] if account else None,
            "account_code": account["account_code"] if account else None,
            "account_name": account["account_name"] if account else None,
            "debit": _str_amount(debit),
            "credit": _str_amount(credit),
            "memo": memo,
        }

    def _build_journal_draft(
        self,
        lines: List[Dict],
        description: str,
        journal_date: str,
    ) -> Dict[str, Any]:
        """Build journal_draft with balance check."""
        total_debit = sum(_safe_decimal(l["debit"]) for l in lines)
        total_credit = sum(_safe_decimal(l["credit"]) for l in lines)

        return {
            "description": description,
            "journal_date": journal_date or "",
            "source_type": "DOCUMENT_INTELLIGENCE",
            "lines": lines,
            "total_debit": _str_amount(total_debit),
            "total_credit": _str_amount(total_credit),
            "is_balanced": total_debit == total_credit and total_debit > 0,
        }

    async def _resolve_account(
        self,
        tenant_id: str,
        code: Optional[str] = None,
        name_pattern: Optional[str] = None,
        account_type: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """
        Resolve account from chart_of_accounts (Law 27).
        Priority: code → name_pattern → account_type → None
        """
        cache_key = f"{tenant_id}:{code}:{name_pattern}:{account_type}"
        if cache_key in self._account_cache:
            return self._account_cache[cache_key]

        row = None

        if code:
            row = await self.conn.fetchrow(
                """
                SELECT id::text AS account_id, account_code, name AS account_name
                FROM chart_of_accounts
                WHERE tenant_id = $1 AND account_code = $2
                  AND is_active = true AND is_header = false
                LIMIT 1
                """,
                tenant_id,
                code,
            )

        if not row and name_pattern:
            row = await self.conn.fetchrow(
                """
                SELECT id::text AS account_id, account_code, name AS account_name
                FROM chart_of_accounts
                WHERE tenant_id = $1
                  AND name ILIKE '%' || $2 || '%'
                  AND is_active = true AND is_header = false
                ORDER BY account_code
                LIMIT 1
                """,
                tenant_id,
                name_pattern,
            )

        if not row and account_type:
            row = await self.conn.fetchrow(
                """
                SELECT id::text AS account_id, account_code, name AS account_name
                FROM chart_of_accounts
                WHERE tenant_id = $1 AND account_type = $2
                  AND is_active = true AND is_header = false
                ORDER BY account_code
                LIMIT 1
                """,
                tenant_id,
                account_type,
            )

        result = dict(row) if row else None
        self._account_cache[cache_key] = result
        return result

    async def _resolve_bank_by_name(
        self, tenant_id: str, bank_name: str
    ) -> Optional[Dict]:
        """Match bank account by name similarity."""
        if not bank_name:
            return None

        row = await self.conn.fetchrow(
            """
            SELECT
                ba.id::text AS bank_id,
                ba.account_name,
                ba.bank_name,
                coa.id::text AS coa_id,
                coa.account_code,
                coa.name AS coa_name
            FROM bank_accounts ba
            JOIN chart_of_accounts coa ON coa.id = ba.coa_id
            WHERE ba.tenant_id = $1
              AND ba.is_active = true
              AND (
                  ba.bank_name ILIKE '%' || $2 || '%'
                  OR ba.account_name ILIKE '%' || $2 || '%'
              )
            LIMIT 1
            """,
            tenant_id,
            bank_name,
        )

        if row:
            return {
                "bank_id": row["bank_id"],
                "coa": {
                    "account_id": row["coa_id"],
                    "account_code": row["account_code"],
                    "account_name": row["coa_name"],
                },
            }
        return None

    async def _resolve_first_bank(
        self, tenant_id: str
    ) -> Optional[Dict]:
        """Get first active bank account as fallback."""
        cache_key = f"{tenant_id}:first_bank"
        if cache_key in self._account_cache:
            return self._account_cache[cache_key]

        row = await self.conn.fetchrow(
            """
            SELECT
                ba.id::text AS bank_id,
                ba.account_name,
                coa.id::text AS coa_id,
                coa.account_code,
                coa.name AS coa_name
            FROM bank_accounts ba
            JOIN chart_of_accounts coa ON coa.id = ba.coa_id
            WHERE ba.tenant_id = $1
              AND ba.is_active = true
            ORDER BY ba.is_default DESC, ba.created_at ASC
            LIMIT 1
            """,
            tenant_id,
        )

        result = None
        if row:
            result = {
                "bank_id": row["bank_id"],
                "coa": {
                    "account_id": row["coa_id"],
                    "account_code": row["account_code"],
                    "account_name": row["coa_name"],
                },
            }
        self._account_cache[cache_key] = result
        return result
