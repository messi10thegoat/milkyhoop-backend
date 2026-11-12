"""Daftar BIAYA standar SO (tahap 1, kontrak MASTER 7 Nov 2026; pola QBO/NetSuite Expenses): kunci aksi, teks posisi, dokumen per baris
-- SATU penentu untuk GET /api/expenses (baris, ?aksi=, with_totals) dan /api/expenses/summary ({k}_count/_value). Server menghitung, FE
hanya memformat.

Kunci (saring; bukan partisi):
  draf        = status draft                                              -> PILL (hitung), desak 1
  tanpa_bukti = status posted ∧ tak ada lampiran (document_attachments)    -> PILL (hitung), desak 2
  bulan_ini   = status <> void ∧ bulan expense_date == bulan tanggal bisnis tenant -> KARTU (bukan nilai `aksi` baris)
Bukti = lampiran document_attachments entity expense, belum dihapus (kebenaran), BUKAN has_receipt (bendera saat buat; tak diturunkan saat lampiran dihapus).
Python `aksi_semua` dan SQL `sql_aksi` WAJIB setara (uji berpasangan). Nominal = total_amount dokumen (beku sesudah posted, Law 19)."""
from typing import Optional

AKSI_BIAYA = ("draf", "tanpa_bukti")   # urut desak; pill hitung
KARTU_BIAYA = ("bulan_ini",)           # kartu ringkasan, bukan pill
SEMUA_KUNCI = AKSI_BIAYA + KARTU_BIAYA


# B8a: SATU sumber bukti = lampiran NYATA (documents tertaut document_attachments entity 'expense', belum dihapus) --
# sama dgn GET /expenses/{id}/attachments. Tabel expense_attachments MATI (0 baris, tanpa penulis): tak boleh dibaca.
_BUKTI_DARI = ("document_attachments da JOIN documents d ON d.id = da.document_id AND d.tenant_id = da.tenant_id "
               "WHERE da.entity_type = 'expense' AND da.entity_id = {a}.id AND da.tenant_id = {a}.tenant_id AND d.deleted_at IS NULL")


def sql_ada_bukti(a: str = "expenses") -> str:
    return "EXISTS (SELECT 1 FROM " + _BUKTI_DARI.format(a=a) + ")"


def sql_jumlah_bukti(a: str = "expenses") -> str:
    return "(SELECT COUNT(*) FROM " + _BUKTI_DARI.format(a=a) + ")"


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


# ---- B1 (Biaya BE gelombang 1, 12 Nov 2026): keadaan + teks status dari SERVER (FE tak memetakan `status` mentah)
KEADAAN_BIAYA = {"draft": "draf", "posted": "dibayar", "void": "dibatalkan"}
STATUS_TEXT = {"draf": "Draf", "dibayar": "Dibayar", "dibatalkan": "Dibatalkan"}


def keadaan(status: Optional[str]) -> Optional[str]:
    """status DB -> biaya_keadaan (draf|dibayar|dibatalkan); status tak dikenal -> None (tak pernah menebak)."""
    return KEADAAN_BIAYA.get(status or "")


def status_text(status: Optional[str]) -> Optional[str]:
    """Kolom Status: Draf | Dibayar | Dibatalkan (position_text tetap teks ringkas "Dibayar dari BCA · tanpa bukti")."""
    k = keadaan(status)
    return STATUS_TEXT[k] if k else None


# ---- B4: lingkup tiap angka summary dinyatakan SERVER (total_* ikut period; pill/kartu = dokumen, bukan per periode) tanpa mengubah makna angka
PERIOD_LABEL = {"week": "7 hari terakhir", "month": "Bulan ini", "quarter": "Kuartal ini", "year": "Tahun ini"}


def period_label(period: str) -> str:
    return PERIOD_LABEL.get(period, period)


def cakupan(period: str) -> dict:
    """{total: label periode, draf: Semua, tanpa_bukti: Semua, bulan_ini: Bulan ini} -- klik pill = ?aksi= global (jumlah cocok), kartu bulan_ini = bulan tanggal bisnis."""
    return {"total": period_label(period), "draf": "Semua", "tanpa_bukti": "Semua", "bulan_ini": "Bulan ini"}


def blok_hapus(existing) -> Optional[tuple]:
    """B7: SATU penentu hapus draf untuk DELETE /{id} DAN POST /{id}/delete/preview -> (status HTTP, kode, kalimat) | None."""
    if not existing:
        return 404, "EXPENSE_TIDAK_DITEMUKAN", "Biaya tidak ditemukan."
    if existing["status"] != "draft":
        return 400, "BIAYA_BUKAN_DRAF", "Hanya biaya draf yang bisa dihapus; batalkan biaya yang sudah diterbitkan."
    return None


def documents_baris(journal_id, jumlah_bukti: int) -> list:
    """documents[{type}] baris daftar: biaya itu sendiri · jurnal (bila ada) · tiap bukti (pola kolom Dok. modul lain)."""
    return [{"type": "expense"}] + ([{"type": "journal"}] if journal_id else []) + [{"type": "attachment"}] * int(jumlah_bukti or 0)
