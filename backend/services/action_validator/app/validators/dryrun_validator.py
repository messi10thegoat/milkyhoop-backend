"""
Layer 5: DRY_RUN
Generates journal entry previews based on action type.
Verifies debit == credit and calculates impact summary.
"""
import logging
from typing import Dict, List, Tuple

from .base import BaseValidator, ValidationContext

logger = logging.getLogger(__name__)

# ActionType enum values
ACTION_TYPE_CREATE_SALES_INVOICE = 10
ACTION_TYPE_CREATE_PURCHASE_INVOICE = 11
ACTION_TYPE_CREATE_EXPENSE = 12
ACTION_TYPE_RECEIVE_PAYMENT = 20
ACTION_TYPE_MAKE_PAYMENT = 21
ACTION_TYPE_POST_GENERAL_JOURNAL = 30
ACTION_TYPE_REVERSE_JOURNAL = 31
ACTION_TYPE_CLOSE_PERIOD = 32
ACTION_TYPE_REOPEN_PERIOD = 33
ACTION_TYPE_CREATE_CREDIT_NOTE = 40
ACTION_TYPE_BANK_TRANSFER = 22
ACTION_TYPE_CREATE_PURCHASE_ORDER = 14

# Master data action types - no journal entries generated
ACTION_TYPE_CREATE_CUSTOMER = 0
ACTION_TYPE_UPDATE_CUSTOMER = 1
ACTION_TYPE_CREATE_VENDOR = 2
ACTION_TYPE_CREATE_PRODUCT = 3
MASTER_DATA_ACTIONS = {
    ACTION_TYPE_CREATE_CUSTOMER,
    ACTION_TYPE_UPDATE_CUSTOMER,
    ACTION_TYPE_CREATE_VENDOR,
    ACTION_TYPE_CREATE_PRODUCT,
    ACTION_TYPE_CREATE_PURCHASE_ORDER,  # PO does not generate journal entries
    ACTION_TYPE_REVERSE_JOURNAL,  # No preview needed, just reverse
    ACTION_TYPE_CLOSE_PERIOD,     # Auto-generates closing entries
    ACTION_TYPE_REOPEN_PERIOD,    # No journal
}

# Default account codes for Indonesian accounting
ACCOUNTS = {
    "persediaan_beban": ("5-1100", "Beban Pokok / Persediaan"),
    "hutang_usaha": ("2-1100", "Hutang Usaha"),
    "ppn_masukan": ("1-10800", "PPN Masukan"),
    "piutang_usaha": ("1-1300", "Piutang Usaha"),
    "pendapatan": ("4-1100", "Pendapatan Penjualan"),
    "ppn_keluaran": ("2-1700", "PPN Keluaran"),
    "kas_bank": ("1-1100", "Kas / Bank"),
    "beban_umum": ("5-2100", "Beban Umum & Administrasi"),
}


