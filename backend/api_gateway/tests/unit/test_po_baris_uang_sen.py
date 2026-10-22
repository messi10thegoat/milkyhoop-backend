"""Temuan WORKSPACE 22 Okt 2026: skema respons baris PO masih int -> PO ber-sen (unit_price 2dp sejak P-U2) membuat GET detail 500."""
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")
from app.schemas import purchase_orders as PS  # noqa: E402


def test_baris_ber_sen_lolos_validasi_respons():
    b = PS.PurchaseOrderItemResponse(id="x", description="Kain", quantity=3, unit_price=1500.50, discount_amount=150.05,
                                     tax_amount=148.55, subtotal=4501.50, total=4500.00)
    assert b.unit_price == 1500.50 and b.discount_amount == 150.05 and b.tax_amount == 148.55 and b.subtotal == 4501.50


def test_detail_dan_daftar_juga_float():
    for kelas in (PS.PurchaseOrderDetail, PS.PurchaseOrderListItem):
        for k, f in kelas.model_fields.items():
            if any(x in k for x in ("amount", "subtotal", "total", "price", "sisa_ditagih")) and k != "doc_count":
                assert f.annotation is not int, (kelas.__name__, k)
