"""Item 7 Pembelian (25 Okt 2026): tipe medan uang RESPONS. Dulu `int` (Pydantic memotong / menolak sen). `Union[int, float]` saja TIDAK cukup:
Decimal('50000.00') dari DB divalidasi jadi float -> JSON "50000.0" (dulu "50000"). UangJson: bulat -> int (JSON byte-identik dgn dulu),
ber-sen -> float ("999.9"). Tanpa pembulatan di sini -- nilai sudah 2 desimal di DB/penghitung (q2)."""
from decimal import Decimal
from typing import Annotated, Union

from pydantic import BeforeValidator, Field, PlainSerializer


def _uang_json(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v))
        return int(d) if d == d.to_integral_value() else float(d)
    return v


UangJson = Annotated[Union[int, float], BeforeValidator(_uang_json)]


def _uang_json_keluar(d):
    """Decimal -> JSON: bulat -> int, ber-sen -> float (aturan SAMA dengan _uang_json)."""
    return _uang_json(d)


# ND-BUAT (29 Okt 2026, Law 9/25): tipe medan uang PERMINTAAN. Di dalam = Decimal 2 desimal (int lama tetap diterima; >2 desimal ditolak 422);
# keluar ke JSON (model_dump(mode="json") -> payload pratinjau + sidik idempotensi) = int bila bulat, float bila ber-sen -> payload/sidik untuk
# nominal bulat BYTE-IDENTIK dengan medan `int` lama, dan "1000" / 1000 / "1000.00" bermaksud sama -> sidik sama (Law 14).
UangMasuk = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2),
                      PlainSerializer(_uang_json_keluar, return_type=Union[int, float], when_used="json")]
