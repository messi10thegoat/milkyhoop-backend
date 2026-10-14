"""W-20261014-PESAN-BAWAH-DITAGIH: 409 SO_TOTAL_BELOW_COMMITTED menyebut HANYA komponen pengikat (tertinggi) + selisih;
komponen nol tak disebut; rincian (minimum, komponen) tetap di badan untuk FE. Pembangun TUNGGAL dipakai ubah()."""
import inspect
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.services import so_ubah_terkonfirmasi as SUT


def _batas(dp=0, pf=0, rnb=0, fk=0):
    dp, pf, rnb, fk = D(dp), D(pf), D(rnb), D(fk)
    return {"uang_muka_diterima": dp, "proforma_terbit": pf, "uang_muka_di_luar_tagihan": rnb,
            "tertagih_proforma": pf + rnb, "faktur_terbit": fk, "batas": max(dp, pf + rnb, fk)}


def _tolak(total, b):
    with pytest.raises(HTTPException) as e:
        SUT.galat_di_bawah_tertagih(D(total), b)
    assert e.value.status_code == 409 and e.value.detail["code"] == "SO_TOTAL_BELOW_COMMITTED"
    return e.value.detail


def test_grapgrap_so_2610_0014_seri_dp_proforma_uang_muka_menang():
    d = _tolak("6149988", _batas(dp=6150000, pf=6150000))
    assert d["message"] == "Total Rp 6.149.988 kurang dari uang muka diterima Rp 6.150.000 (selisih Rp 12)."
    assert d["binding_component"] == "uang_muka_diterima" and d["shortfall"] == 12.0
    assert d["minimum_total"] == 6150000.0 and d["new_total"] == 6149988.0
    assert d["components"] == {"uang_muka_diterima": 6150000.0, "proforma_terbit": 6150000.0,
                               "uang_muka_di_luar_tagihan": 0.0, "faktur_terbit": 0.0}


def test_faktur_tertinggi():
    d = _tolak("900000", _batas(dp=500000, pf=500000, fk=1000000))
    assert d["message"] == "Total Rp 900.000 kurang dari faktur terbit Rp 1.000.000 (selisih Rp 100.000)."
    assert d["binding_component"] == "faktur_terbit"


def test_faktur_seri_uang_muka_faktur_menang():
    assert _tolak("1", _batas(dp=1000, fk=1000))["binding_component"] == "faktur_terbit"


def test_proforma_tertinggi_tanpa_uang_muka_lepas():
    d = _tolak("700000", _batas(dp=300000, pf=800000))
    assert d["message"] == "Total Rp 700.000 kurang dari proforma terbit Rp 800.000 (selisih Rp 100.000)."
    assert d["binding_component"] == "tertagih_proforma"


def test_proforma_plus_uang_muka_lepas():
    d = _tolak("1000000", _batas(dp=1200000, pf=800000, rnb=500000))
    assert d["message"] == ("Total Rp 1.000.000 kurang dari proforma terbit + uang muka di luar tagihan "
                            "Rp 1.300.000 (selisih Rp 300.000).")


def test_nol_tak_disebut():
    m = _tolak("100", _batas(dp=500))["message"]
    for kata in ("proforma", "faktur", "di luar tagihan", "Rp 0"):
        assert kata not in m


def test_ubah_memakai_pembangun_tunggal():
    src = inspect.getsource(SUT.ubah)
    assert "galat_di_bawah_tertagih(total_baru, batas)" in src
    assert "SO_TOTAL_BELOW_COMMITTED" not in src  # kode galat hanya di pembangun
