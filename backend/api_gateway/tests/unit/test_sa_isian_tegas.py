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


@pytest.mark.parametrize("jenis,baris", [
    ("increase", {**IT, "unit": "Karton", "serials": ["S1"]}),
    ("decrease", {"product_id": "p1", "quantity_adjustment": -2, "reason_detail": "rusak", "warehouse_id": "w"}),
    ("recount", {"product_id": "p1", "quantity_adjustment": 0, "physical_quantity": 7}),
    ("damaged", {**IT}), ("expired", {**IT}),
    ("decrease", {"product_id": "p1", "quantity_adjustment": -1, "warehouse_id": "t", "source_transfer_id": "x", "reason_detail": "Hilang"}),
])
def test_kontrak_payload_pratinjau_diterima_create(jenis, baris):
    """FE buat draf = payload pratinjau apa adanya (= body.model_dump(mode='json')); harus lolos skema create, semua jenis."""
    body = S.CreateStockAdjustmentRequest(adjustment_date="2026-11-28", adjustment_type=jenis, warehouse_id="w",
                                          reference_no="R", notes="n", items=[baris])
    payload = body.model_dump(mode="json")
    assert S.CreateStockAdjustmentRequest.model_validate(payload).model_dump(mode="json") == payload


def test_kontrak_isian_dari_pemindahan():
    import inspect
    from app.services import stok_sesuai as SS
    src = inspect.getsource(SS.tarik_pemindahan)
    for k in S.ISIAN_DARI_PEMINDAHAN:
        assert f'"{k}"' in src and k in S.StockAdjustmentItemCreate.model_fields


def test_model_lama_items_tidak_ditegaskan():
    from app.routers import items as R
    for nama in ("StockAdjustmentRequest", "QuickStockAdjustmentRequest"):
        m = getattr(R, nama, None)
        if m is not None:
            assert m.model_config.get("extra") != "forbid"
