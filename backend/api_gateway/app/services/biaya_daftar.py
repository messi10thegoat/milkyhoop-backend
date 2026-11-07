"""Daftar BIAYA standar SO (tahap 1, kontrak MASTER 7 Nov 2026; pola QBO/NetSuite Expenses): kunci aksi, teks posisi, dokumen per baris
-- SATU penentu untuk GET /api/expenses (baris, ?aksi=, with_totals) dan /api/expenses/summary ({k}_count/_value). Server menghitung, FE
hanya memformat.

Kunci (saring; bukan partisi):
  draf        = status draft                                              -> PILL (hitung), desak 1
  tanpa_bukti = status posted ∧ tak ada lampiran (expense_attachments)    -> PILL (hitung), desak 2
  bulan_ini   = status <> void ∧ bulan expense_date == bulan tanggal bisnis tenant -> KARTU (bukan nilai `aksi` baris)
Bukti = baris expense_attachments (kebenaran), BUKAN has_receipt (bendera saat buat; tak diturunkan saat lampiran dihapus).
Python `aksi_semua` dan SQL `sql_aksi` WAJIB setara (uji berpasangan). Nominal = total_amount dokumen (beku sesudah posted, Law 19)."""
from typing import Optional

AKSI_BIAYA = ("draf", "tanpa_bukti")   # urut desak; pill hitung
KARTU_BIAYA = ("bulan_ini",)           # kartu ringkasan, bukan pill
SEMUA_KUNCI = AKSI_BIAYA + KARTU_BIAYA


def sql_ada_bukti(a: str = "expenses") -> str:
    return f"EXISTS (SELECT 1 FROM expense_attachments ea WHERE ea.expense_id = {a}.id AND ea.tenant_id = {a}.tenant_id)"


def sql_aksi(kunci: str, p_hari: str, a: str = "expenses") -> str:
    """Predikat SQL setara aksi_semua(...) / bulan_ini. p_hari = placeholder tanggal bisnis tenant."""
    return {
        "draf": f"{a}.status = 'draft'",
        "tanpa_bukti": f"({a}.status = 'posted' AND NOT {sql_ada_bukti(a)})",
        "bulan_ini": f"({a}.status <> 'void' AND date_trunc('month', {a}.expense_date) = date_trunc('month', {p_hari}::date))",
    }[kunci]


def aksi_semua(status: Optional[str], ada_bukti: bool) -> list:
    hasil = []
    if status == "draft":
        hasil.append("draf")
    if status == "posted" and not ada_bukti:
        hasil.append("tanpa_bukti")
    return hasil


def position_text(status: Optional[str], rekening: Optional[str], ada_bukti: bool) -> str:
    """Teks posisi SERVER (putusan MASTER 7 Nov: "· tanpa bukti" ikut, ringkas)."""
    if status == "draft":
        return "Draf"
    if status == "void":
        return "Dibatalkan"
    teks = f"Dibayar dari {rekening}" if rekening else "Dibayar"
    return teks if ada_bukti else f"{teks} · tanpa bukti"


def documents_baris(journal_id, jumlah_bukti: int) -> list:
    """documents[{type}] baris daftar: biaya itu sendiri · jurnal (bila ada) · tiap bukti (pola kolom Dok. modul lain)."""
    return [{"type": "expense"}] + ([{"type": "journal"}] if journal_id else []) + [{"type": "attachment"}] * int(jumlah_bukti or 0)
