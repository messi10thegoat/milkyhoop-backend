"""Tool inti hitung_harga_sasaran: pengguna menyebut TOTAL sasaran yang tak cocok dengan harga master.
Model tidak boleh membagi/mengurangi sendiri (aturan ANGKA), jadi opsi dihitung di sini (Decimal, rupiah bulat HALF_UP)
lalu model WAJIB memeriksa opsi pilihan dengan hitung_penawaran (BE) sebelum menyebut total. Angka di sini = perkiraan.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import List, Optional

_RP = Decimal("1")
_PERSEN = Decimal("0.01")


def _d(x) -> Decimal:
    if isinstance(x, bool):
        raise ValueError("bukan angka")
    return Decimal(str(x))


def hitung(baris: List[dict], target_total, tarif_pajak=0, termasuk_pajak: bool = False) -> dict:
    """baris = [{harga_satuan, jumlah}] dari harga master; tarif_pajak persen (mis. 11); termasuk_pajak = target sudah
    termasuk pajak. Hasil: subtotal master, DPP sasaran, dan opsi (diskon dokumen rupiah, diskon persen per baris,
    harga satuan baru / jumlah pas bila satu baris)."""
    try:
        if not baris:
            raise ValueError("baris kosong")
        harga = [_d(b["harga_satuan"]) for b in baris]
        jumlah = [_d(b["jumlah"]) for b in baris]
        target, tarif = _d(target_total), _d(tarif_pajak or 0)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError("masukan tidak sah: baris[{harga_satuan, jumlah}], target_total, tarif_pajak angka")
    if target <= 0 or tarif < 0 or any(h < 0 for h in harga) or any(j <= 0 for j in jumlah):
        raise ValueError("target_total > 0, tarif_pajak >= 0, harga >= 0, jumlah > 0")
    subtotal = sum((h * j for h, j in zip(harga, jumlah)), Decimal(0))
    dpp = (target / (1 + tarif / 100)) if termasuk_pajak else target
    dpp = dpp.quantize(_RP, ROUND_HALF_UP)
    opsi: List[dict] = []
    if subtotal > 0 and dpp < subtotal:
        opsi.append({"jenis": "diskon_dokumen_rupiah", "discount_type": "fixed",
                     "discount_value": str(subtotal - dpp)})
        persen = ((1 - dpp / subtotal) * 100).quantize(_PERSEN, ROUND_HALF_UP)
        opsi.append({"jenis": "diskon_persen_baris", "discount_percent": str(persen)})
    if len(baris) == 1:
        harga_baru = (dpp / jumlah[0]).quantize(_RP, ROUND_HALF_UP)
        if harga_baru != harga[0]:
            opsi.append({"jenis": "harga_satuan_baru", "unit_price": str(harga_baru), "quantity": str(jumlah[0])})
        if harga[0] > 0:
            pas: Optional[Decimal] = dpp / harga[0]
            if pas == pas.to_integral_value() and pas != jumlah[0]:
                opsi.append({"jenis": "jumlah_pas", "quantity": str(pas.to_integral_value()), "unit_price": str(harga[0])})
    return {"subtotal_master": str(subtotal), "target_total": str(target), "termasuk_pajak": bool(termasuk_pajak),
            "tarif_pajak": str(tarif), "dpp_sasaran": str(dpp), "opsi": opsi,
            "catatan": "Perkiraan. Periksa opsi dengan hitung_penawaran sebelum menyebut total; pembulatan pajak "
                       "server bisa beda beberapa rupiah. Pilihan opsi = keputusan pengguna."}
