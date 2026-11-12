"""BUG-184a (+ mitigasi interaksi BUG-195): satuan Perintah Produksi (WO) = yang dikirim; bila tak dikirim ikut satuan output BOM HANYA bila setara satuan dasar barang
(penentu/alias services/satuan_dasar); BOM bersatuan non-dasar ('Lusin') -> NULL (perilaku lama; Penerimaan produksi tetap lolos)."""
import asyncio
import inspect

import pytest

import app.routers.production as P
from app.services import satuan_dasar as SD


@pytest.mark.parametrize("body,bom,setara,harap", [
    ("Pcs", "Meter", False, "Pcs"), ("Pcs", "Meter", True, "Pcs"),             # yang dikirim selalu menang
    (None, "Pcs", True, "Pcs"), ("", "Pcs", True, "Pcs"), ("   ", " Pcs ", True, "Pcs"),
    (None, "Lusin", False, None), ("", "Lusin", False, None),                    # BOM non-dasar -> NULL
    (None, None, True, None), (None, "", True, None), (None, "Pcs", False, None),
])
def test_satuan_wo(body, bom, setara, harap):
    assert P.satuan_wo(body, bom, setara) == harap


def _ktx(dasar="Pcs", alias=None):
    return {"alias": alias or {}, "barang": {"p1": {"nama": "Kaos", "dasar": dasar, "konversi": {}}}}


def _setara(monkeypatch, unit, dasar="Pcs", alias=None, product="p1"):
    async def muat(conn, tid, ids):
        return _ktx(dasar, alias)
    monkeypatch.setattr(SD, "muat", muat)
    return asyncio.run(P.bom_unit_setara(None, "t1", product, unit))


def test_bom_pcs_atau_alias_setara_terisi_lusin_tidak(monkeypatch):
    assert _setara(monkeypatch, "Pcs") is True and _setara(monkeypatch, " pcs ") is True              # huruf besar/kecil + spasi dinormalisasi
    assert _setara(monkeypatch, "buah", alias={"buah": "pcs", "pcs": "pcs"}) is True                  # alias kamus tenant (penentu QTY-DASAR yang sama)
    assert _setara(monkeypatch, "Lusin") is False and _setara(monkeypatch, "Lusin-UJI") is False
    assert _setara(monkeypatch, "") is False and _setara(monkeypatch, None) is False
    assert _setara(monkeypatch, "Pcs", product="tak-ada") is False                                    # barang tak dikenal -> tak diisi


def test_buat_wo_memakai_satuan_turunan_bom_dengan_cek_kesetaraan():
    s = inspect.getsource(P.create_production_order)
    assert "_setara = await bom_unit_setara(conn, ctx[\"tenant_id\"], bom[\"product_id\"], _bom_unit)" in s and "wo_unit = satuan_wo(body.unit, _bom_unit, _setara)" in s
    assert s.index("wo_unit,") > s.index("body.planned_quantity,") and "                    body.unit,\n" not in s
    h = inspect.getsource(P.bom_unit_setara)
    assert "SD.hitung(" in h and '"tolak"' in h and "satuan_dasar" in h                               # penentu bersama, bukan daftar alias baru
