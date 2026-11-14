"""TEKS server untuk detail Barang (BUG-235/236, FRONTEND 2 via MASTER, 14 Nov 2026): FE tak membuat peta label.
- `jenis_text(movement_type, source_type)`: teks Indonesia ringkas per jenis gerak ledger (history). SATU kamus; kosakata TERUKUR di klon + penulis kode.
  Kunci (movement, source) lebih dulu, lalu per movement_type, lalu "Mutasi stok" -- tak pernah kode mentah.
- `norm_aktivitas(description)`: "Item ..." -> "Barang ..." saat DIBACA (baris lama tak diubah; BUG-221).
- `nama_tampil(profil, nama_pengguna, cadangan)`: nama tampil pengguna; email (actor_name tersimpan) hanya cadangan."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)
GENERIK = "Mutasi stok"

# (movement_type, source_type) -> teks
PER_SUMBER = {
    ("PURCHASE", "BILL"): "Pembelian (faktur)",
    ("PURCHASE", "GOODS_RECEIPT"): "Penerimaan barang",
    ("PURCHASE_RETURN", "BILL_VOID"): "Pembatalan faktur pembelian",
    ("PURCHASE_RETURN", "STOCK_ADJUSTMENT_VOID"): "Pembatalan penyesuaian stok",
    ("REVALUASI_MASUK", "PPV_REVALUASI_VOID"): "Pembatalan revaluasi masuk",
    ("REVALUASI_KELUAR", "PPV_REVALUASI_VOID"): "Pembatalan revaluasi keluar",
    ("SALE", "DELIVERY_NOTE"): "Penjualan (surat jalan)",
    ("SALE", "INVOICE_FULFILLMENT"): "Penjualan (pengiriman faktur)",
    ("SALE", "SALES_INVOICE"): "Penjualan (faktur)",
    ("SALE", "POS_SALE"): "Penjualan kasir",
    ("SALE", "VENDOR_CREDIT"): "Retur ke pemasok (nota debit)",
    ("SALES_RETURN", "CREDIT_NOTE"): "Retur penjualan (nota kredit)",
    ("VOID_REVERSAL", "DELIVERY_NOTE_VOID"): "Pembatalan surat jalan",
    ("VOID_REVERSAL", "INVOICE_FULFILLMENT_VOID"): "Pembatalan pengiriman faktur",
    ("VOID_REVERSAL", "SALES_INVOICE_VOID"): "Pembatalan faktur penjualan",
    ("VOID_REVERSAL", "POS_SALE_VOID"): "Pembatalan penjualan kasir",
    ("VOID_REVERSAL", "STOCK_ADJUSTMENT_VOID"): "Pembatalan penyesuaian stok",
    ("VOID_REVERSAL", "VENDOR_CREDIT_VOID"): "Pembatalan nota debit",
}
# movement_type -> teks (bila sumber tak dikenal / kosong)
PER_JENIS = {
    "ADJUSTMENT": "Penyesuaian stok",
    "STOCK_ADJUSTMENT": "Penyesuaian stok",
    "STOCK_ADJUSTMENT_REVERSAL": "Pembalikan penyesuaian stok",
    "GOODS_RECEIPT_REVERSAL": "Pembalikan penerimaan barang",
    "MATERIAL_ISSUE": "Pemakaian bahan produksi",
    "MATERIAL_ISSUE_REVERSAL": "Pembalikan pemakaian bahan",
    "OPENING_BALANCE": "Saldo awal",
    "PRODUCTION_OUTPUT": "Hasil produksi",
    "PRODUCTION_OUTPUT_REVERSAL": "Pembalikan hasil produksi",
    "PURCHASE": "Pembelian",
    "PURCHASE_RETURN": "Retur pembelian",
    "RELOKASI_GUDANG_VOID": "Pembatalan pemindahan gudang",
    "RELOKASI_KELUAR": "Pemindahan gudang keluar",
    "RELOKASI_MASUK": "Pemindahan gudang masuk",
    "REVALUASI_KELUAR": "Revaluasi keluar",
    "REVALUASI_MASUK": "Revaluasi masuk",
    "SALE": "Penjualan",
    "SALES_RETURN": "Retur penjualan",
    "SALES_RETURN_REVERSAL": "Pembalikan retur penjualan",
    "VOID_REVERSAL": "Pembalikan",
}


def jenis_text(movement_type: Optional[str], source_type: Optional[str]) -> str:
    mv, src = (movement_type or "").strip().upper(), (source_type or "").strip().upper()
    t = PER_SUMBER.get((mv, src)) or PER_JENIS.get(mv)
    if t is None:
        logger.warning("barang_teks: jenis gerak ledger tak dikenal (%s, %s) -> %s; lengkapi kamus", mv or "-", src or "-", GENERIK)
        return GENERIK
    return t


def norm_aktivitas(description: Optional[str]) -> Optional[str]:
    if description and description.startswith("Item "):
        return "Barang " + description[len("Item "):]
    return description


def nama_tampil(profil: Optional[str], nama_pengguna: Optional[str], cadangan: Optional[str]) -> Optional[str]:
    for x in (profil, nama_pengguna, cadangan):
        if x and str(x).strip():
            return str(x).strip()
    return None
