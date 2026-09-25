"""Kosakata inventory_ledger.source_type untuk PENJUALAN — SATU sumber (26 Sep 2026).

Terukur di prod: keluar-jual lewat Pengiriman tercatat INVOICE_FULFILLMENT (kaos 5/5 baris, source_id =
sales_invoices.id), sedangkan top-products/slow-moving hanya membaca SALES_INVOICE/POS_SALE/CASH_SALE/
SALES_RECEIPT_COGS -> penjualan fulfillment tak pernah terhitung. CASH_SALE & SALE tak punya penulis;
SALES_RECEIPT_COGS adalah source_type JURNAL (inventory_helpers.record_inventory_outbound), bukan ledger.

Penulis nyata (diukur): SALES_INVOICE (record_inventory_outbound), INVOICE_FULFILLMENT (_execute_fulfillment),
POS_SALE (sales_receipts / transactions), DOCUMENT_INTAKE arah keluar (kernel_document_executor, movement SALE).
Pembatal = f"{sumber}_VOID" (inventory_helpers.record_inventory_reversal) — baris MASUK yang meniadakan jual.
"""
import re

KELUAR_JUAL = ("SALES_INVOICE", "INVOICE_FULFILLMENT", "POS_SALE", "DOCUMENT_INTAKE")
KELUAR_JUAL_FAKTUR = ("SALES_INVOICE", "INVOICE_FULFILLMENT")          # source_id -> sales_invoices.id
BATAL_JUAL = tuple(f"{s}_VOID" for s in ("SALES_INVOICE", "INVOICE_FULFILLMENT", "POS_SALE"))
# source_type ledger lain yang DIKENAL (bukan penjualan). Penulis baru wajib diklasifikasi di salah satu daftar.
BUKAN_JUAL = (
    "BILL", "BILL_VOID", "PURCHASE_INVOICE", "CREDIT_NOTE", "CREDIT_NOTE_VOID", "VENDOR_CREDIT",
    "STOCK_ADJUSTMENT", "STOCK_ADJUSTMENT_VOID", "PRODUCTION_OUTPUT", "MATERIAL_ISSUE", "RELOKASI_GUDANG",
    "OPENING_BALANCE", "STOCK_TRANSFER", "STOCK_TRANSFER_CANCEL",
)
DIKENAL = KELUAR_JUAL + BATAL_JUAL + BUKAN_JUAL

_AMAN = re.compile(r"^[A-Z][A-Z_]*$")


def sql_daftar(nilai) -> str:
    """Literal SQL `('A', 'B')` dari konstanta di berkas ini (hanya [A-Z_] -> aman disisipkan)."""
    assert nilai and all(_AMAN.match(v) for v in nilai), nilai
    return "(" + ", ".join(f"'{v}'" for v in nilai) + ")"
