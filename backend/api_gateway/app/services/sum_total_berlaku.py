"""BUG-196 (putusan pemilik 13 Nov 2026): SATU penentu "berlaku untuk Σ" per modul untuk `sum_total` daftar seragam (`?with_totals=true`).

Aturan MH sendiri (Accurate tak punya Σ di daftar): batal/void/ditolak/kedaluwarsa TIDAK pernah ikut `sum_total`; DRAF tetap ikut di semua modul.
Satu tabel status "tidak berlaku" per modul; daftar-daftar memakai `sql_berlaku` (SQL) / `berlaku` (Python) -- tak ada literal FILTER tersebar.
"""
from typing import Optional

TIDAK_BERLAKU = {
    "expense": ("void",),
    "sales_invoice": ("void",),
    "sales_order": ("cancelled",),
    "quote": ("void", "declined", "expired"),     # + kedaluwarsa TURUNAN (sent/viewed lewat tanggal) -> p_hari
    "bill": ("void",),
    "purchase_order": ("cancelled",),             # closed = Selesai (tetap ikut); PO tak punya status ditolak/kedaluwarsa
    "bill_payment": ("voided",),
    "receive_payment": ("voided",),
    "vendor_credit": ("void",),                   # nota debit
    "credit_note": ("void",),
    "customer_deposit": ("void",),
    "vendor_deposit": ("void",),
    "proforma": ("cancelled", "expired"),
}


def berlaku(modul: str, status: Optional[str]) -> bool:
    """Python: baris dengan status ini ikut `sum_total`? (status tak dikenal/None -> ikut, sama dgn perilaku SQL lama kecuali NULL)."""
    return status not in TIDAK_BERLAKU[modul]


def sql_berlaku(modul: str, kolom: str, p_hari: Optional[str] = None) -> str:
    """SQL: predikat 'berlaku untuk Σ' atas ekspresi status `kolom`. Penawaran wajib memberi `p_hari` (placeholder tanggal hari ini, mis. "$7")
    karena kedaluwarsa = turunan tanggal (services/penawaran_kedaluwarsa.sql_kedaluwarsa, penentu yang sama dgn filter ?status=expired)."""
    daftar = ", ".join(f"'{s}'" for s in TIDAK_BERLAKU[modul])
    teks = f"(({kolom}) NOT IN ({daftar}))"
    if modul == "quote":
        if not p_hari:
            raise ValueError("penawaran: p_hari wajib (kedaluwarsa turunan tanggal)")
        from .penawaran_kedaluwarsa import sql_kedaluwarsa
        teks = f"({teks} AND NOT {sql_kedaluwarsa(p_hari)})"
    return teks
