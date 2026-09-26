"""Jatuh tempo — hari keterlambatan (26 Sep 2026). Status jatuh tempo SENDIRI tetap milik aturan tiap modul
(faktur: routers/sales_invoices._syarat_jatuh_tempo; tagihan: CASE calculated_status di bills_service) supaya
detail == daftar; di sini hanya turunan hari-nya, dari tanggal BISNIS tenant (tanggal_dokumen)."""
from datetime import date
from typing import Optional


def hari_terlambat(jatuh_tempo: bool, due_date: Optional[date], hari_ini: date) -> int:
    if not jatuh_tempo or due_date is None:
        return 0
    return max(0, (hari_ini - due_date).days)
