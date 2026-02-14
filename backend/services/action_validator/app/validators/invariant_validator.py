"""
Layer 3: INVARIANTS
Validates data invariants:
- Amounts must be > 0
- Required fields must be present per action type
- No negative quantities in line items
"""
import logging
from typing import Dict, List, Set

from .base import BaseValidator, ValidationContext

logger = logging.getLogger(__name__)

# ActionType enum values from proto
ACTION_TYPE_CREATE_CUSTOMER = 0
ACTION_TYPE_UPDATE_CUSTOMER = 1
ACTION_TYPE_CREATE_VENDOR = 2
ACTION_TYPE_CREATE_PRODUCT = 3
ACTION_TYPE_CREATE_SALES_INVOICE = 10
ACTION_TYPE_CREATE_PURCHASE_INVOICE = 11
ACTION_TYPE_CREATE_EXPENSE = 12
ACTION_TYPE_RECEIVE_PAYMENT = 20
ACTION_TYPE_MAKE_PAYMENT = 21
ACTION_TYPE_POST_GENERAL_JOURNAL = 30
ACTION_TYPE_REVERSE_JOURNAL = 31
ACTION_TYPE_CLOSE_PERIOD = 32
ACTION_TYPE_REOPEN_PERIOD = 33
ACTION_TYPE_CREATE_CREDIT_NOTE = 13
ACTION_TYPE_CREATE_PURCHASE_ORDER = 14
ACTION_TYPE_BANK_TRANSFER = 22

# Master data action types (for guard checks)
MASTER_DATA_ACTIONS = {
    ACTION_TYPE_CREATE_CUSTOMER,
    ACTION_TYPE_UPDATE_CUSTOMER,
    ACTION_TYPE_CREATE_VENDOR,
    ACTION_TYPE_CREATE_PRODUCT,
}

# Required fields per action type
REQUIRED_FIELDS: Dict[int, List[str]] = {
    ACTION_TYPE_CREATE_SALES_INVOICE: ["customer_id", "items"],
    ACTION_TYPE_CREATE_PURCHASE_INVOICE: ["vendor_id", "items"],
    ACTION_TYPE_CREATE_EXPENSE: ["amount"],
    ACTION_TYPE_RECEIVE_PAYMENT: ["amount", "customer_id"],
    ACTION_TYPE_MAKE_PAYMENT: ["amount", "vendor_id"],
    ACTION_TYPE_POST_GENERAL_JOURNAL: ["entries"],
    ACTION_TYPE_REVERSE_JOURNAL: ["journal_id"],
    ACTION_TYPE_CLOSE_PERIOD: ["period_id"],
    ACTION_TYPE_REOPEN_PERIOD: ["period_id"],
    ACTION_TYPE_CREATE_CREDIT_NOTE: ["customer_id", "items"],
    ACTION_TYPE_CREATE_CUSTOMER: ["customer_name"],
    ACTION_TYPE_UPDATE_CUSTOMER: ["customer_id"],
    ACTION_TYPE_CREATE_VENDOR: ["vendor_name"],
    ACTION_TYPE_CREATE_PRODUCT: ["product_name"],
    ACTION_TYPE_BANK_TRANSFER: ["from_bank_id", "to_bank_id", "amount"],
    ACTION_TYPE_CREATE_PURCHASE_ORDER: ["vendor_name", "items"],
}

# Fields that should contain positive amounts
AMOUNT_FIELDS: Set[str] = {"amount", "total", "subtotal", "tax_amount", "grand_total", "dpp", "ppn"}


