"""Penjaga tanggal: tanggal relatif ("besok", "2 hari lagi", "lewat 5 hari") harus cocok dengan kalender.

Temuan eval 24 Okt (K9, Haiku 5.5): "berlaku sampai 26 Oktober 2026, besok" padahal hari ini 24 Okt. Penjaga angka
tidak menangkapnya. Aturan: selisih hari dihitung SERVER (tool inti `hitung_tanggal`), bukan model.

Yang diperiksa: ungkapan relatif yang berdampingan dengan tanggal mutlak di KALIMAT yang sama. Ungkapan tanpa
tanggal di kalimatnya tidak bisa diperiksa dan dilewati (dicatat sebagai batas).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import List, Optional

_BULAN = {
    "jan": 1, "januari": 1, "feb": 2, "februari": 2, "mar": 3, "maret": 3, "apr": 4, "april": 4, "mei": 5,
    "jun": 6, "juni": 6, "jul": 7, "juli": 7, "agu": 8, "agt": 8, "agustus": 8, "sep": 9, "september": 9,
    "okt": 10, "oktober": 10, "nov": 11, "november": 11, "des": 12, "desember": 12,
}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")
_TEKS = re.compile(r"\b(\d{1,2})\s+(" + "|".join(sorted(_BULAN, key=len, reverse=True)) + r")\.?(?:\s+(\d{4}))?\b", re.I)

_RELATIF = [
    (re.compile(r"\blusa\b", re.I), lambda m: 2),
    (re.compile(r"\bbesok\b", re.I), lambda m: 1),
    (re.compile(r"\bkemarin\b", re.I), lambda m: -1),
    (re.compile(r"\b(?:tinggal|sisa|dalam|kurang)\s+(\d+)\s+hari\b", re.I), lambda m: int(m.group(1))),
    (re.compile(r"\b(\d+)\s+hari\s+lagi\b", re.I), lambda m: int(m.group(1))),
    (re.compile(r"\b(?:lewat|telat|terlambat)\s+(\d+)\s+hari\b", re.I), lambda m: -int(m.group(1))),
    (re.compile(r"\b(\d+)\s+hari\s+(?:yang\s+)?lalu\b", re.I), lambda m: -int(m.group(1))),
]


def _tanggal_di(kalimat: str, hari_ini: date) -> List[date]:
    hasil = []
    for m in _ISO.finditer(kalimat):
        try:
            hasil.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in _DMY.finditer(kalimat):
        try:
            hasil.append(date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
        except ValueError:
            pass
    for m in _TEKS.finditer(kalimat):
        try:
            hasil.append(date(int(m.group(3) or hari_ini.year), _BULAN[m.group(2).lower()], int(m.group(1))))
        except ValueError:
            pass
    return hasil


def periksa(teks: str, hari_ini: Optional[date]) -> List[str]:
    """Daftar potongan kalimat yang tanggal relatifnya tidak cocok dengan tanggal mutlak di kalimat itu."""
    if not hari_ini or not teks:
        return []
    salah = []
    for kalimat in re.split(r"(?<=[.!?;])\s+|\n+", teks):
        tanggal = _tanggal_di(kalimat, hari_ini)
        if not tanggal:
            continue
        for pola, geser in _RELATIF:
            for m in pola.finditer(kalimat):
                target = hari_ini + timedelta(days=geser(m))
                if target not in tanggal:
                    salah.append(f"'{m.group(0)}' di \"{kalimat.strip()[:120]}\" (hari ini {hari_ini.isoformat()})")
    return salah


def hitung(dari: date, sampai: date) -> dict:
    """Hasil tool inti hitung_tanggal: selisih hari kalender + nama hari (server, bukan model)."""
    nama = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    return {"dari": dari.isoformat(), "sampai": sampai.isoformat(), "selisih_hari": (sampai - dari).days,
            "hari_dari": nama[dari.weekday()], "hari_sampai": nama[sampai.weekday()]}
