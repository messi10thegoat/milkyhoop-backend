"""BUG-289 (17 Nov 2026): Gudang -- `country_text` siap tampil dari server (kode ISO "ID" -> "Indonesia"; nama apa adanya; kosong -> None); ada di daftar, detail, dan data sesudah tulis."""
import inspect

import pytest

from app.routers import warehouses as WH
from app.schemas.warehouses import WarehouseData
from app.services import gudang_daftar as GD


@pytest.mark.parametrize("masuk,keluar", [("ID", "Indonesia"), ("id", "Indonesia"), (" my ", "Malaysia"), ("SG", "Singapura"), ("Indonesia", "Indonesia"),
                                          ("  Republik   Ceko ", "Republik Ceko"), ("ZZ", "ZZ"), ("", None), (None, None), ("   ", None)])
def test_negara_text(masuk, keluar):
    assert GD.negara_text(masuk) == keluar


def test_kode_dua_huruf_saja_dipetakan_nama_panjang_tak_disentuh():
    assert GD.negara_text("Id") == "Indonesia" and GD.negara_text("Idn") == "Idn"          # hanya kode alfa-2; "Idn"/nama lain apa adanya
    assert all(len(k) == 2 and k.isupper() for k in GD.NEGARA)


def test_schema_dan_semua_jalur_baca_tulis_mengisi_country_text():
    assert "country_text" in WarehouseData.model_fields
    d = WarehouseData(id="00000000-0000-0000-0000-000000000001", code="G", name="G", is_default=False, is_active=True, is_branch=False, created_at="2026-11-17T00:00:00", country="ID", country_text="Indonesia")
    assert d.model_dump()["country_text"] == "Indonesia" and d.country == "ID"           # kode mentah tetap ada (aditif)
    for fn in (WH.list_warehouses, WH.get_warehouse, WH._data):                                                                      # daftar + detail + data sesudah tulis
        assert 'country_text=GD.negara_text(row["country"])' in inspect.getsource(fn), fn.__name__
