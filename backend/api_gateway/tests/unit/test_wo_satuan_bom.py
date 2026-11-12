"""BUG-184a: satuan Perintah Produksi (WO) terisi dari satuan output BOM bila tak dikirim (dulu NULL)."""
import inspect

import pytest

import app.routers.production as P


@pytest.mark.parametrize("body,bom,harap", [("Pcs", "Meter", "Pcs"), (None, "Pcs", "Pcs"), ("", "Pcs", "Pcs"), ("   ", " Pcs ", "Pcs"), (None, None, None), (None, "", None)])
def test_satuan_wo(body, bom, harap):
    assert P.satuan_wo(body, bom) == harap


def test_buat_wo_memakai_satuan_turunan_bom():
    s = inspect.getsource(P.create_production_order)
    assert 'wo_unit = satuan_wo(body.unit, bom["output_unit"])' in s
    assert s.index("wo_unit,") > s.index("body.planned_quantity,") and "                    body.unit,\n" not in s
