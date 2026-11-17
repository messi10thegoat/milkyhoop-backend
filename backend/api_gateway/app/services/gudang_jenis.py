"""G3 Gudang: teks jenis gudang DARI SERVER (FE tak merakit). is_branch di BE hanya penanda jenis lokasi (diukur dari kode: tak ada pembaca lain; stok/HPP/laporan/pajak tak berubah)."""
from typing import Optional

TEKS_CABANG = "Cabang"
TEKS_BIASA = "Gudang biasa"
CATATAN_CABANG = "Cabang hanya penanda jenis lokasi (mis. toko cabang); tidak mengubah stok, laporan, atau pajak."


def jenis_text(is_branch) -> str:
    return TEKS_CABANG if is_branch else TEKS_BIASA


def jenis_catatan(is_branch) -> Optional[str]:
    return CATATAN_CABANG if is_branch else None
