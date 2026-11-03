"""Deskripsi jurnal -- SATU sumber untuk penulis DAN pratinjau (TEKS-JURNAL-NKND, 3 Nov 2026, OK MASTER; tiket CONVERSATIONAL).
Pola gold Penjualan: "{Dokumen} {nomor} - {pihak}" (sales_invoices: "Faktur Penjualan {no} - {pelanggan}"). Jurnal HPP kedua sisi
TANPA nama pihak (penulis HPP Nota Debit = inventory_helpers.record_inventory_outbound f"HPP {source_number}", helper bersama).
Jurnal lama TIDAK diubah (immutable)."""


def nk(nomor, pelanggan) -> str:
    return f"Nota Kredit {nomor} - {pelanggan}"


def nk_hpp(nomor) -> str:
    return f"Pembalik HPP {nomor}"


def nk_refund(nomor, pelanggan) -> str:
    return f"Pengembalian Dana Nota Kredit {nomor} - {pelanggan}"


def nk_batal(nomor, pelanggan) -> str:
    return f"Batal Nota Kredit {nomor} - {pelanggan}"


def nk_batal_hpp(nomor) -> str:
    return f"Batal Pembalik HPP {nomor}"


def nd(nomor, vendor) -> str:
    return f"Nota Debit {nomor} - {vendor}"


def nd_hpp(nomor) -> str:
    """== inventory_helpers.record_inventory_outbound (penulis bersama, teks TETAP)."""
    return f"HPP {nomor}"


def nd_refund(nomor, vendor) -> str:
    return f"Penerimaan Dana Nota Debit {nomor} - {vendor}"


def nd_batal(nomor, vendor) -> str:
    return f"Batal Nota Debit {nomor} - {vendor}"


def nd_batal_hpp(nomor) -> str:
    return f"Batal HPP {nomor}"
