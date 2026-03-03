"""
Document Classifier
===================
Deterministic classification rules from OCR output to doc_type.
No LLM call — relies on OCR extraction's doc_type_hint + structural analysis.

Phase 3 only: sets doc_type + classification_confidence.
Does NOT do financial analysis, account recommendation, or journal draft.
"""
import logging
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Classification result types
DOC_TYPES = {
    "invoice_purchase",   # AP: Faktur Pembelian
    "invoice_sales",      # AR: Faktur Penjualan
    "receipt",            # Direct expense/revenue receipt
    "bank_transfer_out",  # Payment made
    "bank_transfer_in",   # Payment received
    "bank_statement",     # Multi-transaction bank statement
    "credit_note",        # AR/AP reduction
    "debit_note",         # AR/AP increase
    "tax_document",       # Tax-related
    "unknown",            # Needs manual classification
}


def classify_document(ocr_result: Dict[str, Any]) -> Tuple[str, Decimal]:
    """
    Determine doc_type from OCR result dict.
    Returns: (doc_type, confidence)

    Classification is DETERMINISTIC — no LLM call.
    LLM already provided doc_type_hint during OCR extraction.
    This layer validates and refines that hint using structural signals.
    """
    if not ocr_result:
        return "unknown", Decimal("0.0")

    hint = (ocr_result.get("doc_type_hint") or "unknown").lower()
    ocr_confidence = _to_decimal(ocr_result.get("confidence", "0.5"))
    
    has_line_items = bool(ocr_result.get("line_items"))
    has_counterparty = bool(ocr_result.get("counterparty_name"))
    has_due_date = bool(ocr_result.get("due_date"))
    has_bank_details = bool(ocr_result.get("bank_name") or ocr_result.get("bank_account_number"))
    has_reference = bool(ocr_result.get("reference_number"))
    
    total_amount = _to_decimal(ocr_result.get("total_amount", "0"))
    
    # Rule-based classification
    doc_type, confidence = _apply_rules(
        hint=hint,
        ocr_confidence=ocr_confidence,
        has_line_items=has_line_items,
        has_counterparty=has_counterparty,
        has_due_date=has_due_date,
        has_bank_details=has_bank_details,
        has_reference=has_reference,
        total_amount=total_amount,
        raw_text=(ocr_result.get("raw_text") or "").lower(),
    )
    
    logger.info(
        f"[Classifier] hint={hint} -> type={doc_type} "
        f"confidence={confidence} (line_items={has_line_items}, "
        f"counterparty={has_counterparty}, bank={has_bank_details})"
    )
    
    return doc_type, confidence


