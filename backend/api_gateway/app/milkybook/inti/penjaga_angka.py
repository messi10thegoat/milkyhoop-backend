"""Penjaga angka: setiap angka uang/persen di jawaban harus ada di tool_result atau ucapan pengguna.

Law 0 / 3.1: angka berasal dari server, model hanya menjelaskan. Margin, selisih, atau total
yang dihitung model sendiri = pelanggaran (minta BE menghitung).

Yang diperiksa di teks jawaban:
  - nominal ber-"Rp"            (Rp 9.000.000, Rp9,99 jt)
  - bilangan >= 1.000           (9.000.000, 990.000, 45 ribu, 1,5 juta)
  - persen                      (37,8%, 11 %)
Yang tidak diperiksa: bilangan kecil tanpa Rp/% (jumlah langkah, hari, kuantitas kecil), tahun
di dalam tanggal (2026), nomor dokumen (QT-2610-0001).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, List, Set

_PENGALI = {
    "rb": 1000, "ribu": 1000, "k": 1000,
    "jt": 1_000_000, "juta": 1_000_000,
    "m": 1_000_000_000, "miliar": 1_000_000_000, "milyar": 1_000_000_000,
}

# Bilangan gaya Indonesia: 9.000.000 | 9.000.000,50 | 37,8 | 45 ; opsional pengali / persen.
_POLA = re.compile(
    r"(?<![\w./-])(?P<rp>Rp\.?\s?)?"
    r"(?P<angka>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"
    r"(?:\s?(?P<pengali>rb|ribu|jt|juta|miliar|milyar)\b)?"
    r"(?P<persen>\s?%)?",
    re.IGNORECASE,
)
# Bagian yang dibuang sebelum dipindai: kode dokumen dan tanggal.
_KODE_DOK = re.compile(r"\b[A-Z]{1,5}-[\w-]*\d[\w-]*\b")
_TANGGAL = re.compile(
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Agt|Sep|Okt|Nov|Des)\w*\.?\s+\d{4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b"
    # bulan + tahun tanpa hari ("Oktober 2026"): positif palsu eval 24 Okt (Sonnet PNW-032)
    r"|\b(?:Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Agt|Sep|Okt|Nov|Des)\w*\.?\s+\d{4}\b"
    r"|\b(?:tahun|th\.?)\s+\d{4}\b",
    re.IGNORECASE)


def _ke_decimal(teks: str) -> Decimal:
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", teks):
        teks = teks.replace(".", "").replace(",", ".")
    else:
        teks = teks.replace(",", ".")
    return Decimal(teks)


def angka_dalam_teks(teks: str, semua: bool = False) -> List[tuple]:
    """[(nilai Decimal, potongan asli, persen?)]. semua=False -> hanya yang wajib diperiksa."""
    bersih = _TANGGAL.sub(" ", _KODE_DOK.sub(" ", teks))
    hasil = []
    for m in _POLA.finditer(bersih):
        try:
            nilai = _ke_decimal(m.group("angka"))
        except InvalidOperation:
            continue
        pengali = (m.group("pengali") or "").lower()
        if pengali:
            nilai *= _PENGALI[pengali]
        persen = bool(m.group("persen"))
        wajib = bool(m.group("rp")) or persen or nilai >= 1000
        if semua or wajib:
            hasil.append((nilai, m.group(0).strip(), persen))
    return hasil


def _kumpulkan(obj: Any, keluar: Set[Decimal]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float, Decimal)):
        keluar.add(Decimal(str(obj)).normalize())
        return
    if isinstance(obj, str):
        s = obj.strip()
        try:
            keluar.add(Decimal(s).normalize())
            return
        except InvalidOperation:
            pass
        for nilai, _, _ in angka_dalam_teks(s, semua=True):
            keluar.add(nilai.normalize())
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _kumpulkan(v, keluar)
        return
    if isinstance(obj, (list, tuple)):
        for v in obj:
            _kumpulkan(v, keluar)


def angka_sah(sumber: Iterable[Any]) -> Set[Decimal]:
    """Himpunan angka dari tool_result (objek JSON atau string JSON) dan ucapan pengguna."""
    keluar: Set[Decimal] = set()
    for s in sumber:
        if isinstance(s, str):
            try:
                _kumpulkan(json.loads(s), keluar)
                continue
            except (ValueError, TypeError):
                pass
        _kumpulkan(s, keluar)
    return keluar


def _cocok(nilai: Decimal, persen: bool, sah: Set[Decimal]) -> bool:
    n = nilai.normalize()
    if n in sah:
        return True
    if persen:
        # 11% boleh tercatat 0.11 atau 11; 37,8% boleh 0.378.
        if (nilai / 100).normalize() in sah:
            return True
    # Pembulatan tampilan: "9,99 jt" dari 9990000 -> cocok bila selisih < 0,5% dan ditulis dgn pengali.
    return False


def periksa(teks_jawaban: str, sumber: Iterable[Any]) -> List[str]:
    """Daftar potongan angka yang TIDAK berasal dari sumber. Kosong = lolos."""
    sah = angka_sah(sumber)
    return [asli for nilai, asli, persen in angka_dalam_teks(teks_jawaban) if not _cocok(nilai, persen, sah)]
