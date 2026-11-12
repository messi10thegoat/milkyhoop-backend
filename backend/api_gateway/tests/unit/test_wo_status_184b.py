"""BUG-184b: teks status WO Indonesia dari SATU peta + penanda siap_diselesaikan (tanpa penutupan otomatis) di respons daftar dan detail."""
import inspect
from decimal import Decimal as D

import pytest

import app.routers.production as P
from app.schemas import production as SC
from app.services import wo_status as WS


def test_peta_status_indonesia_semua_status_sah():
    assert WS.STATUS_TEKS == {"draft": "Draf", "planned": "Direncanakan", "released": "Dirilis", "in_progress": "Sedang Proses", "completed": "Selesai", "cancelled": "Dibatalkan"}
    assert WS.status_text("released") == "Dirilis" and WS.status_text("zzz") is None and WS.status_text(None) is None
    assert not any(t.lower() in ("released", "in progress", "completed", "cancelled", "draft") for t in WS.STATUS_TEKS.values())     # bukan teks mentah/Inggris


@pytest.mark.parametrize("status,selesai,rencana,harap", [
    ("in_progress", 50, 50, True), ("in_progress", 60, 50, True), ("in_progress", 49, 50, False), ("in_progress", 0, 50, False),
    ("released", 50, 50, False), ("completed", 50, 50, False), ("cancelled", 50, 50, False), ("draft", 50, 50, False),
    ("in_progress", None, 50, False), ("in_progress", 5, 0, False), ("in_progress", D("49.99"), D("50"), False),
])
def test_siap_diselesaikan(status, selesai, rencana, harap):
    assert WS.siap_diselesaikan(status, selesai, rencana) is harap


def test_respons_daftar_dan_detail_mendeklarasikan_dan_mengisi():
    assert "status_text" in SC.ProductionOrderListItem.model_fields and "siap_diselesaikan" in SC.ProductionOrderListItem.model_fields
    assert "status_text" in SC.ProductionOrderDetail.model_fields and "siap_diselesaikan" in SC.ProductionOrderDetail.model_fields
    for fn in (P.list_production_orders, P.get_production_order):
        s = inspect.getsource(fn)
        assert "WS.status_text(" in s and "WS.siap_diselesaikan(" in s, fn.__name__


def test_tak_ada_penutupan_otomatis():
    s = inspect.getsource(P.report_output)
    assert "status = 'completed'" not in s            # report-output TIDAK menutup WO; hanya POST /{id}/complete
    assert "SET status = 'completed'" in inspect.getsource(P.complete_order)
