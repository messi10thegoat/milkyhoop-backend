"""Kirim faktur: sisa baris dinilai atas TOTAL diminta sebelum dipecah per batch (pesan jujur: diminta 4, bukan potongan 2)."""
import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.routers import sales_invoices as R


class Kon:
    async def fetch(self, sql, *a):
        return [{"id": "b1", "description": "UJI Lot Susu", "quantity": Decimal("10"), "fulfilled_qty": Decimal("8")}]


def test_total_melebihi_sisa_pesan_jujur():
    with pytest.raises(HTTPException) as e:
        asyncio.run(R._cek_sisa_total(Kon(), {"id": "f"}, [{"invoice_item_id": "b1", "quantity": 2}, {"invoice_item_id": "b1", "quantity": 2}, {"invoice_item_id": "b1", "quantity": 0}]))
    assert e.value.status_code == 409 and "hanya 2" in e.value.detail and "diminta 4" in e.value.detail


def test_total_cukup_lolos():
    asyncio.run(R._cek_sisa_total(Kon(), {"id": "f"}, [{"invoice_item_id": "b1", "quantity": 1}, {"invoice_item_id": "b1", "quantity": 1}]))


def test_dipanggil_sebelum_pecah():
    import inspect
    src = inspect.getsource(R._execute_fulfillment)
    assert src.index("_cek_sisa_total(") < src.index("pecah_kirim(")
