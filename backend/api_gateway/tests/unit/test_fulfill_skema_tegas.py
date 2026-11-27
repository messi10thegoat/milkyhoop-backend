"""POST /sales-invoices/{id}/fulfill: isian asing = 422 (dulu diabaikan; TESTER kirim 'allocations' -> tak dinilai)."""
import pytest
from fastapi.exceptions import RequestValidationError

from app.routers import sales_invoices as R
from app.services import galat_validasi as GV

BARIS = {"invoice_item_id": "00000000-0000-0000-0000-000000000001", "quantity": 2}


def test_badan_sah_lolos():
    R._validasi_kirim({"warehouse_id": None, "fulfillment_date": "2026-11-28", "recognize_revenue": True, "notes": "x", "idempotency_key": "k",
                       "items": [{**BARIS, "batch_id": "00000000-0000-0000-0000-000000000002", "serials": ["A1"]}]})
    R._validasi_kirim({"items": []})


def test_allocations_diberi_arah():
    with pytest.raises(RequestValidationError) as e:
        R._validasi_kirim({"items": [{**BARIS, "allocations": [{"batch_id": "x", "qty": 1}]}]})
    assert GV.kalimat(e.value.errors()[0]).startswith("allocations: ")


@pytest.mark.parametrize("badan", [{"items": [{**BARIS, "item_id": "x"}]}, {"items": [BARIS], "foo": 1}, {"items": [{**BARIS, "batch_id": "bukan-uuid"}]}, []])
def test_asing_atau_salah_ditolak(badan):
    with pytest.raises(RequestValidationError):
        R._validasi_kirim(badan)


def test_handler_memvalidasi_dan_meneruskan_422():
    import inspect
    src = inspect.getsource(R.fulfill_invoice)
    assert src.index("_validasi_kirim(body)") < src.index("body.get(\"warehouse_id\")")
    assert "except RequestValidationError:" in src