def _safe_float(value, default: float = 0.0) -> float:
    """Safely convert a value to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _calculate_items_total(items: list) -> Tuple[float, float, float]:
    """Calculate subtotal, tax, and grand_total from line items."""
    subtotal = 0.0
    total_tax = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = _safe_float(item.get("quantity") or item.get("qty"), 1.0)
        price = _safe_float(item.get("unit_price") or item.get("price"), 0.0)
        line_total = qty * price
        subtotal += line_total
        tax = _safe_float(item.get("tax_amount") or item.get("ppn"), 0.0)
        total_tax += tax
    return subtotal, total_tax, subtotal + total_tax


class DryRunValidator(BaseValidator):
    """Layer 5: Generate journal preview and verify balance."""

    async def validate(self, ctx: ValidationContext) -> None:
        logger.debug("Running DRY_RUN validation")

        # Master data actions don't generate journal entries
        if ctx.action_type in MASTER_DATA_ACTIONS:
            logger.info(f"Skipping dry-run for master data action: {ctx.action_type}")
            return

        payload = ctx.payload
        entries: List[Dict] = []

        if ctx.action_type == ACTION_TYPE_CREATE_PURCHASE_INVOICE:
            entries = self._purchase_invoice_entries(payload)
        elif ctx.action_type == ACTION_TYPE_CREATE_SALES_INVOICE:
            entries = self._sales_invoice_entries(payload)
        elif ctx.action_type == ACTION_TYPE_CREATE_EXPENSE:
            entries = self._expense_entries(payload)
        elif ctx.action_type == ACTION_TYPE_RECEIVE_PAYMENT:
            entries = self._receive_payment_entries(payload)
        elif ctx.action_type == ACTION_TYPE_MAKE_PAYMENT:
            entries = self._make_payment_entries(payload)
        elif ctx.action_type == ACTION_TYPE_POST_GENERAL_JOURNAL:
            entries = self._general_journal_entries(payload)
        elif ctx.action_type == ACTION_TYPE_CREATE_CREDIT_NOTE:
            entries = self._credit_note_entries(payload)
        elif ctx.action_type == ACTION_TYPE_BANK_TRANSFER:
            entries = self._bank_transfer_entries(payload)
        else:
            # For non-journal action types, skip dry run
            ctx.add_warning(
                layer="DRY_RUN",
                code="NO_JOURNAL_PREVIEW",
                message=f"No journal preview available for action_type={ctx.action_type}",
            )
            return

        # Calculate totals
        total_debit = sum(e.get("debit", 0.0) for e in entries)
        total_credit = sum(e.get("credit", 0.0) for e in entries)
        balanced = abs(total_debit - total_credit) < 0.01  # Allow 1 cent tolerance

        if not balanced:
            ctx.add_error(
                layer="DRY_RUN",
                code="UNBALANCED_JOURNAL",
                message=f"Journal is not balanced: debit={total_debit:.2f}, credit={total_credit:.2f}, diff={abs(total_debit - total_credit):.2f}",
                blocking=True,
            )

        # Store results in context
        ctx.journal_entries = entries
        ctx.total_debit = total_debit
        ctx.total_credit = total_credit
        ctx.balanced = balanced
        ctx.impact_summary = {
            "total_amount": f"{total_debit:.2f}",
            "entry_count": str(len(entries)),
            "action_type": str(ctx.action_type),
        }

        logger.debug(f"DRY_RUN: {len(entries)} entries, debit={total_debit:.2f}, credit={total_credit:.2f}, balanced={balanced}")

    def _bank_transfer_entries(self, payload: dict) -> List[Dict]:
        """
        BANK_TRANSFER journal:
          DR Bank Tujuan (destination)    amount
          CR Bank Asal (source)           amount
        """
        amount = _safe_float(payload.get("amount"))
        from_bank = payload.get("from_bank_name") or payload.get("from_bank_id") or "Bank Asal"
        to_bank = payload.get("to_bank_name") or payload.get("to_bank_id") or "Bank Tujuan"

        return [
            {
                "account_code": str(to_bank),
                "account_name": f"Bank Tujuan ({to_bank})",
                "debit": amount,
                "credit": 0.0,
                "description": "Transfer masuk",
            },
            {
                "account_code": str(from_bank),
                "account_name": f"Bank Asal ({from_bank})",
                "debit": 0.0,
                "credit": amount,
                "description": "Transfer keluar",
            },
        ]

    def _purchase_invoice_entries(self, payload: dict) -> List[Dict]:
        """
        CREATE_PURCHASE_INVOICE journal:
        Without tax:
          DR 5-1100 (Persediaan/Beban)    amount
          CR 2-1100 (Hutang Usaha)        amount
        With tax:
          DR 5-1100 (Persediaan/Beban)    dpp
          DR 1-1700 (PPN Masukan)         ppn
          CR 2-1100 (Hutang Usaha)        total
        """
        items = payload.get("items") or payload.get("line_items") or []
        amount = _safe_float(payload.get("amount") or payload.get("total") or payload.get("grand_total"))
        tax_amount = _safe_float(payload.get("tax_amount") or payload.get("ppn"))

        if items:
            subtotal, tax_from_items, grand_total = _calculate_items_total(items)
            if amount == 0:
                amount = grand_total
            if tax_amount == 0:
                tax_amount = tax_from_items

        dpp = amount - tax_amount if tax_amount > 0 else amount
        entries = []

        # Debit: Persediaan / Beban
        acc_code = payload.get("expense_account") or ACCOUNTS["persediaan_beban"][0]
        acc_name = ACCOUNTS["persediaan_beban"][1]
        entries.append({
            "account_code": acc_code,
            "account_name": acc_name,
            "debit": dpp,
            "credit": 0.0,
            "description": "Pembelian barang/jasa",
        })

        # Debit: PPN Masukan (if tax)
        if tax_amount > 0:
            entries.append({
                "account_code": ACCOUNTS["ppn_masukan"][0],
                "account_name": ACCOUNTS["ppn_masukan"][1],
                "debit": tax_amount,
                "credit": 0.0,
                "description": "PPN Masukan",
            })

        # Credit: Hutang Usaha
        entries.append({
            "account_code": ACCOUNTS["hutang_usaha"][0],
            "account_name": ACCOUNTS["hutang_usaha"][1],
            "debit": 0.0,
            "credit": amount if tax_amount > 0 else dpp,
            "description": "Hutang atas pembelian",
        })

        return entries

    def _sales_invoice_entries(self, payload: dict) -> List[Dict]:
        """
        CREATE_SALES_INVOICE journal:
          DR 1-1300 (Piutang Usaha)       total
          CR 4-1100 (Pendapatan)           dpp
          CR 2-1700 (PPN Keluaran)         ppn (if applicable)
        """
        items = payload.get("items") or payload.get("line_items") or []
        amount = _safe_float(payload.get("amount") or payload.get("total") or payload.get("grand_total"))
        tax_amount = _safe_float(payload.get("tax_amount") or payload.get("ppn"))

        if items:
            subtotal, tax_from_items, grand_total = _calculate_items_total(items)
            if amount == 0:
                amount = grand_total
            if tax_amount == 0:
                tax_amount = tax_from_items

        dpp = amount - tax_amount if tax_amount > 0 else amount
        entries = []

        # Debit: Piutang Usaha
        entries.append({
            "account_code": ACCOUNTS["piutang_usaha"][0],
            "account_name": ACCOUNTS["piutang_usaha"][1],
            "debit": amount if tax_amount > 0 else dpp,
            "credit": 0.0,
            "description": "Piutang atas penjualan",
        })

        # Credit: Pendapatan
        entries.append({
            "account_code": ACCOUNTS["pendapatan"][0],
            "account_name": ACCOUNTS["pendapatan"][1],
            "debit": 0.0,
            "credit": dpp,
            "description": "Pendapatan penjualan",
        })

        # Credit: PPN Keluaran (if tax)
        if tax_amount > 0:
            entries.append({
                "account_code": ACCOUNTS["ppn_keluaran"][0],
                "account_name": ACCOUNTS["ppn_keluaran"][1],
                "debit": 0.0,
                "credit": tax_amount,
                "description": "PPN Keluaran",
            })

        return entries

    def _expense_entries(self, payload: dict) -> List[Dict]:
        """
        CREATE_EXPENSE journal:
          DR {expense_account}    amount
          CR 1-1100 (Kas/Bank)    amount
        """
        amount = _safe_float(payload.get("amount") or payload.get("total"))
        acc_code = payload.get("account_code") or payload.get("expense_account") or ACCOUNTS["beban_umum"][0]
        acc_name = payload.get("account_name") or ACCOUNTS["beban_umum"][1]

        return [
            {
                "account_code": acc_code,
                "account_name": acc_name,
                "debit": amount,
                "credit": 0.0,
                "description": payload.get("description") or "Beban operasional",
            },
            {
                "account_code": ACCOUNTS["kas_bank"][0],
                "account_name": ACCOUNTS["kas_bank"][1],
                "debit": 0.0,
                "credit": amount,
                "description": "Pembayaran kas/bank",
            },
        ]

    def _receive_payment_entries(self, payload: dict) -> List[Dict]:
        """
        RECEIVE_PAYMENT journal:
          DR 1-1100 (Kas/Bank)         amount
          CR 1-1300 (Piutang Usaha)    amount
        """
        amount = _safe_float(payload.get("amount"))
        return [
            {
                "account_code": ACCOUNTS["kas_bank"][0],
                "account_name": ACCOUNTS["kas_bank"][1],
                "debit": amount,
                "credit": 0.0,
                "description": "Penerimaan pembayaran",
            },
            {
                "account_code": ACCOUNTS["piutang_usaha"][0],
                "account_name": ACCOUNTS["piutang_usaha"][1],
                "debit": 0.0,
                "credit": amount,
                "description": "Pelunasan piutang",
            },
        ]

    def _make_payment_entries(self, payload: dict) -> List[Dict]:
        """
        MAKE_PAYMENT journal:
          DR 2-1100 (Hutang Usaha)    amount
          CR 1-1100 (Kas/Bank)        amount
        """
        amount = _safe_float(payload.get("amount"))
        return [
            {
                "account_code": ACCOUNTS["hutang_usaha"][0],
                "account_name": ACCOUNTS["hutang_usaha"][1],
                "debit": amount,
                "credit": 0.0,
                "description": "Pembayaran hutang",
            },
            {
                "account_code": ACCOUNTS["kas_bank"][0],
                "account_name": ACCOUNTS["kas_bank"][1],
                "debit": 0.0,
                "credit": amount,
                "description": "Pengeluaran kas/bank",
            },
        ]

    def _general_journal_entries(self, payload: dict) -> List[Dict]:
        """POST_GENERAL_JOURNAL: use entries directly from payload."""
        raw_entries = payload.get("entries") or payload.get("journal_entries") or payload.get("line_items") or []
        entries = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            entries.append({
                "account_code": str(entry.get("account_code") or entry.get("account", "")),
                "account_name": str(entry.get("account_name") or entry.get("name", "")),
                "debit": _safe_float(entry.get("debit")),
                "credit": _safe_float(entry.get("credit")),
                "description": str(entry.get("description") or entry.get("memo", "")),
            })
        return entries

    def _credit_note_entries(self, payload: dict) -> List[Dict]:
        """
        CREATE_CREDIT_NOTE journal (reverses sales invoice):
          DR 4-1100 (Pendapatan)           dpp
          DR 2-1700 (PPN Keluaran)         ppn (if applicable)
          CR 1-1300 (Piutang Usaha)        total
        """
        items = payload.get("items") or payload.get("line_items") or []
        amount = _safe_float(payload.get("amount") or payload.get("total") or payload.get("grand_total"))
        tax_amount = _safe_float(payload.get("tax_amount") or payload.get("ppn"))

        if items:
            subtotal, tax_from_items, grand_total = _calculate_items_total(items)
            if amount == 0:
                amount = grand_total
            if tax_amount == 0:
                tax_amount = tax_from_items

        dpp = amount - tax_amount if tax_amount > 0 else amount
        entries = []

        # Debit: Pendapatan (reverse the sale)
        entries.append({
            "account_code": ACCOUNTS["pendapatan"][0],
            "account_name": ACCOUNTS["pendapatan"][1],
            "debit": dpp,
            "credit": 0.0,
            "description": "Retur penjualan / nota kredit",
        })

        # Debit: PPN Keluaran (if tax, reverse it)
        if tax_amount > 0:
            entries.append({
                "account_code": ACCOUNTS["ppn_keluaran"][0],
                "account_name": ACCOUNTS["ppn_keluaran"][1],
                "debit": tax_amount,
                "credit": 0.0,
                "description": "PPN Keluaran (retur)",
            })

        # Credit: Piutang Usaha
        entries.append({
            "account_code": ACCOUNTS["piutang_usaha"][0],
            "account_name": ACCOUNTS["piutang_usaha"][1],
            "debit": 0.0,
            "credit": amount if tax_amount > 0 else dpp,
            "description": "Pengurangan piutang (nota kredit)",
        })

        return entries
