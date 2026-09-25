"""Q-017 (26 Sep 2026): SATU aturan "penawaran kedaluwarsa" untuk daftar, detail, mendekati-kedaluwarsa & ringkasan.

kedaluwarsa = status menunggu jawaban (sent ATAU viewed — putusan MASTER: dilihat tapi tak dijawab lewat tanggal
tetap kedaluwarsa) DAN expiry_date < tanggal BISNIS tenant (bukan date.today()/CURRENT_DATE UTC server).
`hari_ini` WAJIB dari tanggal_dokumen(conn, tenant) dan dipakai SAMA oleh Python & SQL (satu tanggal per aksi).
Status tersimpan 'expired' (trigger check_quote_expiry) tidak diubah artinya.
"""
from datetime import date
from typing import Optional

MENUNGGU = ("sent", "viewed")


def kedaluwarsa(status: Optional[str], expiry_date: Optional[date], hari_ini: date) -> bool:
    return status in MENUNGGU and expiry_date is not None and expiry_date < hari_ini


def sql_kedaluwarsa(p: str) -> str:
    """Predikat SQL setara `kedaluwarsa` dengan hari_ini di parameter `p` (mis. "$2")."""
    return f"(status IN ('sent', 'viewed') AND expiry_date IS NOT NULL AND expiry_date < {p}::date)"


def sql_menunggu_aktif(p: str) -> str:
    """Menunggu jawaban & BELUM kedaluwarsa (tanpa tanggal = belum kedaluwarsa)."""
    return f"(status IN ('sent', 'viewed') AND (expiry_date IS NULL OR expiry_date >= {p}::date))"
