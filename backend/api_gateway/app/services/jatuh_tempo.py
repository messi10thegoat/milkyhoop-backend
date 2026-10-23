"""Jatuh tempo — hari keterlambatan (26 Sep 2026). Status jatuh tempo SENDIRI tetap milik aturan tiap modul
(faktur: routers/sales_invoices._syarat_jatuh_tempo; tagihan: CASE calculated_status di bills_service) supaya
detail == daftar; di sini hanya turunan hari-nya, dari tanggal BISNIS tenant (tanggal_dokumen)."""
from datetime import date
from typing import Optional


def hari_terlambat(jatuh_tempo: bool, due_date: Optional[date], hari_ini: date) -> int:
    if not jatuh_tempo or due_date is None:
        return 0
    return max(0, (hari_ini - due_date).days)


# ---- UMUR (aging) dari jatuh tempo EFEKTIF -- SATU rumus untuk Faktur Penjualan (faktur_aksi), Faktur Pembelian (tagihan_daftar) dan
# widget aging dashboard T2 (KONTRAK-dashboard-T2 §5, 23 Okt 2026; pola QBO/Xero A/R-A/P Aging). Telat = hari ini − jatuh tempo efektif
# (due ?? tanggal dokumen, ditentukan pemanggil): ≤ 0 current · 1–30 · 31–60 · 61–90 · > 90. Tanpa tanggal = tanpa umur.
# Kunci umur_* = kunci SARING ?aksi= (BUKAN pill: partisi pill & nilai `aksi` baris tak berubah). Predikat "terbuka / sisa > 0" milik
# modul masing-masing; di sini hanya umurnya. Python `kelas_umur` dan SQL `sql_umur` WAJIB setara (uji berpasangan di semua batas).
UMUR_KUNCI = ("umur_current", "umur_1_30", "umur_31_60", "umur_61_90", "umur_90_plus")
UMUR_KE_BUCKET = {"umur_current": "current", "umur_1_30": "d1_30", "umur_31_60": "d31_60", "umur_61_90": "d61_90",
                  "umur_90_plus": "d90_plus"}  # kunci bucket dashboard T2 §5
_RENTANG = (("umur_1_30", 1, 30), ("umur_31_60", 31, 60), ("umur_61_90", 61, 90))


def kelas_umur(jt_efektif: Optional[date], hari_ini: date) -> Optional[str]:
    if jt_efektif is None:
        return None
    telat = (hari_ini - jt_efektif).days
    if telat <= 0:
        return "umur_current"
    for kunci, bawah, atas in _RENTANG:
        if bawah <= telat <= atas:
            return kunci
    return "umur_90_plus"


def sql_umur(kunci: str, eff: str, p_hari: str) -> str:
    """Predikat SQL setara kelas_umur(eff, hari) == kunci. eff = ungkapan tanggal (NULL -> tak cocok); p_hari = placeholder tanggal."""
    h = f"{p_hari}::date"
    if kunci == "umur_current":
        return f"({eff} >= {h})"
    if kunci == "umur_90_plus":
        return f"({eff} < {h} - 90)"
    _, bawah, atas = next(r for r in _RENTANG if r[0] == kunci)
    return f"({eff} <= {h} - {bawah} AND {eff} >= {h} - {atas})"
