"""Faktor konversi DESIMAL (putusan pemilik LANGSUNG 5 Nov, pola Accurate): SATU validator satuan_dasar.faktor_sah untuk form barang
(schemas/items FaktorKonversi) DAN layar satuan (units.set_conversion): Decimal > 0, <= 10.000, <= 6 desimal. Bentuk baca tunggal
faktor_json (float) di semua GET -- dulu int/float bercampur, dan skema respons int akan gagal begitu ada faktor 2,5."""
import asyncio
import inspect
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.routers import items as RI
from app.routers import units as RU
from app.schemas.items import ItemListConversion, UnitConversionInput, UnitConversionResponse, UnitConversionUpdate
from app.services import satuan_dasar as SD

D = Decimal


@pytest.mark.parametrize("v,harap", [(2.5, D("2.5")), ("2.5", D("2.5")), (12, D("12")), ("12.000000", D("12")), (10000, D("10000")),
                                     ("0.000001", D("0.000001")), ("1.123456", D("1.123456"))])
def test_faktor_sah_diterima(v, harap):
    assert SD.faktor_sah(v) == harap


@pytest.mark.parametrize("v", [0, -1, 10001, "10000.000001", "1.1234567", True, "abc", "NaN", "Infinity", None, ""])
def test_faktor_sah_ditolak_berkalimat(v):
    with pytest.raises(ValueError) as e:
        SD.faktor_sah(v)
    assert str(e.value) == SD.PESAN_FAKTOR


def test_kedua_jalur_tulis_satu_validator_dan_skema_baca_desimal():
    assert UnitConversionInput(conversion_unit="Sak", conversion_factor=2.5).conversion_factor == D("2.5")
    assert UnitConversionUpdate(conversion_unit="Sak", conversion_factor="2.5").conversion_factor == D("2.5")
    for bad in (0, 10001, "1.1234567"):
        with pytest.raises(ValidationError):
            UnitConversionInput(conversion_unit="Sak", conversion_factor=bad)
    assert "SD.faktor_sah(" in inspect.getsource(RU.set_conversion)
    assert ItemListConversion(conversion_unit="Sak", conversion_factor=2.5).conversion_factor == 2.5  # dulu int -> galat baca
    assert UnitConversionResponse.model_fields["conversion_factor"].annotation is float


def test_bentuk_baca_tunggal_di_semua_get():
    assert SD.faktor_json(D("24.000000")) == 24.0 and SD.faktor_json(D("2.5")) == 2.5
    for fn in (RU.get_conversions, RU.list_all_conversions):
        s = inspect.getsource(fn)
        assert "SD.faktor_json(" in s and "int(r[\"conversion_factor\"])" not in s, fn.__name__
    assert inspect.getsource(RI).count("SD_faktor_json(") == 2


class _K:
    async def fetch(self, sql, *a):
        if "product_units" in sql:
            return []
        if "unit_conversions" in sql:
            return [{"product_id": "p", "base_unit": "kg", "conversion_unit": "Sak", "conversion_factor": D("2.5")}]
        return [{"id": "p", "nama_produk": "Pakan", "dasar": "kg"}]


def test_qty_dan_biaya_dasar_faktor_desimal_presisi_penuh():
    ktx = asyncio.run(SD.muat(_K(), "t", ["p"]))
    h = SD.hitung(ktx, "p", "Sak", 3, "konversi")
    assert (h["qty_dasar"], h["faktor"]) == (D("7.5"), D("2.5"))
    assert SD.biaya_dasar(D("300000"), h["qty_dasar"]) == D("40000")
