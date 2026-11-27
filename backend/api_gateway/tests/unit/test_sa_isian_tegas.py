"""Isian tak dikenal di Penyesuaian = 422 (dulu diabaikan diam-diam); batch_id/unit_cost/total_value diberi arah."""
import pytest
from pydantic import ValidationError

from app.schemas import stock_adjustments as S
from app.services import galat_validasi as GV

IT = {"product_id": "p1", "quantity_adjustment": 1}


@pytest.mark.parametrize("k", ["batch_id", "unit_cost", "total_value"])
def test_isian_salah_diberi_arah(k):
    with pytest.raises(ValidationError) as e:
        S.StockAdjustmentItemCreate(**IT, **{k: 1})
    assert GV.kalimat(e.value.errors()[0]).startswith(f"{k}: ")


@pytest.mark.parametrize("m", [S.StockAdjustmentItemCreate, S.StockAdjustmentItemUpdate])
def test_isian_asing_ditolak(m):
    with pytest.raises(ValidationError) as e:
        m(**IT, foo=1)
    assert e.value.errors()[0]["type"] == "extra_forbidden"


def test_kepala_tegas_dan_badan_sah_tetap_lolos():
    with pytest.raises(ValidationError):
        S.CreateStockAdjustmentRequest(adjustment_date="2026-11-28", adjustment_type="increase", items=[IT], foo=1)
    with pytest.raises(ValidationError):
        S.UpdateStockAdjustmentRequest(foo=1)
    S.CreateStockAdjustmentRequest(adjustment_date="2026-11-28", adjustment_type="increase", warehouse_id="w",
                                   items=[{**IT, "unit": "Pcs", "serials": ["A1"], "warehouse_id": "w"}])
