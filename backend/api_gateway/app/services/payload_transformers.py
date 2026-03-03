"""
Payload Transformers — Intelligence Layer → REST Endpoint routing.

Converts draft_plan (Phase 5) + document data into payload dicts
matching existing REST endpoint Pydantic schemas.

RULES:
- Field names MUST match Pydantic schema (not DB column names)
- Amounts as int (matching schema) — Law 25
- UUIDs as str
- Optional fields: include if available, omit if not
- NEVER create journal/inventory directly — endpoint handles it
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Helpers ────────────────────────────────────────────────────────────

def _decimal_to_int(value: Any) -> int:
    """Convert string/Decimal amount to int (Rupiah, no sub-units)."""
    if value is None:
        return 0
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _parse_date(value: Any) -> Optional[str]:
    """Parse date to ISO string, or None."""
    if not value:
        return None
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None


def _extract_counterparty_name(draft_plan: dict, document: dict) -> Optional[str]:
    """
    Extract vendor/customer name from analysis_result or draft_plan.
    Priority:
    1. analysis_result.anomalies[].details.counterparty_name
    2. journal_draft.description (after ' - ')
    3. warnings (pattern: 'Name' belum terdaftar)
    """
    ar = document.get("analysis_result") or {}
    if isinstance(ar, dict):
        # From anomalies (most reliable — Phase 3 extraction)
        for anomaly in (ar.get("anomalies") or []):
            if anomaly.get("type") == "new_counterparty":
                name = (anomaly.get("details") or {}).get("counterparty_name")
                if name:
                    return name

        # Direct fields
        for key in ("supplier_name", "vendor_name", "customer_name", "counterparty_name"):
            if ar.get(key):
                return ar[key]

        # Nested supplier/vendor/customer dicts
        for key in ("supplier", "vendor", "customer"):
            obj = ar.get(key)
            if isinstance(obj, dict) and obj.get("name"):
                return obj["name"]

    # From journal description: "Invoice #PB-2602-0099 - PT Maju Jaya Sentosa"
    desc = (draft_plan.get("journal_draft") or {}).get("description", "")
    if " - " in desc:
        parts = desc.split(" - ", 1)
        if len(parts) == 2 and len(parts[1].strip()) > 2:
            return parts[1].strip()

    # From warnings: "'PT Maju Jaya Sentosa' belum terdaftar..."
    for w in (draft_plan.get("warnings") or []):
        match = re.match(r"^'(.+?)'\s+belum terdaftar", w)
        if match:
            return match.group(1)

    return None


def _extract_invoice_ref(draft_plan: dict, document: dict) -> Optional[str]:
    """Extract original invoice/reference number."""
    ar = document.get("analysis_result") or {}
    if isinstance(ar, dict):
        for key in ("invoice_number", "nomor_faktur", "ref_no", "reference_number"):
            if ar.get(key):
                return str(ar[key])

    desc = (draft_plan.get("journal_draft") or {}).get("description", "")
    match = re.search(r"#([\w\-]+)", desc)
    if match:
        return match.group(1)
    return None


def _extract_due_date(draft_plan: dict, document: dict, issue_date_str: str) -> str:
    """Extract due date or default issue_date + 30 days."""
    ar = document.get("analysis_result") or {}
    if isinstance(ar, dict):
        for key in ("due_date", "tanggal_jatuh_tempo", "jatuh_tempo"):
            val = _parse_date(ar.get(key))
            if val:
                return val
    try:
        issue = date.fromisoformat(issue_date_str)
        return (issue + timedelta(days=30)).isoformat()
    except (ValueError, TypeError):
        return (date.today() + timedelta(days=30)).isoformat()


def _detect_tax_rate(draft_plan: dict) -> int:
    """Detect tax rate from PPN lines in journal draft."""
    lines = (draft_plan.get("journal_draft") or {}).get("lines", [])
    for line in lines:
        memo = (line.get("memo") or "").lower()
        code = line.get("account_code") or ""
        if "ppn" in memo or code in ("1-10800", "2-10600"):
            return 11
    return 0


# ─── Bill Items Builders ───────────────────────────────────────────────

def _build_bill_items_from_movements(draft_plan: dict) -> List[dict]:
    """Build BillItemRequestV2 items from inventory_movements (direction=in)."""
    items = []
    for mov in (draft_plan.get("inventory_movements") or []):
        if mov.get("direction") != "in":
            continue
        item = {
            "product_name": mov["product_name"],
            "qty": max(1, _decimal_to_int(mov.get("quantity", "1"))),
            "price": max(1, _decimal_to_int(mov.get("unit_cost", "0"))),
        }
        if mov.get("product_id"):
            item["product_id"] = str(mov["product_id"])
        unit = mov.get("unit")
        if not unit:
            suggestion = mov.get("new_product_suggestion") or {}
            unit = suggestion.get("suggested_unit")
        if unit:
            item["unit"] = unit
        items.append(item)
    return items


def _build_bill_items_from_journal(draft_plan: dict) -> List[dict]:
    """Fallback: build bill items from debit journal lines (for service bills)."""
    items = []
    skip_codes = {"2-10100", "1-10800", "2-10600"}  # AP, PPN-in, PPN-out
    lines = (draft_plan.get("journal_draft") or {}).get("lines", [])
    for line in lines:
        code = line.get("account_code") or ""
        debit = Decimal(str(line.get("debit", "0")))
        if debit > 0 and code not in skip_codes:
            items.append({
                "product_name": line.get("memo") or line.get("account_name") or "Item",
                "qty": 1,
                "price": _decimal_to_int(debit),
            })
    return items


# ─── Main Transformer Functions ────────────────────────────────────────

def transform_to_bill_payload(draft_plan: dict, document: dict) -> dict:
    """
    Transform draft_plan → POST /api/bills/v2 payload (CreateBillRequestV2).
    For action_type: create_purchase_invoice, create_bill
    """
    journal_draft = draft_plan.get("journal_draft") or {}
    issue_date = _parse_date(journal_draft.get("journal_date")) or date.today().isoformat()

    items = _build_bill_items_from_movements(draft_plan)
    if not items:
        items = _build_bill_items_from_journal(draft_plan)
    if not items:
        raise ValueError("Cannot build bill items: no inventory_movements or debit lines")

    payload = {
        "vendor_name": _extract_counterparty_name(draft_plan, document) or "Unknown Vendor",
        "issue_date": issue_date,
        "due_date": _extract_due_date(draft_plan, document, issue_date),
        "items": items,
        "notes": journal_draft.get("description", ""),
        "tax_rate": _detect_tax_rate(draft_plan),
        "status": "posted",
    }

    ref_no = _extract_invoice_ref(draft_plan, document)
    if ref_no:
        payload["ref_no"] = ref_no

    return payload


def transform_to_sales_invoice_payload(draft_plan: dict, document: dict) -> dict:
    """
    Transform draft_plan → POST /api/sales-invoices payload (CreateInvoiceRequest).
    For action_type: create_sales_invoice
    """
    journal_draft = draft_plan.get("journal_draft") or {}
    invoice_date = _parse_date(journal_draft.get("journal_date")) or date.today().isoformat()

    items = []
    for mov in (draft_plan.get("inventory_movements") or []):
        if mov.get("direction") != "out":
            continue
        item = {
            "description": mov["product_name"],
            "quantity": max(1, float(Decimal(str(mov.get("quantity", "1"))))),
            "unit_price": _decimal_to_int(mov.get("unit_cost", "0")),
        }
        if mov.get("product_id"):
            item["item_id"] = str(mov["product_id"])
        items.append(item)

    # Fallback: revenue credit lines
    if not items:
        revenue_codes = {"4-10100"}
        for line in (journal_draft.get("lines") or []):
            code = line.get("account_code") or ""
            credit = Decimal(str(line.get("credit", "0")))
            if credit > 0 and code in revenue_codes:
                items.append({
                    "description": line.get("memo") or "Item",
                    "quantity": 1,
                    "unit_price": _decimal_to_int(credit),
                })

    if not items:
        raise ValueError("Cannot build invoice items: no outbound movements or revenue lines")

    payload = {
        "customer_name": _extract_counterparty_name(draft_plan, document) or "Unknown Customer",
        "invoice_date": invoice_date,
        "due_date": _extract_due_date(draft_plan, document, invoice_date),
        "items": items,
        "notes": journal_draft.get("description", ""),
        "tax_rate": float(_detect_tax_rate(draft_plan)),
        "auto_post": True,
    }

    ref_no = _extract_invoice_ref(draft_plan, document)
    if ref_no:
        payload["ref_no"] = ref_no

    return payload


def transform_to_expense_payload(draft_plan: dict, document: dict) -> dict:
    """
    Transform draft_plan → POST /api/expenses payload (CreateExpenseRequest).
    For action_type: record_expense
    """
    journal_draft = draft_plan.get("journal_draft") or {}
    bank_draft = draft_plan.get("bank_draft")
    expense_date = _parse_date(journal_draft.get("journal_date")) or date.today().isoformat()

    if not bank_draft or not bank_draft.get("bank_account_id"):
        raise ValueError("Expense requires bank_draft.bank_account_id (paid_through)")

    paid_through_id = str(bank_draft["bank_account_id"])

    # Find expense debit lines (exclude bank/cash credits and tax)
    lines = journal_draft.get("lines", [])
    credit_codes = set()
    tax_codes = {"1-10800", "2-10600"}

    for line in lines:
        credit = Decimal(str(line.get("credit", "0")))
        if credit > 0:
            credit_codes.add(line.get("account_code") or "")

    expense_lines = []
    for line in lines:
        code = line.get("account_code") or ""
        debit = Decimal(str(line.get("debit", "0")))
        if debit > 0 and code not in credit_codes and code not in tax_codes:
            expense_lines.append({
                "account_id": line.get("account_id"),
                "account_name": line.get("account_name") or line.get("memo"),
                "amount": _decimal_to_int(debit),
                "notes": line.get("memo"),
            })

    if not expense_lines:
        raise ValueError("Cannot build expense: no expense debit lines found")

    if len(expense_lines) == 1:
        payload = {
            "expense_date": expense_date,
            "paid_through_id": paid_through_id,
            "account_id": expense_lines[0]["account_id"],
            "amount": expense_lines[0]["amount"],
            "notes": journal_draft.get("description", ""),
        }
    else:
        payload = {
            "expense_date": expense_date,
            "paid_through_id": paid_through_id,
            "is_itemized": True,
            "line_items": [
                {"account_id": el["account_id"], "amount": el["amount"], "notes": el.get("notes")}
                for el in expense_lines
            ],
            "notes": journal_draft.get("description", ""),
        }

    vendor_name = _extract_counterparty_name(draft_plan, document)
    if vendor_name:
        payload["vendor_name"] = vendor_name

    tax_rate = _detect_tax_rate(draft_plan)
    if tax_rate > 0:
        payload["tax_rate"] = str(tax_rate)

    ref = _extract_invoice_ref(draft_plan, document)
    if ref:
        payload["reference"] = ref

    return payload


def transform_to_bill_payment_payload(draft_plan: dict, document: dict) -> dict:
    """
    Transform draft_plan → POST /api/bill-payments payload.
    For action_type: record_payment_made
    Requires matched_to with bill reference.
    """
    journal_draft = draft_plan.get("journal_draft") or {}
    bank_draft = draft_plan.get("bank_draft")
    matched_to = draft_plan.get("matched_to")

    if not bank_draft or not bank_draft.get("bank_account_id"):
        raise ValueError("Bill payment requires bank_draft.bank_account_id")
    if not matched_to or not matched_to.get("source_id"):
        raise ValueError("Bill payment requires matched_to with bill reference")

    payment_date = _parse_date(journal_draft.get("journal_date")) or date.today().isoformat()
    amount = _decimal_to_int(bank_draft.get("amount", "0"))
    if amount <= 0:
        raise ValueError("Bill payment amount must be > 0")

    # Vendor ID needed — extract from analysis_result or matched_to
    ar = document.get("analysis_result") or {}
    vendor_id = None
    if isinstance(ar, dict):
        vendor_id = ar.get("vendor_id")
    if not vendor_id:
        vendor_id = matched_to.get("vendor_id")
    if not vendor_id:
        raise ValueError("Bill payment requires vendor_id")

    return {
        "vendor_id": str(vendor_id),
        "payment_date": payment_date,
        "payment_method": "bank_transfer",
        "bank_account_id": str(bank_draft["bank_account_id"]),
        "total_amount": amount,
        "allocations": [{
            "bill_id": str(matched_to["source_id"]),
            "amount_applied": amount,
        }],
        "notes": journal_draft.get("description", ""),
    }


def transform_to_receive_payment_payload(draft_plan: dict, document: dict) -> dict:
    """
    Transform draft_plan → POST /api/receive-payments payload.
    For action_type: record_payment_received
    Requires matched_to with invoice reference.
    """
    journal_draft = draft_plan.get("journal_draft") or {}
    bank_draft = draft_plan.get("bank_draft")
    matched_to = draft_plan.get("matched_to")

    if not bank_draft or not bank_draft.get("bank_account_id"):
        raise ValueError("Receive payment requires bank_draft.bank_account_id")
    if not matched_to or not matched_to.get("source_id"):
        raise ValueError("Receive payment requires matched_to with invoice reference")

    payment_date = _parse_date(journal_draft.get("journal_date")) or date.today().isoformat()
    amount = _decimal_to_int(bank_draft.get("amount", "0"))
    if amount <= 0:
        raise ValueError("Receive payment amount must be > 0")

    ar = document.get("analysis_result") or {}
    customer_id = None
    if isinstance(ar, dict):
        customer_id = ar.get("customer_id")
    if not customer_id:
        customer_id = matched_to.get("customer_id")
    if not customer_id:
        raise ValueError("Receive payment requires customer_id")

    return {
        "customer_id": str(customer_id),
        "payment_date": payment_date,
        "payment_method": "bank_transfer",
        "bank_account_id": str(bank_draft["bank_account_id"]),
        "total_amount": amount,
        "allocations": [{
            "invoice_id": str(matched_to["source_id"]),
            "amount_applied": amount,
        }],
        "notes": journal_draft.get("description", ""),
    }


def transform_to_journal_payload(draft_plan: dict, document: dict) -> dict:
    """
    Transform draft_plan → POST /api/journals payload (CreateJournalRequest).
    Fallback for action_types without a specific endpoint.
    """
    journal_draft = draft_plan.get("journal_draft") or {}
    if not journal_draft.get("lines"):
        raise ValueError("Journal requires at least 2 lines")

    entry_date = _parse_date(journal_draft.get("journal_date")) or date.today().isoformat()

    lines = []
    for line in journal_draft["lines"]:
        debit = Decimal(str(line.get("debit", "0")))
        credit = Decimal(str(line.get("credit", "0")))
        if debit == 0 and credit == 0:
            continue
        lines.append({
            "account_id": line["account_id"],
            "description": line.get("memo") or "",
            "debit": str(debit),
            "credit": str(credit),
        })

    if len(lines) < 2:
        raise ValueError("Journal requires at least 2 lines")

    return {
        "entry_date": entry_date,
        "description": journal_draft.get("description", "Document intake journal"),
        "lines": lines,
        "save_as_draft": False,
    }


# ─── Route Registry ────────────────────────────────────────────────────

# action_type → (endpoint, method, transformer, transaction_type)
ACTION_ROUTES = {
    "create_purchase_invoice": ("/api/bills/v2", "POST", transform_to_bill_payload, "bill"),
    "create_bill": ("/api/bills/v2", "POST", transform_to_bill_payload, "bill"),
    "create_sales_invoice": ("/api/sales-invoices", "POST", transform_to_sales_invoice_payload, "sales_invoice"),
    "record_expense": ("/api/expenses", "POST", transform_to_expense_payload, "expense"),
    "record_payment_made": ("/api/bill-payments", "POST", transform_to_bill_payment_payload, "bill_payment"),
    "record_payment_received": ("/api/receive-payments", "POST", transform_to_receive_payment_payload, "receive_payment"),
    "create_journal_entry": ("/api/journals", "POST", transform_to_journal_payload, "journal_entry"),
}


def get_route(action_type: str):
    """Get route for action_type. Returns (endpoint, method, transformer, tx_type) or None."""
    return ACTION_ROUTES.get(action_type)