def _safe_float(value, default: float = 0.0) -> float:
    """Safely convert a value to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class InvariantValidator(BaseValidator):
    """Layer 3: Validate data invariants and required fields."""

    async def validate(self, ctx: ValidationContext) -> None:
        logger.debug("Running INVARIANTS validation")
        payload = ctx.payload

        # --- Check required fields ---
        required = REQUIRED_FIELDS.get(ctx.action_type, [])
        for field_name in required:
            value = payload.get(field_name)
            # Accept "items" or "line_items" interchangeably
            if field_name == "items" and value is None:
                value = payload.get("line_items")
            if field_name == "entries" and value is None:
                value = payload.get("journal_entries") or payload.get("line_items")
            # Fallback: accept "name" for master data if specific field missing
            if value is None and field_name in ("vendor_name", "customer_name", "product_name"):
                value = payload.get("name")
            # Fallback: accept "total_amount" for "amount" (RECEIVE_PAYMENT uses total_amount)
            if value is None and field_name == "amount":
                value = payload.get("total_amount")
            # Fallback: accept "customer_name" for "customer_id" (NLU text parsing)
            if value is None and field_name == "customer_id":
                value = payload.get("customer_name")
            # Fallback: accept "vendor_name" for "vendor_id" (NLU text parsing)
            if value is None and field_name == "vendor_id":
                value = payload.get("vendor_name")
            if value is None or (isinstance(value, str) and not value.strip()):
                ctx.add_error(
                    layer="INVARIANTS",
                    code="MISSING_REQUIRED_FIELD",
                    message=f"Required field missing: {field_name}",
                    blocking=True,
                    field_name=field_name,
                )
            elif isinstance(value, list) and len(value) == 0:
                ctx.add_error(
                    layer="INVARIANTS",
                    code="EMPTY_REQUIRED_FIELD",
                    message=f"Required field is empty: {field_name}",
                    blocking=True,
                    field_name=field_name,
                )

        # --- Check amounts are positive ---
        for field_name in AMOUNT_FIELDS:
            value = payload.get(field_name)
            if value is not None:
                try:
                    numeric_value = float(value)
                    if numeric_value <= 0:
                        ctx.add_error(
                            layer="INVARIANTS",
                            code="NON_POSITIVE_AMOUNT",
                            message=f"{field_name} must be > 0, got {numeric_value}",
                            blocking=True,
                            field_name=field_name,
                        )
                except (ValueError, TypeError):
                    ctx.add_error(
                        layer="INVARIANTS",
                        code="INVALID_AMOUNT",
                        message=f"{field_name} is not a valid number: {value}",
                        blocking=True,
                        field_name=field_name,
                    )


        # --- Check bank transfer same-account ---
        if ctx.action_type == ACTION_TYPE_BANK_TRANSFER:
            from_bank_id = payload.get("from_bank_id") or payload.get("source_account_id")
            to_bank_id = payload.get("to_bank_id") or payload.get("destination_account_id")
            if from_bank_id and to_bank_id and str(from_bank_id).strip() == str(to_bank_id).strip():
                ctx.add_error(
                    layer="INVARIANTS",
                    code="SAME_BANK_ACCOUNT",
                    message="Cannot transfer to the same bank account",
                    blocking=True,
                    field_name="to_bank_id",
                )

        # --- Check line items for negative quantities ---
        items = payload.get("items") or payload.get("line_items") or []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            # Check quantity
            qty = item.get("quantity") or item.get("qty")
            if qty is not None:
                try:
                    qty_val = float(qty)
                    if qty_val <= 0:
                        ctx.add_error(
                            layer="INVARIANTS",
                            code="NON_POSITIVE_QUANTITY",
                            message=f"Item [{i}] quantity must be > 0, got {qty_val}",
                            blocking=True,
                            field_name=f"items[{i}].quantity",
                        )
                except (ValueError, TypeError):
                    ctx.add_error(
                        layer="INVARIANTS",
                        code="INVALID_QUANTITY",
                        message=f"Item [{i}] quantity is not a valid number: {qty}",
                        blocking=True,
                        field_name=f"items[{i}].quantity",
                    )

            # Check unit price
            price = item.get("unit_price") or item.get("price")
            if price is not None:
                try:
                    price_val = float(price)
                    if price_val < 0:
                        ctx.add_error(
                            layer="INVARIANTS",
                            code="NEGATIVE_PRICE",
                            message=f"Item [{i}] price must be >= 0, got {price_val}",
                            blocking=True,
                            field_name=f"items[{i}].unit_price",
                        )
                except (ValueError, TypeError):
                    ctx.add_error(
                        layer="INVARIANTS",
                        code="INVALID_PRICE",
                        message=f"Item [{i}] price is not a valid number: {price}",
                        blocking=True,
                        field_name=f"items[{i}].unit_price",
                    )

        # --- Check journal entries for debit/credit consistency ---
        entries = payload.get("entries") or payload.get("journal_entries") or []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            debit = entry.get("debit", 0)
            credit = entry.get("credit", 0)
            try:
                d = float(debit)
                c = float(credit)
                if d < 0 or c < 0:
                    ctx.add_error(
                        layer="INVARIANTS",
                        code="NEGATIVE_JOURNAL_AMOUNT",
                        message=f"Entry [{i}] has negative debit/credit",
                        blocking=True,
                        field_name=f"entries[{i}]",
                    )
                if d == 0 and c == 0:
                    ctx.add_warning(
                        layer="INVARIANTS",
                        code="ZERO_JOURNAL_ENTRY",
                        message=f"Entry [{i}] has both debit and credit = 0",
                        field_name=f"entries[{i}]",
                    )
            except (ValueError, TypeError):
                ctx.add_error(
                    layer="INVARIANTS",
                    code="INVALID_JOURNAL_AMOUNT",
                    message=f"Entry [{i}] has invalid debit/credit values",
                    blocking=True,
                    field_name=f"entries[{i}]",
                )

        # --- Check journal balance (POST_GENERAL_JOURNAL) ---
        if ctx.action_type == ACTION_TYPE_POST_GENERAL_JOURNAL:
            lines = payload.get("entries") or payload.get("journal_entries") or payload.get("line_items") or []
            if not lines:
                ctx.add_error(
                    layer="INVARIANTS",
                    code="EMPTY_JOURNAL_LINES",
                    message="Journal lines cannot be empty",
                    blocking=True,
                )
            elif len(lines) < 2:
                ctx.add_error(
                    layer="INVARIANTS",
                    code="INSUFFICIENT_JOURNAL_LINES",
                    message="Journal must have at least 2 lines",
                    blocking=True,
                )
            else:
                # Calculate totals
                total_debit = sum(_safe_float(line.get("debit", 0)) for line in lines if isinstance(line, dict))
                total_credit = sum(_safe_float(line.get("credit", 0)) for line in lines if isinstance(line, dict))
                
                # Check balance
                if abs(total_debit - total_credit) > 0.01:  # 1 cent tolerance
                    diff = total_debit - total_credit
                    ctx.add_error(
                        layer="INVARIANTS",
                        code="JOURNAL_UNBALANCED",
                        message=f"Jurnal tidak balance: debit Rp {total_debit:,.0f} ≠ kredit Rp {total_credit:,.0f}. Selisih Rp {abs(diff):,.0f}",
                        blocking=True,
                        field_name="entries",
                    )
                
                # Check each line has debit XOR credit
                for i, line in enumerate(lines):
                    if not isinstance(line, dict):
                        continue
                    debit = _safe_float(line.get("debit", 0))
                    credit = _safe_float(line.get("credit", 0))
                    if (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
                        ctx.add_error(
                            layer="INVARIANTS",
                            code="INVALID_JOURNAL_LINE",
                            message=f"Line {i+1}: must have debit OR credit, not both or neither",
                            blocking=True,
                            field_name=f"entries[{i}]",
                        )
                    if debit < 0 or credit < 0:
                        ctx.add_error(
                            layer="INVARIANTS",
                            code="NEGATIVE_AMOUNT",
                            message=f"Line {i+1}: amounts cannot be negative",
                            blocking=True,
                            field_name=f"entries[{i}]",
                        )

        # --- Check product prices are not negative (optional fields) ---
        if ctx.action_type in (ACTION_TYPE_CREATE_PRODUCT,):
            for price_field in ["buy_price", "sell_price", "purchase_price", "sales_price", "harga_jual"]:
                price = payload.get(price_field)
                if price is not None:
                    try:
                        price_val = float(price)
                        if price_val < 0:
                            ctx.add_error(
                                layer="INVARIANTS",
                                code="PRICE_NEGATIVE",
                                message=f"Harga ({price_field}) tidak boleh negatif",
                                blocking=True,
                                field_name=price_field,
                            )
                    except (ValueError, TypeError):
                        ctx.add_error(
                            layer="INVARIANTS",
                            code="INVALID_PRICE",
                            message=f"Harga ({price_field}) bukan angka valid: {price}",
                            blocking=True,
                            field_name=price_field,
                        )

        logger.debug("INVARIANTS validation completed")
