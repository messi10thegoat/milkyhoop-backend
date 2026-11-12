"""Teks status + 'utama' + label BOM dari SATU peta (BUG-189, putusan pemilik 12 Nov 2026: ikut Accurate -- banyak BOM aktif per produk, satu UTAMA, WO memilih).

AKTIF (status 'active') = boleh dipakai Perintah Produksi. UTAMA = aktif DAN is_current = bawaan per produk (tepat satu bila produk punya BOM aktif). is_current pada draf/usang
TIDAK berarti apa-apa (default DB true) -> FE memakai `utama` dari server. USANG = manual."""
from typing import Optional

from . import teks_galat as tg

STATUS_TEKS = {"draft": "Draf", "active": "Aktif", "obsolete": "Usang"}


def status_text(status: Optional[str]) -> Optional[str]:
    return STATUS_TEKS.get(status) if isinstance(status, str) else None


def utama(status: Optional[str], is_current) -> bool:
    return status == "active" and bool(is_current)


def label(version, total_cost, status: Optional[str], is_current) -> str:
    """'v2 · Rp 52.300 · utama' | 'v1 · Rp 50.300' (aktif non-utama) | 'v3 · Rp 55.000 · draf' | '… · usang'."""
    bagian = [f"v{version}", tg.rp(total_cost or 0)]
    if utama(status, is_current):
        bagian.append("utama")
    elif status == "draft":
        bagian.append("draf")
    elif status == "obsolete":
        bagian.append("usang")
    return " · ".join(bagian)