def _apply_rules(
    *,
    hint: str,
    ocr_confidence: Decimal,
    has_line_items: bool,
    has_counterparty: bool,
    has_due_date: bool,
    has_bank_details: bool,
    has_reference: bool,
    total_amount: Decimal,
    raw_text: str,
) -> Tuple[str, Decimal]:
    """Apply classification rules. Returns (doc_type, confidence)."""

    # ── Invoice detection ────────────────────────────────────
    if hint == "invoice":
        if has_line_items and has_counterparty:
            # Distinguish purchase vs sales invoice
            if _is_purchase_invoice(raw_text):
                return "invoice_purchase", _boost(ocr_confidence, Decimal("0.10"))
            elif _is_sales_invoice(raw_text):
                return "invoice_sales", _boost(ocr_confidence, Decimal("0.10"))
            # Default: purchase invoice (more common in upload scenario)
            return "invoice_purchase", ocr_confidence
        
        if has_counterparty and has_due_date:
            return "invoice_purchase", _reduce(ocr_confidence, Decimal("0.05"))
        
        # Weak invoice signal
        return "invoice_purchase", _reduce(ocr_confidence, Decimal("0.15"))

    # ── Bank transfer detection ──────────────────────────────
    if hint == "bank_transfer":
        if has_bank_details and has_reference:
            if _is_outgoing_transfer(raw_text):
                return "bank_transfer_out", _boost(ocr_confidence, Decimal("0.05"))
            elif _is_incoming_transfer(raw_text):
                return "bank_transfer_in", _boost(ocr_confidence, Decimal("0.05"))
            # Default: outgoing (more common in upload scenario)
            return "bank_transfer_out", ocr_confidence
        
        if has_bank_details:
            return "bank_transfer_out", _reduce(ocr_confidence, Decimal("0.10"))
        
        return "bank_transfer_out", _reduce(ocr_confidence, Decimal("0.20"))

    # ── Receipt detection ────────────────────────────────────
    if hint == "receipt":
        if total_amount > 0 and not has_line_items:
            return "receipt", _boost(ocr_confidence, Decimal("0.05"))
        if total_amount > 0:
            return "receipt", ocr_confidence
        return "receipt", _reduce(ocr_confidence, Decimal("0.10"))

    # ── Bank statement detection ─────────────────────────────
    if hint == "bank_statement":
        return "bank_statement", ocr_confidence

    # ── Credit/debit note detection ──────────────────────────
    if hint in ("credit_note", "nota_kredit"):
        return "credit_note", ocr_confidence
    if hint in ("debit_note", "nota_debit"):
        return "debit_note", ocr_confidence

    # ── Tax document detection ───────────────────────────────
    if hint in ("tax_document", "tax", "faktur_pajak"):
        return "tax_document", ocr_confidence

    # ── Fallback: try to infer from structural signals ───────
    if hint == "unknown" or ocr_confidence < Decimal("0.4"):
        return _infer_from_structure(
            has_line_items=has_line_items,
            has_counterparty=has_counterparty,
            has_due_date=has_due_date,
            has_bank_details=has_bank_details,
            total_amount=total_amount,
            raw_text=raw_text,
        )

    # ── Last resort ──────────────────────────────────────────
    return "unknown", min(ocr_confidence, Decimal("0.3"))


def _infer_from_structure(
    *,
    has_line_items: bool,
    has_counterparty: bool,
    has_due_date: bool,
    has_bank_details: bool,
    total_amount: Decimal,
    raw_text: str,
) -> Tuple[str, Decimal]:
    """Infer doc_type purely from structural signals when hint is unknown."""
    
    if has_line_items and has_counterparty and has_due_date:
        return "invoice_purchase", Decimal("0.55")
    
    if has_bank_details and total_amount > 0:
        return "bank_transfer_out", Decimal("0.50")
    
    if has_line_items and has_counterparty:
        return "invoice_purchase", Decimal("0.45")
    
    if total_amount > 0 and not has_line_items:
        return "receipt", Decimal("0.40")
    
    return "unknown", Decimal("0.20")


# ── Helper functions ─────────────────────────────────────────

def _is_purchase_invoice(text: str) -> bool:
    """Check if text indicates a purchase invoice."""
    purchase_signals = [
        "faktur pembelian", "purchase invoice", "bill to",
        "tagihan", "invoice from", "supplier", "vendor",
    ]
    return any(s in text for s in purchase_signals)


def _is_sales_invoice(text: str) -> bool:
    """Check if text indicates a sales invoice."""
    sales_signals = [
        "faktur penjualan", "sales invoice", "sold to",
        "customer", "pelanggan", "invoice to",
    ]
    return any(s in text for s in sales_signals)


def _is_outgoing_transfer(text: str) -> bool:
    """Check if text indicates outgoing payment."""
    out_signals = [
        "transfer ke", "pembayaran", "payment to", "paid to",
        "debit", "keluar", "outgoing",
    ]
    return any(s in text for s in out_signals)


def _is_incoming_transfer(text: str) -> bool:
    """Check if text indicates incoming payment."""
    in_signals = [
        "terima dari", "penerimaan", "received from", "credit",
        "masuk", "incoming", "transfer dari",
    ]
    return any(s in text for s in in_signals)


def _to_decimal(value: Any) -> Decimal:
    """Safely convert any value to Decimal."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _boost(confidence: Decimal, amount: Decimal) -> Decimal:
    """Boost confidence, capped at 0.99."""
    return min(confidence + amount, Decimal("0.99"))


def _reduce(confidence: Decimal, amount: Decimal) -> Decimal:
    """Reduce confidence, floored at 0.10."""
    return max(confidence - amount, Decimal("0.10"))
