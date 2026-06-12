"""Lane C — deterministic capture-and-explain narration.

When the document intake pipeline cannot turn an uploaded file into a posted
transaction, Lane C produces an HONEST short narration instead of silently
leaking the turn to the generic chat LLM.

Rules (design brief Phase 1a §3):
  * NO LLM. Pure code. Filled only from header OCR fields that already exist.
  * The chat OCR path is HEADER-ONLY (no line_items) — never promise per-row detail.
  * Bahasa Indonesia, singkat, + satu saran lanjut.
"""

from typing import Optional

# Outcome constants (also written to documents.intake_outcome).
CAPTURED_NON_TRANSACTION = "lane_c_captured_non_transaction"
CAPTURED_UNSUPPORTED_TXTYPE = "lane_c_captured_unsupported_txtype"
CAPTURED_UNSUPPORTED_FILETYPE = "lane_c_captured_unsupported_filetype"

_DOC_TYPE_LABEL = {
    "invoice": "faktur pembelian",
    "purchase_invoice": "faktur pembelian",
    "bill": "faktur pembelian",
    "sales_invoice": "faktur penjualan",
    "quotation": "penawaran",
    "quote": "penawaran",
    "contract": "kontrak",
    "receipt": "struk",
    "nota": "nota",
    "kwitansi": "kwitansi",
    "bank_statement": "rekening koran",
    "bank_transfer": "bukti transfer",
    "catalog": "katalog",
    "id_card": "dokumen identitas",
    "po": "purchase order",
    "purchase_order": "purchase order",
}


def _fmt_idr(amount) -> Optional[str]:
    """Indonesian thousand-separated Rupiah, or None when not a positive amount."""
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return "Rp " + f"{v:,.0f}".replace(",", ".")


def _label(doc_type: Optional[str]) -> str:
    return _DOC_TYPE_LABEL.get((doc_type or "").lower().strip(), "dokumen")


def _header_fragment(header: dict) -> str:
    vendor = (
        header.get("vendor_name")
        or header.get("counterparty_name")
        or header.get("customer_name")
        or ""
    ).strip()
    total = _fmt_idr(header.get("total_amount") or header.get("amount"))
    frag = f"Ini sepertinya **{_label(header.get('doc_type'))}**"
    if vendor:
        frag += f" dari **{vendor}**"
    if total:
        frag += f", total **{total}**"
    return frag + "."


def render_lane_c_narration(
    outcome: str, header: Optional[dict], filename: str = ""
) -> str:
    header = header or {}

    if outcome == CAPTURED_UNSUPPORTED_FILETYPE:
        fn = filename or "itu"
        return (
            f"File **{fn}** sudah tersimpan, tapi aku belum bisa membacanya "
            f"otomatis. Kalau ini dokumen keuangan, kirim ulang sebagai "
            f"**foto atau PDF** ya."
        )

    frag = _header_fragment(header)

    if outcome == CAPTURED_UNSUPPORTED_TXTYPE:
        return (
            f"{frag} Pencatatan otomatis jenis dokumen ini dari foto **belum "
            f"aktif** — tapi **dokumennya sudah tersimpan & terlampir** di chat "
            f"ini. Mau aku bantu **input manual** sekarang?"
        )

    # CAPTURED_NON_TRANSACTION (default)
    return (
        f"{frag} Aku belum bisa mencatatnya sebagai transaksi otomatis — tapi "
        f"**dokumennya sudah tersimpan & terlampir** di chat ini. Ada lagi yang "
        f"bisa aku bantu soal dokumen ini?"
    )


def suggestion_prompt(outcome: str, header: Optional[dict] = None) -> str:
    """Text a tappable suggestion button sends back (empty = no button)."""
    if outcome == CAPTURED_UNSUPPORTED_TXTYPE:
        return "Ya, bantu aku input manual dokumen ini"
    if outcome == CAPTURED_UNSUPPORTED_FILETYPE:
        return ""
    return ""


# documents.category is constrained by chk_doc_category — map doc_type into it.
_DOC_TYPE_TO_CATEGORY = {
    "invoice": "invoice",
    "purchase_invoice": "invoice",
    "bill": "invoice",
    "sales_invoice": "invoice",
    "quotation": "invoice",
    "quote": "invoice",
    "po": "invoice",
    "purchase_order": "invoice",
    "receipt": "receipt",
    "nota": "receipt",
    "kwitansi": "receipt",
    "struk": "receipt",
    "payment_receipt": "receipt",
    "bank_transfer": "receipt",
    "contract": "contract",
    "bank_statement": "statement",
    "statement": "statement",
    "photo": "photo",
    "report": "report",
    "certificate": "certificate",
    "catalog": "other",
    "id_card": "other",
}


def category_for_outcome(outcome: str, header: Optional[dict] = None) -> str:
    """Real documents.category (constraint-safe) — replaces hardcoded 'receipt'."""
    header = header or {}
    dt = (header.get("doc_type") or "").lower().strip()
    mapped = _DOC_TYPE_TO_CATEGORY.get(dt)
    if mapped:
        return mapped
    if outcome == CAPTURED_UNSUPPORTED_FILETYPE:
        return "other"
    return "unclassified"
