"""BUG-192: /labor tarif standar 0 -> biaya diterapkan 0 TANPA peringatan. Satu penentu `tarif ditemukan` + teks peringatan (perilaku terap TIDAK berubah; respons memberi tahu)."""
from decimal import Decimal
from typing import Optional

PERINGATAN_TARIF_NOL = ("Tarif standar 0: tidak ada biaya diterapkan. Pilih operasi atau Work Center bertarif; "
                        "biaya standar BOM tetap diterapkan saat output.")


def tarif_ditemukan(labor_rate_std, oh_rate_std) -> bool:
    """Ada tarif standar yang ditemukan: tarif labor ATAU overhead > 0 (keduanya 0 = tidak ditemukan)."""
    return Decimal(str(labor_rate_std or 0)) > 0 or Decimal(str(oh_rate_std or 0)) > 0


def peringatan(labor_rate_std, oh_rate_std) -> Optional[str]:
    return None if tarif_ditemukan(labor_rate_std, oh_rate_std) else PERINGATAN_TARIF_NOL
