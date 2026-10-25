"""Item 7 Pembelian (25 Okt 2026): tipe medan uang RESPONS. Dulu `int` (Pydantic memotong / menolak sen). `Union[int, float]` saja TIDAK cukup:
Decimal('50000.00') dari DB divalidasi jadi float -> JSON "50000.0" (dulu "50000"). UangJson: bulat -> int (JSON byte-identik dgn dulu),
ber-sen -> float ("999.9"). Tanpa pembulatan di sini -- nilai sudah 2 desimal di DB/penghitung (q2)."""
from decimal import Decimal
from typing import Annotated, Union

from pydantic import BeforeValidator


def _uang_json(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v))
        return int(d) if d == d.to_integral_value() else float(d)
    return v


UangJson = Annotated[Union[int, float], BeforeValidator(_uang_json)]
