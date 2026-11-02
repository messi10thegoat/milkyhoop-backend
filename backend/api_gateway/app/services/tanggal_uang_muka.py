"""Tanggal penerapan uang muka -- SATU sumber (TGL-UM, 2 Nov 2026; putusan LANGSUNG pemilik 2 Nov, pola NetSuite).

- OTOMATIS (posting faktur dari SO: DP -> SI; posting FPB dari PO: UMP -> FPB): tanggal penerapan = max(tanggal faktur,
  tanggal uang muka). Penerapan tak pernah bertanggal sebelum uangnya ada. Periode tetap diperiksa penulis (Law 5).
- MANUAL (/customer-deposits/{id}/apply, /vendor-deposits/{id}/apply + pratinjaunya): tanggal < tanggal uang muka -> DITOLAK
  berkalimat (bukan digeser diam-diam). Dipasang di penentu rencana_terapkan, jadi pratinjau == tulis; jalur otomatis tak
  pernah kena karena tanggalnya sudah max.
Data lama (3 penerapan DP grapgrap sebelum tanggal DP) DIBIARKAN (putusan pemilik): blok hanya pada terapkan, bukan lepas/void.
"""
from datetime import date

KODE = "TANGGAL_SEBELUM_UANG_MUKA"
MEDAN_DP = "application_date"
MEDAN_UMP = "applied_date"
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _tanggal(x):
    if x is None or isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def tgl(x) -> str:
    d = _tanggal(x)
    return f"{d.day} {BULAN[d.month - 1]} {d.year}" if d else "-"


def tanggal_terap_otomatis(tgl_faktur, tgl_uang_muka):
    """max(tanggal faktur, tanggal uang muka); tanggal uang muka kosong -> tanggal faktur."""
    f, u = _tanggal(tgl_faktur), _tanggal(tgl_uang_muka)
    if u is None or f is None:
        return f if f is not None else u
    return max(f, u)


def sebelum_uang_muka(tgl_terap, tgl_uang_muka) -> bool:
    t, u = _tanggal(tgl_terap), _tanggal(tgl_uang_muka)
    return t is not None and u is not None and t < u


def kalimat(nomor_uang_muka, tgl_terap, tgl_uang_muka) -> str:
    return (f"Tanggal penerapan {tgl(tgl_terap)} tidak boleh sebelum tanggal uang muka {nomor_uang_muka} "
            f"({tgl(tgl_uang_muka)}).")
