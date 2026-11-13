"""BUG-203: teks barang ditolak + petunjuk sisa PO untuk respons penerimaan (SATU pembentuk, dipakai detail GRN dan respons /receive)."""
from decimal import Decimal
from typing import Optional


def _q(v) -> str:
    d = Decimal(str(v or 0)).quantize(Decimal("0.0001")).normalize()
    t = format(d, "f")
    return t.rstrip("0").rstrip(".") if "." in t else t


def ditolak_text(qty, unit: Optional[str], alasan: Optional[str]) -> str:
    """'Ditolak 2 Pcs · rusak' (alasan hanya bila ada); qty 0 -> '' (baris tak punya penolakan)."""
    if Decimal(str(qty or 0)) <= 0:
        return ""
    bagian = [f"Ditolak {_q(qty)}" + (f" {unit}" if unit else "")]
    if alasan and str(alasan).strip():
        bagian.append(str(alasan).strip())
    return " · ".join(bagian)


def petunjuk_sisa(n_barang: int) -> str:
    return (f"Sisa pesanan masih terbuka ({n_barang} barang) dan menunggu kirim ulang. "
            "Bila sisanya tak akan datang, tutup pesanan (aksi Tutup).")
