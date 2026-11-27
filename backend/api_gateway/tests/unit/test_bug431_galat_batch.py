"""BUG-431: galat batch di kirim faktur BERKODE (dulu 400 tanpa kode): BATCH_STOK_KURANG 409 / BATCH_TIDAK_DIKENAL 422, menyebut batch + gudang."""
import asyncio
from decimal import Decimal

import pytest

from app.routers import sales_invoices as R
from app.services.galat_berkode import GalatBerkode


class Kon:
    def __init__(self, batch):
        self.batch = batch

    async def fetchval(self, sql, *a):
        return "Gudang Utama"

    async def fetchrow(self, sql, *a):
        return self.batch


def jalankan(k, batch_id, pid="p1"):
    with pytest.raises(GalatBerkode) as e:
        asyncio.run(R._galat_batch(k, "t", batch_id, "w", "Susu", Decimal("2"), Decimal("4"), product_id=pid))
    return e.value


def test_batch_kurang_berkode_sebut_batch_dan_gudang():
    e = jalankan(Kon({"batch_number": "L2", "item_id": "p1"}), "b2")
    assert (e.status_code, e.code) == (409, "BATCH_STOK_KURANG") and "L2" in e.detail and "Gudang Utama" in e.detail and "diminta 4" in e.detail


@pytest.mark.parametrize("batch", [None, {"batch_number": "X", "item_id": "lain"}])
def test_batch_fiktif_atau_barang_lain(batch):
    e = jalankan(Kon(batch), "bx")
    assert (e.status_code, e.code) == (422, "BATCH_TIDAK_DIKENAL")


def test_fefo_kurang_berkode():
    e = jalankan(Kon(None), None)
    assert (e.status_code, e.code) == (409, "BATCH_STOK_KURANG")


def test_tak_ada_lagi_400_batch_tanpa_kode():
    import inspect
    assert "HTTPException(400, f\"Stok batch" not in inspect.getsource(R._execute_fulfillment)
