"""Rentang periode laporan (26 Sep 2026, sapuan tanggal bisnis B) — this_month / last_month / this_year.

Batas dari tanggal BISNIS tenant (tanggal_dokumen), dibagi per tanggal DOKUMEN (bukan created_at): bulan =
services/rp_periode.batas_periode (tgl 1–akhir bulan, definisi yang sama dengan ringkasan Penerimaan/Pembayaran);
tahun = 1 Jan–31 Des. "all"/lainnya = None (tanpa saringan).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple

from .rp_periode import batas_periode


def rentang_periode(period: str, hari_ini: date) -> Optional[Tuple[date, date]]:
    if period == "this_month":
        b = batas_periode(hari_ini)
        return b["month_start"], b["month_end"]
    if period == "last_month":
        akhir_lalu = hari_ini.replace(day=1) - timedelta(days=1)
        b = batas_periode(akhir_lalu)
        return b["month_start"], b["month_end"]
    if period == "this_year":
        return date(hari_ini.year, 1, 1), date(hari_ini.year, 12, 31)
    return None
